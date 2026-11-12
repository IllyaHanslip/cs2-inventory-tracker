"""Autocomplete catalogue: names built from CSFloat's schema, merged with market lists (no network)."""
from __future__ import annotations

import pytest

from backend import cache, catalogue
from backend.markets import SOURCES
from tests.conftest import HEADERS

SCHEMA = {
    "weapons": {
        "7": {"name": "AK-47", "type": "Weapons", "paints": {
            "282": {"name": "Redline", "min": 0.1, "max": 0.7, "stattrak": True},
            "300": {"name": "Safari Mesh", "min": 0.06, "max": 0.08, "souvenir": True},
        }},
        "507": {"name": "Karambit", "type": "Knives", "paints": {
            "418": {"name": "Doppler (Phase 1)", "min": 0.0, "max": 0.08, "stattrak": True},
            "419": {"name": "Doppler (Phase 2)", "min": 0.0, "max": 0.08, "stattrak": True},
        }},
        "5030": {"name": "Sport Gloves", "type": "Gloves", "paints": {
            "10048": {"name": "Vice", "min": 0.06, "max": 0.8},
        }},
            "9": {"name": "AWP", "type": "Weapons", "paints": {
            "259": {"name": "Redline", "min": 0.1, "max": 0.7, "stattrak": True},
        }},
    "60": {"name": "M4A4", "type": "Weapons", "paints": {
            "400": {"name": "龍王 (Dragon King)", "min": 0.0, "max": 0.07},
        }},
    },
    "stickers": {
        "8001": {"market_hash_name": "Sticker | 3DMAX (Gold) | Budapest 2025"},
        "9001": {"market_hash_name": "Patch | Phoenix"},
        "9002": {},
    },
    "keychains": {"1": {"market_hash_name": "Charm | Lil' Ava"}},
    "highlight_reels": {"1": {"market_hash_name": "Souvenir Charm | Austin 2025 Highlight | Ace"}},
    "containers": {"4001": {"market_hash_name": "CS:GO Weapon Case"}},
    "agents": {"4613": {"market_hash_name": "Bloody Darryl The Strapped | The Professionals"}},
    "collectibles": {"4682": {"market_hash_name": "Civil Protection Pin"}},
    "music_kits": {"10": {"market_hash_name": "Music Kit | Sasha, LNOE", "stattrak_price": 358},
                   "11": {"market_hash_name": "Music Kit | Quiet One"}},
}


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def fresh(client, monkeypatch):
    """Clean per-test state; any request to CSFloat fails the test unless a test replaces it."""
    monkeypatch.setattr(catalogue, "_retry_at", 0.0)
    monkeypatch.setattr(catalogue, "_memo", None)

    def no_network(*args, **kwargs):
        raise AssertionError("unexpected network request")

    monkeypatch.setattr(SOURCES["csfloat"].client, "get", no_network)
    return client


def test_build_names_matches_steam_naming():
    names = set(catalogue.build_names(SCHEMA))
    # Wears follow the float range: Redline 0.10-0.70 has no Factory New.
    assert {"AK-47 | Redline (Minimal Wear)", "StatTrak™ AK-47 | Redline (Battle-Scarred)"} <= names
    assert "AK-47 | Redline (Factory New)" not in names
    assert {"AK-47 | Safari Mesh (Factory New)", "Souvenir AK-47 | Safari Mesh (Minimal Wear)"} <= names
    assert "StatTrak™ AK-47 | Safari Mesh (Factory New)" not in names
    # Doppler phases share one Steam name; plain knives exist too.
    assert {"★ Karambit | Doppler (Factory New)", "★ StatTrak™ Karambit | Doppler (Minimal Wear)",
            "★ Karambit", "★ StatTrak™ Karambit"} <= names
    assert not any("Phase" in n for n in names)
    # Gloves: star, never StatTrak. Brackets that are part of a finish's name are kept.
    assert "★ Sport Gloves | Vice (Field-Tested)" in names
    assert not any("StatTrak™ Sport Gloves" in n for n in names)
    assert "M4A4 | 龍王 (Dragon King) (Factory New)" in names
    # Every sticker can be slabbed; patches can't.
    assert {"Sticker | 3DMAX (Gold) | Budapest 2025", "Sticker Slab | 3DMAX (Gold) | Budapest 2025",
            "Patch | Phoenix"} <= names
    assert not any(n.startswith("Sticker Slab | Patch") or n == "Sticker Slab | Phoenix" for n in names)
    assert {"Charm | Lil' Ava", "Souvenir Charm | Austin 2025 Highlight | Ace", "CS:GO Weapon Case",
            "Bloody Darryl The Strapped | The Professionals", "Civil Protection Pin",
            "Music Kit | Sasha, LNOE", "StatTrak™ Music Kit | Sasha, LNOE", "Music Kit | Quiet One"} <= names
    assert "StatTrak™ Music Kit | Quiet One" not in names


def test_unlisted_items_are_suggested(fresh):
    cache.set_cached(catalogue.NAMES_KEY, catalogue.build_names(SCHEMA))
    # The market lists only hold what's for sale right now - the slab isn't on any of them.
    cache.set_cached(SOURCES["skinport"]._cache_key(), [["Sticker | 3DMAX (Gold) | Budapest 2025", 0.4, 12, None]])
    cache.set_cached(SOURCES["waxpeer"]._cache_key(), [["Sealed Graffiti | Fart (SWAT Blue)", 0.03, 50, None]])
    names = fresh.get("/api/names?q=3dmax gold budapest").get_json()["names"]
    assert names == ["Sticker | 3DMAX (Gold) | Budapest 2025", "Sticker Slab | 3DMAX (Gold) | Budapest 2025"]
    # Market-only names (graffiti isn't in the schema) still come through.
    assert fresh.get("/api/names?q=fart swat").get_json()["names"] == ["Sealed Graffiti | Fart (SWAT Blue)"]


def test_ranking_and_sticker_kind(fresh):
    cache.set_cached(catalogue.NAMES_KEY, catalogue.build_names(SCHEMA))
    fresh.post("/api/items", json={"market_hash_name": "StatTrak™ AK-47 | Redline (Field-Tested)"}, headers=HEADERS)
    names = fresh.get("/api/names?q=ak redline").get_json()["names"]
    assert names[0] == "StatTrak™ AK-47 | Redline (Field-Tested)"          # items you own come first
    # Then plain before StatTrak, each in wear order (Redline has no Factory New); 'ak' only
    # matching inside 'StatTrak' ranks last.
    assert names[1:] == [f"AK-47 | Redline ({w})" for w in ("Minimal Wear", "Field-Tested", "Well-Worn", "Battle-Scarred")] + \
        [f"StatTrak™ AK-47 | Redline ({w})" for w in ("Minimal Wear", "Well-Worn", "Battle-Scarred")] +         [f"StatTrak™ AWP | Redline ({w})" for w in ("Minimal Wear", "Field-Tested", "Well-Worn", "Battle-Scarred")]
    # The sticker picker only offers loose stickers, not slabs.
    assert fresh.get("/api/names?q=3dmax&kind=sticker").get_json()["names"] == ["Sticker | 3DMAX (Gold) | Budapest 2025"]
    assert fresh.get("/api/names?q=a").get_json()["names"] == []


def test_schema_downloaded_once_and_shared(fresh, monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None):
        calls.append(url)
        assert headers is None                       # public schema: the API key is never sent
        return FakeResponse(200, SCHEMA)

    monkeypatch.setattr(SOURCES["csfloat"].client, "get", fake_get)
    assert "Sticker Slab | 3DMAX (Gold) | Budapest 2025" in fresh.get("/api/names?q=slab 3dmax").get_json()["names"]
    fresh.get("/api/names?q=karambit")
    assert calls == [catalogue.SCHEMA_URL]
    # The sticker IDs used for CSFloat comparisons come from the same download.
    from backend import comparables
    assert comparables.sticker_names()[8001] == "Sticker | 3DMAX (Gold) | Budapest 2025"
    assert calls == [catalogue.SCHEMA_URL]


def test_download_failure_falls_back_and_waits(fresh, monkeypatch):
    calls = []

    def failing_get(url, params=None, headers=None):
        calls.append(url)
        return FakeResponse(503)

    monkeypatch.setattr(SOURCES["csfloat"].client, "get", failing_get)
    cache.set_cached(SOURCES["skinport"]._cache_key(), [["AK-47 | Redline (Field-Tested)", 30.0, 5, None]])
    assert fresh.get("/api/names?q=redline").get_json()["names"] == ["AK-47 | Redline (Field-Tested)"]
    fresh.get("/api/names?q=karambit")
    assert len(calls) == 1                           # no retry on every keystroke
