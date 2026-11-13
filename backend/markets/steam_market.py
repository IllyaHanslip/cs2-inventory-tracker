"""Steam Community Market prices via the public ``priceoverview`` endpoint.

The endpoint needs no login but is strictly rate-limited (roughly 20 requests a
minute per IP), so requests are spaced out and 429 responses trigger a cool-down.
"""
from __future__ import annotations

from urllib.parse import quote

from config import Config

from ..http_client import ApiError, RateLimitedClient
from .base import MarketSource, Quote, parse_int, parse_money

PRICEOVERVIEW_URL = "https://steamcommunity.com/market/priceoverview/"
STEAM_CURRENCY_USD = 1


def steam_listing_url(market_hash_name: str) -> str:
    return f"https://steamcommunity.com/market/listings/730/{quote(market_hash_name, safe='')}"


class SteamMarket(MarketSource):
    key = "steam"
    label = "Steam Market"

    def __init__(self) -> None:
        # One polite retry after a 429; if Steam is still limiting us we stop for this run
        # (the remaining items are retried on the next refresh) instead of hammering it.
        self.client = RateLimitedClient(
            "Steam Market", min_interval=Config.STEAM_MARKET_MIN_INTERVAL, base_backoff=45, max_retries=1
        )

    def fetch_one(self, market_hash_name: str) -> Quote | None:
        resp = self.client.get(
            PRICEOVERVIEW_URL,
            params={"appid": 730, "currency": STEAM_CURRENCY_USD, "market_hash_name": market_hash_name},
        )
        # Steam answers 500 {"success": false} for items that are not on the market.
        if resp.status_code in (400, 404, 500):
            return None
        if resp.status_code != 200:
            raise ApiError(f"Steam Market returned HTTP {resp.status_code}", resp.status_code)
        try:
            data = resp.json() or {}
        except ValueError:
            return None
        if not data.get("success"):
            return None
        price = parse_money(data.get("lowest_price"))
        sold = parse_money(data.get("median_price"))   # what it has recently sold for
        if price is None:
            price = sold
        if price is None:
            return None
        return Quote(market_hash_name, price, "USD", parse_int(data.get("volume")), steam_listing_url(market_hash_name),
                     sold_price=sold)
