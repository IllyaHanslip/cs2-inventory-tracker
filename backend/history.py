"""Daily snapshots, price-change calculations and chart series (pandas based).

Values are what you'd receive selling, after each market's fees. Item-level history is worked
out from the per-market prices saved every day, so it always uses your current fee settings.
"""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any

import pandas as pd

from . import db, sanity, settings
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
    # Each scope is valued exactly as its own tab values it: a stack split between Steam and a
    # storage unit is counted together for "total" but only its Steam copies make up "public",
    # and how many sell at a good price depends on how many there are.
    public = portfolio.build_frame(v, "public", with_changes=False)
    for scope, subset in (("public", public), ("total", frame)):
        value = float(subset["total_value"].fillna(0).sum()) if not subset.empty else 0.0  # after fees
        market = float(subset["market_total_value"].fillna(0).sum()) if not subset.empty else 0.0
        count = int(subset["quantity"].sum()) if not subset.empty else 0
        totals[scope] = value
        db.execute(
            "INSERT INTO portfolio_snapshots (snapshot_date, scope, total_value, item_count, currency, market_value, "
            "basis, screened) VALUES (?, ?, ?, ?, ?, ?, 'after_fees', 1) ON CONFLICT(snapshot_date, scope) DO UPDATE SET "
            "total_value = excluded.total_value, item_count = excluded.item_count, currency = excluded.currency, "
            "market_value = excluded.market_value, basis = excluded.basis, screened = 1",
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


def _screened(hist: pd.DataFrame, valuator) -> pd.Series:
    """Which saved prices to keep: each day's listings get the same check as today's (sanity.py),
    against that day's other markets and the item's recent sales / sticker inside."""
    usd = valuator.fx.convert(1.0, "USD", valuator.base) or 1.0
    keep = pd.Series(True, index=hist.index)
    for (name, _day), group in hist.groupby(["market_hash_name", "date"]):
        listings = dict(zip(group["market"], group["gross"]))
        inner_name = sanity.sticker_inside(name)
        inner = valuator.price(inner_name) if inner_name else None
        _, aside, _ = sanity.screen(listings, valuator.sales.get(name, {}).get("price"), inner, usd)
        for market in aside:
            keep[group.index[group["market"] == market]] = False
    return keep


def _after_fees(rows: list[dict], valuator, screen: bool = True) -> pd.DataFrame:
    """Saved market prices -> what you'd have received after that market's fees (base currency).
    Only markets you use now count (enabled ones and your manual quotes), and unrealistic
    listings are left out unless ``screen`` is off."""
    if not rows:
        return pd.DataFrame(columns=["market_hash_name", "market", "date", "value"])
    hist = pd.DataFrame(rows)
    enabled = set(valuator.prefs["enabled_markets"])
    hist = hist[hist["market"].isin(enabled) | hist["market"].str.startswith(MANUAL_PREFIX)].copy()
    if hist.empty:
        return pd.DataFrame(columns=["market_hash_name", "market", "date", "value"])
    hist["gross"] = _to_base(hist, "price", valuator.fx, valuator.base)
    hist = hist.dropna(subset=["gross"])
    if screen and not hist.empty:
        hist = hist[_screened(hist, valuator)]
    hist["value"] = [valuator.net(m, g) for m, g in zip(hist["market"], hist["gross"])]
    hist = hist.dropna(subset=["value"])
    return hist[hist["value"] > 0]


def best_after_fees(names: list[str], valuator, since: str | None = None, screen: bool = True) -> pd.DataFrame:
    """Per item and day: the most you'd have received for a plain copy on any market, after fees."""
    hist = _after_fees(_snapshot_rows(names, since, valuation=False), valuator, screen)
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


def chained_change(points: list[tuple[float | None, int | None]]) -> float | None:
    """% change along (value, item count) points in date order, leaving out every step where the
    number of items changed: adding 50 capsules isn't a 50-capsule gain. With no items added or
    removed this is simply last / first. None when no step compares like with like."""
    growth, used = 1.0, False
    for (v0, c0), (v1, c1) in zip(points, points[1:]):
        if c0 != c1 or v0 is None or v1 is None or not (math.isfinite(v0) and math.isfinite(v1)) or v0 <= 0:
            continue
        growth *= v1 / v0
        used = True
    return (growth - 1) * 100 if used else None


def portfolio_changes(scope: str, current_total: float, fx: FxConverter, base: str,
                      current_count: int | None = None) -> dict[str, float | None]:
    """How the portfolio's value moved over each window, from the saved daily totals to the value
    now. Steps where items were added or removed are left out (see ``chained_change``)."""
    rows = db.query(
        "SELECT snapshot_date AS date, total_value, item_count, currency FROM portfolio_snapshots "
        "WHERE scope = ? AND snapshot_date >= ? AND snapshot_date < ? ORDER BY snapshot_date",
        (scope, _days_ago(max(d + s for d, s in CHANGE_WINDOWS.values())), today()),
    )
    out: dict[str, float | None] = {col: None for col in CHANGE_WINDOWS}
    if not rows:
        return out
    hist = pd.DataFrame(rows)
    hist["value"] = _to_base(hist, "total_value", fx, base)
    hist = hist.dropna(subset=["value"])
    for col, (days, slack) in CHANGE_WINDOWS.items():
        target, earliest = _days_ago(days), _days_ago(days + slack)
        window = hist[(hist["date"] <= target) & (hist["date"] >= earliest)]
        if window.empty:
            continue
        steps = hist[hist["date"] >= window["date"].max()]
        points = [(float(v), int(c) if pd.notna(c) else None) for v, c in zip(steps["value"], steps["item_count"])]
        out[col] = chained_change(points + [(current_total, current_count)])
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
    # "today" lets the dashboard draw today's point at the value it shows right now (which also
    # counts anything you've changed since today's snapshot), so the two always agree.
    if not rows:
        return {"labels": [], "values": [], "counts": [], "currency": base, "today": today()}
    df = pd.DataFrame(rows)
    df["value"] = _to_base(df, "total_value", fx, base)
    df.index = pd.to_datetime(df["date"])
    daily = df[["value", "item_count"]].asfreq("D").ffill()  # continuous line across missed days
    return {
        "labels": [d.strftime("%Y-%m-%d") for d in daily.index],
        "values": [round(v, 2) if pd.notna(v) else None for v in daily["value"]],
        "counts": [int(c) if pd.notna(c) else None for c in daily["item_count"]],
        "currency": base,
        "today": today(),
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


# ---------------------------------------------------------------------------
# One-off clean-up of totals inflated by unrealistic listings
# ---------------------------------------------------------------------------
def remove_outlier_inflation(valuator=None) -> int:
    """Portfolio totals saved before listings were checked (sanity.py) may count an unrealistic
    listing - say a slab listed at £1,500 that sells for 62p. Each such snapshot is corrected
    once: the value those listings added that day is taken off, worked out from that day's saved
    prices for the items you hold. Runs during a price refresh, when sales and the stickers inside
    slabs have just been fetched. Returns how many snapshots changed."""
    from . import portfolio  # local import to avoid a circular dependency
    from .valuation import Valuator

    rows = db.query("SELECT snapshot_date AS date, scope, total_value FROM portfolio_snapshots "
                    "WHERE basis = 'after_fees' AND screened = 0")
    if not rows:
        return 0
    v = valuator or Valuator()
    frame = portfolio.build_frame(v, "total", with_changes=False)
    if frame.empty:
        return 0  # nothing priced yet - try again next refresh
    changed = 0
    if not frame.empty:
        listed = frame[~frame["price_source"].isin(["custom", "evidence"])]
        names = listed["market_hash_name"].unique().tolist()
        raw = _snapshot_rows(names, None, valuation=False)
        before = _after_fees(raw, v, screen=False).groupby(["market_hash_name", "date"])["value"].max()
        after = _after_fees(raw, v, screen=True).groupby(["market_hash_name", "date"])["value"].max()
        # Where every listing that day was unrealistic, count what the item is really worth now.
        fallback = {n: max((o["net"] for o in v.sell_options(v.fallback_quotes(n))), default=0.0) for n in names}
        excess = {}
        for key, value in before.items():
            kept = after.get(key)
            excess[key] = max(value - (kept if kept is not None else fallback.get(key[0], 0.0)), 0.0)
        updates = []
        for row in rows:
            subset = listed if row["scope"] == "total" else listed[listed["source"] == "steam"]
            held = subset.groupby("market_hash_name")["quantity"].sum()
            extra = sum(qty * excess.get((name, row["date"]), 0.0) for name, qty in held.items())
            if extra > 0.01 * max(row["total_value"], 1.0):   # ignore rounding-sized differences
                updates.append((max(row["total_value"] - extra, 0.0), row["date"], row["scope"]))
        db.executemany("UPDATE portfolio_snapshots SET total_value = ? WHERE snapshot_date = ? AND scope = ?", updates)
        changed = len(updates)
        if changed:
            log.info("Removed unrealistic listings from %d portfolio snapshots", changed)
    db.executemany("UPDATE portfolio_snapshots SET screened = 1 WHERE snapshot_date = ? AND scope = ?",
                   [(r["date"], r["scope"]) for r in rows])
    return changed
