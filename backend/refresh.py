"""Price refresh: pulls quotes from every enabled market and stores them."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from config import Config

from . import comparables, currency, db, history, images, settings
from .classify import applied_hash_name
from .http_client import ApiError, AuthError, CancelledError, RateLimitError
from .markets import SOURCES, MarketSource, Quote

log = logging.getLogger(__name__)


# Items that can never be listed on a market - don't spend rate-limited requests on them.
NEVER_LISTED = {"Storage Unit"}


def tracked_names() -> list[str]:
    """Every item name we need a price for, including applied stickers/patches/charms."""
    names = {r["market_hash_name"] for r in db.query(
        "SELECT DISTINCT market_hash_name FROM items WHERE marketable = 1 OR source = 'manual'"
    )}
    names.update(applied_hash_name(r["kind"], r["name"]) for r in db.query("SELECT DISTINCT kind, name FROM item_stickers"))
    return sorted(n for n in names if n and n not in NEVER_LISTED)


def save_quotes(market: str, quotes: Iterable[Quote], *, manual: bool = False) -> None:
    quotes = list(quotes)
    if not quotes:
        return
    now, day = db.utcnow_iso(), history.today()
    db.executemany(
        "INSERT INTO market_prices (market_hash_name, market, price, currency, volume, url, is_manual, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(market_hash_name, market) DO UPDATE SET "
        "price = excluded.price, currency = excluded.currency, volume = excluded.volume, url = excluded.url, "
        "is_manual = excluded.is_manual, fetched_at = excluded.fetched_at",
        [(q.market_hash_name, market, q.price, q.currency, q.volume, q.url, int(manual), now) for q in quotes],
    )
    db.executemany(
        "INSERT INTO price_snapshots (market_hash_name, market, snapshot_date, price, currency) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(market_hash_name, market, snapshot_date) DO UPDATE SET price = excluded.price, "
        "currency = excluded.currency",
        [(q.market_hash_name, market, day, q.price, q.currency) for q in quotes],
    )


def drop_quotes(market: str, names: list[str]) -> None:
    """Forget quotes for items a market no longer lists, so stale prices don't linger."""
    for i in range(0, len(names), 800):
        chunk = names[i:i + 800]
        db.execute(
            f"DELETE FROM market_prices WHERE market = ? AND market_hash_name IN ({','.join('?' * len(chunk))})",
            (market, *chunk),
        )


def _mark_checked(market: str, *names: str) -> None:
    now = db.utcnow_iso()
    db.executemany(
        "INSERT INTO price_checks (market_hash_name, market, checked_at) VALUES (?, ?, ?) "
        "ON CONFLICT(market_hash_name, market) DO UPDATE SET checked_at = excluded.checked_at",
        [(name, market, now) for name in names],
    )


def _note_attempt(market: str) -> None:
    attempts = settings.get("market_attempts") or {}
    attempts[market] = db.utcnow_iso()
    settings.set_value("market_attempts", attempts)


def _hours_since(iso: str | None) -> float:
    if not iso:
        return float("inf")
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 3600
    except ValueError:
        return float("inf")


def refresh_reasons() -> list[str]:
    """Why a price refresh is due right now (empty list = prices are fine).
    Used at start-up and by the hourly scheduler, so new markets/keys fill in without waiting a day."""
    if not db.query_one("SELECT 1 FROM items LIMIT 1"):
        return []
    prefs = settings.get_all()
    attempts = settings.get("market_attempts") or {}
    have = {r["market"] for r in db.query("SELECT DISTINCT market FROM market_prices WHERE is_manual = 0")}
    reasons = []
    for key in prefs["enabled_markets"]:
        source = SOURCES[key]
        if source.available()[0] and key not in have and _hours_since(attempts.get(key)) > Config.PRICE_TTL_HOURS:
            reasons.append(f"{source.label} has no prices yet")
    newest = db.query_one("SELECT MAX(fetched_at) AS t FROM market_prices WHERE is_manual = 0")["t"]
    if newest and _hours_since(newest) > Config.PRICE_TTL_HOURS:
        reasons.append(f"prices are more than {Config.PRICE_TTL_HOURS:g} hours old")
    return reasons


def _stale(market: str, names: list[str], force: bool) -> list[str]:
    if force:
        return names
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=Config.PRICE_TTL_HOURS)).replace(microsecond=0).isoformat()
    fresh = {r["market_hash_name"] for r in db.query(
        "SELECT market_hash_name FROM price_checks WHERE market = ? AND checked_at >= ?", (market, cutoff)
    )}
    return [n for n in names if n not in fresh]


def _run_per_item_source(source: MarketSource, todo: list[str], job, counter: dict) -> tuple[int, str | None]:
    """Fetch one item at a time from a throttled source. Returns (prices found, warning)."""
    client = getattr(source, "client", None)
    if client is not None:
        client.listener = lambda message: job.update(message=message)
        client.should_cancel = lambda: job.cancelled
    try:
        return _fetch_items(source, todo, job, counter)
    finally:
        if client is not None:
            client.listener = client.should_cancel = None


def _fetch_items(source: MarketSource, todo: list[str], job, counter: dict) -> tuple[int, str | None]:
    found = errors = 0
    for index, name in enumerate(todo):
        if job.cancelled:
            return found, f"{source.label}: cancelled after {index} of {len(todo)} items."
        job.update(message=f"{source.label}: {name} ({index + 1}/{len(todo)})")
        try:
            quote = source.fetch_one(name)
        except CancelledError:
            return found, f"{source.label}: cancelled after {index} of {len(todo)} items."
        except AuthError as exc:
            return found, str(exc)
        except RateLimitError as exc:
            done = f" after {index} of {len(todo)} items" if index else ""
            return found, f"{exc} Stopped{done}; the rest are retried on the next refresh."
        except ApiError as exc:
            errors += 1
            if errors >= 5:
                return found, f"{source.label}: stopped after repeated errors ({exc})."
            quote = None
        else:
            if quote:
                save_quotes(source.key, [quote])
                found += 1
            else:
                drop_quotes(source.key, [name])
            _mark_checked(source.key, name)
        with counter["lock"]:
            counter["done"] += 1
            job.update(progress=counter["done"])
    return found, None


def refresh_prices(job, force: bool = False) -> dict[str, Any]:
    prefs = settings.get_all()
    job.update(message="Updating exchange rates…")
    currency.refresh_rates()

    names = tracked_names()
    if not names:
        job.update(message="No items to price yet.")
        return {"names": 0, "quotes": {}, "warnings": []}

    found: dict[str, int] = {}
    per_item: list[MarketSource] = []
    for key in prefs["enabled_markets"]:
        source = SOURCES[key]
        usable, reason = source.available()
        if not usable:
            job.warn(f"{source.label} skipped: {reason}.")
            continue
        _note_attempt(key)
        if not source.bulk:
            per_item.append(source)
            continue
        job.update(message=f"Fetching {source.label} prices…")
        try:
            quotes = source.fetch_bulk(set(names))
        except ApiError as exc:
            job.warn(f"{source.label}: {exc}")
            continue
        save_quotes(key, quotes.values())
        drop_quotes(key, [n for n in names if n not in quotes])
        _mark_checked(key, *names)
        found[key] = len(quotes)

    work = {s.key: _stale(s.key, names, force) for s in per_item}
    counter = {"done": 0, "lock": threading.Lock()}
    job.update(progress=0, total=sum(len(todo) for todo in work.values()))
    if any(work.values()):
        with ThreadPoolExecutor(max_workers=len(per_item)) as pool:  # one thread per market
            futures = {s.key: pool.submit(_run_per_item_source, s, work[s.key], job, counter) for s in per_item}
            for key, future in futures.items():
                count, warning = future.result()
                found[key] = count
                if warning:
                    job.warn(warning)

    # Items whose stickers are worth more than the skin: value them from CSFloat comparables.
    sticker_evidence = comparables.refresh_evidence(job)

    job.update(message="Finding pictures for items added by hand…")
    images.backfill()

    job.update(message="Saving today's snapshot…")
    snapshot = history.take_snapshot()
    job.update(message="Prices updated.")
    return {"names": len(names), "quotes": found, "sticker_evidence": sticker_evidence, "snapshot": snapshot}
