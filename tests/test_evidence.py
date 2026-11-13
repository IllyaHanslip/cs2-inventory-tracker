"""Evidence-based values for items with valuable stickers, per-market status in the item view,
and the catch-up rules that refresh prices when a market has none yet. No network."""
import pytest

from backend import comparables, db, evidence, settings
from backend.markets import SOURCES
from backend.refresh import refresh_reasons
from config import Config
from tests.conftest import HEADERS, add_quote

SKIN = "AK-47 | Blue Laminate (Field-Tested)"


def make_item(client):
    add_quote(SKIN, "skinport", 9.0)
    add_quote("Sticker | Team Dignitas | Katowice 2014", "skinport", 800.0)
    body = {"market_hash_name": SKIN, "stickers": [{"kind": "sticker", "name": "Team Dignitas | Katowice 2014"}]}
    return client.post("/api/items", json=body, headers=HEADERS).get_json()["item"]["id"]


def get_item(client):
    return client.get("/api/items?scope=total").get_json()["items"][0]


def test_evidence_becomes_the_items_value(client):
    item_id = make_item(client)
    before = get_item(client)
    assert before["price_source"] == "median" and before["sticker_capped"]

    sig = evidence.signature([{"kind": "sticker", "name": "Team Dignitas | Katowice 2014"}])
    evidence.save(item_id, sig, 118.0, "USD", "1 usable listing (listed now with Team Dignitas)", ["note"])
    item = get_item(client)
    assert item["price_source"] == "evidence" and item["market_unit_value"] == pytest.approx(118.0)
    assert item["unit_value"] == pytest.approx(118.0 * 0.98 * 0.975)   # sold on CSFloat: lowest fees
    assert item["sell_market"] == "csfloat"
    assert item["sticker_included"] and item["sticker_premium"] == 0
    assert item["market_price"] == pytest.approx(9.0)          # plain skin still shown
    assert item["change_24h"] is None                            # not comparable with plain-skin history

    # A custom price still wins over evidence.
    client.put(f"/api/items/{item_id}", json={"custom_price": 60, "custom_price_currency": "USD"}, headers=HEADERS)
    assert get_item(client)["market_unit_value"] == pytest.approx(60)
    client.put(f"/api/items/{item_id}", json={"custom_price": ""}, headers=HEADERS)

    # Evidence measured for different stickers is ignored.
    evidence.save(item_id, "sticker:Something Else", 500.0, "USD", "x", [])
    assert get_item(client)["price_source"] == "median"

    # And the feature can be switched off.
    evidence.save(item_id, sig, 118.0, "USD", "x", [])
    settings.set_value("sticker_evidence", False)
    assert get_item(client)["price_source"] == "median"


def test_refresh_gathers_evidence_for_items_that_need_it(client, monkeypatch):
    item_id = make_item(client)
    add_quote("Recoil Case", "skinport", 0.3)
    client.post("/api/items", json={"market_hash_name": "Recoil Case"}, headers=HEADERS)
    monkeypatch.setattr(Config, "CSFLOAT_API_KEY", "test-key")
    asked = []

    def fake_find(record, valuator, refresh=False):
        asked.append(record["name"])
        return {"currency": valuator.base, "suggestion": {"value": 110.0, "basis": "2 usable sales", "notes": []}}

    monkeypatch.setattr(comparables, "find_comparables", fake_find)
    result = comparables.refresh_evidence()
    assert asked == [SKIN] and result == {"checked": 1, "valued": 1}   # the plain case isn't compared
    assert get_item(client)["market_unit_value"] == pytest.approx(110.0)
    assert comparables.refresh_evidence() == {"checked": 0, "valued": 0}  # not re-checked within a day
    assert evidence.load_all()[item_id]["basis"] == "2 usable sales"


def test_item_view_lists_every_enabled_market(client, monkeypatch):
    make_item(client)
    monkeypatch.setattr(Config, "CSFLOAT_API_KEY", "")
    db.execute("INSERT INTO price_checks VALUES (?, 'waxpeer', '2026-01-01T00:00:00+00:00')", (SKIN,))
    data = client.get("/api/quotes", query_string={"name": SKIN}).get_json()
    assert [q["market"] for q in data["quotes"]] == ["skinport"]
    status = {m["market"]: m["status"] for m in data["missing"]}
    assert set(status) == {"steam", "csfloat", "marketcsgo", "waxpeer"}
    assert status["waxpeer"] == "Not listed there right now"
    assert "Refresh prices" in status["marketcsgo"]
    assert "CSFLOAT_API_KEY" in status["csfloat"]


def test_refresh_reasons(client, monkeypatch):
    assert refresh_reasons() == []                                  # nothing to price yet
    make_item(client)
    reasons = refresh_reasons()
    assert "Market.CSGO has no prices yet" in reasons and "Waxpeer has no prices yet" in reasons
    assert not any("CSFloat" in r for r in reasons)                 # no key, so it's not expected
    assert "recent sales haven't been fetched yet" in reasons
    settings.set_value("market_attempts", {k: db.utcnow_iso() for k in [*SOURCES, "sales"]})
    db.execute("UPDATE market_prices SET fetched_at = ?", (db.utcnow_iso(),))
    assert refresh_reasons() == []                                  # tried recently - don't hammer
    db.execute("UPDATE market_prices SET fetched_at = '2020-01-01T00:00:00+00:00'")
    assert refresh_reasons() == []          # old prices wait for the daily update (see test_schedule.py)
