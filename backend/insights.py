"""What changed: biggest movers, and the short summary shown in a notification after an update."""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from config import Config

from . import settings

SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€", "CNY": "¥", "JPY": "¥"}


def fmt_money(value: float, currency: str) -> str:
    symbol = SYMBOLS.get(currency)
    text = f"{abs(value):,.2f}"
    sign = "-" if value < 0 else ""
    return f"{sign}{symbol}{text}" if symbol else f"{sign}{text} {currency}"


def _storage(frame: pd.DataFrame) -> pd.Series:
    """Storage units and other containers holding items (cases and capsules are items like any other)."""
    return (frame["is_storage"].fillna(0).astype(int) == 1) | (frame["contents_count"] > 0)


def _change_in_value(value: float, pct: float) -> float:
    """How much a value moved, given its value now and its % change."""
    return value - value / (1 + pct / 100) if pct > -100 else value


def movers(frame: pd.DataFrame, window: str = "change_24h", limit: int = 5) -> dict[str, list[dict[str, Any]]]:
    """Items whose after-fee value rose or fell the most (in money) over ``window``.
    Copies across entries are added together."""
    empty = {"gainers": [], "losers": []}
    if frame.empty or window not in frame:
        return empty
    rows = frame[~_storage(frame) & frame[window].notna() & frame["total_value"].notna()]
    if rows.empty:
        return empty
    moved: dict[str, dict[str, Any]] = {}
    for row in rows.itertuples(index=False):
        pct = float(getattr(row, window))
        if not math.isfinite(pct):
            continue
        entry = moved.setdefault(row.market_hash_name, {"name": row.market_hash_name, "pct": pct, "change": 0.0,
                                                        "value": 0.0, "quantity": 0, "icon_url": row.icon_url})
        entry["change"] += _change_in_value(float(row.total_value), pct)
        entry["value"] += float(row.total_value)
        entry["quantity"] += int(row.quantity)
    ranked = sorted(moved.values(), key=lambda e: e["change"])
    return {"gainers": [e for e in reversed(ranked) if e["change"] > 0][:limit],
            "losers": [e for e in ranked if e["change"] < 0][:limit]}


def best_markets(frame: pd.DataFrame) -> dict[str, str]:
    """Where each item sells best right now (name -> market)."""
    if frame.empty:
        return {}
    rows = frame[frame["sell_market"].notna()].drop_duplicates("market_hash_name")
    return dict(zip(rows["market_hash_name"], rows["sell_market"]))


def update_summary(frame: pd.DataFrame, summary: dict[str, Any], currency: str) -> dict[str, Any]:
    """The few things worth telling you after an update: total and today's change, the biggest
    mover each way, items whose best market changed since last time, big moves and ignored listings."""
    from .markets import market_label

    total = float(summary.get("total_value") or 0.0)
    pct = summary.get("change_24h")
    today = _change_in_value(total, pct) if pct is not None else None
    moved = movers(frame, limit=1)
    threshold = Config.ALERT_MOVE_PCT
    big = 0
    if not frame.empty and "change_24h" in frame:
        listed = frame[~_storage(frame) & frame["change_24h"].notna()]
        big = int((listed["change_24h"].abs() >= threshold).sum())
    now = best_markets(frame)
    before = settings.get("last_best_markets") or {}
    switched = [name for name, market in now.items() if before.get(name) and before[name] != market]
    settings.set_value("last_best_markets", now)
    ignored = int(frame["set_aside"].map(len).sum()) if not frame.empty and "set_aside" in frame else 0
    return {
        "total": total, "currency": currency, "today": today, "today_pct": pct,
        "gainer": moved["gainers"][0] if moved["gainers"] else None,
        "loser": moved["losers"][0] if moved["losers"] else None,
        "big_moves": big, "threshold": threshold,
        "switched": len(switched),
        "switched_example": (switched[0], market_label(now[switched[0]])) if switched else None,
        "ignored": ignored,
    }


def notification_text(info: dict[str, Any]) -> tuple[str, list[str]]:
    """Title and up to two short lines for the notification."""
    cur = info["currency"]
    title = f"Inventory {fmt_money(info['total'], cur)}"
    if info["today"] is not None and abs(info["today"]) >= 0.01:
        arrow = "▲" if info["today"] > 0 else "▼"
        title += f"  {arrow} {fmt_money(abs(info['today']), cur)} ({info['today_pct']:+.1f}%) since yesterday"
    lines = []
    movers_line = []
    if info["gainer"]:
        g = info["gainer"]
        movers_line.append(f"Up most: {g['name']} {g['pct']:+.0f}% (+{fmt_money(g['change'], cur)})")
    if info["loser"]:
        lo = info["loser"]
        movers_line.append(f"Down most: {lo['name']} {lo['pct']:+.0f}% ({fmt_money(lo['change'], cur)})")
    if movers_line:
        lines.append(" · ".join(movers_line))
    extra = []
    if info["big_moves"]:
        extra.append(f"{info['big_moves']} item{'s' if info['big_moves'] != 1 else ''} moved {info['threshold']:g}%+")
    if info["switched"]:
        name, market = info["switched_example"]
        extra.append(f"{name} now sells best on {market}" if info["switched"] == 1
                     else f"{info['switched']} items now sell best on another market")
    if info["ignored"]:
        extra.append(f"{info['ignored']} unrealistic listing{'s' if info['ignored'] != 1 else ''} ignored")
    if extra:
        lines.append(" · ".join(extra))
    if not lines:
        lines.append("Prices are up to date. Open the dashboard for details.")
    return title, lines


# ---------------------------------------------------------------------------
# Portfolio mix
# ---------------------------------------------------------------------------
# Fixed order (and so fixed colours): a group keeps its colour whatever else is shown.
MIX_GROUPS = (
    ("knives", "Knives & gloves", {"Knife", "Gloves"}),
    ("skins", "Weapon skins", None),                       # any other weapon type
    ("stickers", "Stickers & patches", {"Sticker", "Patch"}),
    ("slabs", "Slabs & charms", {"Sticker slab", "Charm"}),
    ("containers", "Cases & capsules", {"Case", "Capsule", "Souvenir package", "Container", "Storage unit"}),
    ("other", "Other", set()),
)
WEAPON_TYPES = {"Rifle", "Sniper rifle", "Pistol", "SMG", "Shotgun", "Machine gun", "Zeus", "Other weapon"}


def mix_group(item_type: str | None) -> str:
    for key, _label, types in MIX_GROUPS:
        if types and item_type in types:
            return key
    return "skins" if item_type in WEAPON_TYPES else "other"


def mix(frame: pd.DataFrame, valuator, range_key: str = "90d") -> dict[str, Any]:
    """How the after-fee value splits across groups now, and how that split has changed.

    The history is an estimate for what you hold now: each item's value today, followed back
    through that item's own daily price (the best you'd have got after fees). So the last bar is
    exactly the split shown for "now" - big stacks, stickers and custom prices included - and
    earlier bars move with each item's market. Items with no price history stay level."""
    from . import history

    labels = {key: label for key, label, _ in MIX_GROUPS}
    empty_history = {"labels": [], "series": []}
    if frame.empty:
        return {"groups": [], "history": empty_history}
    groups = frame["tags"].map(lambda t: mix_group((t or {}).get("type")))
    now = frame.assign(group=groups).groupby("group")["total_value"].sum(min_count=1).fillna(0.0)
    current = [{"key": key, "label": labels[key], "value": float(now.get(key, 0.0))}
               for key, _label, _types in MIX_GROUPS if now.get(key, 0.0) > 0]

    held = frame[~_storage(frame) & (frame["total_value"].fillna(0) > 0)].assign(group=groups)
    per_item = held.groupby("market_hash_name").agg(value=("total_value", "sum"), group=("group", "first"))
    if per_item.empty:
        return {"groups": current, "history": empty_history}
    days = history.RANGES.get(range_key, 90)
    since = history._days_ago(days) if days else None
    daily = history.best_after_fees(per_item.index.tolist(), valuator, since)
    if daily.empty:
        return {"groups": current, "history": empty_history}

    prices = daily.pivot_table(index="date", columns="market_hash_name", values="value", aggfunc="last")
    prices.index = pd.to_datetime(prices.index)
    today = pd.Timestamp(history.today())
    price_now = pd.Series({name: valuator.best_net.get(name) for name in per_item.index}, dtype="float64")
    for name, price in price_now.dropna().items():
        if price > 0:
            prices.loc[today, name] = price            # today's bar uses today's live price
    days_shown = pd.date_range(min(prices.index.min(), today), today, freq="D")
    prices = prices.sort_index().reindex(days_shown).ffill().bfill()

    columns = {}
    for name, item in per_item.iterrows():
        price = price_now.get(name)
        if name in prices.columns and pd.notna(price) and price > 0:
            columns[name] = item["value"] * prices[name] / price
        else:
            columns[name] = pd.Series(item["value"], index=days_shown)
    table = pd.DataFrame(columns, index=days_shown).T.groupby(per_item["group"]).sum().T
    series = [{"key": key, "label": labels[key], "values": [round(float(v), 2) for v in table[key]]}
              for key, _label, _types in MIX_GROUPS if key in table.columns and table[key].sum() > 0]
    return {"groups": current, "history": {"labels": [d.strftime("%Y-%m-%d") for d in table.index], "series": series}}
