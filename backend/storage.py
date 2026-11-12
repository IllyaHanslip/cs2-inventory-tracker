"""Item and container operations: stacking, the 1,000-item capacity, partial moves and removals.

Every function takes an open connection so a whole request runs in one transaction.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from config import Config

from . import db

CAPACITY = Config.CONTAINER_CAPACITY
STORAGE_UNIT = "Storage Unit"

# Columns copied when an item is split (partial move).
_COPY_COLUMNS = (
    "source", "steam_id", "market_hash_name", "name", "category", "item_type", "weapon", "rarity", "rarity_color",
    "exterior", "float_value", "paint_seed", "name_tag", "icon_url", "inspect_link", "tradable", "marketable",
    "custom_price", "custom_price_currency", "purchase_price", "purchase_currency", "notes", "created_at",
)


# ---------------------------------------------------------------------------
# Rules & capacity
# ---------------------------------------------------------------------------
def contents_count(conn: sqlite3.Connection, container_id: int, exclude_id: int | None = None) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(quantity), 0) FROM items WHERE container_id = ? AND id != ?",
        (container_id, exclude_id or -1),
    ).fetchone()
    return int(row[0])


def check_capacity(conn: sqlite3.Connection, container_id: int, adding: int, exclude_id: int | None = None) -> None:
    used = contents_count(conn, container_id, exclude_id)
    if used + adding > CAPACITY:
        room = max(CAPACITY - used, 0)
        raise ValueError(
            f"A container holds up to {CAPACITY:,} items. This one already has {used:,}, "
            f"so you can add {room:,} more."
        )


def check_placement(conn: sqlite3.Connection, item_id: int | None, item: dict[str, Any], container_id: int | None) -> None:
    """Can ``item`` (merged old+new values) be stored in ``container_id``?"""
    if item.get("category") != "container" and item_id is not None:
        if conn.execute("SELECT 1 FROM items WHERE container_id = ?", (item_id,)).fetchone():
            raise ValueError("This container still holds items. Move them out before changing its category.")
    if container_id is None:
        return
    if item.get("source") == "steam":
        raise ValueError("Steam-synced items can't be placed inside containers.")
    if item_id is not None and container_id == item_id:
        raise ValueError("An item can't be stored inside itself.")
    # One level deep: a case can sit in a storage unit, but nothing can hold items while stored.
    if item_id is not None and conn.execute("SELECT 1 FROM items WHERE container_id = ?", (item_id,)).fetchone():
        raise ValueError("This container holds items, so it can't be stored inside another container.")
    target = conn.execute("SELECT category, container_id FROM items WHERE id = ?", (container_id,)).fetchone()
    if not target or target["category"] != "container":
        raise ValueError("The chosen container does not exist.")
    if target["container_id"] is not None:
        raise ValueError("That container is itself stored inside another container. Choose a top-level container.")


def mark_storage(conn: sqlite3.Connection, container_id: int | None) -> None:
    """A container that holds items is a storage container (shown on the Containers tab)."""
    if container_id is not None:
        conn.execute("UPDATE items SET is_storage = 1 WHERE id = ?", (container_id,))


# ---------------------------------------------------------------------------
# Stacking
# ---------------------------------------------------------------------------
def _has_stickers(conn: sqlite3.Connection, item_id: int) -> bool:
    return conn.execute("SELECT 1 FROM item_stickers WHERE item_id = ?", (item_id,)).fetchone() is not None


def _merged_purchase(target: sqlite3.Row, values: dict[str, Any]) -> tuple[bool, float | None, str | None]:
    """Weighted-average purchase price, or (False, ...) when the two can't be combined."""
    old_price, new_price = target["purchase_price"], values.get("purchase_price")
    if old_price is None and new_price is None:
        return True, None, None
    if old_price is not None and new_price is not None and (target["purchase_currency"] or "") == (values.get("purchase_currency") or ""):
        total = target["quantity"] + values["quantity"]
        return True, (old_price * target["quantity"] + new_price * values["quantity"]) / total, target["purchase_currency"]
    return False, None, None


def find_stack(conn: sqlite3.Connection, values: dict[str, Any], has_stickers: bool,
               exclude_id: int | None = None) -> sqlite3.Row | None:
    """An existing plain manual entry the new one can be stacked onto (same item, same place)."""
    plain = not has_stickers and all(
        values.get(key) in (None, "") for key in ("float_value", "paint_seed", "name_tag", "custom_price", "notes")
    )
    # Cases stack like any other item; storage containers (and anything holding items) never do.
    if not plain or values.get("source", "manual") != "manual" or values.get("is_storage"):
        return None
    rows = conn.execute(
        "SELECT * FROM items WHERE source = 'manual' AND market_hash_name = ? AND category = ? "
        "AND IFNULL(container_id, 0) = IFNULL(?, 0) AND id != ? AND float_value IS NULL AND paint_seed IS NULL "
        "AND name_tag IS NULL AND custom_price IS NULL AND is_storage = 0 "
        "AND NOT EXISTS (SELECT 1 FROM item_stickers s WHERE s.item_id = items.id) "
        "AND NOT EXISTS (SELECT 1 FROM items c WHERE c.container_id = items.id) ORDER BY id",
        (values["market_hash_name"], values.get("category"), values.get("container_id"), exclude_id or -1),
    ).fetchall()
    for row in rows:
        if _merged_purchase(row, values)[0]:
            return row
    return None


def add_item(conn: sqlite3.Connection, values: dict[str, Any], stickers: list[dict[str, Any]]) -> tuple[int, bool]:
    """Insert a manual item, or stack it onto a matching entry. Returns (item id, stacked?)."""
    target = find_stack(conn, values, bool(stickers))
    if target is not None:
        _, price, currency = _merged_purchase(target, values)
        conn.execute(
            "UPDATE items SET quantity = quantity + ?, purchase_price = ?, purchase_currency = ?, updated_at = ? "
            "WHERE id = ?",
            (values["quantity"], price, currency, db.utcnow_iso(), target["id"]),
        )
        return target["id"], True
    cols = list(values)
    item_id = conn.execute(
        f"INSERT INTO items ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [values[c] for c in cols]
    ).lastrowid
    conn.executemany(
        "INSERT INTO item_stickers (item_id, slot, kind, name, scrape) VALUES (?, ?, ?, ?, ?)",
        [(item_id, s["slot"], s["kind"], s["name"], s["scrape"]) for s in stickers],
    )
    return item_id, False


# ---------------------------------------------------------------------------
# Moving & removing
# ---------------------------------------------------------------------------
def move(conn: sqlite3.Connection, item: dict[str, Any], container_id: int | None, quantity: int | None = None) -> int:
    """Move all or part of a stack into a container (or out to the main inventory)."""
    qty = item["quantity"] if quantity is None else quantity
    if not 1 <= qty <= item["quantity"]:
        raise ValueError(f"You can move between 1 and {item['quantity']} of this item.")
    if container_id == item["container_id"]:
        return item["id"]
    check_placement(conn, item["id"], item, container_id)
    if container_id is not None:
        check_capacity(conn, container_id, qty)
        mark_storage(conn, container_id)

    stickers = [dict(r) for r in conn.execute(
        "SELECT slot, kind, name, scrape FROM item_stickers WHERE item_id = ?", (item["id"],))]
    now = db.utcnow_iso()
    moving = {col: item.get(col) for col in _COPY_COLUMNS}
    moving.update(quantity=qty, container_id=container_id, updated_at=now)

    if qty == item["quantity"]:
        target = find_stack(conn, moving, bool(stickers), exclude_id=item["id"])
        if target is None:
            conn.execute("UPDATE items SET container_id = ?, updated_at = ? WHERE id = ?", (container_id, now, item["id"]))
            return item["id"]
        _, price, currency = _merged_purchase(target, moving)
        conn.execute("UPDATE items SET quantity = quantity + ?, purchase_price = ?, purchase_currency = ?, "
                     "updated_at = ? WHERE id = ?", (qty, price, currency, now, target["id"]))
        conn.execute("DELETE FROM items WHERE id = ?", (item["id"],))
        return target["id"]

    conn.execute("UPDATE items SET quantity = quantity - ?, updated_at = ? WHERE id = ?", (qty, now, item["id"]))
    new_id, _ = add_item(conn, moving, stickers)
    return new_id


def remove(conn: sqlite3.Connection, item: dict[str, Any], quantity: int | None = None,
           contents: str = "keep") -> int:
    """Remove some or all of a manual stack. Returns how many remain (0 = entry deleted).
    For a container, ``contents`` decides whether stored items are kept (moved out) or deleted."""
    if item["source"] == "steam":
        raise ValueError("Steam items are removed automatically when they leave your inventory. Re-sync instead.")
    qty = item["quantity"] if quantity is None else quantity
    if not 1 <= qty <= item["quantity"]:
        raise ValueError(f"You can remove between 1 and {item['quantity']} of this item.")
    if qty < item["quantity"]:
        conn.execute("UPDATE items SET quantity = quantity - ?, updated_at = ? WHERE id = ?",
                     (qty, db.utcnow_iso(), item["id"]))
        return item["quantity"] - qty
    clear_contents(conn, item["id"], contents)
    conn.execute("DELETE FROM items WHERE id = ?", (item["id"],))
    return 0


def clear_contents(conn: sqlite3.Connection, container_id: int, contents: str) -> int:
    """Delete everything in a container, or move it all out to the main inventory (stacking as it goes)."""
    rows = [dict(r) for r in conn.execute("SELECT * FROM items WHERE container_id = ?", (container_id,))]
    if contents == "delete":
        conn.execute("DELETE FROM items WHERE container_id = ?", (container_id,))
    else:
        for row in rows:
            move(conn, row, None)
    return sum(r["quantity"] for r in rows)
