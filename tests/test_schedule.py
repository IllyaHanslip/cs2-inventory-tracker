"""When the daily update runs: automatically never before 12:00 (the dashboard any time), once a day,
retried hourly if the markets are unreachable."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend import cache, currency, db, jobs, refresh, settings
from backend.http_client import ApiError
from backend.markets import SOURCES
from tests.conftest import HEADERS

TODAY = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)


def at(hour, minute=0, days=0):
    return TODAY + timedelta(days=days, hours=hour, minutes=minute)


def mark_done(when):
    settings.set_value("last_daily_update", when.astimezone(timezone.utc).isoformat())


@pytest.fixture
def clock(client, monkeypatch):
    """Items to update, and a clock the test sets: clock["now"] = at(13)."""
    client.post("/api/items", json={"market_hash_name": "Recoil Case"}, headers=HEADERS)
    state = {"now": at(9)}
    monkeypatch.setattr(jobs, "_now", lambda: state["now"])
    return state


def test_automatic_from_midday_dashboard_any_time(clock):
    auto = lambda when: jobs.daily_update_due(when, automatic=True)   # noqa: E731
    assert not auto(at(9)) and not auto(at(11, 59))             # automatically: never before 12:00
    assert auto(at(12)) and auto(at(17, 30))                    # at 12:00, or whenever the PC is on later
    assert jobs.daily_update_due(at(9))                         # opening the dashboard: any time
    mark_done(at(9, 30))                                        # updated this morning (dashboard open)...
    assert not auto(at(12)) and not jobs.daily_update_due(at(15))   # ...so nothing more today
    assert not auto(at(9, days=1)) and auto(at(12, days=1))     # tomorrow from 12:00 again


def test_daily_job_counts_only_when_prices_arrived(clock, monkeypatch):
    outcome = {"complete": False}
    monkeypatch.setattr(refresh, "refresh_prices", lambda job: dict(outcome))
    job = jobs.Job("daily", "Daily update")
    assert jobs.daily_job(job)["complete"] is False
    assert settings.get("last_daily_update") is None and "tried again next hour" in job.warnings[0]
    outcome["complete"] = True
    assert jobs.daily_job(jobs.Job("daily", "Daily update"))["complete"] is True
    assert settings.get("last_daily_update")


def test_background_update_waits_for_midday_and_retries_hourly(clock, monkeypatch):
    runs = []
    reachable = {"ok": False}

    def fake_refresh(job):
        runs.append(clock["now"])
        return {"complete": reachable["ok"]}

    monkeypatch.setattr(refresh, "refresh_prices", fake_refresh)
    clock["now"] = at(8, 50)                                     # PC switched on in the morning
    assert "before 12:00" in jobs.background_update()["skipped"] and runs == []
    clock["now"] = at(12)                                       # 12:00, but the internet is down
    assert jobs.background_update()["complete"] is False
    clock["now"] = at(13)                                       # tried again the next hour
    reachable["ok"] = True
    assert jobs.background_update()["complete"] is True
    clock["now"] = at(14)                                       # done for today
    assert jobs.background_update() == {"skipped": "today's daily update is already done"}
    assert runs == [at(12), at(13)]
    clock["now"] = at(12, days=1)                               # next day: the dashboard updated at 9:00
    mark_done(at(9, days=1))
    assert jobs.background_update() == {"skipped": "today's daily update is already done"}
    assert not jobs.background_update_running()                 # the marker is cleared afterwards


def test_dashboard_schedule(clock, monkeypatch):
    monkeypatch.setattr(refresh, "refresh_reasons", lambda: [])
    assert jobs.scheduled_action(at(10))[0] == "daily"          # opened in the morning: update now
    mark_done(at(10, 1))
    assert jobs.scheduled_action(at(15)) is None
    monkeypatch.setattr(refresh, "refresh_reasons", lambda: ["Waxpeer has no prices yet"])
    assert jobs.scheduled_action(at(10)) == ("refresh", "Waxpeer has no prices yet")   # one-off catch-ups
    assert jobs.seconds_to_next_hour(at(11, 59) + timedelta(seconds=30)) == pytest.approx(35)
    assert jobs.seconds_to_next_hour(at(12) + timedelta(seconds=5)) == pytest.approx(3600)


def test_offline_refresh_is_not_complete(client, monkeypatch):
    client.post("/api/items", json={"market_hash_name": "Recoil Case"}, headers=HEADERS)
    settings.set_value("enabled_markets", ["skinport", "marketcsgo", "waxpeer"])
    monkeypatch.setattr(currency, "refresh_rates", lambda: None)
    monkeypatch.setattr(SOURCES["skinport"], "sales", lambda: (_ for _ in ()).throw(ApiError("offline")))

    def offline(*args, **kwargs):
        raise ApiError("network error")

    for key in ("skinport", "marketcsgo", "waxpeer"):
        monkeypatch.setattr(SOURCES[key].client, "get", offline)
    # Market.CSGO has an old saved list: it's still used, but doesn't count as fresh.
    cache.set_cached(SOURCES["marketcsgo"]._cache_key(), [["Recoil Case", 0.5, 10, None]])
    db.execute("UPDATE api_cache SET fetched_at = 0")

    class Job:
        warnings: list = []
        cancelled = False
        def update(self, **kw): pass
        def warn(self, message): self.warnings.append(message)

    result = refresh.refresh_prices(Job())
    assert result["complete"] is False and result["fresh"] == []
    assert set(result["unreachable"]) == {"skinport", "marketcsgo", "waxpeer"}

    # Back online: one market answering is enough.
    monkeypatch.setattr(SOURCES["waxpeer"], "load_rows", lambda: [["Recoil Case", 0.6, 5, None]])
    result = refresh.refresh_prices(Job())
    assert result["complete"] is True and result["fresh"] == ["waxpeer"]
