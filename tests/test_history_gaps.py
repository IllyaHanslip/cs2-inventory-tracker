"""Days a market wasn't checked, and reusing results until the data changes."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend import db, history, memo
from backend.valuation import Valuator
from tests.conftest import HEADERS, add_quote

PACKAGE = "ESL One Cologne 2015 Dust II Souvenir Package"
CSFLOAT = 0.98 * 0.975          # 2 % sale fee, 2.5 % withdrawal
MCSGO = 0.95 * 0.97             # 5 % sale fee, 3 % withdrawal


def day(n):
    return (date.today() - timedelta(days=n)).isoformat()


def saved(rows):
    """Saved daily prices: (days ago, market, price in USD)."""
    db.executemany("INSERT INTO price_snapshots VALUES (?, ?, ?, ?, 'USD')", [(name, m, day(n), p) for name, n, m, p in rows])


def test_a_day_a_market_wasnt_checked_keeps_its_last_price(client):
    """CSFloat (asked one item at a time) saved nothing on day 1; Market.CSGO's list was fetched that
    day without the package, so it had stopped listing it. The 24-hour change compares CSFloat with
    CSFloat in both "Values from" modes, not with whatever happened to be saved that day."""
    client.post("/api/items", json={"market_hash_name": PACKAGE}, headers=HEADERS)
    add_quote(PACKAGE, "csfloat", 45.09)
    add_quote(PACKAGE, "steam", 47.16)
    saved([(PACKAGE, 2, "csfloat", 46.26), (PACKAGE, 2, "marketcsgo", 59.0), (PACKAGE, 2, "steam", 47.16),
           (PACKAGE, 1, "steam", 47.16), ("Recoil Case", 1, "marketcsgo", 1.0),      # Market.CSGO ran on day 1
           (PACKAGE, 0, "csfloat", 45.09), (PACKAGE, 0, "steam", 47.16)])
    changes = {}
    for cash in (False, True):
        v = Valuator()
        v.cash_only = cash
        best = history.best_after_fees([PACKAGE], v, day(5)).set_index("date")["value"]
        assert best[day(1)] == pytest.approx(46.26 * CSFLOAT)        # CSFloat carried over day 1...
        assert best[day(2)] == pytest.approx(59.0 * MCSGO)           # ...Market.CSGO's day-2 listing isn't
        changes[cash] = history.price_changes([PACKAGE], v)["change_24h"].iloc[0]
    assert changes[False] == pytest.approx(changes[True]) == pytest.approx((45.09 / 46.26 - 1) * 100)


def test_carried_for_a_few_days_at_most(client):
    client.post("/api/items", json={"market_hash_name": PACKAGE}, headers=HEADERS)
    saved([(PACKAGE, 6, "csfloat", 40.0)] + [(PACKAGE, n, "steam", 47.0) for n in range(6, -1, -1)])
    v = Valuator()
    hist = history._market_history([PACKAGE], day(10), v)
    carried = sorted(hist[hist["market"] == "csfloat"]["date"])
    assert carried == [day(6), day(5), day(4), day(3)]               # the saved day + 3 more


def test_results_are_reused_until_the_data_changes(client):
    client.post("/api/items", json={"market_hash_name": "Recoil Case"}, headers=HEADERS)
    add_quote("Recoil Case", "csfloat", 1.0)
    first = Valuator()
    assert Valuator().quotes is first.quotes                          # the same, reused
    client.put("/api/settings", json={"cash_only": True}, headers=HEADERS)
    assert Valuator().quotes is first.quotes                          # a setting: still reused
    add_quote("Recoil Case", "csfloat", 2.0)                          # new data: worked out again
    assert Valuator().prices["Recoil Case"] == pytest.approx(2.0)


def test_nothing_is_reused_while_the_background_update_writes(app):
    from config import Config

    marker = Config.DATA_DIR / ".update-running"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("1", encoding="utf-8")
    try:
        assert memo.cached(("t",), object) is not memo.cached(("t",), object)
    finally:
        marker.unlink()
    once = memo.cached(("t",), object)
    assert memo.cached(("t",), object) is once                        # finished: reused again
