"""Daily snapshots, price-change calculations and chart series (pandas based).

Values are what you'd receive selling, after each market's fees. Item-level history is worked
out from the per-market prices saved every day, so it always uses your current fee settings.
"""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from . import db, memo, sanity, settings
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
        # After fees, both ways: every market counted (Steam Wallet money too), and cash-out markets only.
        value = float(subset["any_value"].fillna(0).sum()) if not subset.empty else 0.0
        cash = float(subset["payout_value"].fillna(0).sum()) if not subset.empty else 0.0
        market = float(subset["market_total_value"].fillna(0).sum()) if not subset.empty else 0.0
        count = int(subset["quantity"].sum()) if not subset.empty else 0
        totals[scope] = cash if v.cash_only else value
        db.execute(
            "INSERT INTO portfolio_snapshots (snapshot_date, scope, total_value, item_count, currency, market_value, "
            "basis, screened, cash_value) VALUES (?, ?, ?, ?, ?, ?, 'after_fees', ?, ?) ON CONFLICT(snapshot_date, scope) "
            "DO UPDATE SET total_value = excluded.total_value, item_count = excluded.item_count, "
            "currency = excluded.currency, market_value = excluded.market_value, basis = excluded.basis, "
            "screened = excluded.screened, cash_value = excluded.cash_value",
            (day, scope, value, count, v.base, market, sanity.RULES, cash),
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


def _screened(hist: pd.DataFrame, valuator, rules: int = sanity.RULES) -> pd.Series:
    """Which saved prices to keep: each day's listings get the same check as today's (sanity.py),
    against that day's other markets and the item's recent sales / sticker inside. ``rules``: an
    older version of the check (to correct totals saved under it)."""
    usd = valuator.fx.convert(1.0, "USD", valuator.base) or 1.0
    rows = hist.sort_values(["market_hash_name", "date"], kind="stable")
    names, days = rows["market_hash_name"].to_numpy(), rows["date"].to_numpy()
    markets, gross, index = rows["market"].to_numpy(), rows["gross"].to_numpy(), rows.index.to_numpy()
    cash = {m: valuator.cash_out(m) for m in set(markets)}
    context: dict[str, tuple] = {}     # per item: its sales, the sticker inside, ... (the same every day)
    drop = []
    i, n = 0, len(rows)
    while i < n:
        name, day = names[i], days[i]
        j = i + 1
        while j < n and names[j] == name and days[j] == day:
            j += 1
        if name not in context:
            inner_name = sanity.sticker_inside(name)
            context[name] = (valuator.sales.get(name, {}).get("price"),
                             valuator.price(inner_name) if inner_name else None,
                             valuator.sales_market(name), valuator.own_sales(name))
        sold, inner, sales_market, own = context[name]
        listings = {markets[k]: float(gross[k]) for k in range(i, j)}
        _, aside, _ = sanity.screen(listings, sold, inner, usd, cash=cash.__getitem__, sales_market=sales_market,
                                    own_sales=own, rules=rules)
        if aside:
            drop.extend(index[k] for k in range(i, j) if markets[k] in aside)
        i = j
    keep = pd.Series(True, index=hist.index)
    if drop:
        keep[drop] = False
    return keep


CARRY_DAYS = 3   # a market's last saved price stands for up to this many saved days it wasn't checked


def _fill_gaps(hist: pd.DataFrame) -> pd.DataFrame:
    """Days a market didn't report on an item it still listed get its last saved price.

    CSFloat is asked one item at a time (and on a day it asks the app to slow down, perhaps not at
    all), and a market whose list couldn't be fetched one day saved nothing for anything. Without
    this, a day's "best price" would be whichever market happened to be checked that day, and the
    24-hour change would compare different markets - say today's CSFloat price with yesterday's
    Steam one. A market whose whole list *was* fetched that day without the item had stopped
    listing it: nothing is carried then, until it lists the item again. At most CARRY_DAYS days."""
    if hist.empty:
        return hist
    days = sorted(hist["date"].unique())
    if len(days) < 2:
        return hist
    from .markets import SOURCES

    fetched = {(r["market"], r["day"]) for r in db.query(
        "SELECT DISTINCT market, snapshot_date AS day FROM price_snapshots WHERE snapshot_date >= ? "
        "AND market != 'valuation'", (days[0],))}
    wide = hist.set_index(["market_hash_name", "market", "date"])[["price", "currency"]].unstack("date")
    price, currency = wide["price"].reindex(columns=days), wide["currency"].reindex(columns=days)
    row_markets = price.index.get_level_values("market")
    per_market = {m: np.array([bool(getattr(SOURCES.get(m), "bulk", False)) and (m, d) in fetched for d in days])
                  for m in set(row_markets)}
    gone = price.isna().to_numpy() & np.vstack([per_market[m] for m in row_markets])   # fetched, not listed
    price = price.mask(gone, -1.0).ffill(axis=1, limit=CARRY_DAYS)
    currency = currency.mask(gone, "").ffill(axis=1, limit=CARRY_DAYS)
    keys = ["market_hash_name", "market"]
    out = price.reset_index().melt(id_vars=keys, var_name="date", value_name="price")
    out["currency"] = currency.reset_index().melt(id_vars=keys, var_name="date", value_name="currency")["currency"]
    return out[out["price"] > 0].reset_index(drop=True)


def _after_fees(rows: list[dict], valuator, screen: bool | int = True) -> pd.DataFrame:
    """Saved market prices -> what you'd have received after that market's fees (base currency).
    Only markets you use now count (enabled ones and your manual quotes), and unrealistic
    listings are left out unless ``screen`` is off (a number: that version of the rules)."""
    if not rows:
        return pd.DataFrame(columns=["market_hash_name", "market", "date", "value"])
    hist = pd.DataFrame(rows)
    enabled = set(valuator.prefs["enabled_markets"])
    hist = hist[hist["market"].isin(enabled) | hist["market"].str.startswith(MANUAL_PREFIX)]
    if hist.empty:
        return pd.DataFrame(columns=["market_hash_name", "market", "date", "value"])
    hist = _fill_gaps(hist).copy()
    hist["gross"] = _to_base(hist, "price", valuator.fx, valuator.base)
    hist = hist.dropna(subset=["gross"])
    if screen and not hist.empty:
        hist = hist[_screened(hist, valuator, sanity.RULES if screen is True else int(screen))]
    hist["value"] = [valuator.net(m, g) for m, g in zip(hist["market"], hist["gross"])]
    hist = hist.dropna(subset=["value"])
    return hist[hist["value"] > 0]


def _market_history(names: list[str], since: str | None, valuator, screen: bool = True) -> pd.DataFrame:
    """Every market's saved prices for these items, after fees, gaps filled and unrealistic listings
    left out - worked out once and reused until the data changes (memo.py). Read-only."""
    def compute() -> pd.DataFrame:
        return _after_fees(_snapshot_rows(names, since, valuation=False), valuator, screen)

    if not getattr(valuator, "shared", False):
        return compute()
    return memo.cached(("market_history", frozenset(names), since, screen, valuator.signature()), compute)


def best_after_fees(names: list[str], valuator, since: str | None = None, screen: bool = True,
                    cash_only: bool | None = None) -> pd.DataFrame:
    """Per item and day: the most you'd have received for a plain copy after fees, on any market
    that counts (``cash_only``: only markets whose money you can withdraw; default: the switch)."""
    hist = _market_history(names, since, valuator, screen)
    if valuator.cash_only if cash_only is None else cash_only:
        hist = hist[[valuator.cash_out(m) for m in hist["market"]]] if not hist.empty else hist
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


def _value_column(cash_only: bool) -> str:
    """The saved daily total to use: with "Cash-out markets only", the cash-out figure (estimated
    for days saved before it was kept, see ``fill_cash_values``)."""
    return "COALESCE(cash_value, total_value) AS total_value" if cash_only else "total_value"


def portfolio_changes(scope: str, current_total: float, fx: FxConverter, base: str,
                      current_count: int | None = None, cash_only: bool = False) -> dict[str, float | None]:
    """How the portfolio's value moved over each window, from the saved daily totals to the value
    now. Steps where items were added or removed are left out (see ``chained_change``)."""
    rows = db.query(
        f"SELECT snapshot_date AS date, {_value_column(cash_only)}, item_count, currency FROM portfolio_snapshots "
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


def portfolio_history(scope: str, range_key: str, fx: FxConverter, base: str, cash_only: bool = False) -> dict[str, Any]:
    cutoff = _cutoff(range_key)
    sql = (f"SELECT snapshot_date AS date, {_value_column(cash_only)}, item_count, currency FROM portfolio_snapshots "
           "WHERE scope = ?")
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
    (the "valuation" line) and which market that was each day. Markets that don't count under
    the "Values from" switch are still listed (``counted: false``) but aren't in the best line.
    Plain copies only: stickers aren't part of market price history."""
    base = valuator.base
    hist = _market_history([market_hash_name], _cutoff(range_key), valuator)
    if hist.empty:
        return {"labels": [], "series": [], "best_markets": [], "currency": base, "cash_only": valuator.cash_only}
    wide = hist.pivot_table(index="date", columns="market", values="value", aggfunc="last")
    wide.index = pd.to_datetime(wide.index)
    saved = wide.index
    wide = wide.asfreq("D")
    # Days nothing at all was saved (the PC was off): bridge short gaps. (Days a market wasn't
    # checked are already filled in, and days it had stopped listing the item stay empty.)
    bridged = ~wide.index.isin(saved)
    wide[bridged] = wide.ffill(limit=7)[bridged]
    counted = [c for c in wide.columns if valuator.counts(c)]
    best = wide[counted].max(axis=1, skipna=True) if counted else pd.Series(float("nan"), index=wide.index)
    leader = [row.idxmax() if counted and row.notna().any() else None for _, row in wide[counted].iterrows()] \
        if counted else [None] * len(wide)
    wide.insert(0, "valuation", best)
    series = [
        {
            "market": col,
            "label": "Best after fees" if col == "valuation" else market_label(col),
            "values": [round(v, 4) if pd.notna(v) else None for v in wide[col]],
            "counted": col == "valuation" or valuator.counts(col),
            "wallet": col != "valuation" and not valuator.cash_out(col),
        }
        for col in ["valuation"] + sorted(c for c in wide.columns if c != "valuation")
    ]
    return {"labels": [d.strftime("%Y-%m-%d") for d in wide.index], "series": series,
            "best_markets": leader, "currency": base, "cash_only": valuator.cash_only}


def fill_cash_values(valuator=None) -> int:
    """Days saved before the cash-out total was kept get an estimate: that day's total times the
    share it would have come to counting only markets you can withdraw from, from each market's
    saved prices that day for what you hold now. (Before Steam prices arrived the two are the
    same.) Returns how many rows were filled."""
    rows = db.query("SELECT snapshot_date AS date, scope, total_value FROM portfolio_snapshots WHERE cash_value IS NULL")
    if not rows:
        return 0
    from . import portfolio  # local import to avoid a circular dependency
    from .valuation import Valuator

    v = valuator or Valuator()
    frame = portfolio.build_frame(v, "total", with_changes=False)
    ratio = pd.Series(dtype="float64")
    if not frame.empty:
        held = frame[frame["any_value"].notna()].groupby("market_hash_name")["quantity"].sum()
        names = held.index.tolist()
        since = min(r["date"] for r in rows)
        every = best_after_fees(names, v, since, cash_only=False)
        cash = best_after_fees(names, v, since, cash_only=True)
        if not every.empty:
            every["worth"] = every["value"] * every["market_hash_name"].map(held)
            totals = every.groupby("date")["worth"].sum()
            if not cash.empty:
                cash["worth"] = cash["value"] * cash["market_hash_name"].map(held)
                cash_totals = cash.groupby("date")["worth"].sum().reindex(totals.index).fillna(0.0)
            else:
                cash_totals = pd.Series(0.0, index=totals.index)
            ratio = (cash_totals / totals).where(totals > 0).dropna().clip(0, 1).sort_index()
    filled = []
    for row in rows:
        known = ratio[ratio.index <= row["date"]]
        share = float(known.iloc[-1]) if not known.empty else (float(ratio.iloc[0]) if not ratio.empty else 1.0)
        filled.append((round(row["total_value"] * share, 2), row["date"], row["scope"]))
    db.executemany("UPDATE portfolio_snapshots SET cash_value = ? WHERE snapshot_date = ? AND scope = ?", filled)
    return len(filled)


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
    """Portfolio totals saved before listings were checked (sanity.py), or under an older version
    of that check, may count an unrealistic listing - say a slab listed at £1,500 that sells for
    62p, or a capsule listed at three times what it sells for. Each such snapshot is corrected once:
    the difference those listings made that day is worked out from that day's saved prices for the
    items you hold, valued the way the totals are (a big stack spread over the markets by how many
    each sells), counting every market (the total) and cash-out markets only (the cash total); the
    snapshot is then marked with the current rules. Runs during a price refresh, when sales and the
    stickers inside slabs have just been fetched. Returns how many snapshots changed."""
    from . import portfolio  # local import to avoid a circular dependency
    from .valuation import Valuator

    rows = db.query("SELECT snapshot_date AS date, scope, total_value, cash_value, screened FROM portfolio_snapshots "
                    "WHERE basis = 'after_fees' AND screened < ?", (sanity.RULES,))
    if not rows:
        return 0
    v = valuator or Valuator()
    frame = portfolio.build_frame(v, "total", with_changes=False)
    if frame.empty:
        return 0  # nothing priced yet - try again next refresh
    listed = frame[~frame["price_source"].isin(["custom", "evidence"])]
    names = listed["market_hash_name"].unique().tolist()
    raw = _snapshot_rows(names, None, valuation=False)
    held = {scope: (listed if scope == "total" else listed[listed["source"] == "steam"])
            .groupby("market_hash_name")["quantity"].sum().to_dict() for scope in ("total", "public")}
    days = {r["date"] for r in rows}
    hists: dict[int, pd.DataFrame] = {0: _after_fees(raw, v, screen=False)}
    priced = set(zip(hists[0]["market_hash_name"], hists[0]["date"])) if not hists[0].empty else set()

    def worth(rules: int, scope: str, cash: bool) -> dict[str, float]:
        """Per day: what the items held were worth from that day's saved prices, under ``rules``."""
        if rules not in hists:
            hists[rules] = _after_fees(raw, v, screen=False if rules == 0 else rules)
        hist = hists[rules]
        hist = hist[hist["date"].isin(days)] if not hist.empty else hist
        out: dict[str, float] = {}
        for name, qty in held[scope].items():
            rows_for = hist[hist["market_hash_name"] == name] if not hist.empty else hist
            by_day = dict(tuple(rows_for.groupby("date"))) if not rows_for.empty else {}
            for day in days:
                group = by_day.get(day)
                options = [] if group is None else [
                    {"market": m, "net": float(n), "wallet": not v.cash_out(m)}
                    for m, n in zip(group["market"], group["value"]) if not cash or v.cash_out(m)]
                if not options:   # every listing that day unrealistic (or none): what it's really worth now
                    if (name, day) not in priced:
                        continue
                    options = [o for o in v.sell_options(v.fallback_quotes(name)) if not cash or not o["wallet"]]
                options.sort(key=lambda o: -o["net"])
                total, _plan = v.bulk_plan(name, options, int(qty)) if options else (0.0, [])
                out[day] = out.get(day, 0.0) + total
        return out

    cache: dict[tuple, dict[str, float]] = {}

    def day_worth(rules: int, scope: str, cash: bool) -> dict[str, float]:
        if (rules, scope, cash) not in cache:
            cache[(rules, scope, cash)] = worth(rules, scope, cash)
        return cache[(rules, scope, cash)]

    updates = []
    for row in rows:
        fixed = {}
        for column, cash in (("total_value", False), ("cash_value", True)):
            if row[column] is None:
                continue
            then = day_worth(row["screened"], row["scope"], cash).get(row["date"])
            now = day_worth(sanity.RULES, row["scope"], cash).get(row["date"])
            if then is None or now is None:
                continue
            extra = then - now
            if abs(extra) > 0.01 * max(row[column], 1.0):   # ignore rounding-sized differences
                fixed[column] = max(row[column] - extra, 0.0)
        if fixed:
            updates.append((fixed.get("total_value", row["total_value"]), fixed.get("cash_value", row["cash_value"]),
                            row["date"], row["scope"]))
    db.executemany("UPDATE portfolio_snapshots SET total_value = ?, cash_value = ? WHERE snapshot_date = ? AND scope = ?",
                   updates)
    changed = len(updates)
    if changed:
        log.info("Corrected %d portfolio snapshots for unrealistic listings", changed)
    db.executemany("UPDATE portfolio_snapshots SET screened = ? WHERE snapshot_date = ? AND scope = ?",
                   [(sanity.RULES, r["date"], r["scope"]) for r in rows])
    return changed
