"""Steam Community Market prices, from CSGOTrader's free daily price file.

Steam's own price endpoint answers one item per request and is rate-limited hard: ask too often
and it refuses every request from your connection for hours. So the app never asks Steam for
prices. Instead it downloads one public file, about 0.5 MB, at most a few times a day from
CSGOTrader (https://csgotrader.app, an open-source CS2 trading browser extension). The file holds
what every item has sold for on the Steam Market on average over the last 24 hours, 7, 30 and
90 days, in US dollars. The request goes to CSGOTrader's server, never to Steam.

Because it's an average of real sales rather than the cheapest listing right now, it's also saved
as the item's recent Steam sales (used to spot unrealistic listings elsewhere).
"""
from __future__ import annotations

from urllib.parse import quote

from ..http_client import ApiError, RateLimitedClient
from .base import Quote
from .bulk import BulkPriceList

FEED_URL = "https://prices.csgotrader.app/latest/steam.json"
FEED_PAGE = "https://csgotrader.app"
# The file is rebuilt once a day; checking a few times a day is plenty (and each check after the
# first only downloads it again if it has changed).
FEED_CACHE_HOURS = 6
# Averages, most recent first: the shortest window with any sales is used.
WINDOWS = (("last_24h", "24 hours"), ("last_7d", "7 days"), ("last_30d", "30 days"), ("last_90d", "90 days"))


def steam_listing_url(market_hash_name: str) -> str:
    return f"https://steamcommunity.com/market/listings/730/{quote(market_hash_name, safe='')}"


def pick_average(entry) -> tuple[float, str] | None:
    """(average sale price, period) from one item's entry: the most recent window with sales."""
    if not isinstance(entry, dict):
        return None
    for key, period in WINDOWS:
        value = entry.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value), period
    return None


class SteamMarket(BulkPriceList):
    key = "steam"
    label = "Steam Market"
    url = FEED_URL
    currency = "USD"
    cache_minutes = FEED_CACHE_HOURS * 60
    revalidate = True   # ask "has it changed?" (ETag) instead of downloading it again
    publisher = "CSGOTrader's daily price file (where they come from)"
    sales_from_prices = True   # its prices are averages of real sales

    def __init__(self) -> None:
        super().__init__()
        # (Downloads are already limited by the cache above; this only spaces out retries.)
        self.client = RateLimitedClient("CSGOTrader (Steam prices)", min_interval=10.0, base_backoff=60, max_retries=1)

    def listing_url(self, market_hash_name: str) -> str | None:
        return steam_listing_url(market_hash_name)

    def parse(self, data):
        if not isinstance(data, dict) or not data:
            raise ApiError("the Steam price file had an unexpected format")
        rows = []
        for name, entry in data.items():
            picked = pick_average(entry)
            if name and picked:
                rows.append((name, picked[0], None, None, picked[1]))
        return rows

    def _quotes(self, rows, names):
        # The price is an average sale, so it's also the item's recent Steam sales (no sale count).
        return {
            row[0]: Quote(row[0], float(row[1]), self.currency, None, steam_listing_url(row[0]),
                          sold_price=float(row[1]), sold_period=row[4] if len(row) > 4 else None)
            for row in rows
            if row[0] in names
        }
