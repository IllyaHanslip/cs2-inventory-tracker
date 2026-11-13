"""Selling prices: every value is what you'd receive after each market's fees."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend import db, fees, history
from tests.conftest import HEADERS, add_quote

CSFLOAT = 0.98 * 0.975          # 2 % sale fee, then 2.5 % withdrawal
MARKETCSGO = 0.95 * 0.97


def items(client):
    return client.get("/api/items?scope=total").get_json()


def test_fee_breakdown_steps():
    steam = fees.breakdown("steam", 11.5)
    assert steam["net"] == pytest.approx(10.0) and steam["sale_fee"] == pytest.approx(1.5)
    assert steam["withdraw_fee"] == 0 and not steam["cash_out"]
    csfloat = fees.breakdown("csfloat", 100.0)
    assert csfloat["sale_fee"] == pytest.approx(2.0) and csfloat["after_sale"] == pytest.approx(98.0)
    assert csfloat["withdraw_fee"] == pytest.approx(2.45) and csfloat["net"] == pytest.approx(95.55)
    assert fees.net("csfloat", 100.0) == csfloat["net"] and fees.breakdown("csfloat", 0) is None


def test_best_market_wins_and_steam_is_flagged_as_wallet(client):
    name = "AK-47 | Slate (Minimal Wear)"
    add_quote(name, "steam", 10.0)
    add_quote(name, "csfloat", 8.5)
    add_quote(name, "marketcsgo", 8.0)
    client.post("/api/items", json={"market_hash_name": name, "quantity": 2}, headers=HEADERS)
    data = items(client)
    item = data["items"][0]
    # Steam pays most after its 15 % (wallet money), so it sets the selling price...
    assert item["sell_market"] == "steam" and item["unit_value"] == pytest.approx(10 / 1.15)
    assert item["total_value"] == pytest.approx(2 * 10 / 1.15)
    assert [q["market"] for q in item["sell_quotes"]] == ["steam", "csfloat", "marketcsgo"]
    assert item["sell_quotes"][0]["wallet"] and not item["sell_quotes"][1]["wallet"]
    # ...while cash-out counts only money you can withdraw.
    assert item["payout_market"] == "csfloat" and item["payout_unit"] == pytest.approx(8.5 * CSFLOAT)
    summary = data["summary"]
    assert summary["total_value"] == pytest.approx(2 * 10 / 1.15)
    assert summary["cash_out_value"] == pytest.approx(2 * 8.5 * CSFLOAT)
    assert summary["market_value"] == pytest.approx(2 * 8.5)      # median listing, what it costs to buy
    # Sorting by price uses the selling price.
    assert client.get("/api/items?scope=total&sort=price").status_code == 200


def test_sticker_premium_is_added_on_every_market_before_fees(client):
    skin = "AK-47 | Blue Laminate (Field-Tested)"
    add_quote(skin, "csfloat", 9.0)
    add_quote(skin, "marketcsgo", 10.0)
    add_quote("Sticker | Crown (Foil)", "csfloat", 100.0)
    body = {"market_hash_name": skin, "stickers": [{"kind": "sticker", "name": "Crown (Foil)"}]}
    client.post("/api/items", json=body, headers=HEADERS)
    item = items(client)["items"][0]
    premium = item["sticker_premium"]
    assert premium > 0
    by_market = {q["market"]: q for q in item["sell_quotes"]}
    assert by_market["csfloat"]["gross"] == pytest.approx(9.0 + premium)
    assert by_market["marketcsgo"]["net"] == pytest.approx((10.0 + premium) * MARKETCSGO)
    assert item["unit_value"] == pytest.approx(max((9.0 + premium) * CSFLOAT, (10.0 + premium) * MARKETCSGO))


def test_containers_and_tiles_use_selling_prices(client):
    add_quote("Recoil Case", "csfloat", 1.0)
    box = client.post("/api/items", json={"market_hash_name": "Storage Unit", "category": "container"},
                      headers=HEADERS).get_json()["item"]["id"]
    client.post(f"/api/containers/{box}/items", json={"items": [{"market_hash_name": "Recoil Case", "quantity": 10}]},
                headers=HEADERS)
    container = client.get(f"/api/containers/{box}").get_json()["container"]
    assert container["contents_value"] == pytest.approx(10 * CSFLOAT)
    summary = items(client)["summary"]
    assert summary["value_in_containers"] == pytest.approx(10 * CSFLOAT)


def test_snapshots_record_after_fee_totals(client):
    add_quote("Recoil Case", "csfloat", 2.0)
    client.post("/api/items", json={"market_hash_name": "Recoil Case", "quantity": 3}, headers=HEADERS)
    client.post("/api/snapshot", headers=HEADERS)
    row = db.query_one("SELECT total_value, market_value, basis FROM portfolio_snapshots WHERE scope = 'total'")
    assert row["basis"] == "after_fees"
    assert row["total_value"] == pytest.approx(3 * 2.0 * CSFLOAT) and row["market_value"] == pytest.approx(6.0)


def test_older_market_value_history_is_converted_once(client):
    name = "Recoil Case"
    add_quote(name, "csfloat", 2.0)
    client.post("/api/items", json={"market_hash_name": name, "quantity": 5}, headers=HEADERS)
    day = (date.today() - timedelta(days=3)).isoformat()
    gap = (date.today() - timedelta(days=2)).isoformat()      # no prices saved this day
    # That day the case listed at $1.00 on CSFloat and $1.20 on Market.CSGO (valuation = median $1.10).
    db.executemany("INSERT INTO price_snapshots VALUES (?, ?, ?, ?, 'USD')",
                   [(name, "csfloat", day, 1.0), (name, "marketcsgo", day, 1.2), (name, "valuation", day, 1.1)])
    db.executemany("INSERT INTO portfolio_snapshots (snapshot_date, scope, total_value, item_count, currency) "
                   "VALUES (?, 'total', ?, 5, 'USD')", [(day, 5.5), (gap, 6.0)])
    assert history.convert_market_snapshots() == 2
    rows = {r["snapshot_date"]: r for r in db.query("SELECT * FROM portfolio_snapshots ORDER BY snapshot_date")}
    ratio = max(1.0 * CSFLOAT, 1.2 * MARKETCSGO) / 1.1          # best after fees vs market value that day
    assert rows[day]["total_value"] == pytest.approx(5.5 * ratio) and rows[day]["market_value"] == 5.5
    assert rows[gap]["total_value"] == pytest.approx(6.0 * ratio)   # nearest day with prices
    assert all(r["basis"] == "after_fees" for r in rows.values())
    assert history.convert_market_snapshots() == 0                 # only ever once


def test_item_history_and_changes_are_after_fees(client):
    name = "Recoil Case"
    add_quote(name, "csfloat", 1.1)
    client.post("/api/items", json={"market_hash_name": name}, headers=HEADERS)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    db.executemany("INSERT INTO price_snapshots VALUES (?, ?, ?, ?, 'USD')",
                   [(name, "csfloat", yesterday, 1.0), (name, "steam", yesterday, 1.15),
                    (name, "valuation", yesterday, 99.0)])   # the old blended row is ignored
    data = client.get("/api/history/item", query_string={"name": name, "range": "7d"}).get_json()
    series = {s["market"]: s for s in data["series"]}
    assert series["valuation"]["label"] == "Best after fees"
    assert series["csfloat"]["values"][0] == pytest.approx(1.0 * CSFLOAT, abs=1e-4)
    assert series["steam"]["values"][0] == pytest.approx(1.0, abs=1e-4)
    assert series["valuation"]["values"][0] == pytest.approx(1.0, abs=1e-4)   # Steam paid most that day
    item = items(client)["items"][0]
    assert item["change_24h"] == pytest.approx((1.1 * CSFLOAT - 1.0) / 1.0 * 100)
