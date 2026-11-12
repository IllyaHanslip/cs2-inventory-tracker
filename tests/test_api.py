"""End-to-end API behaviour: containers, scopes, filters, comparison, history, sync."""
from datetime import date, timedelta

import pytest

from backend import db, steam_inventory
from tests.conftest import FAKE_STEAM_ID, HEADERS, add_quote


def post(client, url, body):
    return client.post(url, json=body, headers=HEADERS)


def test_mutations_require_csrf_header(client):
    assert client.post("/api/items", json={"market_hash_name": "Recoil Case"}).status_code == 403
    assert post(client, "/api/items", {"market_hash_name": "Recoil Case"}).status_code == 201


def test_foreign_host_is_rejected(client):
    assert client.get("/api/status", headers={"Host": "attacker.example"}).status_code == 403


def test_validation_errors(client):
    r = post(client, "/api/items", {"market_hash_name": "AK-47 | Slate (Minimal Wear)", "float_value": 1.5})
    assert r.status_code == 400 and "Float" in r.get_json()["error"]
    assert post(client, "/api/items", {"market_hash_name": ""}).status_code == 400
    assert post(client, "/api/items", {"market_hash_name": "x", "container_id": 999}).status_code == 400


def test_container_lifecycle(client):
    add_quote("Fracture Case", "steam", 0.5)
    add_quote("AK-47 | Slate (Minimal Wear)", "steam", 4.0)
    add_quote("Sticker | Crown (Foil)", "steam", 100.0)

    box = post(client, "/api/items", {"market_hash_name": "Storage Unit", "category": "container", "name_tag": "Stash"})
    box_id = box.get_json()["item"]["id"]
    post(client, "/api/items", {"market_hash_name": "Fracture Case", "quantity": 10, "container_id": box_id})
    post(client, "/api/items", {"market_hash_name": "AK-47 | Slate (Minimal Wear)", "container_id": box_id,
                                "stickers": [{"kind": "sticker", "name": "Crown (Foil)", "scrape": 0}]})
    post(client, "/api/items", {"market_hash_name": "AK-47 | Slate (Minimal Wear)"})

    from backend.valuation import soft_cap
    sticker_premium = soft_cap(100 * 0.15, 4.0)          # capped relative to the $4 skin
    contents_value = 10 * 0.5 + 4.0 + sticker_premium

    data = client.get("/api/items?scope=total").get_json()
    # Contents are listed with everything else by default, and counted once in the total.
    assert len(data["items"]) == 4
    assert data["summary"]["total_value"] == pytest.approx(4.0 + contents_value)
    assert data["summary"]["items_in_containers"] == 11
    hidden = client.get("/api/items?scope=total&contents=hide").get_json()["items"]
    assert {i["name"] for i in hidden} == {"Storage Unit", "AK-47 | Slate (Minimal Wear)"}
    unit = next(i for i in hidden if i["is_container"])
    assert unit["contents_count"] == 11 and unit["is_storage"]
    assert unit["display_value"] == pytest.approx(contents_value)

    inside = client.get("/api/items?scope=total&category=in_container").get_json()["items"]
    assert len(inside) == 2 and all(i["container_name"] == "Storage Unit “Stash”" for i in inside)

    opened = client.get(f"/api/containers/{box_id}").get_json()
    assert len(opened["contents"]) == 2

    # A case can sit in a storage unit, but nesting stops there.
    case_id = next(i["id"] for i in inside if i["name"] == "Fracture Case")
    assert post(client, f"/api/items/{box_id}/move", {"container_id": box_id}).status_code == 400
    assert post(client, "/api/items", {"market_hash_name": "Recoil Case", "container_id": case_id}).status_code == 400
    assert client.get(f"/api/containers/{case_id}").status_code == 400
    # Manual items stay out of the public scope.
    assert client.get("/api/items?scope=public").get_json()["items"] == []

    # Deleting the container keeps its contents by default.
    assert client.delete(f"/api/items/{box_id}", headers=HEADERS).status_code == 200
    remaining = client.get("/api/items?scope=total").get_json()
    assert len(remaining["items"]) == 3
    assert remaining["summary"]["total_value"] == pytest.approx(4.0 + contents_value)


def test_sorting_and_search(client):
    for name, price in (("Recoil Case", 0.3), ("Kilowatt Case", 0.8), ("Revolution Case", 0.6)):
        add_quote(name, "steam", price)
        post(client, "/api/items", {"market_hash_name": name})
    names = [i["name"] for i in client.get("/api/items?scope=total&sort=value&order=asc").get_json()["items"]]
    assert names == ["Recoil Case", "Revolution Case", "Kilowatt Case"]
    names = [i["name"] for i in client.get("/api/items?scope=total&sort=name&order=asc").get_json()["items"]]
    assert names == sorted(names)
    found = client.get("/api/items?scope=total&q=kilo").get_json()["items"]
    assert [i["name"] for i in found] == ["Kilowatt Case"]


def test_market_comparison_and_manual_quote(client):
    name = "AWP | Asiimov (Field-Tested)"
    post(client, "/api/items", {"market_hash_name": name, "quantity": 2})
    add_quote(name, "steam", 10.0)
    add_quote(name, "skinport", 8.1, "EUR")
    r = post(client, "/api/quotes", {"market_hash_name": name, "market_label": "Buff163", "price": 56, "currency": "CNY"})
    assert r.status_code == 201
    row = client.get("/api/market-comparison?scope=total").get_json()["rows"][0]
    assert row["best_market"] == "manual:Buff163"            # 56 CNY = 8.00 USD
    assert row["best_price"] == pytest.approx(8.0)
    assert row["steam_saving"] == pytest.approx(2.0)
    assert row["spread_pct"] == pytest.approx(25.0)
    assert row["prices"]["skinport"]["price_base"] == pytest.approx(9.0)


def test_items_carry_every_market_price_cheapest_first(client):
    name = "AWP | Asiimov (Field-Tested)"
    post(client, "/api/items", {"market_hash_name": name})
    add_quote(name, "steam", 10.0)
    add_quote(name, "skinport", 8.1, "EUR")     # 9.00 USD
    add_quote(name, "waxpeer", 9.5)
    add_quote(name, "manual:Buff163", 56, "CNY", manual=True)  # 8.00 USD
    quotes = client.get("/api/items?scope=total").get_json()["items"][0]["quotes"]
    assert [q["market"] for q in quotes] == ["manual:Buff163", "skinport", "waxpeer", "steam"]
    assert [round(q["price"], 2) for q in quotes] == [8.0, 9.0, 9.5, 10.0]
    assert quotes[0]["label"] == "Buff163 (manual)"


def test_price_changes_from_snapshots(client):
    name = "Recoil Case"
    post(client, "/api/items", {"market_hash_name": name})
    add_quote(name, "steam", 1.10)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    weeks_ago = (date.today() - timedelta(days=50)).isoformat()
    db.executemany("INSERT INTO price_snapshots VALUES (?, 'valuation', ?, ?, 'USD')",
                   [(name, yesterday, 1.0), (name, weeks_ago, 0.5)])
    item = client.get("/api/items?scope=total").get_json()["items"][0]
    assert item["change_24h"] == pytest.approx(10.0)
    assert item["change_7w"] == pytest.approx(120.0)


def test_snapshot_and_history_in_other_currency(client):
    post(client, "/api/items", {"market_hash_name": "Recoil Case", "quantity": 4})
    add_quote("Recoil Case", "steam", 1.0)
    assert client.post("/api/snapshot", headers=HEADERS).status_code == 200
    client.put("/api/settings", json={"base_currency": "EUR"}, headers=HEADERS)
    history = client.get("/api/history/portfolio?scope=total&range=all").get_json()
    assert history["currency"] == "EUR"
    assert history["values"][-1] == pytest.approx(3.6)       # 4 USD -> 3.60 EUR


def _fake_inventory(*asset_ids, unit_tag="Stash"):
    items = []
    for asset_id in asset_ids:
        is_unit = asset_id.startswith("unit")
        items.append({
            "asset_id": asset_id, "steam_id": FAKE_STEAM_ID,
            "market_hash_name": "Storage Unit" if is_unit else "Recoil Case",
            "name": "Storage Unit" if is_unit else "Recoil Case",
            "category": "container", "item_type": None, "weapon": None, "rarity": None, "rarity_color": None,
            "exterior": None, "float_value": None, "paint_seed": None, "name_tag": unit_tag if is_unit else None,
            "quantity": 1, "icon_url": None, "inspect_link": None, "tradable": 1, "marketable": 0 if is_unit else 1,
            "stickers": [],
        })
    return items


def test_steam_sync_reconciles_and_keeps_container_contents(client, monkeypatch):
    inventory = {"items": _fake_inventory("unit-1", "case-1", "case-2")}
    monkeypatch.setattr(steam_inventory, "fetch_inventory", lambda steam_id, job=None: inventory["items"])

    result = steam_inventory.sync_inventory(FAKE_STEAM_ID)
    assert (result["added"], result["removed"]) == (3, 0)
    unit_id = db.query_one("SELECT id FROM items WHERE asset_id = 'unit-1'")["id"]
    post(client, "/api/items", {"market_hash_name": "Fracture Case", "quantity": 5, "container_id": unit_id})

    # Steam re-issues the storage unit's asset id and one case was sold.
    inventory["items"] = _fake_inventory("unit-2", "case-1")
    result = steam_inventory.sync_inventory(FAKE_STEAM_ID)
    assert (result["added"], result["updated"], result["removed"]) == (1, 1, 2)
    new_unit = db.query_one("SELECT id FROM items WHERE asset_id = 'unit-2'")["id"]
    moved = db.query_one("SELECT container_id FROM items WHERE market_hash_name = 'Fracture Case'")
    assert moved["container_id"] == new_unit

    # An empty answer from Steam must not wipe the saved inventory.
    inventory["items"] = []
    with pytest.raises(Exception, match="empty inventory"):
        steam_inventory.sync_inventory(FAKE_STEAM_ID)
    assert db.query_one("SELECT COUNT(*) AS n FROM items WHERE source = 'steam'")["n"] == 2


def test_steam_items_are_protected(client):
    db.execute(
        "INSERT INTO items (source, steam_id, asset_id, market_hash_name, name, category, created_at, updated_at) "
        "VALUES ('steam', ?, '1', 'Recoil Case', 'Recoil Case', 'container', 'x', 'x')", (FAKE_STEAM_ID,))
    db.execute(
        "INSERT INTO items (source, steam_id, asset_id, market_hash_name, name, category, created_at, updated_at) "
        "VALUES ('steam', ?, '2', 'AK-47 | Slate (Minimal Wear)', 'AK', 'weapon_skin', 'x', 'x')", (FAKE_STEAM_ID,))
    steam_item = db.query_one("SELECT id FROM items WHERE asset_id = '2'")["id"]
    box = post(client, "/api/items", {"market_hash_name": "Storage Unit", "category": "container"}).get_json()["item"]
    assert post(client, f"/api/items/{steam_item}/move", {"container_id": box["id"]}).status_code == 400
    assert client.delete(f"/api/items/{steam_item}", headers=HEADERS).status_code == 400
    # Only the permitted fields change on Steam items.
    r = client.put(f"/api/items/{steam_item}", json={"notes": "keeper", "market_hash_name": "Hacked"}, headers=HEADERS)
    assert r.get_json()["item"]["notes"] == "keeper"
    assert r.get_json()["item"]["market_hash_name"] == "AK-47 | Slate (Minimal Wear)"
