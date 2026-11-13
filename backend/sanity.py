"""Spotting listing prices that can't be right.

Rarely traded items often have one listing priced far above what they actually sell for
(say a Sticker Slab listed at £1,500 that last sold for 62p). Left alone, that listing would
set the item's value. This is a simple statistical check, not a trained model, so every
decision can be explained: a listing is set aside when it is far above the best evidence of
what the item is really worth, in this order:

1. recent sales - Skinport's public sales history, and Steam's median sale price when
   Steam answers. A sale is a real transaction; a listing is only an asking price.
   Sales are ignored if they're wildly *below* two or more listings that agree.
2. the same item's listings on the other markets;
3. for a Sticker Slab, the sticker inside: a slab can be cracked open to get its sticker
   back, so it's worth at least that much - and rarely more than a few times it.

Nothing is ever set aside for being cheap: a low listing is a real price you'd compete with.
"""
from __future__ import annotations

import statistics
from typing import Any

HIGH_FACTOR = 3.0        # set aside listings more than 3x the reference...
MIN_GAP_USD = 1.0        # ...and at least $1 above it (so pennies don't trip it)
SLAB_FACTOR = 4.0        # slabs: more than 4x (sticker inside + an empty slab)
EMPTY_SLAB_USD = 0.45    # roughly what the slab itself adds (about 36p)
SLAB_PREFIX = "Sticker Slab | "


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


def check(price: float, others: list[float], sales: float | None, inner: float | None,
          usd: float) -> dict[str, Any] | None:
    """Why a listing at ``price`` should be set aside (``{"reason", "reference"}``), or None.

    ``others``: this item's listings on the other markets; ``sales``: trusted recent sales;
    ``inner``: the sticker inside (slabs only). ``usd`` converts 1 USD into the prices' currency."""
    gap = MIN_GAP_USD * usd
    if sales:
        if price > sales * HIGH_FACTOR and price - sales > gap:
            return {"reason": "sales", "reference": sales}
        return None
    if others:
        typical = statistics.median(others)
        if price > typical * HIGH_FACTOR and price - typical > gap:
            return {"reason": "markets", "reference": typical}
        return None
    if inner:
        limit = (inner + EMPTY_SLAB_USD * usd) * SLAB_FACTOR
        if price > limit and price - limit > gap:
            return {"reason": "sticker_inside", "reference": inner}
    return None


def screen(listings: dict[str, float], sales: float | None, inner: float | None,
           usd: float) -> tuple[dict[str, float], dict[str, dict[str, Any]], float | None]:
    """Split one item's listings ({market: price}) into kept and set aside (with reasons).
    Also returns the sales figure that was trusted, if any."""
    trusted = trusted_sales(sales, list(listings.values()))
    kept: dict[str, float] = {}
    aside: dict[str, dict[str, Any]] = {}
    for market, price in listings.items():
        others = [p for m, p in listings.items() if m != market]
        verdict = check(price, others, trusted, inner, usd)
        if verdict:
            aside[market] = verdict
        else:
            kept[market] = price
    return kept, aside, trusted
