"""Stored market evidence for items with valuable stickers (filled in by comparables.py,
read by the valuation). Kept separate so the valuation never makes network calls."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db

VALID_DAYS = 7       # evidence older than this is no longer used for valuation
RECHECK_HOURS = 24   # the refresh job re-checks evidence older than this


def signature(stickers: list[dict[str, Any]]) -> str:
    """Identifies the set of applied stickers the evidence was gathered for."""
    return "|".join(sorted(f"{s['kind']}:{s['name']}" for s in stickers))


def load_all() -> dict[int, dict[str, Any]]:
    rows = db.query("SELECT * FROM item_evidence")
    return {row["item_id"]: {**row, "notes": json.loads(row["notes"] or "[]")} for row in rows}


def save(item_id: int, sig: str, value: float | None, currency: str | None, basis: str, notes: list[str]) -> None:
    db.execute(
        "INSERT INTO item_evidence (item_id, signature, value, currency, basis, notes, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(item_id) DO UPDATE SET signature = excluded.signature, "
        "value = excluded.value, currency = excluded.currency, basis = excluded.basis, notes = excluded.notes, "
        "fetched_at = excluded.fetched_at",
        (item_id, sig, value, currency, basis, json.dumps(notes), db.utcnow_iso()),
    )


def _age(row: dict[str, Any]) -> timedelta:
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(row["fetched_at"])
    except (TypeError, ValueError):
        return timedelta(days=365)


def usable(row: dict[str, Any] | None, stickers: list[dict[str, Any]]) -> bool:
    """Evidence applies if it was measured for these exact stickers, found a value, and is recent."""
    return bool(row and row["value"] and row["signature"] == signature(stickers)
                and _age(row) <= timedelta(days=VALID_DAYS))


def due(row: dict[str, Any] | None, stickers: list[dict[str, Any]]) -> bool:
    return not row or row["signature"] != signature(stickers) or _age(row) > timedelta(hours=RECHECK_HOURS)
