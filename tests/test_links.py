"""Links to each item's page on every market (checked against the live sites when written)."""
from __future__ import annotations

from backend import db
from backend.markets import SOURCES
from backend.valuation import Valuator
from tests.conftest import HEADERS, add_quote


def test_waxpeer_item_pages():
    url = SOURCES["waxpeer"].listing_url
    assert url("StatTrak™ AK-47 | Redline (Field-Tested)") == "https://waxpeer.com/stattrak-ak-47-redline-field-tested/item"
    assert url("★ Karambit | Doppler (Factory New)") == "https://waxpeer.com/karambit-doppler-factory-new/item"
    assert url("Dreams & Nightmares Case") == "https://waxpeer.com/dreams-nightmares-case/item"
    assert url("Sticker Slab | 3DMAX (Gold) | Budapest 2025") == "https://waxpeer.com/sticker-slab-3dmax-gold-budapest-2025/item"
    assert url("Five-SeveN | Angry Mob (Minimal Wear)") == "https://waxpeer.com/five-seven-angry-mob-minimal-wear/item"


def test_market_csgo_item_pages():
    url = SOURCES["marketcsgo"].listing_url
    assert url("AK-47 | Redline (Field-Tested)") == "https://market.csgo.com/en/Rifle/AK-47%20%7C%20Redline%20%28Field-Tested%29"
    assert url("★ Karambit | Doppler (Factory New)").startswith("https://market.csgo.com/en/Knife/%E2%98%85%20Karambit")
    assert url("Recoil Case") == "https://market.csgo.com/en/Container/Recoil%20Case"
    assert url("Sticker | Crown (Foil)").startswith("https://market.csgo.com/en/Sticker/")
    assert url("AWP | Asiimov (Field-Tested)").startswith("https://market.csgo.com/en/Sniper%20Rifle/")


def test_every_market_links_even_prices_saved_without_one(client):
    for market in ("waxpeer", "marketcsgo", "steam", "csfloat", "skinport"):
        add_quote("Recoil Case", market, 0.3)
    db.execute("UPDATE market_prices SET url = NULL")
    urls = {r.market: r.url for r in Valuator().quotes.itertuples()}
    assert urls["waxpeer"] == "https://waxpeer.com/recoil-case/item"
    assert urls["marketcsgo"] == "https://market.csgo.com/en/Container/Recoil%20Case"
    assert urls["steam"].startswith("https://steamcommunity.com/market/listings/730/Recoil")
    assert urls["csfloat"].startswith("https://csfloat.com/search?market_hash_name=Recoil")
    assert urls["skinport"] == "https://skinport.com/item/recoil-case"
    client.post("/api/items", json={"market_hash_name": "Recoil Case"}, headers=HEADERS)
    item = client.get("/api/items?scope=total").get_json()["items"][0]
    assert all(q["url"] for q in item["sell_quotes"])


def test_manual_quotes_can_have_a_link(client):
    ok = client.post("/api/quotes", json={"market_hash_name": "Recoil Case", "market_label": "Buff163", "price": 2,
                                          "currency": "CNY", "url": "https://buff.163.com/goods/1234"}, headers=HEADERS)
    assert ok.status_code == 201
    assert db.query_one("SELECT url FROM market_prices WHERE market = 'manual:Buff163'")["url"] == "https://buff.163.com/goods/1234"
    for bad in ("javascript:alert(1)", "buff.163.com/goods/1", "https://"):
        r = client.post("/api/quotes", json={"market_hash_name": "Recoil Case", "market_label": "Buff163", "price": 2,
                                             "currency": "CNY", "url": bad}, headers=HEADERS)
        assert r.status_code == 400, bad
