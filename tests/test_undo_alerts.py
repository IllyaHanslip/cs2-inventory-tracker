"""Undo, moving / removing several items at once, confirming several changes at once, price alerts
and the Profit & Loss spreadsheet."""
from __future__ import annotations

import csv
import io

from backend import alerts, db, moves
from tests.conftest import HEADERS, add_quote
from tests.test_moves import make, pending, unit_id


def post(client, path, body=None):
    return client.post(path, json=body or {}, headers=HEADERS)


def container(client, name):
    r = post(client, "/api/items", {"market_hash_name": "Storage Unit", "name_tag": name, "category": "container",
                                    "is_storage": True})
    return r.get_json()["item"]["id"]


def everything():
    return [tuple(r.values()) for r in db.query("SELECT * FROM items ORDER BY id")], \
        [tuple(r.values()) for r in db.query("SELECT * FROM ledger ORDER BY id")]


def test_undo_puts_a_removed_item_back_exactly(client):
    unit = container(client, "Long Holds")
    post(client, f"/api/containers/{unit}/items", {"items": [{"market_hash_name": "Recoil Case", "quantity": 28}]})
    item = db.query_one("SELECT id FROM items WHERE market_hash_name = 'Recoil Case'")["id"]
    before = everything()
    r = client.delete(f"/api/items/{item}?sold_price=0.5", headers=HEADERS).get_json()
    assert r["deleted"] and everything() != before        # gone, and a sale on Profit & Loss
    assert post(client, f"/api/undo/{r['undo']}").status_code == 200
    assert everything() == before                          # same ids, same quantities, sale gone too
    assert post(client, f"/api/undo/{r['undo']}").status_code == 400   # only once


def test_undo_deleting_a_container_with_its_items(client):
    unit = container(client, "Case Stash")
    post(client, f"/api/containers/{unit}/items", {"items": [{"market_hash_name": "Recoil Case", "quantity": 5},
                                                             {"market_hash_name": "Kilowatt Case", "quantity": 2}]})
    before = everything()
    token = client.delete(f"/api/containers/{unit}?contents=delete", headers=HEADERS).get_json()["undo"]
    assert not db.query("SELECT 1 FROM items")
    post(client, f"/api/undo/{token}")
    assert everything() == before


def test_no_undo_once_something_else_has_changed(client):
    unit = container(client, "A")
    post(client, f"/api/containers/{unit}/items", {"items": [{"market_hash_name": "Recoil Case", "quantity": 3}]})
    item = db.query_one("SELECT id FROM items WHERE market_hash_name = 'Recoil Case'")["id"]
    token = client.delete(f"/api/items/{item}?quantity=1", headers=HEADERS).get_json()["undo"]
    post(client, "/api/items", {"market_hash_name": "Kilowatt Case"})      # a change that can't be undone this way
    r = post(client, f"/api/undo/{token}")
    assert r.status_code == 400 and "changed since" in r.get_json()["error"]
    assert db.query_one("SELECT quantity FROM items WHERE id = ?", (item,))["quantity"] == 2   # nothing put back
    # ...and a newer undoable change replaces the older one.
    first = client.delete(f"/api/items/{item}?quantity=1", headers=HEADERS).get_json()["undo"]
    second = client.delete(f"/api/items/{item}?quantity=1", headers=HEADERS).get_json()["undo"]
    assert post(client, f"/api/undo/{first}").status_code == 400
    assert post(client, f"/api/undo/{second}").status_code == 200


def test_move_or_remove_several_at_once(client):
    a, b = container(client, "A"), container(client, "B")
    post(client, f"/api/containers/{a}/items", {"items": [{"market_hash_name": n, "quantity": 2}
                                                          for n in ("Recoil Case", "Kilowatt Case", "Fracture Case")]})
    post(client, f"/api/containers/{b}/items", {"items": [{"market_hash_name": "Recoil Case", "quantity": 1}]})
    ids = [r["id"] for r in db.query("SELECT id FROM items WHERE container_id = ? AND market_hash_name != 'Fracture Case'", (a,))]
    before = everything()
    r = post(client, "/api/items/bulk", {"ids": ids, "action": "move", "container_id": b}).get_json()
    assert (r["done"], r["units"]) == (2, 4)
    held = {x["market_hash_name"]: x["quantity"] for x in db.query("SELECT * FROM items WHERE container_id = ?", (b,))}
    assert held == {"Recoil Case": 3, "Kilowatt Case": 2}        # stacked onto what B already held
    post(client, f"/api/undo/{r['undo']}")
    assert everything() == before

    r = post(client, "/api/items/bulk", {"ids": ids, "action": "remove"}).get_json()
    assert r["done"] == 2 and not db.query("SELECT 1 FROM items WHERE id IN (?, ?)", ids)
    post(client, f"/api/undo/{r['undo']}")
    assert everything() == before


def test_several_at_once_is_all_or_nothing(client):
    a, b = container(client, "A"), container(client, "B")
    post(client, f"/api/containers/{a}/items", {"items": [{"market_hash_name": "Recoil Case", "quantity": 600},
                                                          {"market_hash_name": "Kilowatt Case", "quantity": 300}]})
    post(client, f"/api/containers/{b}/items", {"items": [{"market_hash_name": "Fracture Case", "quantity": 500}]})
    ids = [r["id"] for r in db.query("SELECT id FROM items WHERE container_id = ? ORDER BY id", (a,))]
    before = everything()
    r = post(client, "/api/items/bulk", {"ids": ids, "action": "move", "container_id": b})
    assert r.status_code == 400                                 # 900 more won't fit in B (500 / 1,000)
    assert everything() == before                               # not even the first one moved


def test_confirm_several_changes_at_once_and_undo_them(client, steam):
    steam(make("unit-1", "Storage Unit", storage_count=0), make("c1", "Recoil Case"), make("ak-1", "AK-47 | Slate (Field-Tested)"))
    steam(make("unit-1", "Storage Unit", storage_count=1))
    changes = {c["name"]: c for c in pending()}
    before = everything()
    r = post(client, "/api/changes/batch", {"changes": [
        {"id": changes["Recoil Case"]["id"], "action": "store", "container_id": unit_id()},
        {"id": changes["AK-47 | Slate (Field-Tested)"]["id"], "action": "gone", "price": 12.5},
    ]}).get_json()
    assert r["applied"] == 2 and r["pending"] == 0 and not r["problems"]
    assert db.query_one("SELECT price FROM ledger WHERE kind = 'out'")["price"] == 12.5
    post(client, f"/api/undo/{r['undo']}")
    assert everything() == before and len(pending()) == 2       # both back to review


def test_price_alerts(client):
    post(client, "/api/items", {"market_hash_name": "Recoil Case"})
    add_quote("Recoil Case", "waxpeer", 1.0)                    # receive ~0.94 after Waxpeer's fees
    worth = client.get("/api/alerts").get_json()
    assert worth["alerts"] == [] and worth["unseen"] == 0
    up = post(client, "/api/alerts", {"name": "Recoil Case", "direction": "above", "price": 2}).get_json()
    down = post(client, "/api/alerts", {"name": "Recoil Case", "direction": "below", "price": 0.5}).get_json()
    assert up["fired"] == [] and down["fired"] == []
    add_quote("Recoil Case", "waxpeer", 3.0)
    fired = alerts.check()
    assert [a["id"] for a in fired] == [up["id"]] and fired[0]["value"] > 2
    assert alerts.check() == []                                 # doesn't go off again every refresh
    listing = client.get("/api/alerts").get_json()
    assert listing["unseen"] == 1 and listing["alerts"][0]["id"] == up["id"] and listing["alerts"][0]["now"] > 2
    assert client.get("/api/status").get_json()["alerts_unseen"] == 1
    post(client, "/api/alerts/seen")
    assert client.get("/api/status").get_json()["alerts_unseen"] == 0
    post(client, f"/api/alerts/{up['id']}/rearm")
    assert len(alerts.check()) == 1                             # turned back on, and still above
    client.delete(f"/api/alerts/{down['id']}", headers=HEADERS)
    assert len(client.get("/api/alerts").get_json()["alerts"]) == 1
    # Already past the price when you set it: says so straight away.
    now = post(client, "/api/alerts", {"name": "Recoil Case", "direction": "above", "price": 1}).get_json()
    assert now["fired"] and now["fired"][0]["id"] == now["id"]
    for bad in ({"name": "Recoil Case", "direction": "sideways", "price": 1}, {"name": "Recoil Case", "direction": "above"}):
        assert post(client, "/api/alerts", bad).status_code == 400


def test_profit_and_loss_spreadsheet(client):
    post(client, "/api/ledger", {"kind": "in", "name": "Recoil Case", "quantity": 10, "price": 0.3, "date": "2026-03-01"})
    post(client, "/api/ledger", {"kind": "out", "name": "Recoil Case", "quantity": 4, "price": 0.8, "cost": 0.3,
                                 "date": "2026-04-01"})
    r = client.get("/api/ledger.csv")
    assert r.mimetype == "text/csv" and "profit-and-loss" in r.headers["Content-Disposition"]
    rows = list(csv.DictReader(io.StringIO(r.get_data(as_text=True).lstrip("﻿"))))
    assert [(x["date"], x["type"], x["quantity"]) for x in rows] == [("2026-04-01", "sold", "4"), ("2026-03-01", "bought", "10")]
    assert float(rows[0]["profit_USD"]) == 2.0 and float(rows[1]["total_USD"]) == 3.0
    assert moves.pending_count() == 0
