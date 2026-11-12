"""Bulk price-list markets and automatic enabling of newly added markets (network faked)."""
import pytest

from backend import db, settings
from backend.markets import SOURCES


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def use_payload(monkeypatch, key, payload):
    source = SOURCES[key]
    calls = []
    monkeypatch.setattr(source.client, "get", lambda url, params=None, headers=None: calls.append(url) or FakeResponse(payload))
    return source, calls


def test_market_csgo(app, monkeypatch):
    payload = {"success": True, "currency": "USD", "items": [
        {"market_hash_name": "Recoil Case", "volume": 120, "price": "0.200"},
        {"market_hash_name": "Kilowatt Case", "volume": "7", "price": "0.077"},
    ]}
    source, calls = use_payload(monkeypatch, "marketcsgo", payload)
    quotes = source.fetch_bulk({"Recoil Case"})
    assert list(quotes) == ["Recoil Case"]
    assert (quotes["Recoil Case"].price, quotes["Recoil Case"].currency, quotes["Recoil Case"].volume) == (0.2, "USD", 120)
    source.fetch_bulk({"Kilowatt Case"})
    assert len(calls) == 1                          # second call served from the local cache


def test_waxpeer_prices_are_in_thousandths(app, monkeypatch):
    payload = {"success": True, "items": [
        {"name": "Charm | Die-cast AK", "count": 95, "min": 2237},
        {"name": "Nothing Listed", "count": 0, "min": 0},
    ]}
    source, _ = use_payload(monkeypatch, "waxpeer", payload)
    quotes = source.fetch_bulk({"Charm | Die-cast AK", "Nothing Listed"})
    assert quotes["Charm | Die-cast AK"].price == pytest.approx(2.237)
    assert "Nothing Listed" not in quotes


def test_skinport_keeps_item_page_and_skips_unlisted(app, monkeypatch):
    payload = [
        {"market_hash_name": "Recoil Case", "currency": "EUR", "min_price": 0.21, "quantity": 900,
         "item_page": "https://skinport.com/item/recoil-case"},
        {"market_hash_name": "Rare Thing", "currency": "EUR", "min_price": None, "quantity": 0, "item_page": "x"},
    ]
    source, _ = use_payload(monkeypatch, "skinport", payload)
    quotes = source.fetch_bulk({"Recoil Case", "Rare Thing"})
    assert quotes["Recoil Case"].url == "https://skinport.com/item/recoil-case"
    assert quotes["Recoil Case"].currency == "EUR"
    assert "Rare Thing" not in quotes


def test_new_markets_are_switched_on_once_for_existing_users(app):
    db.execute("INSERT INTO items (source, market_hash_name, name, category, created_at, updated_at) "
               "VALUES ('manual', 'Recoil Case', 'Recoil Case', 'container', 'x', 'x')")
    db.execute("DELETE FROM settings WHERE key = 'known_markets'")
    settings.set_value("enabled_markets", ["steam", "skinport"])  # e.g. CSFloat deliberately off

    assert settings.enable_new_markets() == ["marketcsgo", "waxpeer"]
    assert settings.get_all()["enabled_markets"] == ["steam", "skinport", "marketcsgo", "waxpeer"]

    settings.set_value("enabled_markets", ["steam"])                 # user unticks them later...
    assert settings.enable_new_markets() == []
    assert settings.get_all()["enabled_markets"] == ["steam"]        # ...and that choice sticks


def test_fresh_install_respects_configured_markets(app):
    db.execute("DELETE FROM settings WHERE key = 'known_markets'")
    settings.set_value("enabled_markets", ["skinport"])
    assert settings.enable_new_markets() == []
    assert settings.get_all()["enabled_markets"] == ["skinport"]
