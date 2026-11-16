"""Steam Market prices from CSGOTrader's daily price file (network faked): the app never asks Steam
itself for prices, downloads the file at most a few times a day, and only again when it has changed."""
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone

from backend import cache, currency, db, refresh, settings
from backend.markets import SOURCES
from backend.markets.steam_market import FEED_URL, pick_average
from backend.valuation import Valuator
from tests.conftest import HEADERS

FEED = {
    "Recoil Case": {"last_24h": 0.47, "last_7d": 0.49, "last_30d": 0.55, "last_90d": 0.56},
    "Sticker | Crown (Foil)": {"last_24h": None, "last_7d": 460.43, "last_30d": 484.62, "last_90d": 506.82},
    "Never Sold": {"last_24h": None, "last_7d": None, "last_30d": None, "last_90d": None},
}


class FakeResponse:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


def serve(monkeypatch, *responses):
    """The Steam source's client answers with these responses in turn; returns the requests made."""
    source = SOURCES["steam"]
    calls = []

    def get(url, params=None, headers=None):
        calls.append({"url": url, "headers": headers or {}})
        return responses[min(len(calls), len(responses)) - 1]

    monkeypatch.setattr(source.client, "get", get)
    return source, calls


def published(days_ago=0):
    return format_datetime(datetime.now(timezone.utc) - timedelta(days=days_ago), usegmt=True)


def test_most_recent_average_is_used():
    assert pick_average(FEED["Recoil Case"]) == (0.47, "24 hours")
    assert pick_average(FEED["Sticker | Crown (Foil)"]) == (460.43, "7 days")
    assert pick_average(FEED["Never Sold"]) is None
    assert pick_average("nonsense") is None


def test_prices_come_from_the_file_not_from_steam(app, monkeypatch):
    source, calls = serve(monkeypatch, FakeResponse(200, FEED, {"ETag": '"v1"', "Last-Modified": published()}))
    quotes = source.fetch_bulk({"Recoil Case", "Sticker | Crown (Foil)", "Never Sold", "Not In File"})
    assert [c["url"] for c in calls] == [FEED_URL]
    assert "steamcommunity.com" not in FEED_URL
    assert set(quotes) == {"Recoil Case", "Sticker | Crown (Foil)"}
    crown = quotes["Sticker | Crown (Foil)"]
    assert (crown.price, crown.currency, crown.sold_price, crown.sold_period) == (460.43, "USD", 460.43, "7 days")
    assert crown.url.startswith("https://steamcommunity.com/market/listings/730/")
    source.fetch_bulk({"Recoil Case"})
    assert len(calls) == 1                     # within a few hours: the saved copy is used
    assert source.note is None


def test_downloads_again_only_when_the_file_has_changed(app, monkeypatch):
    source, calls = serve(monkeypatch, FakeResponse(200, FEED, {"ETag": '"v1"', "Last-Modified": published()}),
                          FakeResponse(304))
    source.fetch_bulk({"Recoil Case"})
    db.execute("UPDATE api_cache SET fetched_at = 0")        # hours later
    quotes = source.fetch_bulk({"Recoil Case"})
    assert len(calls) == 2
    assert calls[1]["headers"]["If-None-Match"] == '"v1"' and "If-Modified-Since" in calls[1]["headers"]
    assert quotes["Recoil Case"].price == 0.47             # unchanged, so the saved copy is kept
    assert cache.get_cached(source._cache_key(), 60) is not None   # ...and counts as fresh again


def test_says_so_when_the_file_stops_being_updated(app, monkeypatch):
    source, _ = serve(monkeypatch, FakeResponse(200, FEED, {"ETag": '"v1"', "Last-Modified": published(days_ago=5)}))
    source.fetch_bulk({"Recoil Case"})
    assert source.note and "5 days" in source.note


def test_unreachable_file_keeps_the_last_prices(app, monkeypatch):
    source, _ = serve(monkeypatch, FakeResponse(200, FEED, {"ETag": '"v1"'}), FakeResponse(503))
    source.fetch_bulk({"Recoil Case"})
    db.execute("UPDATE api_cache SET fetched_at = 0")
    assert source.fetch_bulk({"Recoil Case"})["Recoil Case"].price == 0.47
    assert source.stale is True


def test_refresh_saves_steam_sales_and_forgets_items_that_stop_selling(client, monkeypatch):
    for name in ("Recoil Case", "Sticker | Crown (Foil)"):
        client.post("/api/items", json={"market_hash_name": name}, headers=HEADERS)
    settings.set_value("enabled_markets", ["steam"])
    monkeypatch.setattr(currency, "refresh_rates", lambda: None)
    serve(monkeypatch, FakeResponse(200, FEED, {"ETag": '"v1"'}),
          FakeResponse(200, {"Recoil Case": FEED["Recoil Case"]}, {"ETag": '"v2"'}))

    class Job:
        cancelled = False
        def __init__(self): self.warnings = []
        def update(self, **kw): pass
        def step(self, *a, **kw): pass
        def warn(self, message): self.warnings.append(message)

    result = refresh.refresh_prices(Job())
    assert result["quotes"]["steam"] == 2 and result["fresh"] == ["steam"]
    sales = {r["market_hash_name"]: dict(r) for r in db.query("SELECT * FROM sale_prices WHERE source = 'steam'")}
    assert sales["Sticker | Crown (Foil)"]["period"] == "7 days" and sales["Sticker | Crown (Foil)"]["volume"] is None
    valuator = Valuator()
    assert valuator.sell_capacity("Recoil Case", "steam") is None   # no sale count: speed unknown, not "none"
    item = next(i for i in client.get("/api/items?scope=total").get_json()["items"] if i["market_hash_name"] == "Recoil Case")
    assert item["sell_quotes"][0]["market"] == "steam"
    quotes = client.get("/api/quotes?name=Recoil Case").get_json()["quotes"]
    assert quotes[0]["note"] == "average sale, last 24 hours"

    db.execute("UPDATE api_cache SET fetched_at = 0")        # next day: the Crown no longer sells
    refresh.refresh_prices(Job())
    left = {r["market_hash_name"] for r in db.query("SELECT market_hash_name FROM sale_prices WHERE source = 'steam'")}
    assert left == {"Recoil Case"}
    missing = client.get("/api/quotes?name=Sticker | Crown (Foil)").get_json()["missing"]
    assert missing == [{"market": "steam", "label": "Steam Market", "status": "No Steam Market sales in the last 90 days"}]
