"""The "Values from" switch: all markets (Steam Wallet money counts) or cash-out markets only."""
from __future__ import annotations

from backend import alerts, db, history, settings
from backend.valuation import Valuator
from tests.conftest import HEADERS, add_quote


def items(client):
    return {i["market_hash_name"]: i for i in client.get("/api/items?scope=total").get_json()["items"]}


def summary(client):
    return client.get("/api/items?scope=total").get_json()["summary"]


def setup(client):
    for name in ("Recoil Case", "Kilowatt Case"):
        client.post("/api/items", json={"market_hash_name": name}, headers=HEADERS)
    # Steam pays most for the Recoil Case (but into your Steam Wallet); Waxpeer is the best cash market.
    add_quote("Recoil Case", "steam", 3.0)
    add_quote("Recoil Case", "waxpeer", 2.0)
    add_quote("Recoil Case", "csfloat", 1.9)
    add_quote("Kilowatt Case", "steam", 1.0)          # only Steam has a price for this one


def switch(client, cash_only):
    r = client.put("/api/settings", json={"cash_only": cash_only}, headers=HEADERS)
    assert r.status_code == 200 and r.get_json()["settings"]["cash_only"] is cash_only


def test_all_markets_is_the_default_and_steam_can_be_best(client):
    setup(client)
    assert settings.get_all()["cash_only"] is False
    recoil = items(client)["Recoil Case"]
    assert recoil["sell_market"] == "steam" and recoil["sell_quotes"][0]["market"] == "steam"
    assert all(q["counted"] for q in recoil["sell_quotes"])
    assert items(client)["Kilowatt Case"]["total_value"] > 0
    s = summary(client)
    assert s["cash_only"] is False and s["wallet_only_items"] == 0
    assert abs(s["total_value"] - s["all_markets_value"]) < 1e-9


def test_cash_out_only_values_items_on_markets_you_can_withdraw_from(client):
    setup(client)
    before = summary(client)
    switch(client, True)
    data = items(client)
    recoil = data["Recoil Case"]
    assert recoil["sell_market"] == "waxpeer"
    assert [q["market"] for q in recoil["sell_quotes"]] == ["waxpeer", "csfloat", "steam"]   # Steam last
    assert [q["counted"] for q in recoil["sell_quotes"]] == [True, True, False]
    assert recoil["unit_value"] == recoil["sell_quotes"][0]["net"]
    # Priced only on Steam: shown greyed ("wallet only"), left out of every total.
    kilowatt = data["Kilowatt Case"]
    assert kilowatt["wallet_only"] and kilowatt["total_value"] is None and kilowatt["wallet_unit"] > 0
    s = summary(client)
    assert s["wallet_only_items"] == 1 and s["unpriced_items"] == 0
    assert abs(s["total_value"] - recoil["total_value"]) < 1e-9
    assert abs(s["all_markets_value"] - before["total_value"]) < 1e-9          # the other figure, for the tiles
    assert s["total_value"] < before["total_value"]
    # Market comparison marks which markets pay out (the page ranks them by the switch).
    rows = client.get("/api/market-comparison?scope=total").get_json()
    assert {m["key"]: m["cash_out"] for m in rows["markets"]}["steam"] is False
    # The Sell Planner's own "Count Steam Wallet sales" choice decides its markets, whatever the switch.
    plan = client.get("/api/sell-plan?target=1&wallet=1").get_json()
    assert plan["steps"][0]["markets"][0]["market"] == "steam"
    switch(client, False)
    assert items(client)["Recoil Case"]["sell_market"] == "steam"


def test_alerts_follow_the_switch(client):
    setup(client)
    alerts.add("Recoil Case", "above", 2.5)                 # Steam (~£2.61 after fees) is above, Waxpeer isn't
    switch(client, True)
    assert alerts.check() == []
    switch(client, False)
    assert len(alerts.check()) == 1


def test_item_history_best_line_and_which_market_led(client):
    setup(client)
    days = ["2026-01-01", "2026-01-02", "2026-01-03"]
    rows = []
    for day, steam, waxpeer, csfloat in zip(days, (1.0, 3.0, 3.0), (2.0, 2.0, 1.5), (1.5, 1.9, 2.2)):
        rows += [("Recoil Case", "steam", day, steam, "USD"), ("Recoil Case", "waxpeer", day, waxpeer, "USD"),
                 ("Recoil Case", "csfloat", day, csfloat, "USD")]
    db.executemany("INSERT INTO price_snapshots VALUES (?, ?, ?, ?, ?)", rows)
    every = history.item_history("Recoil Case", "all", Valuator())
    assert every["best_markets"] == ["waxpeer", "steam", "steam"]
    assert all(s["counted"] for s in every["series"])
    switch(client, True)
    cash = history.item_history("Recoil Case", "all", Valuator())
    assert cash["best_markets"] == ["waxpeer", "waxpeer", "csfloat"]
    steam = next(s for s in cash["series"] if s["market"] == "steam")
    assert steam["counted"] is False and steam["wallet"] is True and steam["values"][1] is not None   # still shown
    best = cash["series"][0]["values"]
    waxpeer = next(s for s in cash["series"] if s["market"] == "waxpeer")["values"]
    assert best[1] == waxpeer[1]


def test_portfolio_history_both_ways(client):
    setup(client)
    history.take_snapshot()
    row = db.query_one("SELECT total_value, cash_value FROM portfolio_snapshots WHERE scope = 'total'")
    assert row["cash_value"] < row["total_value"]
    fx, base = Valuator().fx, "USD"
    every = history.portfolio_history("total", "all", fx, base)
    cash = history.portfolio_history("total", "all", fx, base, cash_only=True)
    assert every["values"][-1] == round(row["total_value"], 2) and cash["values"][-1] == round(row["cash_value"], 2)
    switch(client, True)
    assert client.get("/api/history/portfolio?scope=total&range=all").get_json()["values"][-1] == round(row["cash_value"], 2)


def test_older_days_get_an_estimated_cash_out_total(client):
    setup(client)
    # Two older days saved before the cash-out total was kept: one before Steam had a price, one after.
    db.executemany("INSERT INTO price_snapshots VALUES (?, ?, ?, ?, ?)", [
        ("Recoil Case", "waxpeer", "2026-01-01", 2.0, "USD"),
        ("Recoil Case", "waxpeer", "2026-01-02", 2.0, "USD"),
        ("Recoil Case", "steam", "2026-01-02", 3.0, "USD"),
    ])
    db.executemany("INSERT INTO portfolio_snapshots (snapshot_date, scope, total_value, item_count, currency, basis, "
                   "screened) VALUES (?, 'total', ?, 2, 'USD', 'after_fees', 1)",
                   [("2026-01-01", 10.0), ("2026-01-02", 20.0)])
    assert history.fill_cash_values() == 2
    got = {r["snapshot_date"]: r["cash_value"] for r in db.query("SELECT * FROM portfolio_snapshots")}
    assert got["2026-01-01"] == 10.0                       # no Steam price yet: the same
    v = Valuator()
    share = v.net("waxpeer", 2.0) / v.net("steam", 3.0)
    assert abs(got["2026-01-02"] - round(20.0 * share, 2)) < 0.011
    assert history.fill_cash_values() == 0                 # only once


def test_the_switch_must_be_true_or_false(client):
    r = client.put("/api/settings", json={"cash_only": "yes"}, headers=HEADERS)
    assert r.status_code == 400
