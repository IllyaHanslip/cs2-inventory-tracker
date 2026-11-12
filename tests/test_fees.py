"""Selling and cash-out fees: per-market maths, overrides, and the best place to sell each item."""
import pytest

from backend import fees, settings
from tests.conftest import HEADERS, add_quote


def test_steam_fee_is_added_on_top_and_never_cash():
    assert fees.net("steam", 100.0) == pytest.approx(100 / 1.15)       # a £100 sale nets ~£86.96
    assert fees.net("steam", 0.03) == pytest.approx(0.01)               # minimum fees still apply
    assert fees.profile("steam")["cash_out"] is False                   # Steam Wallet can't be withdrawn


def test_marketplace_fees():
    assert fees.net("skinport", 100.0, to_eur=0.9) == pytest.approx(92.0)        # 8 %
    assert fees.net("skinport", 2000.0, to_eur=0.9) == pytest.approx(1880.0)     # 6 % at €1,000+
    assert fees.net("csfloat", 100.0) == pytest.approx(100 * 0.98 * 0.975)       # 2 % then 2.5 % withdrawal
    assert fees.net("waxpeer", 100.0) == pytest.approx(100 * 0.94 * 0.98)
    assert fees.net("marketcsgo", 100.0) == pytest.approx(100 * 0.95 * 0.97)
    assert fees.profile("manual:Buff163")["cash_out"] is False
    assert fees.net("manual:Some Site", 50.0) == pytest.approx(50.0)             # unknown manual market: no fees
    assert fees.net("csfloat", None) is None


def test_overrides_and_validation():
    overrides = fees.validate({"csfloat": {"withdraw": 0.005}, "skinport": {"sale": 6}})  # 0.5 % and "6" = 6 %
    assert overrides == {"csfloat": {"withdraw": 0.005}, "skinport": {"sale": 0.06}}
    assert fees.net("csfloat", 100.0, overrides) == pytest.approx(100 * 0.98 * 0.995)
    assert fees.net("skinport", 2000.0, overrides, to_eur=0.9) == pytest.approx(1880.0)  # flat 6 % now
    with pytest.raises(ValueError):
        fees.validate({"csfloat": {"sale": 75}})


def test_items_show_the_best_cash_out(client):
    name = "AWP | Asiimov (Field-Tested)"
    client.post("/api/items", json={"market_hash_name": name, "quantity": 2}, headers=HEADERS)
    add_quote(name, "steam", 12.0)                 # highest net, but Steam Wallet only
    add_quote(name, "skinport", 8.1, "EUR")        # 9.00 USD -> 8.28 after 8 %
    add_quote(name, "csfloat", 9.5)                # -> 9.5 * 0.98 * 0.975 = 9.077
    item = client.get("/api/items?scope=total").get_json()["items"][0]
    assert item["payout_market"] == "csfloat"
    assert item["payout_unit"] == pytest.approx(9.5 * 0.98 * 0.975)
    assert item["payout_value"] == pytest.approx(2 * 9.5 * 0.98 * 0.975)
    steam = next(q for q in item["quotes"] if q["market"] == "steam")
    assert steam["net"] == pytest.approx(12 / 1.15) and steam["cash_out"] is False
    summary = client.get("/api/items?scope=total").get_json()["summary"]
    assert summary["cash_out_value"] == pytest.approx(item["payout_value"])

    # If you set a lower CSFloat withdrawal fee in Settings, it's used straight away.
    settings.set_value("selling_fees", {"csfloat": {"withdraw": 0.005}})
    item = client.get("/api/items?scope=total").get_json()["items"][0]
    assert item["payout_unit"] == pytest.approx(9.5 * 0.98 * 0.995)


def test_steam_only_items_have_no_cash_out(client):
    client.post("/api/items", json={"market_hash_name": "Recoil Case"}, headers=HEADERS)
    add_quote("Recoil Case", "steam", 0.5)
    data = client.get("/api/items?scope=total").get_json()
    assert data["items"][0]["payout_value"] is None
    assert data["summary"]["cash_out_missing"] == 1


def test_custom_priced_items_use_the_cheapest_market_to_sell_on(client):
    client.post("/api/items", json={"market_hash_name": "★ Karambit | Fade (Factory New)", "custom_price": 1000,
                                    "custom_price_currency": "USD"}, headers=HEADERS)
    item = client.get("/api/items?scope=total").get_json()["items"][0]
    assert item["payout_market"] == "csfloat"                      # 2 % + 2.5 % is the lowest total
    assert item["payout_unit"] == pytest.approx(1000 * 0.98 * 0.975)
