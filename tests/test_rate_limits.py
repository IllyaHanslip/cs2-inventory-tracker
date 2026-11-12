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

    def warn(self, message):
        self.warnings.append(message)


class FlakySource:
    key = "steam"
    label = "Steam Market"
    bulk = False

    def __init__(self):
        self.calls = 0

    def available(self):
        return True, ""

    def fetch_one(self, name):
        self.calls += 1
        if self.calls == 1:
            return Quote(name, 1.0, "USD")
        raise RateLimitError("Steam Market is rate-limiting requests from this computer (HTTP 429).")


def test_refresh_stops_politely_on_rate_limit(app):
    import threading
    source = FlakySource()
    job = FakeJob()
    found, warning = refresh._run_per_item_source(source, ["A", "B", "C"], job, {"done": 0, "lock": threading.Lock()})
    assert found == 1
    assert source.calls == 2            # did not keep hammering after the 429
    assert "rate-limiting" in warning and "next refresh" in warning
    from backend import db
    checked = {r["market_hash_name"] for r in db.query("SELECT market_hash_name FROM price_checks")}
    assert checked == {"A"}             # B and C stay due, so they're retried next time
