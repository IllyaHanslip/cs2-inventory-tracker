"""Undo for the last change you made (removing an item, deleting a container, confirming a change
from a Steam sync, deleting a Profit & Loss entry...).

Before such a change, the tables it can touch are copied; the dashboard gets a token for that copy
and shows an Undo button. Undoing puts the tables back exactly as they were. Only the most recent
change can be undone, and only while nothing else in those tables has changed since (another edit,
a Steam sync - even one run by the daily task): putting old tables back then would quietly throw
that away too, so it's refused instead.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from typing import Any

from . import db

# Everything an undoable change can touch. Parents first (rows are put back in this order).
TABLES = ("items", "item_stickers", "item_evidence", "inventory_changes", "ledger")
# What must be unchanged for an undo to be safe (CSFloat sticker evidence is refreshed by itself,
# and putting back the copy from a few minutes earlier is harmless).
WATCHED = ("items", "item_stickers", "inventory_changes", "ledger")
KEEP_SECONDS = 15 * 60   # long after the Undo button has gone; Ctrl+Z works until something else changes

_lock = threading.Lock()
_latest: dict[str, Any] | None = None   # {"token", "label", "tables", "at"}


def capture() -> dict[str, Any]:
    """A copy of every undoable table, as it is now."""
    with db.connect() as conn:
        tables = {}
        for table in TABLES:
            cursor = conn.execute(f"SELECT * FROM {table}")
            tables[table] = ([d[0] for d in cursor.description], cursor.fetchall())
    return {"tables": {name: (cols, [tuple(r) for r in rows]) for name, (cols, rows) in tables.items()}}


def _fingerprint() -> str:
    digest = hashlib.sha256()
    with db.connect() as conn:
        for table in WATCHED:
            for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid"):
                digest.update(repr(tuple(row)).encode())
            digest.update(b"|")
    return digest.hexdigest()


def remember(snapshot: dict[str, Any], label: str) -> str:
    """Keep ``snapshot`` (taken before the change just made) as the one Undo puts back; returns its token."""
    global _latest
    token = secrets.token_urlsafe(12)
    entry = {**snapshot, "token": token, "label": label, "at": time.monotonic(), "after": _fingerprint()}
    with _lock:
        _latest = entry
    return token


def restore(token: str) -> str:
    """Put the tables back as they were before the change ``token`` belongs to. Returns its label."""
    global _latest
    with _lock:
        saved = _latest
        if not saved or saved["token"] != token or time.monotonic() - saved["at"] > KEEP_SECONDS:
            raise ValueError("That can't be undone any more: something else has changed since.")
        if _fingerprint() != saved["after"]:
            _latest = None
            raise ValueError("That can't be undone any more: your items have changed since (another edit or a Steam sync).")
        _latest = None
    conn = db._open()
    try:
        conn.execute("PRAGMA foreign_keys = OFF")   # rows go back with their own ids, in any order
        conn.execute("BEGIN")
        for table in reversed(TABLES):
            conn.execute(f"DELETE FROM {table}")
        for table in TABLES:
            columns, rows = saved["tables"][table]
            if rows:
                conn.executemany(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                                 rows)
        conn.commit()
    except Exception:
        conn.rollback()
        with _lock:
            _latest = saved   # nothing changed: it can still be undone
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()
    return saved["label"]
