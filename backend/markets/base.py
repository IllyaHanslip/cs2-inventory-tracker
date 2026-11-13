"""Common types for market price sources."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Quote:
    """The lowest current listing price for one item on one market."""

    market_hash_name: str
    price: float
    currency: str
    volume: int | None = None
    url: str | None = None
    sold_price: float | None = None   # median recent sale price, when the market reports one
    reference_price: float | None = None   # the market's own estimate of what it sells for (CSFloat)


def page_slug(market_hash_name: str) -> str:
    """The item-page form of a name used by Waxpeer and Skinport: lower case, words joined by "-"
    ("StatTrak™ AK-47 | Redline (Field-Tested)" -> "stattrak-ak-47-redline-field-tested")."""
    return re.sub(r"[^a-z0-9]+", "-", market_hash_name.lower().replace("™", "").replace("★", "")).strip("-")


class MarketSource:
    key = ""
    label = ""
    bulk = False  # True: one request returns every item's price

    def available(self) -> tuple[bool, str]:
        """(usable?, reason if not)."""
        return True, ""

    def fetch_bulk(self, names: set[str]) -> dict[str, Quote]:
        raise NotImplementedError

    def fetch_one(self, market_hash_name: str) -> Quote | None:
        raise NotImplementedError

    def listing_url(self, market_hash_name: str) -> str | None:
        """The item's page on this market (for quotes saved without one)."""
        return None


def parse_money(text: str | None) -> float | None:
    """Parse strings such as '$1,234.56', '1.234,56€' or '¥ 12.30' into a float."""
    if text is None:
        return None
    s = re.sub(r"[^\d.,]", "", str(text))
    if not s or not any(ch.isdigit() for ch in s):
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        s = s.replace(",", "") if len(tail) == 3 and head else s.replace(",", ".")
    if s.count(".") > 1:
        whole, _, frac = s.rpartition(".")
        s = whole.replace(".", "") + "." + frac
    try:
        return float(s)
    except ValueError:
        return None


def parse_int(text: str | int | None) -> int | None:
    if text is None:
        return None
    digits = re.sub(r"\D", "", str(text))
    return int(digits) if digits else None
