"""Pictures for manually added items, from the item catalogue (downloads are faked)."""
from __future__ import annotations

import pytest

from backend import db, images
from config import Config
from tests.conftest import HEADERS

ECON = "https://community.akamai.steamstatic.com/economy/image/"
CATALOGUE = {
    "sticker_slabs": [
        {"name": "Sticker Slab | 3DMAX (Gold) | Budapest 2025", "market_hash_name": None, "image": ECON + "slabHash"},
    ],
    "stickers": [
        {"name": "Sticker | 3DMAX (Gold) | Budapest 2025", "market_hash_name": "Sticker | 3DMAX (Gold) | Budapest 2025",
         "image": ECON + "stickerHash"},
        {"name": "Sticker | Shooter", "image": "https://cdn.steamstatic.com/apps/730/icons/econ/stickers/shooter.png"},
        {"name": "Sticker | Hosted Elsewhere", "image": "https://example.com/not-steam.png"},
    ],
    "skins_not_grouped": [
        {"market_hash_name": "★ Karambit | Doppler (Factory New)", "phase": "Black Pearl", "image": ECON + "gem"},
        {"market_hash_name": "★ Karambit | Doppler (Factory New)", "phase": "Phase 2", "image": ECON + "phase2"},
        {"market_hash_name": "AK-47 | Redline (Field-Tested)", "image": ECON + "redlineFT/360fx360f"},
    ],
    "music_kits": [{"market_hash_name": "Music Kit | Sasha, LNOE", "image": ECON + "sasha"}],
    "crates": [{"market_hash_name": "Kilowatt Case", "image": ECON + "kilowatt"}],
    "tools": [{"name": "Storage Unit", "image": ECON + "storageUnit"}],
}


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def downloads(client, monkeypatch):
    """Enable pictures with a fake catalogue; returns the list of files downloaded."""
    monkeypatch.setattr(Config, "ITEM_IMAGES", True)
    monkeypatch.setattr(images, "_retry_at", 0.0)
    fetched = []

    def fake_get(url, params=None, headers=None):
        file = url.rsplit("/", 1)[1].removesuffix(".json")
        fetched.append(file)
        return FakeResponse(200, CATALOGUE[file]) if file in CATALOGUE else FakeResponse(404)

    monkeypatch.setattr(images.client, "get", fake_get)
    return fetched


def icon_of(client, name):
    items = client.get("/api/items?scope=total").get_json()["items"]
    return next(i["icon"] for i in items if i["market_hash_name"] == name)


def test_manual_item_gets_a_picture(client, downloads):
    name = "Sticker Slab | 3DMAX (Gold) | Budapest 2025"
    client.post("/api/items", json={"market_hash_name": name}, headers=HEADERS)
    assert downloads == ["sticker_slabs"]                      # only the file for that kind of item
    assert icon_of(client, name) == f"{Config.STEAM_IMAGE_CDN}slabHash/128fx128f"
    # A second item of the same kind needs no new download.
    client.post("/api/items", json={"market_hash_name": name, "quantity": 2}, headers=HEADERS)
    assert downloads == ["sticker_slabs"]


def test_container_contents_get_pictures(client, downloads):
    box = client.post("/api/items", json={"market_hash_name": "Storage Unit", "category": "container"},
                      headers=HEADERS).get_json()["item"]["id"]
    rows = [{"market_hash_name": "AK-47 | Redline (Field-Tested)", "quantity": 3},
            {"market_hash_name": "Sticker | Shooter", "quantity": 1}]
    client.post(f"/api/containers/{box}/items", json={"items": rows}, headers=HEADERS)
    assert downloads == ["crates", "tools", "skins_not_grouped", "stickers"]
    assert icon_of(client, "Storage Unit").endswith("/storageUnit/128fx128f")
    contents = client.get(f"/api/containers/{box}").get_json()["contents"]
    icons = {c["market_hash_name"]: c["icon"] for c in contents}
    assert icons["AK-47 | Redline (Field-Tested)"] == f"{Config.STEAM_IMAGE_CDN}redlineFT/128fx128f"
    assert icons["Sticker | Shooter"] == "https://cdn.steamstatic.com/apps/730/icons/econ/stickers/shooter.png"


def test_steam_twin_wins_and_names_without_pictures(client, downloads):
    db.execute(
        "INSERT INTO items (source, market_hash_name, name, category, quantity, icon_url, created_at, updated_at) "
        "VALUES ('steam', 'Sticker | Shooter', 'Sticker | Shooter', 'sticker', 1, 'steamHash', 'x', 'x')"
    )
    client.post("/api/items", json={"market_hash_name": "Sticker | Shooter"}, headers=HEADERS)
    assert downloads == []                                     # borrowed from the synced copy instead
    client.post("/api/items", json={"market_hash_name": "Sticker | Hosted Elsewhere"}, headers=HEADERS)
    assert icon_of(client, "Sticker | Hosted Elsewhere") is None   # only Steam-hosted pictures are used
    # The file was just checked, so another unknown sticker doesn't download it again today.
    client.post("/api/items", json={"market_hash_name": "Sticker | Not Real"}, headers=HEADERS)
    assert downloads == ["stickers"]


def test_backfill_and_failures(client, downloads, monkeypatch):
    # Added while pictures were switched off...
    monkeypatch.setattr(Config, "ITEM_IMAGES", False)
    client.post("/api/items", json={"market_hash_name": "★ Karambit | Doppler (Factory New)"}, headers=HEADERS)
    client.post("/api/items", json={"market_hash_name": "StatTrak™ Music Kit | Sasha, LNOE"}, headers=HEADERS)
    assert downloads == []
    # ...then found later by the hourly check / price refresh.
    monkeypatch.setattr(Config, "ITEM_IMAGES", True)
    assert images.backfill() == set()
    assert icon_of(client, "★ Karambit | Doppler (Factory New)").endswith("/phase2/128fx128f")   # not the gem
    assert icon_of(client, "StatTrak™ Music Kit | Sasha, LNOE").endswith("/sasha/128fx128f")

    # A failed download never breaks adding an item, and isn't retried on every request.
    monkeypatch.setattr(images, "_retry_at", 0.0)
    monkeypatch.setattr(images.client, "get", lambda url, **kw: downloads.append("fail") or FakeResponse(503))
    for _ in range(2):
        resp = client.post("/api/items", json={"market_hash_name": "Sticker Slab | Anything"}, headers=HEADERS)
        assert resp.status_code == 201
    assert downloads.count("fail") == 1


def test_icon_urls_are_validated():
    assert images.icon_from_url(ECON + "abc_-123") == "abc_-123"
    assert images.icon_from_url("https://cdn.steamstatic.com/apps/730/icons/x.png").startswith("https://cdn.")
    for bad in ("http://cdn.steamstatic.com/x.png", "https://steamstatic.com.evil.example/x.png",
                "https://raw.githubusercontent.com/x.png", "javascript:alert(1)", None, 5):
        assert images.icon_from_url(bad) is None
    assert images.files_for("Sticker Slab | X") == ("sticker_slabs",)
    assert images.files_for("Charm | Lil' Ava") == ("keychains",)
    assert images.files_for("Kilowatt Case") == ("crates", "tools")
    assert images.files_for("★ StatTrak™ Karambit") == ("skins_not_grouped",)
