"""Tags for grouping, movers, the portfolio mix, notifications and the sell planner."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from backend import db, insights, notify, planner, refresh, settings
from backend.tags import item_tags
from backend.valuation import Valuator
from tests.conftest import HEADERS, add_quote

CSFLOAT = 0.98 * 0.975
META = {"weapons": {"AK-47": "Weapons", "Karambit": "Knives"},
        "paints": {"AK-47|Redline": ["Classified", ["The Phoenix Collection"]]}, "agents": set()}


def add(client, name, quantity=1, price=None, market="csfloat"):
    if price is not None:
        add_quote(name, market, price)
    client.post("/api/items", json={"market_hash_name": name, "quantity": quantity}, headers=HEADERS)


def history_prices(name, prices, market="csfloat"):
    """Saved daily prices, oldest first, ending yesterday."""
    days = len(prices)
    db.executemany("INSERT INTO price_snapshots VALUES (?, ?, ?, ?, 'USD')",
                   [(name, market, (date.today() - timedelta(days=days - i)).isoformat(), p) for i, p in enumerate(prices)])


# --------------------------------------------------------------------------- tags
def test_tags_from_names():
    assert item_tags("Sticker | NRG (Gold) | Budapest 2025", "sticker", None, META)["event"] == "Budapest 2025"
    capsule = item_tags("Paris 2023 Legends Sticker Capsule", "container", None, META)
    assert capsule["event"] == "Paris 2023" and capsule["type"] == "Capsule"
    assert item_tags("Sticker | Nemiga (Holo) | 2020 RMR", "sticker", None, META)["event"] == "2020 RMR"
    ak = item_tags("StatTrak™ AK-47 | Redline (Field-Tested)", "weapon_skin", None, META)
    assert ak == {"event": None, "collection": "The Phoenix Collection", "type": "Rifle", "weapon": "AK-47",
                  "rarity": "Classified"}
    knife = item_tags("★ Karambit | Doppler Phase 2 (Factory New)", "weapon_skin", None, META)
    assert knife["type"] == "Knife" and knife["weapon"] == "Karambit"
    assert item_tags("★ Sport Gloves | Vice (Field-Tested)", "weapon_skin", None, META)["type"] == "Gloves"
    assert item_tags("2025 Service Medal", "other", None, META) == {"event": None, "collection": None,
                                                                    "type": "Collectible", "weapon": None, "rarity": None}
    assert item_tags("AK-47 | Slate (Minimal Wear)", "weapon_skin", "Restricted", META)["rarity"] == "Restricted"


def test_items_carry_tags(client):
    add(client, "Paris 2023 Legends Sticker Capsule", 3, 0.07)
    item = client.get("/api/items?scope=total").get_json()["items"][0]
    assert item["tags"]["event"] == "Paris 2023" and item["tags"]["type"] == "Capsule"


# --------------------------------------------------------------------------- movers & mix
def test_movers_and_mix(client):
    add(client, "Recoil Case", 10, 1.1)
    add(client, "Sticker | Crown (Foil)", 1, 90.0)
    history_prices("Recoil Case", [1.0, 1.0])          # +10 % since yesterday
    history_prices("Sticker | Crown (Foil)", [100.0, 100.0])   # -10 %
    data = client.get("/api/insights?scope=total&range=7d").get_json()
    up, down = data["movers"]["change_24h"]["gainers"], data["movers"]["change_24h"]["losers"]
    assert up[0]["name"] == "Recoil Case" and up[0]["change"] == pytest.approx(10 * 0.1 * CSFLOAT)
    assert down[0]["name"] == "Sticker | Crown (Foil)" and down[0]["change"] == pytest.approx(-10 * CSFLOAT)
    groups = {g["key"]: g["value"] for g in data["mix"]["groups"]}
    assert groups["containers"] == pytest.approx(10 * 1.1 * CSFLOAT) and groups["stickers"] == pytest.approx(90 * CSFLOAT)
    series = {s["key"]: s["values"] for s in data["mix"]["history"]["series"]}
    assert series["containers"][0] == pytest.approx(10 * 1.0 * CSFLOAT, abs=0.01)


def test_notification_summary(client):
    add(client, "Recoil Case", 10, 1.1)
    history_prices("Recoil Case", [1.0, 1.0])
    settings.set_value("last_best_markets", {"Recoil Case": "skinport"})
    from backend import portfolio
    v = Valuator()
    frame = portfolio.build_frame(v, "total")
    info = insights.update_summary(frame, portfolio.summarize(frame, v, "total"), v.base)
    assert info["switched"] == 1 and info["switched_example"] == ("Recoil Case", "CSFloat")
    assert info["big_moves"] == 1 and info["gainer"]["name"] == "Recoil Case"
    title, lines = insights.notification_text(info)
    assert title.startswith("Inventory $") and "Recoil Case" in lines[0] and "now sells best on CSFloat" in lines[1]
    assert settings.get("last_best_markets") == {"Recoil Case": "csfloat"}
    xml = notify.toast_xml("A & B", ["<line>"], "cs2-inventory:open")
    assert "A &amp; B" in xml and "&lt;line&gt;" in xml and 'launch="cs2-inventory:open"' in xml
    assert notify.toast("x", ["y"]) is False                      # switched off in tests


def test_background_update_sends_a_notification(client, monkeypatch):
    from backend import jobs
    sent = []
    monkeypatch.setattr(notify, "toast", lambda title, lines: sent.append((title, lines)) or True)
    monkeypatch.setattr(jobs, "_now", lambda: datetime.now().replace(hour=13, minute=0))   # after 12:00
    add(client, "Recoil Case", 2, 1.0)
    monkeypatch.setattr(refresh, "refresh_prices", lambda job: {"complete": False})
    jobs.background_update()
    assert sent[0][0] == "Updating your CS2 inventory" and "another notification" in sent[0][1][1]   # it's starting
    assert sent[-1][0] == "Couldn't update your CS2 inventory" and "Trying again at 14:00" in sent[-1][1][0]
    monkeypatch.setattr(refresh, "refresh_prices", lambda job: {"complete": True})
    jobs.background_update()
    assert sent[-1][0].startswith("Inventory $")
    sent.clear()
    jobs.background_update()                                   # done for today: nothing, so no notification
    assert sent == []


def test_open_app_notifies_when_its_own_update_starts_and_ends(client, monkeypatch):
    from backend import jobs
    sent = []
    monkeypatch.setattr(notify, "toast", lambda title, lines: sent.append((title, lines)) or True)
    add(client, "Recoil Case", 2, 1.0)
    run = jobs.automatic("refresh", lambda job: {"complete": True})
    run(jobs.Job("refresh", "Price refresh"))
    assert [t for t, _ in sent][0] == "Refreshing your CS2 prices" and sent[-1][0].startswith("Inventory $")


# --------------------------------------------------------------------------- planner
# The planning itself happens in the page (static/js/plan-calc.js); these check what it's given.
def test_undercut_price():
    assert planner.undercut_price(10.0, 2) == 9.80
    assert planner.undercut_price(10.0, 0) == 10.0               # 0 %: the market price itself
    assert planner.undercut_price(0.05, 2) == 0.04               # always at least 0.01 under
    assert planner.undercut_price(0.01, 5) == 0.01               # can't go lower than 0.01
    assert planner.undercut_price(1.999, 2) == 1.95              # rounded down to a whole 0.01
    assert planner.undercut_price(100.0, 10) == 90.0


def test_sell_candidates(client):
    add(client, "Recoil Case", 100, 0.50)                # falling, on CSFloat (cash)
    history_prices("Recoil Case", [0.80, 0.75, 0.70, 0.65, 0.60, 0.55])
    add_quote("Kilowatt Case", "steam", 2.0)             # Steam only: wallet money
    client.post("/api/items", json={"market_hash_name": "Kilowatt Case", "quantity": 5}, headers=HEADERS)
    knife = client.post("/api/items", json={"market_hash_name": "★ Karambit | Doppler (Factory New)", "custom_price": 900,
                                             "custom_price_currency": "USD"}, headers=HEADERS).get_json()["item"]["id"]
    data = client.get("/api/sell-candidates").get_json()
    assert data["currency"] == "USD" and data["undercuts"] == [0, 1, 2, 3, 5, 10] and data["default_undercut"] == 2
    assert {m["key"]: m["cash_out"] for m in data["markets"]}["steam"] is False
    assert {m["key"]: m["cash_out"] for m in data["markets"]}["csfloat"] is True

    rows = {r["lot"]: r for r in data["rows"]}
    recoil = rows["plain:Recoil Case"]
    assert recoil["quantity"] == 100 and recoil["category"] == "container" and "recoil case" in recoil["search"]
    assert recoil["locked"] is False and recoil["in_container"] is False
    lot = data["lots"]["plain:Recoil Case"]
    assert lot["weekly"] < 0 and lot["weekly_cash"] < 0   # falling either way
    option = next(o for o in lot["options"] if o["market"] == "csfloat")
    zero, five = option["prices"][0], option["prices"][data["undercuts"].index(5)]
    assert zero == [0.5, pytest.approx(0.5 * CSFLOAT)]
    assert five == [0.47, pytest.approx(0.47 * CSFLOAT)]   # 5 % under (rounded down), fees from that price
    assert option["wallet"] is False

    steam = data["lots"]["plain:Kilowatt Case"]["options"][0]
    assert steam["market"] == "steam" and steam["wallet"] is True
    # Your own value is its own lot (never pooled with plain copies), sold at that value everywhere.
    assert f"row:{knife}" in data["lots"] and data["lots"][f"row:{knife}"]["weekly"] is None


def test_sell_candidates_leave_out_storage_units(client):
    unit = client.post("/api/items", json={"market_hash_name": "Storage Unit", "category": "container",
                                           "name_tag": "Long Holds", "is_storage": True}, headers=HEADERS).get_json()["item"]["id"]
    add_quote("Recoil Case", "csfloat", 1.0)
    client.post(f"/api/containers/{unit}/items", json={"items": [{"market_hash_name": "Recoil Case", "quantity": 7}]},
                headers=HEADERS)
    data = client.get("/api/sell-candidates").get_json()
    assert [r["id"] for r in data["rows"]] != [] and unit not in [r["id"] for r in data["rows"]]
    stored = data["rows"][0]
    assert stored["in_container"] is True and "Long Holds" in stored["container_name"] and stored["quantity"] == 7
