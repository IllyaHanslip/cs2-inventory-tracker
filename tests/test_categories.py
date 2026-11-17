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


def test_one_item_by_name(client):
    unit = setup(client)
    item = client.get("/api/item?name=Shanghai 2024 Legends Sticker Capsule&scope=total").get_json()["item"]
    assert item["quantity"] == 30 and item["container_id"] == unit and item["category_label"] == "Case / Capsule"
    assert client.get("/api/item?name=Storage Unit&scope=total").get_json()["item"]["is_storage"] is True
    assert client.get("/api/item?name=Nothing Like It&scope=total").status_code == 404
    assert client.get("/api/item?name=Nothing Like It&scope=total&optional=1").get_json() == {"item": None}
    assert client.get("/api/item?name=Recoil Case&scope=public").status_code == 404   # added by hand, not on Steam
