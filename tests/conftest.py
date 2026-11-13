"""Test fixtures. Every test gets a fresh temporary database and fixed settings,
so results never depend on your own .env file or data - and no test touches the network."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import Config  # noqa: E402

# A made-up SteamID64: it matches the 7656119... format but is below the range
# Steam assigns to real accounts, so it can never point at a real person.
FAKE_STEAM_ID = "76561190000000001"
FX_DAY = "2026-01-01"


@pytest.fixture
def app(tmp_path, monkeypatch):
    overrides = {
        "DATABASE_PATH": tmp_path / "test.db",
        "AUTO_DAILY_SNAPSHOT": False,
        "DEMO": False,
        "HOST": "127.0.0.1",
        "BASE_CURRENCY": "USD",
        "ENABLED_MARKETS": ["steam", "skinport", "csfloat", "marketcsgo", "waxpeer"],
        "VALUATION_STRATEGY": "median",
        "STICKER_RATE_MIN": 0.05,
        "STICKER_RATE_MAX": 0.15,
        "STICKER_PRICE_LOW": 1.0,
        "STICKER_PRICE_HIGH": 100.0,
        "STICKER_CRAFT_BONUS": 1.2,
        "DEFAULT_STEAM_ID": "",
        "LEGACY_STEAM_API_KEY_SET": False,
        "SKINPORT_CURRENCY": "EUR",
        "BULK_CACHE_MINUTES": 30,
        "CSFLOAT_API_KEY": "",
        "ITEM_IMAGES": False,
        "NOTIFICATIONS": False,
        "ALERT_MOVE_PCT": 10.0,
    }
    for key, value in overrides.items():
        monkeypatch.setattr(Config, key, value)

    from app import create_app
    from backend import db
    from backend.markets import SOURCES

    def offline(*args, **kwargs):
        raise AssertionError("tests must not touch the network")

    monkeypatch.setattr(SOURCES["skinport"].sales_client, "get", offline)

    def csfloat_offline(*args, **kwargs):
        from backend.http_client import ApiError

        raise ApiError("CSFloat is offline in tests")

    monkeypatch.setattr(SOURCES["csfloat"].client, "get", csfloat_offline)

    application = create_app()
    db.executemany(
        "INSERT INTO fx_rates (rate_date, currency, per_usd, source) VALUES (?, ?, ?, 'test')",
        [(FX_DAY, "USD", 1.0), (FX_DAY, "EUR", 0.9), (FX_DAY, "CNY", 7.0), (FX_DAY, "GBP", 0.8)],
    )
    return application


@pytest.fixture
def client(app):
    return app.test_client()


HEADERS = {"X-Requested-With": "cs2-tracker"}


def add_quote(name: str, market: str, price: float, currency: str = "USD", manual: bool = False) -> None:
    from backend import db

    db.execute(
        "INSERT OR REPLACE INTO market_prices (market_hash_name, market, price, currency, is_manual, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, '2026-01-01T00:00:00+00:00')",
        (name, market, price, currency, int(manual)),
    )
