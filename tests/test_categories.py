"""Cases, capsules and souvenir packages are their own category, apart from storage units; one
item can be fetched by name (for opening its details from anywhere)."""
from __future__ import annotations

from tests.conftest import HEADERS


def post(client, path, body):
    return client.post(path, json=body, headers=HEADERS)


def setup(client):
    unit = post(client, "/api/items", {"market_hash_name": "Storage Unit", "name_tag": "Long Holds",
                                       "category": "container", "is_storage": True}).get_json()["item"]["id"]
    post(client, f"/api/containers/{unit}/items", {"items": [{"market_hash_name": "Shanghai 2024 Legends Sticker Capsule",
                                                             "quantity": 30}]})
    post(client, "/api/items", {"market_hash_name": "Recoil Case", "quantity": 5})
    post(client, "/api/items", {"market_hash_name": "AK-47 | Redline (Field-Tested)"})
    return unit


def names(client, category):
    return sorted(i["market_hash_name"] for i in client.get(f"/api/items?scope=total&category={category}").get_json()["items"])


def test_cases_and_capsules_apart_from_storage_units(client):
    setup(client)
    data = client.get("/api/items?scope=total").get_json()
    assert data["counts"]["container"] == 2 and data["counts"]["storage"] == 1
    assert names(client, "container") == ["Recoil Case", "Shanghai 2024 Legends Sticker Capsule"]
    assert names(client, "storage") == ["Storage Unit"]
    assert names(client, "storage,weapon_skin") == ["AK-47 | Redline (Field-Tested)", "Storage Unit"]
    labels = {i["market_hash_name"]: i["category_label"] for i in data["items"]}
    assert labels["Storage Unit"] == "Storage Unit" and labels["Recoil Case"] == "Case / Capsule"
    assert client.get("/api/status").get_json()["categories"]["container"] == "Case / Capsule"


def test_chip_counts_follow_the_search(client):
    """A chip's count is what picking it shows, with the search as it is."""
    setup(client)
    counts = client.get("/api/items?scope=total&q=capsule").get_json()["counts"]
    assert counts.get("container") == 1 and not counts.get("storage") and counts.get("in_container") == 1
    assert names(client, "storage&q=capsule") == []                     # ...so 0 here isn't a surprise
    hidden = client.get("/api/items?scope=total&q=capsule&contents=hide").get_json()["counts"]
    assert hidden.get("container") == 1          # a search finds stored items even with them hidden
    assert client.get("/api/items?scope=total").get_json()["counts"]["storage"] == 1   # no search: all


def test_terminals_added_before_the_rule_are_filed_again_once(client):
    """Items added by hand were filed by name; a name the app has since learned moves them, once."""
    from backend import db

    post(client, "/api/items", {"market_hash_name": "Sealed Genesis Terminal", "quantity": 3})
    item = client.get("/api/items?scope=total").get_json()["items"][0]
    assert item["category"] == "container"                               # new ones: straight away
    db.execute("UPDATE items SET category = 'other'")                     # as saved by an older version
    db.execute("DELETE FROM settings WHERE key = 'category_rules'")
    db.init_db()
    assert db.query_one("SELECT category FROM items")["category"] == "container"
    db.execute("UPDATE items SET category = 'other'")                     # chosen by hand afterwards: kept
    db.init_db()
    assert db.query_one("SELECT category FROM items")["category"] == "other"


def test_one_item_by_name(client):
    unit = setup(client)
    item = client.get("/api/item?name=Shanghai 2024 Legends Sticker Capsule&scope=total").get_json()["item"]
    assert item["quantity"] == 30 and item["container_id"] == unit and item["category_label"] == "Case / Capsule"
    assert client.get("/api/item?name=Storage Unit&scope=total").get_json()["item"]["is_storage"] is True
    assert client.get("/api/item?name=Nothing Like It&scope=total").status_code == 404
    assert client.get("/api/item?name=Nothing Like It&scope=total&optional=1").get_json() == {"item": None}
    assert client.get("/api/item?name=Recoil Case&scope=public").status_code == 404   # added by hand, not on Steam
