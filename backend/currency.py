"""Daily exchange rates and currency normalisation.

Rates are stored per day as "units of X per 1 USD", so converting any currency
A to B is ``amount / rate[A] * rate[B]`` using the rates of the relevant day.
Historical snapshots are converted with the rates of their own date.

Sources (no API key needed):
  * Frankfurter (European Central Bank reference rates) - primary
  * open.er-api.com (ExchangeRate-API open access)      - fallback, and used instead when the ECB's
    latest rates are more than a day old (weekends, holidays) and it has newer ones

The rates are checked again with every price update (at most every half hour), not just once a
day: the ECB publishes each working day's rates in the afternoon, so an update in the morning would
otherwise go on using the day before's all day.
"""
from __future__ import annotations

import bisect
import logging
from datetime import date, datetime, timedelta, timezone

from . import cache, db
from .http_client import ApiError, RateLimitedClient

log = logging.getLogger(__name__)

FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"
ER_API_URL = "https://open.er-api.com/v6/latest/USD"

_client = RateLimitedClient("Exchange rates", min_interval=1.0, max_retries=2)


CHECK_MINUTES = 30          # rates are checked again at most this often (each price update)


def _from_frankfurter() -> tuple[dict[str, float], str, str | None]:
    resp = _client.get(FRANKFURTER_URL, params={"base": "USD"})
    if resp.status_code != 200:
        raise ApiError(f"Frankfurter returned HTTP {resp.status_code}")
    data = resp.json()
    return dict(data["rates"]), f"Frankfurter / ECB ({data.get('date', 'latest')})", data.get("date")


def _from_er_api() -> tuple[dict[str, float], str, str | None]:
    resp = _client.get(ER_API_URL)
    if resp.status_code != 200:
        raise ApiError(f"open.er-api.com returned HTTP {resp.status_code}")
    data = resp.json()
    if data.get("result") != "success":
        raise ApiError("open.er-api.com returned an error")
    stamp = data.get("time_last_update_unix")
    day = datetime.fromtimestamp(stamp, timezone.utc).date().isoformat() if isinstance(stamp, (int, float)) else None
    return dict(data["rates"]), f"ExchangeRate-API (open.er-api.com, {day or 'latest'})", day


def _fetch_latest() -> tuple[dict[str, float], str]:
    """The newest rates available: the ECB's (Frankfurter), or ExchangeRate-API's when the ECB's
    are more than a day old and it has newer ones (or the ECB's can't be reached)."""
    errors, found = [], []
    for fetch in (_from_frankfurter, _from_er_api):
        try:
            found.append(fetch())
        except (ApiError, KeyError, ValueError, TypeError) as exc:
            errors.append(str(exc))
            continue
        day = found[-1][2]
        if day and day >= (date.today() - timedelta(days=1)).isoformat():
            break                                    # recent enough: no need to ask the other
    if not found:
        raise ApiError("; ".join(errors))
    rates, source, _day = max(found, key=lambda f: f[2] or "")   # the newest (the ECB's when tied)
    return rates, source


def refresh_rates(force: bool = False) -> dict[str, float]:
    """Check for newer rates (at most every CHECK_MINUTES, or when forced) and store them as today's."""
    today = date.today().isoformat()
    checked = cache.get_cached("fx_last_check", CHECK_MINUTES * 60) is not None
    if not force and checked and db.query_one("SELECT 1 FROM fx_rates WHERE rate_date = ?", (today,)):
        return FxConverter.load().rates_for(today)

    try:
        rates, source = _fetch_latest()
    except ApiError as exc:
        log.warning("Could not refresh exchange rates: %s", exc)
        return FxConverter.load().rates_for(today)  # fall back to the latest stored rates
    finally:
        cache.set_cached("fx_last_check", {"at": datetime.now(timezone.utc).isoformat()})

    rates["USD"] = 1.0
    rows = [(today, code.upper(), float(rate), source) for code, rate in rates.items() if rate and float(rate) > 0]
    db.executemany(
        "INSERT INTO fx_rates (rate_date, currency, per_usd, source) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(rate_date, currency) DO UPDATE SET per_usd = excluded.per_usd, source = excluded.source",
        rows,
    )
    cache.set_cached("fx_last_source", {"date": today, "source": source})
    return {code: rate for _, code, rate, _ in rows}


def last_source() -> dict | None:
    return cache.get_stale("fx_last_source")


class FxConverter:
    """In-memory view of all stored rates for fast, date-aware conversions."""

    def __init__(self, rows: list[dict]):
        self._by_date: dict[str, dict[str, float]] = {}
        for row in rows:
            self._by_date.setdefault(row["rate_date"], {})[row["currency"]] = row["per_usd"]
        self._dates = sorted(self._by_date)

    @classmethod
    def load(cls) -> "FxConverter":
        return cls(db.query("SELECT rate_date, currency, per_usd FROM fx_rates"))

    @property
    def latest_date(self) -> str | None:
        return self._dates[-1] if self._dates else None

    def rates_for(self, on_date: str | None = None) -> dict[str, float]:
        """Rates of the latest day on or before ``on_date`` (or the earliest known day)."""
        if not self._dates:
            return {"USD": 1.0}
        if on_date is None:
            return self._by_date[self._dates[-1]]
        idx = bisect.bisect_right(self._dates, on_date[:10]) - 1
        return self._by_date[self._dates[max(idx, 0)]]

    def currencies(self) -> list[str]:
        return sorted(self.rates_for())

    def convert(self, amount: float | None, from_ccy: str, to_ccy: str, on_date: str | None = None) -> float | None:
        if amount is None:
            return None
        from_ccy, to_ccy = (from_ccy or "").upper(), (to_ccy or "").upper()
        if from_ccy == to_ccy:
            return float(amount)
        rates = self.rates_for(on_date)
        if from_ccy not in rates or to_ccy not in rates:
            return None
        return float(amount) / rates[from_ccy] * rates[to_ccy]
