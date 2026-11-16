"""Price alerts: "tell me when this sells for above / below X".

The price watched is the one in the Price column: what you'd receive for one plain copy, selling
where it pays most, after that market's fees (following the "Values from" switch). Alerts are checked after every price refresh; one
that's set off shows in the dashboard and as a Windows notification, then waits until you turn it
back on (so it doesn't go off again every refresh).
"""
from __future__ import annotations

import logging
from typing import Any

from . import db, settings
from .currency import FxConverter

log = logging.getLogger(__name__)

DIRECTIONS = ("above", "below")
MAX_ALERTS = 500


def _base() -> str:
    return settings.get_all()["base_currency"]


def current_value(valuator, name: str) -> float | None:
    """What one plain copy of ``name`` would bring in now, after fees (base currency)."""
    value = valuator.best_net.get(name)
    if value is None:   # no usable listing: priced from recent sales (or the sticker inside), as the tables are
        best = next((o for o in valuator.sell_options(valuator.fallback_quotes(name)) if o["counted"]), None)
        value = best["net"] if best else None
    return value


def add(name: str, direction: str, price: float) -> int:
    if direction not in DIRECTIONS:
        raise ValueError("Choose above or below.")
    if not price or price <= 0:
        raise ValueError("Enter a price above zero.")
    if (db.query_one("SELECT COUNT(*) AS n FROM price_alerts")["n"] or 0) >= MAX_ALERTS:
        raise ValueError(f"You can have up to {MAX_ALERTS} alerts. Delete some first.")
    return db.execute(
        "INSERT INTO price_alerts (market_hash_name, direction, price, currency, created_at) VALUES (?, ?, ?, ?, ?)",
        (name, direction, float(price), _base(), db.utcnow_iso()))


def listing(valuator) -> dict[str, Any]:
    """Every alert with what the item sells for now, set-off ones first."""
    from . import images

    fx = valuator.fx
    rows = db.query("SELECT * FROM price_alerts ORDER BY triggered_at IS NULL, triggered_at DESC, id DESC")
    icons = images.lookup({r["market_hash_name"] for r in rows})
    out = []
    for row in rows:
        now = current_value(valuator, row["market_hash_name"])
        out.append({
            "id": row["id"], "name": row["market_hash_name"], "direction": row["direction"],
            "price": fx.convert(row["price"], row["currency"], valuator.base),
            "now": now, "triggered_at": row["triggered_at"],
            "triggered_value": (fx.convert(row["triggered_value"], row["currency"], valuator.base)
                                if row["triggered_value"] is not None else None),
            "seen": bool(row["seen"]), "icon": images.icon_src(icons.get(row["market_hash_name"])),
        })
    return {"currency": valuator.base, "alerts": out, "unseen": sum(1 for a in out if a["triggered_at"] and not a["seen"])}


def unseen_count() -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM price_alerts WHERE triggered_at IS NOT NULL AND seen = 0")
    return int(row["n"]) if row else 0


def check(valuator=None) -> list[dict[str, Any]]:
    """Set off every waiting alert whose price has been reached. Returns the ones set off now."""
    waiting = db.query("SELECT * FROM price_alerts WHERE triggered_at IS NULL")
    if not waiting:
        return []
    if valuator is None:
        from .valuation import Valuator

        valuator = Valuator()
    fx: FxConverter = valuator.fx
    now, fired = db.utcnow_iso(), []
    for row in waiting:
        value = current_value(valuator, row["market_hash_name"])
        if value is None:
            continue
        value = fx.convert(value, valuator.base, row["currency"])
        if value is None:
            continue
        reached = value >= row["price"] if row["direction"] == "above" else value <= row["price"]
        if reached:
            db.execute("UPDATE price_alerts SET triggered_at = ?, triggered_value = ?, seen = 0 WHERE id = ?",
                       (now, value, row["id"]))
            fired.append({"id": row["id"], "name": row["market_hash_name"], "direction": row["direction"],
                          "price": row["price"], "value": value, "currency": row["currency"]})
    return fired


def notify_fired(fired: list[dict[str, Any]]) -> None:
    """A Windows notification for alerts that just went off (never breaks the refresh)."""
    if not fired:
        return
    from . import notify

    def line(a):
        return f"{a['name']}: {_money(a['value'], a['currency'])} ({a['direction']} {_money(a['price'], a['currency'])})"

    try:
        title = "Price alert" if len(fired) == 1 else f"{len(fired)} price alerts"
        notify.toast(title, [line(a) for a in fired[:2]])
    except Exception:  # noqa: BLE001
        log.exception("Couldn't show the price alert notification")


def _money(value: float, currency: str) -> str:
    symbol = {"GBP": "£", "USD": "$", "EUR": "€"}.get(currency)
    return f"{symbol}{value:,.2f}" if symbol else f"{value:,.2f} {currency}"


def rearm(alert_id: int) -> None:
    if not db.query_one("SELECT 1 FROM price_alerts WHERE id = ?", (alert_id,)):
        raise ValueError("That alert isn't there any more.")
    db.execute("UPDATE price_alerts SET triggered_at = NULL, triggered_value = NULL, seen = 0 WHERE id = ?", (alert_id,))


def delete(alert_id: int) -> None:
    db.execute("DELETE FROM price_alerts WHERE id = ?", (alert_id,))


def mark_seen() -> None:
    db.execute("UPDATE price_alerts SET seen = 1 WHERE triggered_at IS NOT NULL AND seen = 0")
