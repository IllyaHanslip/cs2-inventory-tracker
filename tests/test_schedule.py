"""When the daily update runs: never before 12:00, once a day, retried hourly if the markets are unreachable."""
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


def test_due_from_midday_until_done(clock):
    assert not jobs.daily_update_due(at(9))                     # never before 12:00
    assert not jobs.daily_update_due(at(11, 59))
    assert jobs.daily_update_due(at(12))                        # at 12:00 if the PC is on
    assert jobs.daily_update_due(at(17, 30))                    # or whenever it's switched on later
    mark_done(at(9, 30))                                        # an update this morning doesn't count
    assert jobs.daily_update_due(at(12))
    mark_done(at(12, 3))
    assert not jobs.daily_update_due(at(12, 30)) and not jobs.daily_update_due(at(23))
    assert not jobs.daily_update_due(at(9, days=1)) and jobs.daily_update_due(at(12, days=1))   # tomorrow


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
    assert not jobs.background_update_running()                 # the marker is cleared afterwards


def test_dashboard_schedule(clock, monkeypatch):
    monkeypatch.setattr(refresh, "refresh_reasons", lambda: [])
    assert jobs.scheduled_action(at(10)) is None
    assert jobs.scheduled_action(at(12))[0] == "daily"
    mark_done(at(12, 1))
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
