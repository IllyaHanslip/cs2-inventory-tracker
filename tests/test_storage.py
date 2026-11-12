"""Container management: stacking, the 1,000-item limit, partial moves/removals, deletion."""
from backend import db
from tests.conftest import FAKE_STEAM_ID, HEADERS


def post(client, url, body):
    return client.post(url, json=body, headers=HEADERS)


def new_container(client, label="Cases"):
    r = post(client, "/api/items", {"market_hash_name": "Storage Unit", "category": "container",
                                    "name_tag": label, "is_storage": True})
    assert r.status_code == 201
    return r.get_json()["item"]["id"]


def contents(client, cid):
    return {i["name"]: i["quantity"] for i in client.get(f"/api/containers/{cid}").get_json()["contents"]}


def test_adding_the_same_item_stacks(client):
    cid = new_container(client)
    post(client, f"/api/containers/{cid}/items", {"items": [{"market_hash_name": "Recoil Case", "quantity": 7}]})
    r = post(client, f"/api/containers/{cid}/items", {"items": [{"market_hash_name": "Recoil Case", "quantity": 21}]})
    assert r.get_json()["stacked"] == 1
    assert contents(client, cid) == {"Recoil Case": 28}
    # The single-item form stacks too, but not across containers.
    post(client, "/api/items", {"market_hash_name": "Recoil Case", "quantity": 2, "container_id": cid})
    post(client, "/api/items", {"market_hash_name": "Recoil Case", "quantity": 5})
    assert contents(client, cid) == {"Recoil Case": 30}
    rows = db.query("SELECT quantity FROM items WHERE market_hash_name = 'Recoil Case' ORDER BY quantity")
    assert [r["quantity"] for r in rows] == [5, 30]


def test_items_with_details_do_not_stack(client):
    cid = new_container(client)
    for _ in range(2):
        post(client, "/api/items", {"market_hash_name": "AK-47 | Slate (Minimal Wear)", "container_id": cid,
                                    "float_value": 0.1})
    assert db.query_one("SELECT COUNT(*) AS n FROM items WHERE container_id = ?", (cid,))["n"] == 2


def test_capacity_is_1000(client):
    cid = new_container(client)
    ok = post(client, f"/api/containers/{cid}/items", {"items": [
        {"market_hash_name": "Recoil Case", "quantity": 600}, {"market_hash_name": "Kilowatt Case", "quantity": 400}]})
    assert ok.status_code == 201 and ok.get_json()["contents_count"] == 1000
    full = post(client, f"/api/containers/{cid}/items", {"items": [{"market_hash_name": "Fracture Case", "quantity": 1}]})
    assert full.status_code == 400 and "1,000" in full.get_json()["error"]
    item = db.query_one("SELECT id FROM items WHERE market_hash_name = 'Kilowatt Case'")["id"]
    over = client.put(f"/api/items/{item}", json={"quantity": 401}, headers=HEADERS)
    assert over.status_code == 400
    assert post(client, f"/api/containers/{cid}/items", {"items": [{"market_hash_name": "X", "quantity": 1001}]}).status_code == 400


def test_partial_remove_and_move(client):
    a, b = new_container(client, "A"), new_container(client, "B")
    post(client, f"/api/containers/{a}/items", {"items": [{"market_hash_name": "Recoil Case", "quantity": 28}]})
    item = db.query_one("SELECT id FROM items WHERE market_hash_name = 'Recoil Case'")["id"]

    r = client.delete(f"/api/items/{item}?quantity=8", headers=HEADERS).get_json()
    assert r == {"deleted": False, "remaining": 20}
    post(client, f"/api/items/{item}/move", {"container_id": b, "quantity": 5})
    assert contents(client, a) == {"Recoil Case": 15} and contents(client, b) == {"Recoil Case": 5}
    post(client, f"/api/items/{item}/move", {"container_id": b})          # the rest stacks onto B's 5
    assert contents(client, a) == {} and contents(client, b) == {"Recoil Case": 20}
    assert post(client, f"/api/items/{item}/move", {"container_id": a, "quantity": 99}).status_code in (400, 404)


def test_deleting_containers(client):
    empty = new_container(client, "Empty")
    assert client.delete(f"/api/containers/{empty}", headers=HEADERS).get_json() == {"deleted": True}

    full = new_container(client, "Full")
    post(client, f"/api/containers/{full}/items", {"items": [{"market_hash_name": "Recoil Case", "quantity": 3}]})
    post(client, "/api/items", {"market_hash_name": "Recoil Case", "quantity": 2})        # already in main inventory
    client.delete(f"/api/containers/{full}?contents=keep", headers=HEADERS)
    rows = db.query("SELECT quantity, container_id FROM items WHERE market_hash_name = 'Recoil Case'")
    assert rows == [{"quantity": 5, "container_id": None}]                             # moved out and stacked

    gone = new_container(client, "Gone")
    post(client, f"/api/containers/{gone}/items", {"items": [{"market_hash_name": "Kilowatt Case", "quantity": 4}]})
    client.delete(f"/api/containers/{gone}?contents=delete", headers=HEADERS)
    assert db.query_one("SELECT COUNT(*) AS n FROM items WHERE market_hash_name = 'Kilowatt Case'")["n"] == 0


def test_steam_storage_unit_is_emptied_not_deleted(client):
    db.execute("INSERT INTO items (source, steam_id, asset_id, market_hash_name, name, category, is_storage, "
               "marketable, created_at, updated_at) VALUES ('steam', ?, '9', 'Storage Unit', 'Storage Unit', "
               "'container', 1, 0, 'x', 'x')", (FAKE_STEAM_ID,))
    unit = db.query_one("SELECT id FROM items WHERE asset_id = '9'")["id"]
    post(client, f"/api/containers/{unit}/items", {"items": [{"market_hash_name": "Recoil Case", "quantity": 3}]})
    r = client.delete(f"/api/containers/{unit}?contents=delete", headers=HEADERS).get_json()
    assert r == {"deleted": False, "emptied": 3}
    assert db.query_one("SELECT COUNT(*) AS n FROM items WHERE id = ?", (unit,))["n"] == 1


def test_containers_overview(client):
    cid = new_container(client, "Cases")
    post(client, f"/api/containers/{cid}/items", {"items": [{"market_hash_name": "Recoil Case", "quantity": 12}]})
    post(client, "/api/items", {"market_hash_name": "Kilowatt Case", "quantity": 3})   # a case, not storage
    data = client.get("/api/containers").get_json()
    assert [c["label"] for c in data["containers"]] == ["Storage Unit “Cases”"]
    card = data["containers"][0]
    assert (card["contents_count"], card["capacity"], card["preview"][0]["quantity"]) == (12, 1000, 12)
    assert data["summary"]["items_stored"] == 12
