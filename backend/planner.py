"""Sell planner: which items to sell, how many, and where, to raise a chosen amount of cash.

Every copy you could sell gets a *keep score* - how much you'd rather hold on to it:
  * trend: falling prices (after fees, from the saved daily prices) lower it, rising ones raise it;
  * stack: copies from a big stack (531 capsules) score lower than a one-off;
  * value: expensive single items score higher, so they're the last to go.
Copies are sold lowest score first until the target is reached. Each item's copies go to its
markets best price first, each market taking only about what it sells in 30 days (as in the bulk
estimate), so later copies of the same item may fetch less. Near the end, cheap copies are used to
cover the last few pounds instead of selling something expensive that overshoots.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from . import history, images, portfolio
from .markets import market_label

TREND_DAYS = 49


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


def keep_score(weekly: float | None, stack: int, unit_value: float) -> float:
    """Higher = keep. Falling items, big stacks and cheap copies score low, so they're sold first."""
    trend = 1.0 if weekly is None else min(max(1 + weekly / 10, 0.4), 2.5)
    stack_factor = 1 / (1 + math.log10(max(stack, 1)))
    value_factor = 1 + 0.25 * math.log10(1 + max(unit_value, 0))
    return trend * stack_factor * value_factor


def plan(target: float, valuator, *, include_wallet: bool = False, keep: set[int] | None = None) -> dict[str, Any]:
    keep = keep or set()
    frame = portfolio.build_frame(valuator, "total")
    out: dict[str, Any] = {"currency": valuator.base, "target": target, "steps": [], "by_market": [],
                           "achieved": 0.0, "max": 0.0, "excluded": {"kept": 0, "locked": 0, "no_price": 0}}
    if frame.empty:
        return out
    sellable = frame[~(frame["is_container"].astype(bool) & ((frame["contents_count"] > 0) | (frame["is_storage"] == 1)))]
    names = sellable["market_hash_name"].unique().tolist()
    trends = weekly_trends(names, valuator)

    # Lots: plain copies of an item are pooled (like the bulk estimate); anything else is its own lot.
    lots: dict[Any, dict[str, Any]] = {}
    for row in sellable.itertuples(index=False):
        if int(row.id) in keep:
            out["excluded"]["kept"] += 1
            continue
        if row.source == "steam" and not row.tradable:
            out["excluded"]["locked"] += 1
            continue
        options = [o for o in row.sell_quotes if include_wallet or not o["wallet"]]
        if not options:
            out["excluded"]["no_price"] += 1
            continue
        plain = row.price_source not in ("custom", "evidence") and not (row.sticker_premium or 0) > 0
        key = ("plain", row.market_hash_name) if plain else ("row", row.id)
        lot = lots.setdefault(key, {"name": row.market_hash_name, "ids": [], "units": 0, "options": options,
                                    "icon": images.icon_src(row.icon_url), "change_7w": row.change_7w})
        lot["ids"].append(int(row.id))
        lot["units"] += int(row.quantity)

    # Every sellable copy, priced where it would actually go (best market first, within its sales).
    copies = []
    for key, lot in lots.items():
        _total, steps = valuator.bulk_plan(lot["name"], lot["options"], lot["units"])
        weekly = trends.get(lot["name"])
        if weekly is None and lot["change_7w"] == lot["change_7w"] and lot["change_7w"] is not None:
            weekly = ((1 + lot["change_7w"] / 100) ** (1 / 7) - 1) * 100
        lot["weekly"] = weekly
        # One score per item (from its best price), so its copies go best-paying market first.
        score = keep_score(weekly, lot["units"], steps[0]["net"]) if steps else math.inf
        position = 0
        for step in steps:
            for _ in range(step["units"]):
                copies.append((score, position, key, step["market"], step["net"], step.get("capacity")))
                position += 1
    out["max"] = round(sum(c[4] for c in copies), 2)

    # Lowest keep score first (and an item's best-paying copies before its later ones).
    copies.sort(key=lambda c: (c[0], c[1]))
    chosen, total = [], 0.0
    later = []
    for copy in copies:
        if total >= target:
            break
        gap = target - total
        if copy[4] > gap * 1.5 and copy[4] - gap > 1.0:
            later.append(copy)        # would overshoot a lot: try cheaper copies first
            continue
        chosen.append(copy)
        total += copy[4]
    if total < target and later:
        # Still short: the copy that covers the rest with the least left over, keep score breaking ties.
        gap = target - total
        covering = [c for c in later if c[4] >= gap]
        pick = min(covering, key=lambda c: (c[4], c[0])) if covering else max(later, key=lambda c: c[4])
        chosen.append(pick)
        total += pick[4]
        if total < target:            # every copy is needed
            for copy in sorted(later, key=lambda c: (c[0], c[1])):
                if copy is pick or total >= target:
                    continue
                chosen.append(copy)
                total += copy[4]

    # Group the chosen copies into steps: per item, how many on which market.
    steps: dict[Any, dict[str, Any]] = {}
    markets: dict[str, dict[str, Any]] = {}
    for score, _pos, key, market, net, capacity in chosen:
        lot = lots[key]
        step = steps.setdefault(key, {"name": lot["name"], "ids": lot["ids"], "icon": lot["icon"], "units": 0,
                                      "held": lot["units"], "proceeds": 0.0, "markets": {}, "score": score,
                                      "weekly": lot["weekly"]})
        step["units"] += 1
        step["proceeds"] += net
        where = step["markets"].setdefault(market, {"market": market, "label": market_label(market), "units": 0,
                                                    "net": net, "capacity": capacity})
        where["units"] += 1
        bucket = markets.setdefault(market, {"market": market, "label": market_label(market), "units": 0, "proceeds": 0.0})
        bucket["units"] += 1
        bucket["proceeds"] += net
    for step in steps.values():
        step["markets"] = list(step["markets"].values())
        step["reasons"] = _reasons(step)
        step["proceeds"] = round(step["proceeds"], 4)
    out["steps"] = sorted(steps.values(), key=lambda s: s["score"])
    out["by_market"] = sorted(markets.values(), key=lambda m: -m["proceeds"])
    out["achieved"] = round(total, 2)
    return out


def _reasons(step: dict[str, Any]) -> list[str]:
    """Short reasons, one per line, all shaped the same way: what it is, then any figures in brackets."""
    reasons = []
    weekly = step["weekly"]
    if weekly is None:
        reasons.append("trend unknown (not enough price history)")
    elif weekly <= -1:
        reasons.append(f"falling (about {abs(weekly):.0f}% a week)")
    elif weekly < 1:
        reasons.append("steady (under 1% a week)")
    elif weekly < 3:
        reasons.append(f"rising slowly (about {weekly:.0f}% a week)")
    else:
        reasons.append(f"rising (about {weekly:.0f}% a week)")
        reasons.append("sold only to reach the target")
    if step["held"] >= 5:
        reasons.append(f"you have {step['held']:,}" + (f" (selling {step['units']:,})" if step["units"] < step["held"] else ""))
    quick = [m for m in step["markets"] if m["capacity"] and m["capacity"] >= m["units"]]
    if quick:
        reasons.append(f"sells steadily on {quick[0]['label']}")
    return reasons
