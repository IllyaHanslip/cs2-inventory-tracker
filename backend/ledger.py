"""Profit & loss: items coming in (bought) and going out (sold), with prices only where you gave them.

Prices are optional everywhere. You might not remember what you paid for something years ago, or
not want to say what you sold it for, so each figure is worked out from the items that have the
prices it needs:

* **Actual profit** - items sold where you gave both what you paid and what you got.
* **Money gained** - everything you sold for: every item sold with a price, whatever you paid.
* **Money spent** - every item bought with a price.

Entries come from the Containers tab (confirming a Steam sync's changes: "sold for…" / "bought
for…"), from adding an item with a purchase price, from removing one you say you sold, and from
anything you record by hand on the Profit & loss tab.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from . import db, images, settings
from .classify import categorize
from .currency import FxConverter
from .insights import MIX_GROUPS, mix_group

MODES = ("profit", "gained", "spent")


def record(conn, kind: str, name: str, quantity: int, price: float | None, currency: str | None, *,
           cost: float | None = None, cost_currency: str | None = None, source: str = "manual",
           happened_at: str | None = None, details: dict | None = None, display_name: str | None = None) -> int:
    """Add an entry (inside the caller's transaction). Returns its id."""
    now = db.utcnow_iso()
    if price is not None:
        currency = currency or base_currency()
    if cost is not None:
        cost_currency = cost_currency or currency or base_currency()
    return conn.execute(
        "INSERT INTO ledger (kind, happened_at, market_hash_name, name, quantity, price, currency, cost, cost_currency, "
        "source, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (kind, happened_at or now, name, display_name or name, int(quantity), price, currency if price is not None else None,
         cost, cost_currency if cost is not None else None, source, json.dumps(details or {}), now),
    ).lastrowid


def base_currency() -> str:
    return settings.get_all()["base_currency"]


# ---------------------------------------------------------------------------
# Working out the figures
# ---------------------------------------------------------------------------
def _group(name: str) -> str:
    """Knives & gloves, weapon skins, stickers... as in the portfolio mix."""
    from . import catalogue
    from .tags import item_tags

    return mix_group(item_tags(name, categorize(name), None, catalogue.item_meta(allow_fetch=False)).get("type"))


def entries(fx: FxConverter | None = None, base: str | None = None) -> list[dict[str, Any]]:
    """Every entry, newest first, with prices converted to your currency (at that day's rate) and
    each sale's profit where it can be worked out."""
    fx = fx or FxConverter.load()
    base = base or base_currency()
    rows = db.query("SELECT * FROM ledger ORDER BY happened_at DESC, id DESC")
    icons = images.lookup({r["market_hash_name"] for r in rows})
    labels = {key: label for key, label, _ in MIX_GROUPS}
    out = []
    for row in rows:
        day = row["happened_at"][:10]
        price = fx.convert(row["price"], row["currency"], base, day) if row["price"] is not None else None
        cost = fx.convert(row["cost"], row["cost_currency"], base, day) if row["cost"] is not None else None
        qty = row["quantity"]
        entry = {
            "id": row["id"], "kind": row["kind"], "happened_at": row["happened_at"], "name": row["name"],
            "market_hash_name": row["market_hash_name"], "quantity": qty, "price": price, "cost": cost,
            "total": price * qty if price is not None else None, "source": row["source"],
            "icon": images.icon_src(icons.get(row["market_hash_name"])),
            "group": labels.get(_group(row["market_hash_name"]), "Other"),
            "profit": None, "gained": None,
        }
        if row["kind"] == "out" and price is not None:
            entry["gained"] = price * qty                               # what you sold it for
            if cost is not None:
                entry["profit"] = (price - cost) * qty
        out.append(entry)
    return out


def summary(holding: dict[str, Any] | None = None) -> dict[str, Any]:
    """Totals for each way of looking at it, month by month, by kind of item, and the best and
    worst sales."""
    fx = FxConverter.load()
    base = base_currency()
    rows = entries(fx, base)
    ins = [r for r in rows if r["kind"] == "in"]
    outs = [r for r in rows if r["kind"] == "out"]
    profit_rows = [r for r in outs if r["profit"] is not None]
    gained_rows = [r for r in outs if r["gained"] is not None]
    spent_rows = [r for r in ins if r["total"] is not None]
    cost_of_sold = sum(r["cost"] * r["quantity"] for r in profit_rows)
    totals = {
        "profit": {"value": sum(r["profit"] for r in profit_rows), "items": sum(r["quantity"] for r in profit_rows),
                   "entries": len(profit_rows), "cost": cost_of_sold,
                   "sold_for": sum(r["total"] for r in profit_rows),
                   "margin": (sum(r["profit"] for r in profit_rows) / cost_of_sold * 100) if cost_of_sold else None},
        "gained": {"value": sum(r["gained"] for r in gained_rows), "items": sum(r["quantity"] for r in gained_rows),
                   "entries": len(gained_rows), "sold_for": sum(r["total"] for r in gained_rows),
                   "with_cost": sum(r["quantity"] for r in gained_rows if r["cost"] is not None)},
        "spent": {"value": sum(r["total"] for r in spent_rows), "items": sum(r["quantity"] for r in spent_rows),
                  "entries": len(spent_rows)},
    }
    counts = {
        "in": sum(r["quantity"] for r in ins), "in_priced": sum(r["quantity"] for r in spent_rows),
        "out": sum(r["quantity"] for r in outs), "out_priced": sum(r["quantity"] for r in gained_rows),
    }

    # Month by month, for each way of looking at it.
    months: dict[str, dict[str, float]] = {}
    for r in rows:
        month = months.setdefault(r["happened_at"][:7], {"profit": 0.0, "gained": 0.0, "spent": 0.0})
        if r["profit"] is not None:
            month["profit"] += r["profit"]
        if r["gained"] is not None:
            month["gained"] += r["gained"]
        if r["kind"] == "in" and r["total"] is not None:
            month["spent"] += r["total"]
    monthly = [{"month": m, **v} for m, v in sorted(months.items())]

    # By kind of item.
    groups: dict[str, dict[str, float]] = {}
    for r in rows:
        g = groups.setdefault(r["group"], {"profit": 0.0, "gained": 0.0, "spent": 0.0, "bought": 0, "sold": 0})
        if r["kind"] == "in":
            g["bought"] += r["quantity"]
            if r["total"] is not None:
                g["spent"] += r["total"]
        else:
            g["sold"] += r["quantity"]
            if r["profit"] is not None:
                g["profit"] += r["profit"]
            if r["gained"] is not None:
                g["gained"] += r["gained"]
    by_group = [{"group": name, **v} for name, v in sorted(groups.items(), key=lambda kv: -abs(kv[1]["gained"]))]

    ranked = sorted(profit_rows, key=lambda r: r["profit"], reverse=True)
    return {
        "currency": base, "totals": totals, "counts": counts, "monthly": monthly, "by_group": by_group,
        "best": [r for r in ranked if r["profit"] > 0][:5], "worst": [r for r in reversed(ranked) if r["profit"] < 0][:5],
        "holding": holding, "entries": rows,
    }


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------
def add(kind: str, name: str, quantity: int, price: float | None, happened_at: str | None,
        cost: float | None = None) -> int:
    if kind not in ("in", "out"):
        raise ValueError("Choose bought or sold.")
    currency = base_currency()
    with db.connect() as conn:
        return record(conn, kind, name, quantity, price, currency, cost=cost if kind == "out" else None,
                      cost_currency=currency, happened_at=happened_at)


def update(entry_id: int, fields: dict[str, Any]) -> None:
    """Change an entry's price (or cost, quantity, date). Prices are in your current currency."""
    row = db.query_one("SELECT * FROM ledger WHERE id = ?", (entry_id,))
    if not row:
        raise ValueError("That entry isn't there any more.")
    currency = base_currency()
    sets, params = [], []
    if "price" in fields:
        sets += ["price = ?", "currency = ?"]
        params += [fields["price"], currency if fields["price"] is not None else None]
    if "cost" in fields and row["kind"] == "out":
        sets += ["cost = ?", "cost_currency = ?"]
        params += [fields["cost"], currency if fields["cost"] is not None else None]
    if fields.get("quantity"):
        sets.append("quantity = ?")
        params.append(int(fields["quantity"]))
    if fields.get("happened_at"):
        sets.append("happened_at = ?")
        params.append(fields["happened_at"])
    if sets:
        db.execute(f"UPDATE ledger SET {', '.join(sets)} WHERE id = ?", (*params, entry_id))


def delete(entry_id: int) -> None:
    db.execute("DELETE FROM ledger WHERE id = ?", (entry_id,))


def today_iso() -> str:
    return date.today().isoformat()
