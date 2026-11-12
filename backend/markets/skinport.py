"""Skinport prices via the official public API (no key required).

``GET /v1/items`` returns every CS2 item in one response. Skinport allows 8
requests per 5 minutes, so the list is cached locally (30 minutes by default).
The API requires Brotli compression (``brotli`` package).
Docs: https://docs.skinport.com/
"""
from __future__ import annotations

from config import Config

from .. import cache
from ..http_client import ApiError, RateLimitedClient
from .bulk import BulkPriceList


class Skinport(BulkPriceList):
    key = "skinport"
    label = "Skinport"
    url = "https://api.skinport.com/v1/items"

    def __init__(self) -> None:
        self.client = RateLimitedClient(
            "Skinport", min_interval=40.0, base_backoff=60, max_retries=1, headers={"Accept-Encoding": "br"}
        )
        self._urls: dict[str, str] = {}

    @property
    def currency(self) -> str:  # type: ignore[override]
        return Config.SKINPORT_CURRENCY

    @property
    def params(self) -> dict:  # type: ignore[override]
        return {"app_id": 730, "currency": self.currency, "tradable": 0}

    def parse(self, data):
        if not isinstance(data, list):
            raise ApiError("Skinport API returned an unexpected response")
        # min_price is null when nothing is listed right now (those rows are dropped).
        return [
            (row.get("market_hash_name"), row.get("min_price"), row.get("quantity"), row.get("item_page"))
            for row in data
        ]

    def all_names(self, allow_fetch: bool = True) -> list[str]:
        """Every CS2 item name Skinport knows about (used for autocomplete)."""
        if not allow_fetch:
            rows = cache.get_stale(self._cache_key()) or []
            return [r[0] for r in rows]
        return [r[0] for r in self.load_rows()]
