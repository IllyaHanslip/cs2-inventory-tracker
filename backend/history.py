"""Daily snapshots, price-change calculations and chart series (pandas based).

Values are what you'd receive selling, after each market's fees. Item-level history is worked
out from the per-market prices saved every day, so it always uses your current fee settings.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import pandas as pd

from . import db, settings
from .classify import applied_hash_name
from .currency import FxConverter
from .markets import MANUAL_PREFIX, market_label

log = logging.getLogger(__name__)

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
        value = float(subset["total_value"].fillna(0).sum())  # after fees
        market = float(subset["market_total_value"].fillna(0).sum())
        count = int(subset["quantity"].sum())
        totals[scope] = value
        db.execute(
            "INSERT INTO portfolio_snapshots (snapshot_date, scope, total_value, item_count, currency, market_value, basis) "
            "VALUES (?, ?, ?, ?, ?, ?, 'after_fees') ON CONFLICT(snapshot_date, scope) DO UPDATE SET "
            "total_value = excluded.total_value, item_count = excluded.item_count, currency = excluded.currency, "
            "market_value = excluded.market_value, basis = excluded.basis",
            (day, scope, value, count, v.base, market),
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


def _snapshot_rows(names: list[str], since: str | None, *, valuation: bool) -> list[dict]:
    """Saved daily prices for these items: the 'valuation' rows, or every market's rows."""
    rows: list[dict] = []
    unique = sorted(set(names))
    for i in range(0, len(unique), 800):  # stay below SQLite's parameter limit
        chunk = unique[i:i + 800]
        sql = ("SELECT market_hash_name, market, snapshot_date AS date, price, currency FROM price_snapshots "
               f"WHERE market {'=' if valuation else '!='} 'valuation' AND market_hash_name IN ({','.join('?' * len(chunk))})")
        params: list[Any] = list(chunk)
        if since:
            sql += " AND snapshot_date >= ?"
            params.append(since)
        rows += db.query(sql, params)
    return rows


def _after_fees(rows: list[dict], valuator) -> pd.DataFrame:
    """Saved market prices -> what you'd have received after that market's fees (base currency).
    Only markets you use now count: enabled ones and your manual quotes."""
    if not rows:
        return pd.DataFrame(columns=["market_hash_name", "market", "date", "value"])
    hist = pd.DataFrame(rows)
    enabled = set(valuator.prefs["enabled_markets"])
    hist = hist[hist["market"].isin(enabled) | hist["market"].str.startswith(MANUAL_PREFIX)].copy()
    if hist.empty:
        return pd.DataFrame(columns=["market_hash_name", "market", "date", "value"])
    gross = _to_base(hist, "price", valuator.fx, valuator.base)
    hist["value"] = [valuator.net(m, g) if g == g else None for m, g in zip(hist["market"], gross)]
    hist = hist.dropna(subset=["value"])
    return hist[hist["value"] > 0]


def best_after_fees(names: list[str], valuator, since: str | None = None) -> pd.DataFrame:
    """Per item and day: the most you'd have received for a plain copy on any market, after fees."""
    hist = _after_fees(_snapshot_rows(names, since, valuation=False), valuator)
    if hist.empty:
        return pd.DataFrame(columns=["market_hash_name", "date", "value"])
    return hist.groupby(["market_hash_name", "date"], as_index=False)["value"].max()


def price_changes(names: list[str], valuator) -> pd.DataFrame:
    """24-hour and 7-week change (%) of each item's best selling price after fees."""
    result = pd.DataFrame({"market_hash_name": list(names)})
    for col in CHANGE_WINDOWS:
        result[col] = float("nan")
    if not names:
        return result

    oldest = _days_ago(max(d + s for d, s in CHANGE_WINDOWS.values()))
    hist = best_after_fees(names, valuator, oldest)
    if hist.empty:
        return result

    current = result["market_hash_name"].map(valuator.best_net)
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


def item_history(market_hash_name: str, range_key: str, valuator) -> dict[str, Any]:
    """What you'd have received each day on each market after its fees, plus the best of them
    (the "valuation" line). Plain copies only: stickers aren't part of market price history."""
    base = valuator.base
    hist = _after_fees(_snapshot_rows([market_hash_name], _cutoff(range_key), valuation=False), valuator)
    if hist.empty:
        return {"labels": [], "series": [], "currency": base}
    wide = hist.pivot_table(index="date", columns="market", values="value", aggfunc="last")
    wide.index = pd.to_datetime(wide.index)
    wide = wide.asfreq("D").ffill(limit=7)  # bridge short gaps only
    wide.insert(0, "valuation", wide.max(axis=1, skipna=True))
    series = [
        {
            "market": col,
            "label": "Best after fees" if col == "valuation" else market_label(col),
            "values": [round(v, 4) if pd.notna(v) else None for v in wide[col]],
        }
        for col in ["valuation"] + sorted(c for c in wide.columns if c != "valuation")
    ]
    return {"labels": [d.strftime("%Y-%m-%d") for d in wide.index], "series": series, "currency": base}


# ---------------------------------------------------------------------------
# One-off conversion of older snapshots
# ---------------------------------------------------------------------------
def convert_market_snapshots(valuator=None) -> int:
    """Older versions saved portfolio totals at market (buying) value. Convert them to after-fee
    values using each day's saved prices: the ratio between what the items you hold would have
    sold for after fees and their market value that day. Returns how many rows were converted."""
    rows = db.query("SELECT snapshot_date AS date, scope, total_value FROM portfolio_snapshots WHERE basis = 'market'")
    if not rows:
        return 0
    from . import portfolio  # local import to avoid a circular dependency
    from .valuation import Valuator

    v = valuator or Valuator()
    frame = portfolio.build_frame(v, "total", with_changes=False)
    if frame.empty:
        return 0
    names = frame["market_hash_name"].unique().tolist()
    sell = best_after_fees(names, v)
    market = pd.DataFrame(_snapshot_rows(names, None, valuation=True))
    if not sell.empty and not market.empty:
        market["value"] = _to_base(market, "price", v.fx, v.base)
        daily = sell.merge(market[["market_hash_name", "date", "value"]].dropna(),
                           on=["market_hash_name", "date"], suffixes=("_sell", "_market"))
    else:
        daily = pd.DataFrame(columns=["market_hash_name", "date", "value_sell", "value_market"])

    ratios: dict[str, tuple[dict[str, float], float | None]] = {}
    for scope, subset in (("public", frame[frame["source"] == "steam"]), ("total", frame)):
        held = subset.groupby("market_hash_name")["quantity"].sum()
        mine = daily[daily["market_hash_name"].isin(held.index)]
        qty = mine["market_hash_name"].map(held)
        sums = pd.DataFrame({"date": mine["date"], "sell": mine["value_sell"] * qty, "market": mine["value_market"] * qty})
        sums = sums.groupby("date")[["sell", "market"]].sum()
        sums = sums[sums["market"] > 0]
        by_day = (sums["sell"] / sums["market"]).to_dict()
        market_now = float(subset["market_total_value"].fillna(0).sum())
        today_ratio = float(subset["total_value"].fillna(0).sum()) / market_now if market_now > 0 else None
        ratios[scope] = (by_day, today_ratio)

    updates = []
    for row in rows:
        by_day, today_ratio = ratios.get(row["scope"], ({}, None))
        ratio = by_day.get(row["date"])
        if ratio is None and by_day:  # no prices saved that day: use the nearest day that has them
            when = date.fromisoformat(row["date"])
            ratio = by_day[min(by_day, key=lambda d: abs((date.fromisoformat(d) - when).days))]
        if ratio is None:
            ratio = today_ratio
        if ratio is None:
            continue  # nothing priced yet - try again next start
        updates.append((row["total_value"] * ratio, row["total_value"], row["date"], row["scope"]))
    db.executemany(
        "UPDATE portfolio_snapshots SET total_value = ?, market_value = ?, basis = 'after_fees' "
        "WHERE snapshot_date = ? AND scope = ? AND basis = 'market'",
        updates,
    )
    if updates:
        log.info("Converted %d portfolio snapshots to after-fee values", len(updates))
    return len(updates)
