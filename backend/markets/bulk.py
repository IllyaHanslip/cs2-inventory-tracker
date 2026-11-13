"""Base class for markets that publish one public price list covering every item.

One request returns the whole list; it is reduced to (name, price, volume) and
cached locally so repeated refreshes don't hit the market again.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from config import Config

from .. import cache
from ..http_client import ApiError, RateLimitedClient
from .base import MarketSource, Quote, page_slug

log = logging.getLogger(__name__)


class BulkPriceList(MarketSource):
    bulk = True
    url = ""
    params: dict[str, Any] = {}
    currency = "USD"

    def __init__(self) -> None:
        self.client = RateLimitedClient(self.label, min_interval=30.0, base_backoff=60, max_retries=1)

    def _cache_key(self) -> str:
        return f"pricelist_{self.key}_{self.currency}"

    def parse(self, data: Any) -> list[tuple]:
        """Turn the raw response into (market_hash_name, price, volume[, page_url]) rows."""
        raise NotImplementedError

    def listing_url(self, market_hash_name: str) -> str | None:
        return None

    def load_rows(self) -> list[list]:
        # ``stale`` is set when the market couldn't be reached and its last saved list was used.
        self.stale = False
        key = self._cache_key()
        cached = cache.get_cached(key, Config.BULK_CACHE_MINUTES * 60)
        if cached is not None:
            return cached
        try:
            resp = self.client.get(self.url, params=self.params or None)
            if resp.status_code != 200:
                raise ApiError(f"{self.label} returned HTTP {resp.status_code}", resp.status_code)
            rows = [[*row[:3], row[3] if len(row) > 3 else None]
                    for row in self.parse(resp.json()) if row[0] and row[1] and row[1] > 0]
            if not rows:
                raise ApiError(f"{self.label} returned an empty price list")
        except (ApiError, ValueError, KeyError, TypeError) as exc:
            stale = cache.get_stale(key)
            if stale is not None:
                log.warning("%s unavailable (%s); using cached prices", self.label, exc)
                self.stale = True
                return stale
            raise ApiError(f"{self.label}: {exc}") from None
        cache.set_cached(key, rows)
        return rows

    def _quotes(self, rows: list[list], names: set[str]) -> dict[str, Quote]:
        return {
            row[0]: Quote(row[0], float(row[1]), self.currency, row[2], row[3] or self.listing_url(row[0]))
            for row in rows
            if row[0] in names
        }

    def fetch_bulk(self, names: set[str]) -> dict[str, Quote]:
        return self._quotes(self.load_rows(), names)

    def saved_quotes(self, names: set[str]) -> dict[str, Quote]:
        """Prices from the list already saved on this PC, however old - never downloads."""
        return self._quotes(cache.get_stale(self._cache_key()) or [], names)


class MarketCSGO(BulkPriceList):
    """market.csgo.com public price list (lowest listing per item, USD). No key needed."""

    key = "marketcsgo"
    label = "Market.CSGO"
    url = "https://market.csgo.com/api/v2/prices/USD.json"

    def listing_url(self, market_hash_name: str) -> str | None:
        # Its item pages are /en/<category>/<exact name>; the item is found by its name.
        return f"https://market.csgo.com/en/{quote(_csgo_category(market_hash_name))}/{quote(market_hash_name, safe='')}"

    def parse(self, data):
        if not data.get("success"):
            raise ApiError("Market.CSGO reported an error")
        return [(i.get("market_hash_name"), float(i["price"]), _int(i.get("volume"))) for i in data["items"]]


class Waxpeer(BulkPriceList):
    """waxpeer.com public price list (lowest listing per item). Prices are in 1/1000 USD. No key needed."""

    key = "waxpeer"
    label = "Waxpeer"
    url = "https://api.waxpeer.com/v1/prices"
    params = {"game": "csgo", "minified": 1}

    def listing_url(self, market_hash_name: str) -> str | None:
        slug = page_slug(market_hash_name)          # waxpeer.com/<slug>/item
        return f"https://waxpeer.com/{slug}/item" if slug else None

    def parse(self, data):
        if not data.get("success"):
            raise ApiError("Waxpeer reported an error")
        return [
            (i.get("name"), i["min"] / 1000.0, _int(i.get("count")))
            for i in data["items"]
            if i.get("min") and (i.get("count") or 0) > 0
        ]


def _csgo_category(name: str) -> str:
    """Market.CSGO's section for an item (Rifle, Knife, Container, Sticker...)."""
    from ..classify import categorize
    from ..tags import WEAPON_CLASS, split_skin

    category = categorize(name)
    if category == "container":
        return "Container"
    if category in ("sticker", "sticker_slab"):
        return "Sticker"
    if category == "weapon_skin":
        if name.startswith("★"):
            return "Gloves" if "Gloves" in name or "Wraps" in name else "Knife"
        weapon, _finish = split_skin(name)
        cls = WEAPON_CLASS.get(weapon or "", "Other")
        return {"Sniper rifle": "Sniper Rifle", "Machine gun": "Machinegun"}.get(cls, cls)
    return "Other"


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
