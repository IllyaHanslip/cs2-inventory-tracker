"""HTTP 429 handling and the price-refresh job's behaviour when a market pushes back.
All network traffic is faked."""
import pytest

from backend import refresh
from backend.http_client import CancelledError, RateLimitedClient, RateLimitError
from backend.markets import Quote


class FakeResponse:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}


def make_client(monkeypatch, statuses, **kwargs):
    client = RateLimitedClient("Test API", min_interval=0, base_backoff=0.01, **kwargs)
    calls = []

    def fake_get(url, **_):
        calls.append(url)
        return FakeResponse(*statuses[min(len(calls), len(statuses)) - 1])

    monkeypatch.setattr(client.session, "get", fake_get)
    return client, calls


def test_retries_after_429_then_succeeds(monkeypatch):
    client, calls = make_client(monkeypatch, [(429, {"Retry-After": "0"}), (200,)], max_retries=2)
    messages = []
    client.listener = messages.append
    assert client.get("https://example.invalid").status_code == 200
    assert len(calls) == 2
    assert any("429" in m for m in messages)


def test_gives_up_after_max_retries(monkeypatch):
    client, calls = make_client(monkeypatch, [(429, {"Retry-After": "0"})], max_retries=1)
    with pytest.raises(RateLimitError):
        client.get("https://example.invalid")
    assert len(calls) == 2


def test_cancel_interrupts_backoff(monkeypatch):
    client, _ = make_client(monkeypatch, [(429, {"Retry-After": "60"})], max_retries=3)
    client.should_cancel = lambda: True
    # The 429 schedules a 60 s cool-down; cancelling must end the wait immediately.
    with pytest.raises(CancelledError):
        client.get("https://example.invalid")


class FakeJob:
    def __init__(self):
        self.cancelled = False
        self.warnings = []
        self.messages = []

    def update(self, message=None, progress=None, total=None):
        if message:
            self.messages.append(message)

    def step(self, key, label=None, done=None, total=None, note=None, finished=False):
        if note:
            self.messages.append(note)

    def warn(self, message):
        self.warnings.append(message)


class FlakySource:
    key = "csfloat"
    label = "CSFloat"
    bulk = False

    def __init__(self):
        self.calls = 0

    def available(self):
        return True, ""

    def fetch_one(self, name):
        self.calls += 1
        if self.calls == 1:
            return Quote(name, 1.0, "USD")
        raise RateLimitError("CSFloat is rate-limiting requests from this computer (HTTP 429).")


def test_refresh_stops_politely_on_rate_limit(app):
    import threading
    source = FlakySource()
    job = FakeJob()
    counter = {"done": 0, "lock": threading.Lock(), "limited": set()}
    found, warning = refresh._run_per_item_source(source, ["A", "B", "C"], job, counter)
    assert found == 1
    assert source.calls == 2            # did not keep hammering after the 429
    assert "slow down (HTTP 429) after 1 of 3 items" in warning and counter["limited"] == {"csfloat"}
    from backend import db
    checked = {r["market_hash_name"] for r in db.query("SELECT market_hash_name FROM price_checks")}
    assert checked == {"A"}             # B and C stay due, so they're retried next time


def test_a_market_that_asks_to_slow_down_is_left_alone_for_longer_each_time(app):
    from datetime import datetime, timezone
    assert refresh.resting_until("csfloat") is None
    hours = []
    for _ in range(6):
        until = refresh._after_run("csfloat", limited=True)
        hours.append(round((until - datetime.now(timezone.utc)).total_seconds() / 3600))
    assert hours == [1, 3, 6, 12, 24, 24]
    assert refresh.resting_until("csfloat") is not None
    refresh._after_run("csfloat", limited=False)          # a run without a 429 starts afresh
    assert refresh.resting_until("csfloat") is None


def hours_from_now(when):
    from datetime import datetime, timezone
    return round((when - datetime.now(timezone.utc)).total_seconds() / 3600)


def test_a_good_batch_before_the_limit_starts_the_wait_again(app):
    for _ in range(6):
        refresh._after_run("csfloat", limited=True)                     # refused early six times: a day
    assert hours_from_now(refresh._after_run("csfloat", True, found=184)) == 1   # 184 prices, then "slow down"
    assert hours_from_now(refresh._after_run("csfloat", True, found=3)) == 3     # then refused early: longer again


def test_never_asked_sooner_than_the_market_says(app):
    assert hours_from_now(refresh._after_run("csfloat", True, found=184, wait=5 * 3600)) == 5
    assert hours_from_now(refresh._after_run("csfloat", True, found=184, wait=30 * 86400)) == 24   # at most a day


def test_rest_times_say_which_day():
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 11, 26, 12, 0).astimezone()                    # a Thursday, local time
    assert refresh.local_time(now + timedelta(hours=5, minutes=28), now) == "17:28 today"
    assert refresh.local_time(now + timedelta(days=1, hours=5, minutes=28), now) == "17:28 tomorrow"
    assert refresh.local_time((now + timedelta(days=1)).astimezone(timezone.utc), now) == "12:00 tomorrow"
    assert refresh.local_time(now + timedelta(days=3), now) == "12:00 on Sunday"
    assert refresh.local_time(now + timedelta(days=10), now) == "12:00 on 6 Dec"


@pytest.mark.parametrize("reset", ["3600", "{epoch}", "{epoch_ms}"])
def test_an_allowance_that_refills_later_stops_at_once_and_is_kept(monkeypatch, reset):
    import time
    later = time.time() + 3600
    value = reset.format(epoch=int(later), epoch_ms=int(later * 1000))
    client, calls = make_client(monkeypatch, [(429, {"X-RateLimit-Reset": value})], max_retries=3)
    with pytest.raises(RateLimitError) as caught:
        client.get("https://example.invalid")
    assert len(calls) == 1                                             # no retries a few seconds later
    assert caught.value.retry_after == pytest.approx(3600, abs=5)


def test_a_refresh_after_a_good_batch_rests_an_hour_and_leaves_stickers_alone(app, monkeypatch):
    from config import Config
    from backend import comparables, currency, settings
    from backend.markets import SOURCES

    names = [f"Test Case {i}" for i in range(30)]
    monkeypatch.setattr(refresh, "tracked_names", lambda: names)
    monkeypatch.setattr(currency, "refresh_rates", lambda: None)
    monkeypatch.setattr(Config, "CSFLOAT_API_KEY", "test-key")
    settings.set_value("enabled_markets", ["csfloat"])
    settings.set_value("rest:csfloat", {"until": "2000-01-01T00:00:00+00:00", "level": 9})  # refused often before
    asked = []

    def fetch_one(name):
        asked.append(name)
        if len(asked) > 25:
            raise RateLimitError("CSFloat is rate-limiting requests from this computer (HTTP 429).")
        return Quote(name, 1.0, "USD")

    compared = []
    monkeypatch.setattr(SOURCES["csfloat"], "fetch_one", fetch_one)
    monkeypatch.setattr(comparables, "refresh_evidence", lambda job=None: compared.append(1) or {"checked": 0, "valued": 0})
    job = FakeJob()
    refresh.refresh_prices(job)
    assert len(asked) == 26 and hours_from_now(refresh.resting_until("csfloat")) == 1
    warning = next(w for w in job.warnings if "HTTP 429" in w)
    assert "after 25 of 30 items. It's left alone until" in warning and "longer each time" not in warning
    assert compared == []                                              # resting: no sticker comparisons either

    again = FakeJob()
    refresh.refresh_prices(again)                                      # straight away: CSFloat isn't asked
    assert len(asked) == 26 and compared == []
    assert any("left alone until" in w for w in again.warnings)


def test_comparing_stickers_leaves_a_resting_csfloat_alone(app, monkeypatch):
    from config import Config
    from backend import comparables
    from backend.markets import SOURCES

    monkeypatch.setattr(Config, "CSFLOAT_API_KEY", "test-key")
    monkeypatch.setattr(comparables, "sticker_ids", lambda: {})
    monkeypatch.setattr(comparables, "_key_stickers", lambda *args: [{"id": 7, "name": "Sticker | Test"}])
    asked = []

    def get(url, **kwargs):
        asked.append(url)
        raise RateLimitError("CSFloat is rate-limiting requests from this computer (HTTP 429).", retry_after=2 * 3600)

    monkeypatch.setattr(SOURCES["csfloat"].client, "get", get)
    item = {"market_hash_name": "AK-47 | Redline (Field-Tested)", "stickers": [{"name": "Sticker | Test"}]}
    with pytest.raises(RateLimitError, match="left alone until"):     # a 429 while comparing starts a rest
        comparables.find_comparables(item, None, refresh=True)
    assert len(asked) == 1 and hours_from_now(refresh.resting_until("csfloat")) == 2
    with pytest.raises(RateLimitError, match="Compare again after that"):
        comparables.find_comparables(item, None, refresh=True)
    assert len(asked) == 1                                             # while resting, CSFloat isn't asked
