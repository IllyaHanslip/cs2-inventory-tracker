"""CSFloat prices via the official API (needs a free API key).

Get a key at https://csfloat.com -> Profile -> Developers, then set
CSFLOAT_API_KEY in your .env file. Prices are returned in US cents.
"""
from __future__ import annotations

from urllib.parse import quote

from config import Config

from ..http_client import ApiError, AuthError, RateLimitedClient
from .base import MarketSource, Quote

LISTINGS_URL = "https://csfloat.com/api/v1/listings"


class CSFloat(MarketSource):
    key = "csfloat"
    label = "CSFloat"

    def __init__(self) -> None:
        self.client = RateLimitedClient("CSFloat", min_interval=Config.CSFLOAT_MIN_INTERVAL, base_backoff=20)

    def available(self) -> tuple[bool, str]:
        if not Config.CSFLOAT_API_KEY:
            return False, "needs CSFLOAT_API_KEY in .env"
        return True, ""

    def listing_url(self, market_hash_name: str) -> str | None:
        return f"https://csfloat.com/search?market_hash_name={quote(market_hash_name, safe='')}"

    def fetch_one(self, market_hash_name: str) -> Quote | None:
        resp = self.client.get(
            LISTINGS_URL,
            params={"market_hash_name": market_hash_name, "sort_by": "lowest_price", "type": "buy_now", "limit": 1},
            headers={"Authorization": Config.CSFLOAT_API_KEY},
        )
        if resp.status_code in (401, 403):
            raise AuthError("CSFloat rejected the API key (HTTP %d). Check CSFLOAT_API_KEY in .env." % resp.status_code,
                            resp.status_code)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise ApiError(f"CSFloat returned HTTP {resp.status_code}", resp.status_code)
        try:
            data = resp.json()
        except ValueError:
            return None
        listings = data.get("data") if isinstance(data, dict) else data
        if not listings:
            return None
        cents = listings[0].get("price")
        if cents is None:
            return None
        # CSFloat's reference price: its estimate of what the item sells for, from recent sales across
        # marketplaces. Used like recent sales (the price check, and a stand-in while Steam is quiet).
        reference = (listings[0].get("reference") or {}).get("base_price")
        return Quote(
            market_hash_name,
            float(cents) / 100.0,
            "USD",
            None,
            self.listing_url(market_hash_name),
            reference_price=float(reference) / 100.0 if isinstance(reference, (int, float)) and reference > 0 else None,
        )
