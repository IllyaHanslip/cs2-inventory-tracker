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
from .base import page_slug
from .bulk import BulkPriceList

SALES_URL = "https://api.skinport.com/v1/sales/history"
# Sales windows Skinport reports, shortest first. A window needs this many sales to be preferred.
SALES_WINDOWS = (("last_7_days", "7 days"), ("last_30_days", "30 days"), ("last_90_days", "90 days"))
SALES_PREFERRED_VOLUME = 3
SALES_CACHE_SECONDS = 24 * 3600   # ~2 MB, and sales move slowly: once a day is plenty


def pick_sales(entry: dict) -> tuple[float, int, str] | None:
    """(median price, number of sales, period) from one sales-history entry: the shortest window
    with a few sales, else the longest window with any."""
    windows = [(entry.get(key) or {}, label) for key, label in SALES_WINDOWS]
    usable = [(w, label) for w, label in windows if (w.get("volume") or 0) > 0 and w.get("median")]
    if not usable:
        return None
    window, label = next(((w, lbl) for w, lbl in usable if w["volume"] >= SALES_PREFERRED_VOLUME), usable[-1])
    return float(window["median"]), int(window["volume"]), label


class Skinport(BulkPriceList):
    key = "skinport"
    label = "Skinport"
    url = "https://api.skinport.com/v1/items"

    def __init__(self) -> None:
        self.client = RateLimitedClient(
            "Skinport", min_interval=40.0, base_backoff=60, max_retries=1, headers={"Accept-Encoding": "br"}
        )
        self._urls: dict[str, str] = {}
        # The sales history is a separate endpoint with its own rate limit.
        self.sales_client = RateLimitedClient(
            "Skinport sales", min_interval=40.0, base_backoff=60, max_retries=1, headers={"Accept-Encoding": "br"}
        )

    @property
    def currency(self) -> str:  # type: ignore[override]
        return Config.SKINPORT_CURRENCY

    @property
    def params(self) -> dict:  # type: ignore[override]
        return {"app_id": 730, "currency": self.currency, "tradable": 0}

    def listing_url(self, market_hash_name: str) -> str | None:
        # Skinport sends each item's page with its prices; this is the same address, for any saved without it.
        slug = page_slug(market_hash_name)
        return f"https://skinport.com/item/{slug}" if slug else None

    def sales(self) -> list[list]:
        """Recent sales of every item: [name, median price, number of sales, period] (cached like the
        price list, but for a day). Sales are real transactions, so they show what listings are realistic."""
        key = f"sales_skinport_{self.currency}"
        cached = cache.get_cached(key, SALES_CACHE_SECONDS)
        if cached is not None:
            return cached
        try:
            resp = self.sales_client.get(SALES_URL, params={"app_id": 730, "currency": self.currency})
            if resp.status_code != 200:
                raise ApiError(f"Skinport sales history returned HTTP {resp.status_code}", resp.status_code)
            data = resp.json()
            if not isinstance(data, list):
                raise ApiError("Skinport sales history had an unexpected format")
            rows = [[e["market_hash_name"], *picked] for e in data
                    if isinstance(e, dict) and e.get("market_hash_name") and (picked := pick_sales(e))]
        except (ApiError, ValueError, KeyError, TypeError) as exc:
            stale = cache.get_stale(key)
            if stale is not None:
                return stale
            raise ApiError(f"Skinport sales history: {exc}") from None
        cache.set_cached(key, rows)
        return rows

    def parse(self, data):
        if not isinstance(data, list):
            raise ApiError("Skinport API returned an unexpected response")
        # min_price is null when nothing is listed right now (those rows are dropped).
        return [
            (row.get("market_hash_name"), row.get("min_price"), row.get("quantity"), row.get("item_page"))
            for row in data
        ]
