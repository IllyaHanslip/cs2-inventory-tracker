"""Currency normalisation, market aggregation and sticker valuation."""
import pytest

from backend.currency import FxConverter
from backend.valuation import Valuator
from tests.conftest import add_quote


def test_fx_conversion(app):
    fx = FxConverter.load()
    assert fx.convert(9, "EUR", "USD") == pytest.approx(10)
    assert fx.convert(9, "EUR", "CNY") == pytest.approx(70)
    assert fx.convert(5, "USD", "USD") == 5
    assert fx.convert(5, "XYZ", "USD") is None


def test_quotes_are_normalised_and_best_deal_found(app):
    name = "AWP | Asiimov (Field-Tested)"
    add_quote(name, "steam", 10.0, "USD")
    add_quote(name, "skinport", 8.1, "EUR")        # = 9.00 USD -> cheapest
    add_quote(name, "csfloat", 9.5, "USD")
    add_quote(name, "manual:Buff163", 66.5, "CNY", manual=True)  # = 9.50 USD
    v = Valuator()
    assert v.price(name) == pytest.approx(9.5)    # median of 10, 9, 9.5, 9.5
    best = v.best_deals()[name]
    assert best["market"] == "skinport"
    assert best["price"] == pytest.approx(9.0)


@pytest.mark.parametrize("strategy, expected", [
    ("lowest", 9.0), ("highest", 10.0), ("mean", (10 + 9 + 9.5) / 3), ("steam", 10.0), ("skinport", 9.0),
])
def test_valuation_strategies(app, strategy, expected):
    from backend import settings
    name = "M4A4 | Howl (Field-Tested)"
    add_quote(name, "steam", 10.0)
    add_quote(name, "skinport", 8.1, "EUR")
    add_quote(name, "csfloat", 9.5)
    settings.set_value("valuation_strategy", strategy)
    assert Valuator().price(name) == pytest.approx(expected)


def test_disabled_market_is_ignored(app):
    from backend import settings
    add_quote("X", "steam", 10.0)
    add_quote("X", "csfloat", 2.0)
    settings.set_value("enabled_markets", ["steam"])
    assert Valuator().price("X") == pytest.approx(10.0)


def test_sticker_rate_interpolates_on_log_scale(app):
    v = Valuator()
    assert v.sticker_rate(0.5) == pytest.approx(0.05)
    assert v.sticker_rate(10.0) == pytest.approx(0.10)   # halfway between $1 and $100 on a log scale
    assert v.sticker_rate(500.0) == pytest.approx(0.15)


def test_applied_premium_with_craft_and_scrape(app):
    add_quote("Sticker | Crown (Foil)", "steam", 100.0)
    v = Valuator()
    expensive_skin = 10_000.0  # far above the cap, so the raw premium passes through almost untouched
    craft = [{"kind": "sticker", "name": "Crown (Foil)", "slot": i, "scrape": 0} for i in range(4)]
    premium, breakdown, summary = v.applied_premium(craft, expensive_skin)
    assert all(b["craft"] for b in breakdown)
    assert summary["raw"] == pytest.approx(4 * 100 * 0.15 * 1.2)
    assert premium == pytest.approx(summary["raw"], rel=0.01)

    scraped = [{"kind": "sticker", "name": "Crown (Foil)", "slot": 0, "scrape": 0.5}]
    _, _, summary = v.applied_premium(scraped, expensive_skin)
    assert summary["raw"] == pytest.approx(100 * 0.15 * 0.5)

    unknown = [{"kind": "sticker", "name": "Never Priced", "slot": 0, "scrape": 0}]
    assert v.applied_premium(unknown, expensive_skin)[0] == 0


def test_rare_stickers_on_a_cheap_skin_are_capped(app):
    """Two Katowice 2014 stickers (~£1,500 loose) on a ~£8 skin must not add ~£225."""
    add_quote("Sticker | Team Dignitas | Katowice 2014", "skinport", 974.6, "EUR")
    add_quote("Sticker | HellRaisers | Katowice 2014", "skinport", 829.0, "EUR")
    v = Valuator()
    stickers = [{"kind": "sticker", "name": n, "slot": i, "scrape": 0}
                for i, n in enumerate(["Team Dignitas | Katowice 2014", "HellRaisers | Katowice 2014"])]
    skin = 9.3
    premium, breakdown, summary = v.applied_premium(stickers, skin)
    assert summary["raw"] == pytest.approx((974.6 + 829.0) / 0.9 * 0.15)   # what the old model added
    assert summary["capped"] and premium <= skin                            # now at most doubles the skin
    assert all(b["thin"] for b in breakdown)                                 # one market, 1-2 listings
    assert sum(b["counted"] for b in breakdown) == pytest.approx(premium)


def test_premium_needs_a_skin_price_and_respects_the_setting(app):
    from backend import settings
    add_quote("Sticker | Crown (Foil)", "steam", 100.0)
    one = [{"kind": "sticker", "name": "Crown (Foil)", "slot": 0, "scrape": 0}]
    premium, _, summary = Valuator().applied_premium(one, None)
    assert premium == 0 and summary["no_base"]
    settings.set_value("sticker_premium_cap", 0.5)
    premium, _, summary = Valuator().applied_premium(one, 20.0)
    assert premium <= 10.0 and summary["cap"] == pytest.approx(10.0)


def test_custom_price_is_the_whole_value(client):
    """A custom price includes the stickers, so they aren't added on top of it."""
    from tests.conftest import HEADERS
    add_quote("AK-47 | Blue Laminate (Field-Tested)", "steam", 9.0)
    add_quote("Sticker | Crown (Foil)", "steam", 100.0)
    body = {"market_hash_name": "AK-47 | Blue Laminate (Field-Tested)",
            "stickers": [{"kind": "sticker", "name": "Crown (Foil)"}]}
    client.post("/api/items", json=body, headers=HEADERS)
    item = client.get("/api/items?scope=total").get_json()["items"][0]
    assert item["sticker_premium"] > 0 and item["market_unit_value"] > 9.0   # market-based: skin + capped premium
    assert item["unit_value"] == pytest.approx((9.0 + item["sticker_premium"]) / 1.15)  # sold on Steam, after fees
    client.put(f"/api/items/{item['id']}", json={"custom_price": 60, "custom_price_currency": "USD",
                                                 "purchase_price": 60, "purchase_currency": "USD"}, headers=HEADERS)
    item = client.get("/api/items?scope=total").get_json()["items"][0]
    assert item["market_unit_value"] == pytest.approx(60) and item["sticker_premium"] == 0
    assert item["unit_value"] == pytest.approx(60 * 0.98 * 0.975)        # the custom price, after CSFloat's fees
    assert item["sticker_included"] and item["profit"] == pytest.approx(60 * 0.98 * 0.975 - 60)


def test_soft_cap():
    from backend.valuation import soft_cap
    assert soft_cap(0.1, 100) == pytest.approx(0.1, rel=0.01)   # tiny premiums barely change
    assert soft_cap(1000, 10) == pytest.approx(10, rel=0.01)    # huge ones approach the cap
    assert soft_cap(5, 0) == 0
