"""User-editable runtime settings stored in SQLite (defaults come from config/.env)."""
from __future__ import annotations

import json
import re
from typing import Any

from config import Config

from . import db

MARKET_KEYS = ("steam", "skinport", "csfloat", "marketcsgo", "waxpeer")
VALUATION_STRATEGIES = ("median", "lowest", "highest", "mean", *MARKET_KEYS)
# Markets that existed before market auto-enabling was added.
ORIGINAL_MARKETS = ("steam", "skinport", "csfloat")
EDITABLE_KEYS = (
    "steam_id",
    "base_currency",
    "valuation_strategy",
    "enabled_markets",
    "sticker_rate_min",
    "sticker_rate_max",
    "sticker_premium_cap",
    "sticker_evidence",
    "selling_fees",
)


def _defaults() -> dict[str, Any]:
    strategy = Config.VALUATION_STRATEGY if Config.VALUATION_STRATEGY in VALUATION_STRATEGIES else "median"
    return {
        "steam_id": Config.DEFAULT_STEAM_ID,
        "base_currency": Config.BASE_CURRENCY,
        "valuation_strategy": strategy,
        "enabled_markets": [m for m in Config.ENABLED_MARKETS if m in MARKET_KEYS],
        "sticker_rate_min": Config.STICKER_RATE_MIN,
        "sticker_rate_max": Config.STICKER_RATE_MAX,
        "sticker_premium_cap": Config.STICKER_PREMIUM_CAP,
        "sticker_evidence": True,   # value items with valuable stickers from CSFloat comparables
        "selling_fees": {},         # your overrides of the default fees in fees.py
    }


def get(key: str, default: Any = None) -> Any:
    row = db.query_one("SELECT value FROM settings WHERE key = ?", (key,))
    if row is not None:
        return json.loads(row["value"])
    return _defaults().get(key, default)


def set_value(key: str, value: Any) -> None:
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def get_all() -> dict[str, Any]:
    values = _defaults()
    for row in db.query("SELECT key, value FROM settings"):
        if row["key"] in values:
            values[row["key"]] = json.loads(row["value"])
    return values


def enable_new_markets() -> list[str]:
    """Switch on, once, any market added since this database last ran. Unticking it in
    Settings afterwards sticks, because it is then no longer 'new'."""
    stored = get("known_markets")
    if stored is None and not db.query_one("SELECT 1 FROM items LIMIT 1"):
        set_value("known_markets", list(MARKET_KEYS))  # fresh install: .env/defaults decide
        return []
    known = stored or list(ORIGINAL_MARKETS)
    new = [m for m in MARKET_KEYS if m not in known]
    if new:
        enabled = get_all()["enabled_markets"]
        set_value("enabled_markets", [m for m in MARKET_KEYS if m in enabled or m in new])
    if new or get("known_markets") is None:
        set_value("known_markets", list(MARKET_KEYS))
    return new


def _as_rate(value: Any, label: str) -> float:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number") from None
    if rate > 1:  # accept percentages such as 10 meaning 10 %
        rate /= 100
    if not 0 <= rate <= 1:
        raise ValueError(f"{label} must be between 0 % and 100 %")
    return round(rate, 4)


def validate(changes: dict[str, Any], *, known_currencies: set[str] | None = None) -> dict[str, Any]:
    """Validate a partial settings update and return the cleaned values."""
    clean: dict[str, Any] = {}
    for key, value in changes.items():
        if key not in EDITABLE_KEYS:
            raise ValueError(f"Unknown setting: {key}")
        if key == "base_currency":
            code = str(value or "").strip().upper()
            if not re.fullmatch(r"[A-Z]{3}", code):
                raise ValueError("Base currency must be a 3-letter ISO code such as USD")
            if known_currencies and code not in known_currencies:
                raise ValueError(f"No exchange rate is available for {code}")
            clean[key] = code
        elif key == "valuation_strategy":
            if value not in VALUATION_STRATEGIES:
                raise ValueError(f"Valuation strategy must be one of: {', '.join(VALUATION_STRATEGIES)}")
            clean[key] = value
        elif key == "enabled_markets":
            if not isinstance(value, list) or any(m not in MARKET_KEYS for m in value):
                raise ValueError(f"Markets must be a list containing only: {', '.join(MARKET_KEYS)}")
            clean[key] = [m for m in MARKET_KEYS if m in value]
        elif key in ("sticker_rate_min", "sticker_rate_max"):
            clean[key] = _as_rate(value, key.replace("_", " "))
        elif key == "sticker_premium_cap":
            try:
                cap = float(value)
            except (TypeError, ValueError):
                raise ValueError("Max sticker premium must be a number") from None
            if not 0 <= cap <= 10:
                raise ValueError("Max sticker premium must be between 0 % and 1000 % of the skin's price")
            clean[key] = round(cap, 4)
        elif key == "steam_id":
            clean[key] = str(value or "").strip()
        elif key == "sticker_evidence":
            if not isinstance(value, bool):
                raise ValueError("sticker_evidence must be true or false")
            clean[key] = value
        elif key == "selling_fees":
            from .fees import validate as validate_fees

            clean[key] = validate_fees(value)
    merged = {**get_all(), **clean}
    if merged["sticker_rate_min"] > merged["sticker_rate_max"]:
        raise ValueError("The minimum sticker rate cannot be higher than the maximum")
    return clean


def update(changes: dict[str, Any], **kwargs) -> dict[str, Any]:
    for key, value in validate(changes, **kwargs).items():
        set_value(key, value)
    return get_all()
