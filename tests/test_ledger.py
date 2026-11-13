"""Profit & loss (ledger.py): prices are optional, and each figure uses the entries that have the prices it needs."""
from __future__ import annotations

import sqlite3

import pytest

from backend import db, ledger
from tests.conftest import HEADERS
from tests.test_moves import make, manual_unit, pending


def totals(client):
    return client.get("/api/ledger").get_json()["totals"]


def test_three_ways_of_looking_at_it(client):
    with db.connect() as conn:
        # A: bought for 10, sold for 15. B: no purchase price, sold for 8. C: bought for 20, not sold.
        # D: bought for 5, sold without saying what for.
        ledger.record(conn, "in", "A", 1, 10.0, "USD")
        ledger.record(conn, "out", "A", 1, 15.0, "USD", cost=10.0, cost_currency="USD")
        ledger.record(conn, "out", "B", 1, 8.0, "USD")
        ledger.record(conn, "in", "C", 1, 20.0, "USD")
        ledger.record(conn, "in", "D", 1, 5.0, "USD")
        ledger.record(conn, "out", "D", 1, None, "USD", cost=5.0, cost_currency="USD")
    t = totals(client)
    assert t["profit"]["value"] == pytest.approx(5)          # only A has both prices
    assert t["gained"]["value"] == pytest.approx(15 + 8)     # everything sold for, whatever was paid
    assert t["spent"]["value"] == pytest.approx(35)          # A + C + D
    data = client.get("/api/ledger").get_json()
    assert data["counts"] == {"in": 3, "in_priced": 3, "out": 3, "out_priced": 2}
    assert [e["name"] for e in data["best"]] == ["A"]


def test_sold_and_bought_from_steam_changes(client, steam):
    unit = manual_unit(client, "Stash", Recoil_Case=40)
    steam(make("case-1", "Recoil Case"), make("ak-1", "AK-47 | Slate (Field-Tested)"))
    ak = db.query_one("SELECT id FROM items WHERE asset_id = 'ak-1'")["id"]
    client.put(f"/api/items/{ak}", json={"purchase_price": 4, "purchase_currency": "USD"}, headers=HEADERS)
    # The AK is sold for 6; the case goes into storage; a new skin arrives.
    steam(make("case-1", "Recoil Case"), make("new-1", "P250 | Sand Dune (Field-Tested)"))
    changes = {c["name"]: c for c in pending()}
    assert changes["P250 | Sand Dune (Field-Tested)"]["kind"] == "new"
    sold = changes["AK-47 | Slate (Field-Tested)"]
    assert sold["purchase_price"] == 4
    r = client.post(f"/api/changes/{sold['id']}", json={"action": "gone", "price": 6}, headers=HEADERS)
    assert r.status_code == 200
    new = changes["P250 | Sand Dune (Field-Tested)"]
    client.post(f"/api/changes/{new['id']}", json={"action": "bought", "price": 0.5}, headers=HEADERS)
    assert db.query_one("SELECT purchase_price FROM items WHERE asset_id = 'new-1'")["purchase_price"] == 0.5
    t = totals(client)
    assert t["profit"]["value"] == pytest.approx(2) and t["spent"]["value"] == pytest.approx(0.5)
    assert not pending()
    assert unit


def test_dismissing_a_change_forgets_it_for_good(client, steam):
    steam(make("case-1", "Recoil Case"))
    steam(make("other", "AK-47 | Slate (Field-Tested)"))
    for change in pending():
        assert client.post(f"/api/changes/{change['id']}", json={"action": "dismiss"}, headers=HEADERS).status_code == 200
    assert not pending()
    assert not db.query("SELECT * FROM ledger")
    # Dismissed changes stay dismissed after later syncs.
    steam(make("other", "AK-47 | Slate (Field-Tested)"))
    assert not pending()


def test_adding_and_removing_by_hand(client):
    item = client.post("/api/items", json={"market_hash_name": "Recoil Case", "quantity": 4, "purchase_price": 0.5,
                                           "purchase_currency": "USD"}, headers=HEADERS).get_json()["item"]
    client.post("/api/items", json={"market_hash_name": "Kilowatt Case", "quantity": 2}, headers=HEADERS)   # no price
    r = client.delete(f"/api/items/{item['id']}?quantity=3&sold_price=0.8", headers=HEADERS)
    assert r.status_code == 200
    t = totals(client)
    assert t["spent"]["value"] == pytest.approx(2.0)
    assert t["profit"]["value"] == pytest.approx(3 * 0.3)
    entries = client.get("/api/ledger").get_json()["entries"]
    assert [(e["kind"], e["name"], e["quantity"]) for e in entries] == [("out", "Recoil Case", 3), ("in", "Recoil Case", 4)]


def test_recording_editing_and_deleting(client):
    r = client.post("/api/ledger", json={"kind": "out", "name": "Snakebite Case", "quantity": 10, "price": 0.4,
                                          "date": "2026-10-01"}, headers=HEADERS)
    assert r.status_code == 201
    entry_id = r.get_json()["id"]
    assert totals(client)["gained"]["value"] == pytest.approx(4)
    assert totals(client)["profit"]["value"] == 0                          # no purchase price
    client.put(f"/api/ledger/{entry_id}", json={"cost": 0.25}, headers=HEADERS)
    assert totals(client)["profit"]["value"] == pytest.approx(1.5)
    assert client.post("/api/ledger", json={"kind": "out", "name": "X", "date": "1st May"}, headers=HEADERS).status_code == 400
    assert client.delete(f"/api/ledger/{entry_id}", headers=HEADERS).status_code == 200
    assert totals(client)["gained"]["value"] == 0


def test_old_changes_table_is_upgraded(tmp_path, monkeypatch):
    from config import Config
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""CREATE TABLE inventory_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL CHECK (kind IN ('left', 'back')),
        detected_at TEXT NOT NULL, market_hash_name TEXT NOT NULL, quantity INTEGER NOT NULL CHECK (quantity > 0),
        details TEXT NOT NULL, suggestion TEXT, container_id INTEGER, unit_before INTEGER, unit_after INTEGER,
        sure INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending', resolution TEXT, resolved_at TEXT);
        INSERT INTO inventory_changes (kind, detected_at, market_hash_name, quantity, details)
        VALUES ('left', '2026-11-13', 'Recoil Case', 2, '{}');""")
    conn.commit()
    conn.close()
    monkeypatch.setattr(Config, "DATABASE_PATH", path)
    db.init_db()
    assert db.query_one("SELECT market_hash_name FROM inventory_changes")["market_hash_name"] == "Recoil Case"
    db.execute("INSERT INTO inventory_changes (kind, detected_at, market_hash_name, quantity, details) "
               "VALUES ('new', '2026-11-13', 'X', 1, '{}')")
    assert db.query_one("SELECT COUNT(*) AS n FROM inventory_changes")["n"] == 2
