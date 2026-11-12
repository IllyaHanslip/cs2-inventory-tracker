"""CSFloat comparables: key-sticker choice, setting aside confounded comparables, and the
suggested value. CSFloat responses are faked - no network, no real key."""
import pytest

from backend import cache
from backend.markets import SOURCES
from config import Config
from tests.conftest import HEADERS, add_quote

SKIN = "AK-47 | Blue Laminate (Field-Tested)"
SCHEMA = {"52": "Sticker | Team Dignitas | Katowice 2014", "57": "Sticker | HellRaisers | Katowice 2014",
          "79": "Sticker | Vox Eminor | Katowice 2014", "999": "Sticker | Global Elite (Foil)",
          "500": "Sticker | Some Cheap Sticker"}


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def listing(price_usd, sticker_ids, listing_id="1", sold=False):
    row = {"id": listing_id, "price": int(price_usd * 100),
           "item": {"float_value": 0.2, "stickers": [{"stickerId": s, "slot": i, "wear": 0} for i, s in enumerate(sticker_ids)]}}
    row["sold_at" if sold else "created_at"] = "2026-11-01T00:00:00Z"
    return row


@pytest.fixture
def setup(client, monkeypatch):
    monkeypatch.setattr(Config, "CSFLOAT_API_KEY", "test-key")
    cache.set_cached("csfloat_sticker_names", SCHEMA)
    reference = {"marketcsgo": [["Sticker | Vox Eminor | Katowice 2014", 600.0, 1, None],
                                ["Sticker | Some Cheap Sticker", 0.5, 10, None]]}
    for key in ("skinport", "marketcsgo", "waxpeer"):
        monkeypatch.setattr(SOURCES[key], "load_rows", lambda key=key: reference.get(key, []))
    add_quote(SKIN, "steam", 10.0)
    add_quote("Sticker | Team Dignitas | Katowice 2014", "steam", 800.0)
    add_quote("Sticker | HellRaisers | Katowice 2014", "steam", 700.0)
    add_quote("Sticker | Global Elite (Foil)", "steam", 8.0)
    body = {"market_hash_name": SKIN, "stickers": [
        {"kind": "sticker", "name": "Team Dignitas | Katowice 2014"},
        {"kind": "sticker", "name": "HellRaisers | Katowice 2014"},
        {"kind": "sticker", "name": "Global Elite (Foil)"}]}
    item_id = client.post("/api/items", json=body, headers=HEADERS).get_json()["item"]["id"]

    def install(combo=(), top=(), sales=()):
        def fake_get(url, params=None, headers=None):
            assert headers and headers.get("Authorization") == "test-key"
            if "/history/" in url:
                return FakeResponse(list(sales))
            return FakeResponse({"data": list(combo if params["stickers"] == '[{"i":52},{"i":57}]' else top)})
        monkeypatch.setattr(SOURCES["csfloat"].client, "get", fake_get)

    return item_id, install


def test_needs_a_csfloat_key(client):
    item_id = client.post("/api/items", json={"market_hash_name": SKIN}, headers=HEADERS).get_json()["item"]["id"]
    r = client.get(f"/api/items/{item_id}/comparables")
    assert r.status_code == 400 and "CSFLOAT_API_KEY" in r.get_json()["error"]


def test_scales_single_sticker_evidence_and_sets_aside_confounded(client, setup):
    item_id, install = setup
    install(
        combo=[listing(400, [52, 57, 79])],               # also has a ~600 Kato sticker -> set aside
        top=[listing(80, [52]), listing(90, [52, 79])],    # only the first is clean
        sales=[listing(120, [52, 500], sold=True)],        # only shares Team Dignitas; its extra sticker is cheap
    )
    data = client.get(f"/api/items/{item_id}/comparables").get_json()
    assert [k["name"] for k in data["key_stickers"]] == ["Team Dignitas | Katowice 2014", "HellRaisers | Katowice 2014"]
    groups = {g["id"]: g for g in data["groups"]}
    assert [r["clean"] for r in groups["combo_listings"]["rows"]] == [False]
    assert groups["combo_sales"]["rows"] == []
    # The clean sale with Team Dignitas (+ a cheap sticker) is the strongest evidence left:
    # it added (120 - 10) / 800 = 13.75 % of the sticker's price, applied to both stickers.
    assert data["suggestion"]["value"] == pytest.approx(10 + (110 / 800) * 1500, rel=1e-3)


def test_prefers_sales_with_the_same_stickers(client, setup):
    item_id, install = setup
    install(combo=[listing(300, [52, 57])], top=[listing(80, [52])],
            sales=[listing(150, [57, 52], "a", sold=True), listing(170, [52, 57, 500], "b", sold=True)])
    data = client.get(f"/api/items/{item_id}/comparables").get_json()
    assert data["suggestion"]["value"] == pytest.approx(160)        # median of the two matching sales
    assert "sales" in data["suggestion"]["basis"]


def test_listing_only_evidence_is_discounted(client, setup):
    item_id, install = setup
    install(combo=[listing(300, [52, 57])])
    data = client.get(f"/api/items/{item_id}/comparables").get_json()
    assert data["suggestion"]["value"] == pytest.approx(300 * 0.85)
    assert any("asking prices" in note for note in data["suggestion"]["notes"])
