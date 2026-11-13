"""Application configuration.

Values are read from environment variables, optionally loaded from a local
``.env`` file (git-ignored). Copy ``.env.example`` to ``.env`` and edit it.
Settings marked "UI" can also be changed later from the Settings dialog; the
value saved there takes priority over the one in ``.env``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
# The one-file Windows app (PyInstaller) unpacks its code to a temporary folder each run, so your
# data and .env live in a lasting folder instead: %LOCALAPPDATA%\CS2 Inventory Intelligence.
FROZEN = bool(getattr(sys, "frozen", False))
HOME_DIR = (Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "CS2 Inventory Intelligence") if FROZEN else BASE_DIR
load_dotenv(HOME_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None or not value.strip() else value.strip()


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _list(name: str, default: str) -> list[str]:
    return [part.strip().lower() for part in _env(name, default).split(",") if part.strip()]


class Config:
    FROZEN = FROZEN        # running as the packaged Windows app
    HOME_DIR = HOME_DIR    # where data/ and .env live

    # --- Server ---------------------------------------------------------------
    HOST = _env("HOST", "127.0.0.1")          # keep 127.0.0.1 unless you know why
    PORT = _int("PORT", 5000)
    DEBUG = _bool("FLASK_DEBUG", False)
    DEMO = False                               # set by `python app.py --demo`

    # --- Storage --------------------------------------------------------------
    DATA_DIR = Path(_env("DATA_DIR", str(HOME_DIR / "data")))
    DATABASE_PATH = Path(_env("DATABASE_PATH", str(DATA_DIR / "cs2_tracker.db")))

    # --- API keys (optional) --------------------------------------------------
    CSFLOAT_API_KEY = _env("CSFLOAT_API_KEY")  # enables the CSFloat price source
    # Older versions used a Steam Web API key. It is no longer needed; if one is still
    # set, the app reminds you to delete it from .env and revoke it.
    LEGACY_STEAM_API_KEY_SET = bool(_env("STEAM_API_KEY"))

    # --- Defaults that can be overridden in the UI ----------------------------
    DEFAULT_STEAM_ID = _env("STEAM_ID64")
    BASE_CURRENCY = _env("BASE_CURRENCY", "USD").upper()
    ENABLED_MARKETS = _list("ENABLED_MARKETS", "steam,skinport,csfloat,marketcsgo,waxpeer")
    VALUATION_STRATEGY = _env("VALUATION_STRATEGY", "median").lower()
    STICKER_RATE_MIN = _float("STICKER_RATE_MIN", 0.05)
    STICKER_RATE_MAX = _float("STICKER_RATE_MAX", 0.15)

    # --- Sticker valuation tuning (prices in USD) -----------------------------
    # A sticker cheaper than STICKER_PRICE_LOW adds STICKER_RATE_MIN of its loose
    # price; one dearer than STICKER_PRICE_HIGH adds STICKER_RATE_MAX. In between
    # the rate is interpolated on a log scale.
    STICKER_PRICE_LOW = _float("STICKER_PRICE_LOW", 1.0)
    STICKER_PRICE_HIGH = _float("STICKER_PRICE_HIGH", 100.0)
    STICKER_CRAFT_BONUS = _float("STICKER_CRAFT_BONUS", 1.2)  # 4+ identical stickers
    # The total sticker premium on one item is capped relative to the skin's own price
    # (1.0 = can at most double it), with diminishing returns as it nears the cap (UI).
    STICKER_PREMIUM_CAP = _float("STICKER_PREMIUM_CAP", 1.0)
    STICKER_CRAFT_CAP_MULTIPLIER = _float("STICKER_CRAFT_CAP_MULTIPLIER", 3.0)  # 4-of-a-kind crafts

    # --- Containers -----------------------------------------------------------
    CONTAINER_CAPACITY = 1000  # a CS2 Storage Unit holds up to 1,000 items

    # --- Market sources -------------------------------------------------------
    SKINPORT_CURRENCY = _env("SKINPORT_CURRENCY", "EUR").upper()
    BULK_CACHE_MINUTES = max(5.0, _float("BULK_CACHE_MINUTES", 30))  # Skinport, Market.CSGO, Waxpeer lists
    STEAM_MARKET_MIN_INTERVAL = max(1.0, _float("STEAM_MARKET_MIN_INTERVAL", 3.5))
    CSFLOAT_MIN_INTERVAL = max(0.5, _float("CSFLOAT_MIN_INTERVAL", 1.5))
    STEAM_INVENTORY_MIN_INTERVAL = 5.0
    PRICE_TTL_HOURS = _float("PRICE_TTL_HOURS", 12)  # per-item sources re-fetch after this
    # Selling many of one item: each market is assumed to take about what it sells in this many days.
    BULK_SELL_DAYS = max(1, _int("BULK_SELL_DAYS", 30))

    # --- HTTP behaviour -------------------------------------------------------
    REQUEST_TIMEOUT = _float("REQUEST_TIMEOUT", 25)
    MAX_RETRIES = _int("MAX_RETRIES", 3)
    MAX_BACKOFF_SECONDS = _float("MAX_BACKOFF_SECONDS", 180)
    USER_AGENT = _env("USER_AGENT", "cs2-inventory-tracker/1.0 (local personal-use app)")

    # --- Scheduler ------------------------------------------------------------
    AUTO_DAILY_SNAPSHOT = _bool("AUTO_DAILY_SNAPSHOT", True)
    AUTO_SYNC_STEAM = _bool("AUTO_SYNC_STEAM", True)
    # The daily update (Steam re-sync, prices, snapshot) never runs before this time of day (24-hour, local).
    DAILY_UPDATE_TIME = _env("DAILY_UPDATE_TIME", "12:00")
    # A Windows notification after each background update, saying what it found.
    NOTIFICATIONS = _bool("NOTIFICATIONS", True)
    ALERT_MOVE_PCT = max(1.0, _float("ALERT_MOVE_PCT", 10))   # an item moving this much (%) is worth a mention

    # --- Misc -----------------------------------------------------------------
    STEAM_IMAGE_CDN = _env("STEAM_IMAGE_CDN", "https://community.steamstatic.com/economy/image/")
    # Look up pictures for manually added items in a public item catalogue (see backend/images.py).
    ITEM_IMAGES = _bool("ITEM_IMAGES", True)
