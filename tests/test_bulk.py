"""Selling many copies at once: each market takes about what it sells in 30 days."""
from __future__ import annotations

import pytest

from backend import refresh
from backend.valuation import Valuator
from tests.conftest import HEADERS, add_quote

CAPSULE = "Paris 2023 Legends Sticker Capsule"
SKINPORT = 0.92                  # 8 % sale fee
CSFLOAT = 0.98 * 0.975           # 2 % sale + 2.5 % withdrawal


def add_sales(name, volume, period, source="skinport", price=0.14):
    refresh.save_sales(source, [(name, price, "USD", volume, period)])


def items(client):
    return client.get("/api/items?scope=total").get_json()


def capsule_market(client, quantity=531):
    add_quote(CAPSULE, "skinport", 0.14)          # dearer, but sells slowly there...
    add_quote(CAPSULE, "csfloat", 0.07)           # ...and no sales figures here
    add_sales(CAPSULE, 8, "7 days")               # 8 sold in a week -> ~34 in 30 days
    client.post("/api/items", json={"market_hash_name": CAPSULE, "quantity": quantity}, headers=HEADERS)


def test_sell_capacity_from_recent_sales(client):
    add_sales("A", 8, "7 days")
    add_sales("B", 20, "30 days")
    add_sales("C", 3, "recent", source="steam")    # Steam: sold in the last 24 hours
    v = Valuator()
    assert v.sell_capacity("A", "skinport") == 34 and v.sell_capacity("B", "skinport") == 20
    assert v.sell_capacity("C", "steam") == 90
    assert v.sell_capacity("Z", "skinport") == 0   # covered by Skinport's history, but no sales there
    assert v.sell_capacity("A", "csfloat") is None and v.sell_capacity("Z", "steam") is None   # no figures


def test_bulk_estimate_splits_across_markets(client):
    capsule_market(client)
    data = items(client)
    item = data["items"][0]
    assert item["unit_value"] == pytest.approx(0.14 * SKINPORT)           # one sells best on Skinport...
    expected = 34 * 0.14 * SKINPORT + 497 * 0.07 * CSFLOAT                  # ...but all 531 can't
    assert item["total_value"] == pytest.approx(expected)
    assert item["bulk_limited"] and item["bulk_units"] == 531
    assert [(s["market"], s["units"]) for s in item["bulk_plan"]] == [("skinport", 34), ("csfloat", 497)]
    assert {q["market"]: q["capacity"] for q in item["sell_quotes"]} == {"skinport": 34, "csfloat": None}
    assert data["summary"]["total_value"] == pytest.approx(expected)
    assert data["summary"]["cash_out_value"] == pytest.approx(expected)


def test_copies_pooled_across_rows(client):
    capsule_market(client, quantity=300)
    box = client.post("/api/items", json={"market_hash_name": "Storage Unit", "category": "container"},
                      headers=HEADERS).get_json()["item"]["id"]
    client.post(f"/api/containers/{box}/items", json={"items": [{"market_hash_name": CAPSULE, "quantity": 231}]},
                headers=HEADERS)
    rows = [i for i in items(client)["items"] if i["market_hash_name"] == CAPSULE]
    assert len(rows) == 2 and all(r["bulk_units"] == 531 for r in rows)
    expected = 34 * 0.14 * SKINPORT + 497 * 0.07 * CSFLOAT                  # Skinport's 34 shared, not 34 each
    assert sum(r["total_value"] for r in rows) == pytest.approx(expected)


def test_few_copies_or_quick_markets_are_unchanged(client):
    capsule_market(client, quantity=20)            # Skinport sells ~34 a month: it takes all 20
    item = items(client)["items"][0]
    assert not item["bulk_limited"] and item["total_value"] == pytest.approx(20 * 0.14 * SKINPORT)


def test_single_copy_and_slow_only_market(client):
    add_quote("Recoil Case", "skinport", 1.0)      # only Skinport lists it, and it sold none in 90 days
    add_sales("Something Else", 5, "7 days")       # (the sales history is in use)
    client.post("/api/items", json={"market_hash_name": "Recoil Case", "quantity": 1}, headers=HEADERS)
    client.post("/api/items", json={"market_hash_name": "Fracture Case", "quantity": 5}, headers=HEADERS)
    add_quote("Fracture Case", "skinport", 2.0)
    by_name = {i["market_hash_name"]: i for i in items(client)["items"]}
    assert by_name["Recoil Case"]["total_value"] == pytest.approx(1.0 * SKINPORT)
    assert not by_name["Recoil Case"]["bulk_limited"]                       # one copy: no bulk note
    fracture = by_name["Fracture Case"]                                     # nowhere else to sell them
    assert fracture["total_value"] == pytest.approx(5 * 2.0 * SKINPORT) and fracture["bulk_limited"]
    assert fracture["bulk_plan"][-1]["overflow"]


def test_cash_out_estimate_skips_steam(client):
    add_quote(CAPSULE, "steam", 0.30)             # Steam pays most, but only into the wallet
    add_quote(CAPSULE, "skinport", 0.14)
    add_quote(CAPSULE, "csfloat", 0.07)
    add_sales(CAPSULE, 8, "7 days")
    client.post("/api/items", json={"market_hash_name": CAPSULE, "quantity": 100}, headers=HEADERS)
    data = items(client)
    assert data["summary"]["total_value"] == pytest.approx(100 * 0.30 / 1.15)   # Steam: no sales figures
    assert data["summary"]["cash_out_value"] == pytest.approx(34 * 0.14 * SKINPORT + 66 * 0.07 * CSFLOAT)
