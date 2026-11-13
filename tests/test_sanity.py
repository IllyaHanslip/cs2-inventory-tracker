"""Unrealistic listings, recent sales, the sticker inside slabs, exact names and the background update."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend import catalogue, db, history, refresh, sanity
from backend.comparables import StickerIds
from backend.markets import SOURCES, Quote
from backend.markets.skinport import pick_sales
from tests.conftest import HEADERS, add_quote

SLAB = "Sticker Slab | NRG (Gold) | Budapest 2025"
STICKER = "Sticker | NRG (Gold) | Budapest 2025"
SKINPORT = 0.92                  # 8 % sale fee, no withdrawal fee


def add_sale(name, price, volume=3, source="skinport", currency="USD"):
    refresh.save_sales(source, [(name, price, currency, volume, "30 days")])


def item(client, name):
    data = client.get("/api/items?scope=total").get_json()
    return next(i for i in data["items"] if i["market_hash_name"] == name)


# --------------------------------------------------------------------------- rules
def test_screen_rules():
    usd = 1.0
    # A lone listing far above recent sales is set aside...
    kept, aside, trusted = sanity.screen({"skinport": 1500.0}, 0.7, None, usd)
    assert kept == {} and aside["skinport"]["reason"] == "sales" and trusted == 0.7
    # ...but not one a bit above, nor a cheap one (pennies don't count).
    assert sanity.screen({"skinport": 2.0}, 0.7, None, usd)[1] == {}
    assert sanity.screen({"skinport": 1.1}, 0.2, None, usd)[1] == {}       # 5.5x, but only $0.90 above
    # Markets that agree overrule an odd low sales figure.
    kept, aside, trusted = sanity.screen({"skinport": 3.0, "waxpeer": 3.3}, 0.5, None, usd)
    assert trusted is None and len(kept) == 2
    # Without sales, a listing far above the other markets goes.
    kept, aside, _ = sanity.screen({"skinport": 5.0, "marketcsgo": 40.0}, None, None, usd)
    assert list(aside) == ["marketcsgo"] and aside["marketcsgo"]["reason"] == "markets"
    # Slabs fall back on the sticker inside: 4x (sticker + empty slab) is the limit.
    assert sanity.screen({"waxpeer": 5.0}, None, 1.0, usd)[1] == {}
    assert sanity.screen({"waxpeer": 9.0}, None, 1.0, usd)[1]["waxpeer"]["reason"] == "sticker_inside"
    assert sanity.sticker_inside(SLAB) == STICKER and sanity.sticker_inside(STICKER) is None


def test_pick_sales_prefers_recent_windows_with_a_few_sales():
    entry = {"last_7_days": {"median": 1.2, "volume": 2}, "last_30_days": {"median": 1.0, "volume": 9},
             "last_90_days": {"median": 1.1, "volume": 11}}
    assert pick_sales(entry) == (1.0, 9, "30 days")
    lone = {"last_7_days": {"volume": 0}, "last_30_days": {"median": 0.71, "volume": 1},
            "last_90_days": {"median": 0.71, "volume": 1}}
    assert pick_sales(lone) == (0.71, 1, "90 days")
    assert pick_sales({"last_90_days": {"median": None, "volume": 0}}) is None


# --------------------------------------------------------------------------- valuation
def test_unrealistic_listing_is_ignored_and_sales_used(client):
    client.post("/api/items", json={"market_hash_name": SLAB}, headers=HEADERS)
    add_quote(SLAB, "skinport", 1764.0)
    add_sale(SLAB, 0.71, volume=1)
    found = item(client, SLAB)
    assert found["set_aside"][0]["market"] == "skinport" and found["set_aside"][0]["reason"] == "sales"
    assert found["value_basis"] == "sales" and found["unit_value"] == pytest.approx(0.71 * SKINPORT)
    assert found["sales"]["price"] == pytest.approx(0.71)
    summary = client.get("/api/items?scope=total").get_json()["summary"]
    assert summary["total_value"] < 1


def test_slab_valued_from_the_sticker_inside(client):
    client.post("/api/items", json={"market_hash_name": SLAB}, headers=HEADERS)
    add_quote(SLAB, "waxpeer", 900.0)            # nothing else known about the slab...
    add_quote(STICKER, "csfloat", 0.8)            # ...but the sticker inside has a price
    found = item(client, SLAB)
    assert found["set_aside"][0]["reason"] == "sticker_inside"
    assert found["value_basis"] == "sticker_inside" and found["sell_market"] == "csfloat"
    assert found["sticker_inside"]["name"] == STICKER
    assert found["sticker_inside"]["value"] == pytest.approx(0.8 * 0.98 * 0.975)
    assert STICKER in refresh.tracked_names()     # so it gets priced with every refresh


def test_item_with_only_sales_still_gets_a_value(client):
    client.post("/api/items", json={"market_hash_name": "Recoil Case"}, headers=HEADERS)
    add_sale("Recoil Case", 0.5, volume=40)
    found = item(client, "Recoil Case")
    assert found["value_basis"] == "sales" and found["unit_value"] == pytest.approx(0.5 * SKINPORT)


def test_steam_median_sale_price_is_kept(client):
    refresh.save_quotes("steam", [Quote("Recoil Case", 0.6, "USD", 1200, None, sold_price=0.55)])
    row = db.query_one("SELECT * FROM sale_prices WHERE market_hash_name = 'Recoil Case'")
    assert row["source"] == "steam" and row["price"] == 0.55 and row["volume"] == 1200


def test_skinport_sales_saved_for_tracked_items(client, monkeypatch):
    client.post("/api/items", json={"market_hash_name": "Recoil Case"}, headers=HEADERS)
    add_sale("Recoil Case", 9.0)                   # old figure; Recoil Case is no longer in the list below

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return [{"market_hash_name": SLAB, "last_30_days": {"median": 0.71, "volume": 1}},
                    {"market_hash_name": "Not Mine", "last_7_days": {"median": 5.0, "volume": 5}}]

    monkeypatch.setattr(SOURCES["skinport"].sales_client, "get", lambda url, params=None, headers=None: Response())

    class Job:
        warnings = []
        def update(self, **kw): pass
        def warn(self, message): self.warnings.append(message)

    assert refresh.refresh_sales([SLAB, "Recoil Case"], Job()) == 1
    rows = {r["market_hash_name"]: r for r in db.query("SELECT * FROM sale_prices")}
    assert set(rows) == {SLAB} and rows[SLAB]["currency"] == "EUR" and rows[SLAB]["period"] == "30 days"


# --------------------------------------------------------------------------- history
def test_inflated_history_is_corrected_once(client):
    client.post("/api/items", json={"market_hash_name": SLAB}, headers=HEADERS)
    client.post("/api/items", json={"market_hash_name": "Recoil Case", "quantity": 10}, headers=HEADERS)
    add_quote(SLAB, "skinport", 1764.0)
    add_quote("Recoil Case", "skinport", 1.0)
    add_sale(SLAB, 0.71, volume=1)
    day = (date.today() - timedelta(days=1)).isoformat()
    db.executemany("INSERT INTO price_snapshots VALUES (?, 'skinport', ?, ?, 'USD')",
                   [(SLAB, day, 1764.0), ("Recoil Case", day, 1.0)])
    inflated = 1764.0 * 0.94 + 10 * 1.0 * SKINPORT      # Skinport charges 6 % on items worth €1,000+
    db.execute("INSERT INTO portfolio_snapshots (snapshot_date, scope, total_value, item_count, currency, basis) "
               "VALUES (?, 'total', ?, 11, 'USD', 'after_fees')", (day, inflated))
    assert history.remove_outlier_inflation() == 1
    row = db.query_one("SELECT total_value, screened FROM portfolio_snapshots WHERE snapshot_date = ?", (day,))
    assert row["total_value"] == pytest.approx(0.71 * SKINPORT + 10 * SKINPORT) and row["screened"] == 1
    assert history.remove_outlier_inflation() == 0                     # never twice
    # The item's own chart leaves the listing out too.
    data = client.get("/api/history/item", query_string={"name": SLAB, "range": "7d"}).get_json()
    assert data["series"] == []


# --------------------------------------------------------------------------- names
def test_sticker_ids_match_case_exactly():
    ids = StickerIds({1: "Sticker | NiKo | Budapest 2025", 2: "Sticker | niko | Budapest 2025",
                      3: "Sticker | Mirage (Gold)", 4: "Sticker | Mirage (Gold)", 5: "Sticker | ZywOo (Gold)"})
    assert ids.find("Sticker | NiKo | Budapest 2025") == 1 and ids.find("Sticker | niko | Budapest 2025") == 2
    assert ids.find("Sticker | NIKO | Budapest 2025") is None          # ambiguous: never guessed
    assert ids.find("sticker | zywoo (gold)") == 5                     # unambiguous: wrong case still works
    assert ids.find("Sticker | Mirage (Gold)") in (3, 4)


def test_typed_names_get_steam_spelling_only_when_unambiguous(client):
    from backend import cache

    cache.set_cached(catalogue.NAMES_KEY, ["Sticker | NiKo | Budapest 2025", "Sticker | niko | Budapest 2025",
                                           "Sticker Slab | NRG (Gold) | Budapest 2025"])
    assert catalogue.canonical("sticker slab | nrg (gold) | budapest 2025") == SLAB
    assert catalogue.canonical("Sticker | niko | Budapest 2025") == "Sticker | niko | Budapest 2025"
    assert catalogue.canonical("sticker | NIKO | budapest 2025") == "sticker | NIKO | budapest 2025"   # ambiguous
    created = client.post("/api/items", json={"market_hash_name": "sticker slab | nrg (gold) | budapest 2025"},
                          headers=HEADERS).get_json()["item"]
    assert created["market_hash_name"] == SLAB
    names = client.get("/api/names?q=NiKo").get_json()["names"]
    assert names[:2] == ["Sticker | NiKo | Budapest 2025", "Sticker | niko | Budapest 2025"]
    names = client.get("/api/names?q=niko").get_json()["names"]
    assert names[0] == "Sticker | niko | Budapest 2025"


# --------------------------------------------------------------------------- containers & updates
def test_container_cards_link_to_stored_items(client):
    box = client.post("/api/items", json={"market_hash_name": "Storage Unit", "category": "container"},
                      headers=HEADERS).get_json()["item"]["id"]
    client.post(f"/api/containers/{box}/items", json={"items": [{"market_hash_name": "Recoil Case", "quantity": 4}]},
                headers=HEADERS)
    card = client.get("/api/containers").get_json()["containers"][0]
    stored = client.get(f"/api/containers/{box}").get_json()["contents"][0]
    assert card["preview"][0]["id"] == stored["id"]


def test_new_items_priced_at_once_from_saved_lists(client):
    """Adding items prices them from the lists already saved on this PC - no download needed."""
    from backend import cache

    cache.set_cached(SOURCES["waxpeer"]._cache_key(), [["Recoil Case", 0.5, 30, None], [STICKER, 0.8, 5, None]])
    cache.set_cached(f"sales_skinport_{SOURCES['skinport'].currency}", [[SLAB, 0.71, 1, "90 days"]])
    db.execute("UPDATE api_cache SET fetched_at = 0")          # old lists still count: nothing is downloaded
    client.post("/api/items", json={"market_hash_name": "Recoil Case", "quantity": 3}, headers=HEADERS)
    assert item(client, "Recoil Case")["unit_value"] == pytest.approx(0.5 * 0.94 * 0.98)   # Waxpeer: 6 % + 2 %
    box = client.post("/api/items", json={"market_hash_name": "Storage Unit", "category": "container"},
                      headers=HEADERS).get_json()["item"]["id"]
    client.post(f"/api/containers/{box}/items", json={"items": [{"market_hash_name": SLAB, "quantity": 2}]},
                headers=HEADERS)
    slab = item(client, SLAB)
    assert slab["value_basis"] == "sales" and slab["sticker_inside"]["value"] is not None
