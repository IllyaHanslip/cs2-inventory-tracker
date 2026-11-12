"""Tiny persistent JSON cache backed by the ``api_cache`` table."""
from __future__ import annotations

import json
import time
from typing import Any

from . import db


def get_cached(key: str, max_age_seconds: float) -> Any | None:
    row = db.query_one("SELECT fetched_at, payload FROM api_cache WHERE cache_key = ?", (key,))
    if row and time.time() - row["fetched_at"] <= max_age_seconds:
        return json.loads(row["payload"])
    return None


def get_stale(key: str) -> Any | None:
    """Return cached data regardless of age (used as a fallback when an API is down)."""
    row = db.query_one("SELECT payload FROM api_cache WHERE cache_key = ?", (key,))
    return json.loads(row["payload"]) if row else None


def set_cached(key: str, payload: Any) -> None:
    db.execute(
        "INSERT INTO api_cache (cache_key, fetched_at, payload) VALUES (?, ?, ?) "
        "ON CONFLICT(cache_key) DO UPDATE SET fetched_at = excluded.fetched_at, payload = excluded.payload",
        (key, time.time(), json.dumps(payload, separators=(",", ":"))),
    )
