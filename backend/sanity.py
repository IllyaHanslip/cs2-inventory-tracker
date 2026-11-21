"""Spotting listing prices that can't be right.

A market's cheapest listing is only an asking price. When it's far above what the item actually
sells for, nobody buys at it, so it mustn't set the item's value or where you'd sell. (Say a
Sticker Slab listed at £1,500 that last sold for 62p, or a 15p capsule whose cheapest listing on
one market is 45p.) This is a simple statistical check, not a trained model, so every decision
can be explained. A listing is set aside when it is far above *what the item goes for elsewhere*:
the middle (median) of

* the item's cheapest listings on the other markets, and
* its recent sales - Skinport's public sales history, the average Steam Market sale price
  (CSGOTrader's daily file; Steam's own "listing" is that average, so it counts once) or CSFloat's
  reference price. Sales are ignored if they're wildly *below* two or more listings that agree.

"Far above" means more than twice that, and at least 10 cents above it (so pennies don't trip
it), for a market you can withdraw money from - 3 times when there's only one other figure to
compare with. Steam keeps the wider margin it has always had (3 times, and at least $1 above): its
price is an average of real Steam sales, paid in Steam Wallet money, which for cheap skins is often
several times the cash markets. And a listing no higher than what the item has recently sold for
*on that same market* (Skinport's sales history, CSFloat's reference price) is never set aside:
people do pay that there. A Sticker Slab with nothing else to go on is compared with the sticker
inside it: a slab can be cracked open to get its sticker back, so it's worth at least that - and
rarely more than a few times it.

Nothing is ever set aside for being cheap: a low listing is a real price you'd compete with.
"""
from __future__ import annotations

import statistics
from typing import Any, Callable

# The rules' version. Bump it whenever they change: totals saved on earlier days under older rules
# are then corrected once (history.remove_outlier_inflation).
RULES = 2

CASH_FACTOR = 2.0        # a market you can withdraw from: more than twice what it goes for elsewhere
MIN_GAP_USD = 0.10       # ...and at least 10 cents above it (so pennies don't trip it)
LONE_FACTOR = 3.0        # only one other figure to go on: be less sure
WALLET_FACTOR = 3.0      # Steam / no cash-out (its own sales, in wallet money): 3x...
WALLET_GAP_USD = 1.0     # ...and at least $1 above, as it has always been
OWN_SALES_MARGIN = 1.25  # at most this much above its recent sales on the same market: realistic there
HIGH_FACTOR = 3.0        # sales this far below two agreeing listings aren't trusted
SLAB_FACTOR = 4.0        # slabs: more than 4x (sticker inside + an empty slab)
EMPTY_SLAB_USD = 0.45    # roughly what the slab itself adds (about 36p)
SLAB_PREFIX = "Sticker Slab | "

# Rules 1 (up to v1.5.3): sales first, else the other markets; 3x and at least $1 above - so
# items worth under a dollar or two were never checked. Kept only to correct older saved totals.
V1_FACTOR = 3.0
V1_GAP_USD = 1.0


def sticker_inside(name: str) -> str | None:
    """'Sticker Slab | NRG (Gold) | Budapest 2025' -> 'Sticker | NRG (Gold) | Budapest 2025'."""
    return "Sticker | " + name[len(SLAB_PREFIX):] if name.startswith(SLAB_PREFIX) else None


def trusted_sales(sales: float | None, listings: list[float]) -> float | None:
    """Recent sales, unless they're wildly below two or more listings that agree with each other."""
    if not sales or sales <= 0:
        return None
    if len(listings) >= 2:
        low, high = min(listings), max(listings)
        if high <= low * HIGH_FACTOR and sales * HIGH_FACTOR < low:
            return None
    return sales


def check(price: float, others: list[float], sales: float | None, inner: float | None, usd: float,
          cash: bool = True) -> dict[str, Any] | None:
    """Why a listing at ``price`` should be set aside (``{"reason", "reference", "factor"}``), or None.

    ``others``: this item's listings on the other markets; ``sales``: trusted recent sales (not
    already one of ``others``); ``inner``: the sticker inside (slabs only); ``cash``: whether the
    listing's market pays money you can withdraw. ``usd`` converts 1 USD into the prices' currency."""
    gap = (MIN_GAP_USD if cash else WALLET_GAP_USD) * usd
    evidence = [*others, *([sales] if sales else [])]
    if evidence:
        reference = statistics.median(evidence)
        factor = WALLET_FACTOR if not cash else (LONE_FACTOR if len(evidence) == 1 else CASH_FACTOR)
        if price > reference * factor and price - reference > gap:
            reason = "markets" if not sales else ("sales" if not others else "elsewhere")
            return {"reason": reason, "reference": reference, "factor": factor}
        return None
    if inner:
        limit = (inner + EMPTY_SLAB_USD * usd) * SLAB_FACTOR
        if price > limit and price - limit > gap:
            return {"reason": "sticker_inside", "reference": inner, "factor": SLAB_FACTOR}
    return None


def _check_v1(price: float, others: list[float], sales: float | None, inner: float | None,
              usd: float) -> dict[str, Any] | None:
    gap = V1_GAP_USD * usd
    if sales:
        return {"reason": "sales", "reference": sales} if price > sales * V1_FACTOR and price - sales > gap else None
    if others:
        typical = statistics.median(others)
        return {"reason": "markets", "reference": typical} if price > typical * V1_FACTOR and price - typical > gap else None
    if inner:
        limit = (inner + EMPTY_SLAB_USD * usd) * SLAB_FACTOR
        if price > limit and price - limit > gap:
            return {"reason": "sticker_inside", "reference": inner}
    return None


def screen(listings: dict[str, float], sales: float | None, inner: float | None, usd: float, *,
           cash: Callable[[str], bool] | None = None, sales_market: str | None = None,
           own_sales: dict[str, float] | None = None, rules: int = RULES) -> tuple[dict[str, float], dict[str, dict[str, Any]], float | None]:
    """Split one item's listings ({market: price}) into kept and set aside (with reasons).
    Also returns the sales figure that was trusted, if any.

    ``cash(market)``: whether that market's money can be withdrawn (default: all but Steam).
    ``sales_market``: the market whose listed price *is* the sales figure (Steam's prices are its
    average sales), so it isn't counted twice. ``own_sales``: {market: what it has recently sold
    for on that market}, for markets with their own sales history. ``rules``: an older version of
    the rules (to correct history saved under it)."""
    trusted = trusted_sales(sales, list(listings.values()))
    cash = cash or (lambda market: market != "steam")
    kept: dict[str, float] = {}
    aside: dict[str, dict[str, Any]] = {}
    for market, price in listings.items():
        others = [p for m, p in listings.items() if m != market]
        own = (own_sales or {}).get(market)
        if rules == 1:
            verdict = _check_v1(price, others, trusted, inner, usd)
        elif own and price <= own * OWN_SALES_MARGIN:
            verdict = None                                          # people pay that there
        else:
            extra = None if sales_market in listings else trusted   # (already among the listings)
            verdict = check(price, others, extra, inner, usd, cash=cash(market))
        if verdict:
            aside[market] = verdict
        else:
            kept[market] = price
    return kept, aside, trusted
