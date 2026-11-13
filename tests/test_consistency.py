"""Figures that must agree wherever they're shown: the headline and the chart's last point, the
change tiles when items are added, the portfolio mix now and its history, and the demo."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend import db, history, insights, portfolio
from backend.valuation import Valuator
from tests.conftest import HEADERS
from tests.test_bulk import CAPSULE, capsule_market
from tests.test_insights import CSFLOAT, add, history_prices


def saved_total(scope, days_ago, value, count):
    db.execute(
        "INSERT OR REPLACE INTO portfolio_snapshots (snapshot_date, scope, total_value, item_count, currency, basis, "
        "screened) VALUES (?, ?, ?, ?, 'USD', 'after_fees', 1)",
        ((date.today() - timedelta(days=days_ago)).isoformat(), scope, value, count),
    )


def summary(client, scope="total"):
    return client.get(f"/api/items?scope={scope}").get_json()["summary"]


def test_chained_change_leaves_out_items_added():
    assert history.chained_change([(100, 10), (110, 10)]) == pytest.approx(10)
    assert history.chained_change([(100, 10), (300, 30)]) is None               # only items added
    assert history.chained_change([(100, 10), (110, 10), (330, 30), (363, 30)]) == pytest.approx(21)
    assert history.chained_change([(100, None), (120, None)]) == pytest.approx(20)


def test_change_tiles_ignore_items_added(client):
    add(client, "Recoil Case", 10, 1.1)
    history_prices("Recoil Case", [1.0, 1.0])                  # +10 % since yesterday
    saved_total("total", 1, 10 * 1.0 * CSFLOAT, 10)
    assert summary(client)["change_24h"] == pytest.approx(10)
    client.post("/api/items", json={"market_hash_name": "Recoil Case", "quantity": 20}, headers=HEADERS)
    now = summary(client)
    assert now["total_value"] == pytest.approx(30 * 1.1 * CSFLOAT) and now["item_count"] == 30
    assert now["change_24h"] == pytest.approx(10)               # not +230 %: 20 more cases aren't a gain


def test_saved_totals_match_each_tab(client):
    """A stack split between Steam and a storage unit sells as one stack in the total, but the
    public tab only has the Steam copies - each saved total must be the one its tab shows."""
    capsule_market(client, quantity=300)                        # manual, and bulk-limited
    now = "2026-01-01T00:00:00+00:00"
    db.execute("INSERT INTO items (source, steam_id, asset_id, market_hash_name, name, category, quantity, created_at, "
               "updated_at) VALUES ('steam', 'x', 'a1', ?, ?, 'container', 200, ?, ?)", (CAPSULE, CAPSULE, now, now))
    history.take_snapshot()
    total = portfolio.build_frame(Valuator(), "total")
    steam_share_of_total = float(total.loc[total["source"] == "steam", "total_value"].sum())
    assert steam_share_of_total != pytest.approx(summary(client, "public")["total_value"], abs=0.01)  # the old way
    for scope in ("public", "total"):
        headline = summary(client, scope)["total_value"]
        chart = client.get(f"/api/history/portfolio?scope={scope}&range=7d").get_json()
        assert chart["labels"][-1] == chart["today"] == date.today().isoformat()
        assert chart["values"][-1] == pytest.approx(headline, abs=0.006), scope


def test_mix_history_ends_at_mix_now(client):
    add(client, "Recoil Case", 10, 1.1)
    history_prices("Recoil Case", [1.0, 1.0])
    capsule_market(client, quantity=200)                        # bulk-limited stack
    history_prices(CAPSULE, [0.12, 0.13], market="skinport")
    knife = client.post("/api/items", json={"market_hash_name": "★ Karambit | Doppler (Factory New)", "custom_price": 900,
                                             "custom_price_currency": "USD"}, headers=HEADERS).get_json()["item"]["id"]
    assert knife
    v = Valuator()
    frame = portfolio.build_frame(v, "total")
    result = insights.mix(frame, v, "7d")
    now = {g["key"]: g["value"] for g in result["groups"]}
    last = {s["key"]: s["values"][-1] for s in result["history"]["series"]}
    assert last == pytest.approx(now, abs=0.01)
    assert sum(now.values()) == pytest.approx(summary(client)["total_value"], abs=0.01)
    knives = next(s["values"] for s in result["history"]["series"] if s["key"] == "knives")
    assert len(set(knives)) == 1                                # a custom price has no history: level
    cases = next(s["values"] for s in result["history"]["series"] if s["key"] == "containers")
    assert cases[0] < cases[-1]                                 # prices rose


def test_demo_numbers_agree(app, tmp_path, monkeypatch):
    """The demo (and so the live demo site) never shows two different figures for one thing."""
    from config import Config

    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    from app import create_app

    demo = create_app(demo=True, reset_demo=True).test_client()
    for scope in ("public", "total"):
        data = demo.get(f"/api/items?scope={scope}").get_json()
        headline = data["summary"]["total_value"]
        for range_key in ("7d", "1y"):
            chart = demo.get(f"/api/history/portfolio?scope={scope}&range={range_key}").get_json()
            assert chart["values"][-1] == pytest.approx(headline, abs=0.006)
            assert abs(chart["values"][-1] / chart["values"][-2] - 1) < 0.05      # no jump on the last day
        mix = demo.get(f"/api/insights?scope={scope}&range=90d").get_json()["mix"]
        assert sum(g["value"] for g in mix["groups"]) == pytest.approx(headline, abs=0.01)
        assert sum(s["values"][-1] for s in mix["history"]["series"]) == pytest.approx(headline, abs=0.05)
        assert abs(data["summary"]["change_24h"]) < 5                              # price moves, not items added
