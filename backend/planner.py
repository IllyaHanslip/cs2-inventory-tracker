"""Sell planner: what the page needs to work out which items to sell, how many and where.

The planning itself happens in the page (static/js/plan-calc.js), so filters, markets, the undercut
and the amount answer instantly, and the live demo can plan too. This module supplies every row you
could sell with what the filters look at, each item's price trend, and per market what one copy
brings in after fees at each undercut (listed a little under the market's price - at least 0.01 -
so it sells first) and how many that market sells in 30 days.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from . import history, images, portfolio, sanity
from .markets import market_label

TREND_DAYS = 49
UNDERCUT_DEFAULT = 2.0                  # % under each market's price, so your copies sell first
UNDERCUT_CHOICES = (0, 1, 2, 3, 5, 10)  # what the Sell Planner offers (the "Undercut by" choice)


def undercut_price(price: float, percent: float) -> float:
    """The price to list at so yours is the cheapest: ``percent`` % under ``price`` and at least 0.01
    under, rounded down to a whole 0.01 (never below 0.01). 0 % lists at ``price`` itself."""
    if percent <= 0 or price <= 0.01:
        return price
    lower = price - max(price * percent / 100, 0.01)
    return max(math.floor(lower * 100 + 1e-6) / 100, 0.01)


def weekly_trends(names: list[str], valuator) -> dict[str, float]:
    """Per item: % change per week of its best after-fee price, fitted over the last 7 weeks
    (needs at least 5 days of prices)."""
    daily = history.best_after_fees(names, valuator, history._days_ago(TREND_DAYS))
    trends: dict[str, float] = {}
    for name, rows in daily.groupby("market_hash_name"):
        if len(rows) < 5:
            continue
        days = pd.to_datetime(rows["date"])
        x = (days - days.min()).dt.days.to_numpy(dtype=float)
        if x.max() < 4:
            continue
        slope = np.polyfit(x, np.log(rows["value"].to_numpy(dtype=float)), 1)[0]
        trends[name] = (math.exp(slope * 7) - 1) * 100
    return trends


def _clean(value: Any) -> Any:
    """None for missing values (NaN), so the answer is valid JSON."""
    return None if value is None or (isinstance(value, float) and value != value) else value


def candidates(valuator) -> dict[str, Any]:
    """Everything the Sell Planner needs to plan in the page (static/js/plan-calc.js), whatever the
    filters, markets, undercut or amount: every row you could sell, with what the item filters look
    at, and per *lot* (plain copies of an item pooled, anything else on its own) each market's
    after-fee money for one copy at each undercut choice and how many it sells in 30 days."""
    valuator.cash_only = False   # every market, Steam included: the page chooses
    frame = portfolio.build_frame(valuator, "total")
    out: dict[str, Any] = {"currency": valuator.base, "undercuts": list(UNDERCUT_CHOICES),
                           "default_undercut": UNDERCUT_DEFAULT, "markets": [], "lots": {}, "rows": []}
    if frame.empty:
        return out
    # Storage units (and anything holding items) aren't sold themselves.
    sellable = frame[~(frame["is_container"].astype(bool) & ((frame["contents_count"] > 0) | (frame["is_storage"] == 1)))]
    if sellable.empty:
        return out
    # Price trends two ways: from every market (when you'll sell where money can't be withdrawn too)
    # and from cash-out markets only.
    names = sellable["market_hash_name"].unique().tolist()
    trends = weekly_trends(names, valuator)
    valuator.cash_only = True
    trends_cash = weekly_trends(names, valuator)
    changes_cash = history.price_changes(names, valuator).drop_duplicates("market_hash_name")
    changes_cash = dict(zip(changes_cash["market_hash_name"], changes_cash["change_7w"]))
    valuator.cash_only = False

    def weekly(trend: float | None, change: Any) -> float | None:
        """The fitted trend, else the 7-week change spread over 7 weeks."""
        change = _clean(change)
        if trend is None and change is not None:
            return ((1 + change / 100) ** (1 / 7) - 1) * 100
        return trend

    seen: dict[str, bool] = {}
    for row, category in zip(sellable.itertuples(index=False), portfolio.filter_category(sellable)):
        name = row.market_hash_name
        plain = row.price_source not in ("custom", "evidence") and not (row.sticker_premium or 0) > 0
        key = f"plain:{name}" if plain else f"row:{int(row.id)}"
        if key not in out["lots"]:
            own_price = row.price_source in ("custom", "evidence")   # no market change for your own value
            options = []
            for option in row.sell_quotes:
                source = sanity.sticker_inside(name) if option.get("basis") == "sticker_inside" else name
                prices = []
                for undercut in UNDERCUT_CHOICES:
                    listed = undercut_price(option["gross"], undercut)
                    steps = option if listed == option["gross"] else valuator.fee_steps(option["market"], listed)
                    ok = steps is not None and steps["net"] > 0
                    prices.append([round(listed, 4), round(steps["net"], 6)] if ok else None)
                options.append({"market": option["market"], "url": option.get("url"), "wallet": bool(option["wallet"]),
                                "capacity": valuator.sell_capacity(source, option["market"]), "prices": prices})
                seen.setdefault(option["market"], not option["wallet"])
            out["lots"][key] = {
                "name": name, "icon": images.icon_src(row.icon_url), "options": options,
                "weekly": weekly(trends.get(name), row.change_7w),
                "weekly_cash": weekly(trends_cash.get(name), None if own_price else changes_cash.get(name)),
            }
        out["rows"].append({
            "id": int(row.id), "lot": key, "quantity": int(row.quantity), "category": category,
            "in_container": _clean(row.container_id) is not None, "container_name": _clean(row.container_name),
            "exterior": _clean(row.exterior), "tags": row.tags or {}, "search": row.search_text,
            "is_storage": False, "locked": row.source == "steam" and not row.tradable,
        })
    # Markets in the order they're switched on in Settings, then any others (manual quotes) by name.
    enabled = list(valuator.prefs["enabled_markets"])
    order = sorted(seen, key=lambda m: (enabled.index(m) if m in enabled else len(enabled), market_label(m).lower()))
    out["markets"] = [{"key": m, "label": market_label(m), "cash_out": seen[m]} for m in order]
    return out
