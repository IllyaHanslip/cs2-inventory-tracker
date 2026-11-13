"""SQLite access layer: schema creation and small query helpers.

Each call opens its own short-lived connection, which keeps things simple and
safe when background jobs and web requests run on different threads.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    source                TEXT NOT NULL CHECK (source IN ('steam', 'manual')),
    steam_id              TEXT,
    asset_id              TEXT,
    market_hash_name      TEXT NOT NULL,
    name                  TEXT NOT NULL,
    category              TEXT NOT NULL DEFAULT 'other',
    item_type             TEXT,
    weapon                TEXT,
    rarity                TEXT,
    rarity_color          TEXT,
    exterior              TEXT,
    float_value           REAL,
    paint_seed            INTEGER,
    name_tag              TEXT,
    quantity              INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    container_id          INTEGER REFERENCES items(id) ON DELETE SET NULL,
    is_storage            INTEGER NOT NULL DEFAULT 0,
    icon_url              TEXT,
    inspect_link          TEXT,
    tradable              INTEGER NOT NULL DEFAULT 1,
    marketable            INTEGER NOT NULL DEFAULT 1,
    custom_price          REAL,
    custom_price_currency TEXT,
    purchase_price        REAL,
    purchase_currency     TEXT,
    notes                 TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_items_steam_asset
    ON items (steam_id, asset_id) WHERE source = 'steam';
CREATE INDEX IF NOT EXISTS ix_items_container ON items (container_id);
CREATE INDEX IF NOT EXISTS ix_items_name ON items (market_hash_name);

CREATE TABLE IF NOT EXISTS item_stickers (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    slot    INTEGER NOT NULL DEFAULT 0,
    kind    TEXT NOT NULL DEFAULT 'sticker' CHECK (kind IN ('sticker', 'patch', 'charm')),
    name    TEXT NOT NULL,
    scrape  REAL NOT NULL DEFAULT 0 CHECK (scrape >= 0 AND scrape <= 1)
);
CREATE INDEX IF NOT EXISTS ix_stickers_item ON item_stickers (item_id);

-- Latest known quote per item per market (manual quotes use market 'manual:<label>').
CREATE TABLE IF NOT EXISTS market_prices (
    market_hash_name TEXT NOT NULL,
    market           TEXT NOT NULL,
    price            REAL NOT NULL,
    currency         TEXT NOT NULL,
    volume           INTEGER,
    url              TEXT,
    is_manual        INTEGER NOT NULL DEFAULT 0,
    fetched_at       TEXT NOT NULL,
    PRIMARY KEY (market_hash_name, market)
);

-- When each per-item source was last asked about an item (even if it had no listing),
-- so the refresh job respects the cache TTL instead of hammering the API.
CREATE TABLE IF NOT EXISTS price_checks (
    market_hash_name TEXT NOT NULL,
    market           TEXT NOT NULL,
    checked_at       TEXT NOT NULL,
    PRIMARY KEY (market_hash_name, market)
);

-- Daily item-level price history. market = 'valuation' holds the blended price
-- used for portfolio maths; other rows are raw per-market quotes.
CREATE TABLE IF NOT EXISTS price_snapshots (
    market_hash_name TEXT NOT NULL,
    market           TEXT NOT NULL,
    snapshot_date    TEXT NOT NULL,
    price            REAL NOT NULL,
    currency         TEXT NOT NULL,
    PRIMARY KEY (market_hash_name, market, snapshot_date)
);
CREATE INDEX IF NOT EXISTS ix_snapshots_name_date ON price_snapshots (market_hash_name, snapshot_date);

-- total_value is what you'd receive selling everything after fees (basis 'after_fees');
-- market_value is what it would cost to buy. Rows saved by older versions (basis 'market')
-- are converted once at start-up, see history.convert_market_snapshots().
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    snapshot_date TEXT NOT NULL,
    scope         TEXT NOT NULL CHECK (scope IN ('public', 'total')),
    total_value   REAL NOT NULL,
    item_count    INTEGER NOT NULL,
    currency      TEXT NOT NULL,
    market_value  REAL,
    basis         TEXT NOT NULL DEFAULT 'market',
    screened      INTEGER NOT NULL DEFAULT 0,   -- 1 once unrealistic listings are kept out (sanity.py)
    PRIMARY KEY (snapshot_date, scope)
);

-- Daily FX rates, stored as units of `currency` per 1 USD.
CREATE TABLE IF NOT EXISTS fx_rates (
    rate_date TEXT NOT NULL,
    currency  TEXT NOT NULL,
    per_usd   REAL NOT NULL CHECK (per_usd > 0),
    source    TEXT,
    PRIMARY KEY (rate_date, currency)
);

-- What CSFloat comparables say an item with valuable stickers is worth (see comparables.py).
CREATE TABLE IF NOT EXISTS item_evidence (
    item_id    INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    signature  TEXT NOT NULL,      -- the stickers it was measured with
    value      REAL,               -- NULL = no usable comparables were found
    currency   TEXT,
    basis      TEXT,
    notes      TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_cache (
    cache_key  TEXT PRIMARY KEY,
    fetched_at REAL NOT NULL,
    payload    TEXT NOT NULL
);

-- Recent sale prices (what items actually sold for, not asking prices), used to spot
-- unrealistic listings - see sanity.py. One row per item and source.
CREATE TABLE IF NOT EXISTS sale_prices (
    market_hash_name TEXT NOT NULL,
    source           TEXT NOT NULL,   -- 'skinport' (sales history) or 'steam' (median sale price)
    price            REAL NOT NULL,
    currency         TEXT NOT NULL,
    volume           INTEGER,         -- number of sales behind the price
    period           TEXT,            -- e.g. '30 days'
    fetched_at       TEXT NOT NULL,
    PRIMARY KEY (market_hash_name, source)
);

-- Pictures for manually added items, by market name (see images.py).
CREATE TABLE IF NOT EXISTS item_images (
    market_hash_name TEXT PRIMARY KEY,
    icon             TEXT NOT NULL,   -- Steam image hash, or a full Steam CDN URL
    source           TEXT NOT NULL    -- catalogue file it came from
);

-- What a Steam sync noticed: items that left your Steam inventory (moved into a storage unit,
-- or sold / traded) and items that came back out of a storage unit, waiting for you to confirm.
CREATE TABLE IF NOT EXISTS inventory_changes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    kind             TEXT NOT NULL CHECK (kind IN ('left', 'back')),
    detected_at      TEXT NOT NULL,
    market_hash_name TEXT NOT NULL,
    quantity         INTEGER NOT NULL CHECK (quantity > 0),
    details          TEXT NOT NULL,            -- JSON: the item as it was (wear, float, stickers, your notes...)
    suggestion       TEXT,                     -- 'store', 'gone', 'take_out', or NULL when unsure
    container_id     INTEGER,                  -- the storage unit it probably went into / came out of
    unit_before      INTEGER,                  -- that unit's item count from Steam, before and after
    unit_after       INTEGER,
    sure             INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'pending',   -- pending / applied / dismissed
    resolution       TEXT,                     -- what you chose: store / gone / take_out / keep / returned
    resolved_at      TEXT
);
CREATE INDEX IF NOT EXISTS ix_changes_status ON inventory_changes (status);
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _open() -> sqlite3.Connection:
    Config.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(Config.DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, roll back on error, always close."""
    conn = _open()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring databases created by older versions up to date (safe to run every start)."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    if "is_storage" not in columns:
        # Storage containers are the ones you keep items in (as opposed to unopened cases).
        conn.execute("ALTER TABLE items ADD COLUMN is_storage INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            "UPDATE items SET is_storage = 1 WHERE category = 'container' AND (market_hash_name = 'Storage Unit' "
            "OR id IN (SELECT container_id FROM items WHERE container_id IS NOT NULL))"
        )
    if "steam_count" not in columns:
        # How many items Steam says a storage unit holds (if a public inventory ever includes it).
        conn.execute("ALTER TABLE items ADD COLUMN steam_count INTEGER")
    if "game_count" not in columns:
        # The item count CS2 shows for a storage unit, typed in by you (public inventories hide
        # storage units), and when: moves confirmed after that keep it up to date.
        conn.execute("ALTER TABLE items ADD COLUMN game_count INTEGER")
        conn.execute("ALTER TABLE items ADD COLUMN game_count_at TEXT")
    snapshot_columns = {row[1] for row in conn.execute("PRAGMA table_info(portfolio_snapshots)")}
    if "basis" not in snapshot_columns:
        conn.execute("ALTER TABLE portfolio_snapshots ADD COLUMN market_value REAL")
        conn.execute("ALTER TABLE portfolio_snapshots ADD COLUMN basis TEXT NOT NULL DEFAULT 'market'")
    if "screened" not in snapshot_columns:
        conn.execute("ALTER TABLE portfolio_snapshots ADD COLUMN screened INTEGER NOT NULL DEFAULT 0")


def query(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple | list = ()) -> int:
    """Run a write statement and return lastrowid."""
    with connect() as conn:
        return conn.execute(sql, params).lastrowid


def executemany(sql: str, rows: list[tuple]) -> None:
    if not rows:
        return
    with connect() as conn:
        conn.executemany(sql, rows)
