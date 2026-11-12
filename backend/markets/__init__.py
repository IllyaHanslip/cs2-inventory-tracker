"""Registry of market price sources.

Only official, public, read-only APIs are used. Marketplaces whose prices can only
be read with a logged-in session (for example BUFF163) are deliberately not
automated - add their prices as manual quotes instead.
"""
from __future__ import annotations

from .base import MarketSource, Quote
from .bulk import MarketCSGO, Waxpeer
from .csfloat import CSFloat
from .skinport import Skinport
from .steam_market import SteamMarket, steam_listing_url

# Singletons, so each source keeps its own throttle/cool-down state between runs.
SOURCES: dict[str, MarketSource] = {
    "steam": SteamMarket(),
    "skinport": Skinport(),
    "csfloat": CSFloat(),
    "marketcsgo": MarketCSGO(),
    "waxpeer": Waxpeer(),
}
MANUAL_PREFIX = "manual:"


def market_label(key: str) -> str:
    if key.startswith(MANUAL_PREFIX):
        return f"{key[len(MANUAL_PREFIX):]} (manual)"
    if key == "valuation":
        return "Portfolio valuation"
    source = SOURCES.get(key)
    return source.label if source else key


__all__ = ["SOURCES", "MANUAL_PREFIX", "MarketSource", "Quote", "market_label", "steam_listing_url"]
