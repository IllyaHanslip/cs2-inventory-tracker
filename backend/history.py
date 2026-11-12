"""Daily snapshots, price-change calculations and chart series (pandas based)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from . import db, settings
from .classify import applied_hash_name
from .currency import FxConverter
from .markets import market_label

RANGES = {"7d": 7, "30d": 30, "90d": 90, "1y": 365, "all": None}
CHANGE_WINDOWS = {  # column: (days back, how many extra days of slack are acceptable)
    "change_24h": (1, 2),
    "change_7w": (49, 10),
}


def today() -> str:
    return date.today().isoformat()


def _days_ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def has_snapshot_today() -> bool:
    return db.query_one("SELECT 1 FROM portfolio_snapshots WHERE snapshot_date = ?", (today(),)) is not None


# ---------------------------------------------------------------------------
# Writing snapshots
# ---------------------------------------------------------------------------
def take_snapshot(valuator=None) -> dict[str, Any]:
    """Store today's valuation price for every owned item plus portfolio totals."""
    from . import portfolio  # local import to avoid a circular dependency
    from .valuation import Valuator

    v = valuator or Valuator()
    frame = portfolio.build_frame(v, "total", with_changes=False)
    if frame.empty:
        return {"skipped": True, "reason": "no items"}

    names = set(frame["market_hash_name"])
    for applied in frame["stickers"]:
        names.update(applied_hash_name(a["kind"], a["name"]) for a in applied)
    day = today()
    rows = [(n, "valuation", day, p, v.base) for n in sorted(names) if (p := v.price(n)) is not None]
    db.executemany(
        "INSERT INTO price_snapshots (market_hash_name, market, snapshot_date, price, currency) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(market_hash_name, market, snapshot_date) DO UPDATE SET price = excluded.price, "
        "currency = excluded.currency",
        rows,
    )

    totals = {}
    for scope, subset in (("public", frame[frame["source"] == "steam"]), ("total", frame)):
        value = float(subset["total_value"].fillna(0).sum())
        count = int(subset["quantity"].sum())
        totals[scope] = value
        db.execute(
            "INSERT INTO portfolio_snapshots (snapshot_date, scope, total_value, item_count, currency) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(snapshot_date, scope) DO UPDATE SET "
            "total_value = excluded.total_value, item_count = excluded.item_count, currency = excluded.currency",
            (day, scope, value, count, v.base),
        )
    settings.set_value("last_snapshot", db.utcnow_iso())
    return {"date": day, "prices": len(rows), "totals": totals, "currency": v.base}


# ---------------------------------------------------------------------------
# Change calculations
# ---------------------------------------------------------------------------
def _to_base(df: pd.DataFrame, value_col: str, fx: FxConverter, base: str) -> pd.Series:
    return pd.Series(
        [fx.convert(v, c, base, d) for v, c, d in zip(df[value_col], df["currency"], df["date"])],
        index=df.index,
        dtype="float64",
    )


def _reference_values(hist: pd.DataFrame, key: str, days: int, slack: int) -> pd.Series:
    """Latest value per key dated within [today-days-slack, today-days]."""
    target, earliest = _days_ago(days), _days_ago(days + slack)
    window = hist[(hist["date"] <= target) & (hist["date"] >= earliest)]
    return window.sort_values("date").groupby(key)["value"].last()


def price_changes(names: list[str], valuator) -> pd.DataFrame:
    """24-hour and 7-week change (%) of each item's valuation price."""
    result = pd.DataFrame({"market_hash_name": list(names)})
    for col in CHANGE_WINDOWS:
        result[col] = float("nan")
    if not names:
        return result

    oldest = _days_ago(max(d + s for d, s in CHANGE_WINDOWS.values()))
    rows: list[dict] = []
    unique = sorted(set(names))
    for i in range(0, len(unique), 800):  # stay below SQLite's parameter limit
        chunk = unique[i:i + 800]
        rows += db.query(
            "SELECT market_hash_name, snapshot_date AS date, price, currency FROM price_snapshots "
            f"WHERE market = 'valuation' AND snapshot_date >= ? AND market_hash_name IN ({','.join('?' * len(chunk))})",
            (oldest, *chunk),
        )
    if not rows:
        return result
    hist = pd.DataFrame(rows)
    hist["value"] = _to_base(hist, "price", valuator.fx, valuator.base)
    hist = hist.dropna(subset=["value"])
    hist = hist[hist["value"] > 0]

    current = result["market_hash_name"].map(valuator.prices)
    for col, (days, slack) in CHANGE_WINDOWS.items():
        ref = result["market_hash_name"].map(_reference_values(hist, "market_hash_name", days, slack))
        result[col] = (current - ref) / ref * 100
    return result


def portfolio_changes(scope: str, current_total: float, fx: FxConverter, base: str) -> dict[str, float | None]:
    rows = db.query(
        "SELECT snapshot_date AS date, total_value, currency FROM portfolio_snapshots WHERE scope = ? AND snapshot_date >= ?",
        (scope, _days_ago(max(d + s for d, s in CHANGE_WINDOWS.values()))),
    )
    out: dict[str, float | None] = {col: None for col in CHANGE_WINDOWS}
    if not rows:
        return out
    hist = pd.DataFrame(rows)
    hist["scope"] = scope
    hist["value"] = _to_base(hist, "total_value", fx, base)
    for col, (days, slack) in CHANGE_WINDOWS.items():
        ref = _reference_values(hist.dropna(subset=["value"]), "scope", days, slack)
        if scope in ref and ref[scope] > 0:
            out[col] = (current_total - ref[scope]) / ref[scope] * 100
    return out


# ---------------------------------------------------------------------------
# Chart series
# ---------------------------------------------------------------------------
def _cutoff(range_key: str) -> str | None:
    days = RANGES.get(range_key, 90)
    return _days_ago(days) if days else None


def portfolio_history(scope: str, range_key: str, fx: FxConverter, base: str) -> dict[str, Any]:
    cutoff = _cutoff(range_key)
    sql = "SELECT snapshot_date AS date, total_value, item_count, currency FROM portfolio_snapshots WHERE scope = ?"
    params: list[Any] = [scope]
    if cutoff:
        sql += " AND snapshot_date >= ?"
        params.append(cutoff)
    rows = db.query(sql + " ORDER BY snapshot_date", params)
    if not rows:
        return {"labels": [], "values": [], "counts": [], "currency": base}
    df = pd.DataFrame(rows)
    df["value"] = _to_base(df, "total_value", fx, base)
    df.index = pd.to_datetime(df["date"])
    daily = df[["value", "item_count"]].asfreq("D").ffill()  # continuous line across missed days
    return {
        "labels": [d.strftime("%Y-%m-%d") for d in daily.index],
        "values": [round(v, 2) if pd.notna(v) else None for v in daily["value"]],
        "counts": [int(c) if pd.notna(c) else None for c in daily["item_count"]],
        "currency": base,
    }


def item_history(market_hash_name: str, range_key: str, fx: FxConverter, base: str) -> dict[str, Any]:
    cutoff = _cutoff(range_key)
    sql = "SELECT market, snapshot_date AS date, price, currency FROM price_snapshots WHERE market_hash_name = ?"
    params: list[Any] = [market_hash_name]
    if cutoff:
        sql += " AND snapshot_date >= ?"
        params.append(cutoff)
    rows = db.query(sql, params)
    if not rows:
        return {"labels": [], "series": [], "currency": base}
    df = pd.DataFrame(rows)
    df["value"] = _to_base(df, "price", fx, base)
    wide = df.pivot_table(index="date", columns="market", values="value", aggfunc="last")
    wide.index = pd.to_datetime(wide.index)
    wide = wide.asfreq("D").ffill(limit=7)  # bridge short gaps only
    order = ["valuation"] + sorted(c for c in wide.columns if c != "valuation")
    series = [
        {
            "market": col,
            "label": market_label(col),
            "values": [round(v, 4) if pd.notna(v) else None for v in wide[col]],
        }
        for col in order
        if col in wide.columns
    ]
    return {"labels": [d.strftime("%Y-%m-%d") for d in wide.index], "series": series, "currency": base}
