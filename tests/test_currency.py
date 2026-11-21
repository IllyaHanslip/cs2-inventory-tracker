"""Exchange rates: checked again with every price update, and the newest source wins."""
from __future__ import annotations

from datetime import date, timedelta

from backend import currency, db


def days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


def fake_sources(monkeypatch, ecb_day, er_day, calls):
    def ecb():
        calls.append("ecb")
        return {"GBP": 0.76, "EUR": 0.86}, f"Frankfurter / ECB ({ecb_day})", ecb_day

    def er():
        calls.append("er")
        return {"GBP": 0.77, "EUR": 0.87}, f"ExchangeRate-API (open.er-api.com, {er_day})", er_day

    monkeypatch.setattr(currency, "_from_frankfurter", ecb)
    monkeypatch.setattr(currency, "_from_er_api", er)


def test_rates_are_checked_again_with_later_updates(app, monkeypatch):
    calls = []
    fake_sources(monkeypatch, days_ago(1), days_ago(0), calls)
    assert currency.refresh_rates()["GBP"] == 0.76 and calls == ["ecb"]      # yesterday's ECB rates: fine
    currency.refresh_rates()
    assert calls == ["ecb"]                                                  # checked minutes ago: not again
    db.execute("UPDATE api_cache SET fetched_at = 0 WHERE cache_key = 'fx_last_check'")   # half an hour on
    fake_sources(monkeypatch, days_ago(0), days_ago(0), calls)
    currency.refresh_rates()
    assert calls == ["ecb", "ecb"]                                           # the same day, checked again
    assert currency.last_source()["source"] == f"Frankfurter / ECB ({days_ago(0)})"


def test_newer_rates_win_over_old_ecb_ones(app, monkeypatch):
    calls = []
    fake_sources(monkeypatch, days_ago(3), days_ago(0), calls)                # a long weekend at the ECB
    assert currency.refresh_rates()["GBP"] == 0.77 and calls == ["ecb", "er"]
    assert "ExchangeRate-API" in currency.last_source()["source"]


def test_unreachable_sources_keep_the_stored_rates(app, monkeypatch):
    calls = []
    fake_sources(monkeypatch, days_ago(0), days_ago(0), calls)
    currency.refresh_rates()

    def down():
        raise currency.ApiError("offline")

    monkeypatch.setattr(currency, "_from_frankfurter", down)
    monkeypatch.setattr(currency, "_from_er_api", down)
    assert currency.refresh_rates(force=True)["GBP"] == 0.76
