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
    assert sent and sent[-1][0] == "Couldn't update your CS2 inventory" and "Trying again at 14:00" in sent[-1][1][0]
    monkeypatch.setattr(refresh, "refresh_prices", lambda job: {"complete": True})
    jobs.background_update()
    assert sent[-1][0].startswith("Inventory $")


# --------------------------------------------------------------------------- planner
def test_planner_sells_falling_stacks_first_and_keeps_valuables(client):
    add(client, "Recoil Case", 100, 0.50)                # big stack, falling
    add(client, "Kilowatt Case", 20, 0.80)               # smaller stack, rising
    add(client, "★ Karambit | Doppler (Factory New)", 1, 800.0)
    history_prices("Recoil Case", [0.80, 0.75, 0.70, 0.65, 0.60, 0.55])
    history_prices("Kilowatt Case", [0.60, 0.65, 0.70, 0.72, 0.75, 0.78])
    result = planner.plan(20.0, Valuator())
    names = [s["name"] for s in result["steps"]]
    assert names[0] == "Recoil Case" and "★ Karambit | Doppler (Factory New)" not in names
    assert result["achieved"] >= 20.0 and result["achieved"] - 20.0 < 1.0     # no big overshoot
    assert any("falling" in r for r in result["steps"][0]["reasons"])
    assert result["max"] == pytest.approx((100 * 0.5 + 20 * 0.8 + 800) * CSFLOAT, abs=0.01)


def test_planner_respects_keep_and_wallet(client):
    add(client, "Recoil Case", 10, 1.0)
    add_quote("Kilowatt Case", "steam", 2.0)             # Steam only: wallet money
    client.post("/api/items", json={"market_hash_name": "Kilowatt Case", "quantity": 5}, headers=HEADERS)
    recoil_id = next(i["id"] for i in client.get("/api/items?scope=total").get_json()["items"] if i["name"] == "Recoil Case")
    plan = client.get(f"/api/sell-plan?target=5&keep={recoil_id}").get_json()
    assert plan["steps"] == [] and plan["excluded"]["kept"] == 1 and plan["excluded"]["no_price"] == 1
    with_wallet = client.get(f"/api/sell-plan?target=5&keep={recoil_id}&wallet=1").get_json()
    assert [s["name"] for s in with_wallet["steps"]] == ["Kilowatt Case"]
    assert with_wallet["steps"][0]["markets"][0]["market"] == "steam"


def test_planner_uses_cheap_copies_to_finish(client):
    add(client, "AK-47 | Redline (Field-Tested)", 1, 50.0)
    add(client, "Recoil Case", 30, 0.40)
    plan = planner.plan(3.0, Valuator())
    assert [s["name"] for s in plan["steps"]] == ["Recoil Case"] and plan["steps"][0]["units"] < 30
