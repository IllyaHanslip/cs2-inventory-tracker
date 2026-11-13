"""Items moved into or out of storage units, noticed by Steam syncs.

Steam's public inventory can't see inside a storage unit, but it does show items leaving your
inventory and coming back, and each unit's description says how many items it holds. After a
sync, whatever left or came back is matched up with the units whose counts moved:

    15 Clutch Cases and a P250 left your inventory, and "Long Holds" went from 63 to 79
    -> put them in Long Holds?

Items that left while no unit grew were most likely sold or traded. Nothing changes until you
confirm on the Containers tab, and only the public inventory is read: no login, ever.
"""
from __future__ import annotations

import json
from typing import Any

from . import db, images, storage
from .classify import categorize

# What you've recorded about an item yourself: carried over wherever it goes.
USER_FIELDS = ("purchase_price", "purchase_currency", "custom_price", "custom_price_currency", "notes")
KEPT_FIELDS = ("name", "category", "item_type", "weapon", "rarity", "rarity_color", "exterior", "float_value",
               "paint_seed", "name_tag", "icon_url", *USER_FIELDS)
MAX_EXACT = 60          # more separate items than this: skip the "which ones add up exactly" search
ACTIONS = {"left": ("store", "gone"), "back": ("take_out", "keep")}


# ---------------------------------------------------------------------------
# Detecting (called by the Steam sync, inside its transaction)
# ---------------------------------------------------------------------------
def detect(conn, left: list[dict], arrived: list[dict], units: list[dict], now: str) -> int:
    """Record what moved. ``left``: Steam items that disappeared (with their stickers); ``arrived``:
    new Steam items; ``units``: storage units with Steam's item count before and after.
    Returns how many changes are waiting for you."""
    left, arrived = _reissued(conn, left, arrived)
    arrived = _returned(conn, arrived, now)
    if not left and not arrived:
        return 0
    info = {u["id"]: u for u in units}
    # Only when every unit's count is known before and after can "no unit grew" mean "sold". (The
    # first sync after updating has no earlier counts saved yet: then you're asked instead.)
    counts_known = bool(units) and all(u["after"] is not None and u["before"] is not None for u in units)
    deltas = {u["id"]: u["after"] - u["before"] for u in units if u["after"] is not None and u["before"] is not None}
    found: list[tuple] = []

    # Came back out of a storage unit (so it's no longer in there, and would be counted twice).
    taken: dict[int, int] = {}
    for group in _groups(arrived):
        source = _source_unit(conn, group, deltas, taken, counts_known, bool(left))
        if source:
            uid, qty, sure = source
            taken[uid] = taken.get(uid, 0) + qty
            found.append(("back", group, qty, "take_out", uid, sure))

    # Went into a storage unit, or was sold / traded. The units that grew say which: look for the
    # items whose numbers add up exactly to each unit's growth.
    groups = sorted(_groups(left), key=lambda g: (g["unique"], -g["quantity"]))
    remaining = list(range(len(groups)))
    placed: dict[int, tuple[int, bool]] = {}
    exact = True
    growth = sorted(((uid, delta + taken.get(uid, 0)) for uid, delta in deltas.items()), key=lambda x: -x[1])
    for uid, grew in growth:
        if grew <= 0 or not remaining:
            continue
        subset, unique = _exact_subset([groups[i]["quantity"] for i in remaining], grew)
        if subset is not None:
            picks = [remaining[k] for k in subset]
        else:
            picks, room = [], grew              # nothing adds up exactly: the biggest that fit
            for i in remaining:
                if groups[i]["quantity"] <= room:
                    picks.append(i)
                    room -= groups[i]["quantity"]
        exact = exact and subset is not None and unique
        for i in picks:
            placed[i] = (uid, subset is not None and unique)
        remaining = [i for i in remaining if i not in placed]
    for i, group in enumerate(groups):
        if i in placed:
            uid, sure = placed[i]
            found.append(("left", group, group["quantity"], "store", uid, sure))
        elif counts_known:
            found.append(("left", group, group["quantity"], "gone", None, exact))   # no unit grew for it
        else:
            found.append(("left", group, group["quantity"], None, None, False))    # can't tell: you choose

    for kind, group, qty, suggestion, uid, sure in found:
        details = dict(group["details"])
        if kind == "back":
            details["steam_item_ids"] = [r["id"] for r in group["rows"]]
        unit = info.get(uid) or {}
        conn.execute(
            "INSERT INTO inventory_changes (kind, detected_at, market_hash_name, quantity, details, suggestion, "
            "container_id, unit_before, unit_after, sure) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, now, group["market_hash_name"], qty, json.dumps(details), suggestion, uid,
             unit.get("before"), unit.get("after"), int(sure)),
        )
    return len(found)


def _unique(row: dict) -> bool:
    return (row.get("float_value") is not None or row.get("paint_seed") is not None
            or bool(row.get("stickers")) or bool(row.get("name_tag")))


def _same_float(a, b) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) < 1e-9


def _reissued(conn, left: list[dict], arrived: list[dict]) -> tuple[list[dict], list[dict]]:
    """Applying a sticker or a name tag gives an item a new asset id: it's the same item (same
    float), not a move. Keep what you recorded about it (price paid, notes...)."""
    gone, new = set(), set()
    for i, old in enumerate(left):
        if old.get("float_value") is None:
            continue
        for j, item in enumerate(arrived):
            if j in new or item["market_hash_name"] != old["market_hash_name"]:
                continue
            if _same_float(item.get("float_value"), old["float_value"]) and item.get("paint_seed") == old.get("paint_seed"):
                keep = {f: old[f] for f in USER_FIELDS if old.get(f) is not None}
                if keep:
                    conn.execute(f"UPDATE items SET {', '.join(f'{f} = ?' for f in keep)} WHERE id = ?",
                                 (*keep.values(), item["id"]))
                gone.add(i)
                new.add(j)
                break
    return [r for i, r in enumerate(left) if i not in gone], [r for j, r in enumerate(arrived) if j not in new]


def _returned(conn, arrived: list[dict], now: str) -> list[dict]:
    """Items back in your inventory before you'd confirmed where they went: forget that change."""
    rest = []
    for item in arrived:
        qty = int(item.get("quantity") or 1)
        for change in conn.execute("SELECT * FROM inventory_changes WHERE status = 'pending' AND kind = 'left' "
                                   "AND market_hash_name = ? ORDER BY id", (item["market_hash_name"],)).fetchall():
            details = json.loads(change["details"])
            if _unique(item) != _unique({**details, "stickers": details.get("stickers")}):
                continue
            if details.get("float_value") is not None and not _same_float(details["float_value"], item.get("float_value")):
                continue
            take = min(qty, change["quantity"])
            if take == change["quantity"]:
                conn.execute("UPDATE inventory_changes SET status = 'dismissed', resolution = 'returned', resolved_at = ? "
                             "WHERE id = ?", (now, change["id"]))
            else:
                conn.execute("UPDATE inventory_changes SET quantity = quantity - ? WHERE id = ?", (take, change["id"]))
            keep = {f: details[f] for f in USER_FIELDS if details.get(f) is not None}
            if keep and _unique(item):
                conn.execute(f"UPDATE items SET {', '.join(f'{f} = ?' for f in keep)} WHERE id = ?",
                             (*keep.values(), item["id"]))
            qty -= take
            if qty <= 0:
                break
        if qty > 0:
            rest.append({**item, "quantity": qty})
    return rest


def _groups(rows: list[dict]) -> list[dict]:
    """Plain copies of an item together (15 Clutch Cases); anything with a float, stickers or a
    name tag on its own."""
    groups: list[dict] = []
    plain: dict[tuple, dict] = {}
    for row in rows:
        details = {f: row.get(f) for f in KEPT_FIELDS}
        details["stickers"] = [{k: s.get(k) for k in ("slot", "kind", "name", "scrape")} for s in row.get("stickers") or []]
        qty = int(row.get("quantity") or 1)
        if _unique(row):
            groups.append({"market_hash_name": row["market_hash_name"], "quantity": qty, "details": details,
                           "unique": True, "rows": [row]})
            continue
        key = (row["market_hash_name"], *(row.get(f) for f in USER_FIELDS))
        if key in plain:
            plain[key]["quantity"] += qty
            plain[key]["rows"].append(row)
        else:
            plain[key] = {"market_hash_name": row["market_hash_name"], "quantity": qty, "details": details,
                          "unique": False, "rows": [row]}
            groups.append(plain[key])
    return groups


def _exact_subset(quantities: list[int], target: int) -> tuple[list[int] | None, bool]:
    """Which of these quantities add up to exactly ``target``, and whether that's the only way.
    Earlier entries are preferred when there's more than one."""
    n = len(quantities)
    if target <= 0 or not n or n > MAX_EXACT or sum(quantities) < target:
        return None, False
    ways = [[0] * (target + 1) for _ in range(n + 1)]     # ways[i][s]: from the first i, capped at 2
    ways[0][0] = 1
    for i, q in enumerate(quantities, start=1):
        before, row = ways[i - 1], ways[i]
        for s in range(target + 1):
            row[s] = min(before[s] + (before[s - q] if s >= q else 0), 2)
    if not ways[n][target]:
        return None, False
    chosen, s = [], target
    for i in range(n, 0, -1):
        if ways[i - 1][s]:
            continue                                      # possible without this one: leave it
        chosen.append(i - 1)
        s -= quantities[i - 1]
    return sorted(chosen), ways[n][target] == 1


def _source_unit(conn, group: dict, deltas: dict[int, int], taken: dict[int, int], counts_known: bool,
                 anything_left: bool) -> tuple[int, int, bool] | None:
    """The storage unit this probably came out of: one listing it whose count fell. Without counts
    from Steam, only an exact match (same float) is suggested, so buying more cases isn't mistaken
    for taking some out."""
    rows = conn.execute(
        "SELECT i.container_id AS unit, i.quantity, i.float_value FROM items i JOIN items c ON c.id = i.container_id "
        "WHERE i.market_hash_name = ? AND i.source = 'manual' AND c.is_storage = 1", (group["market_hash_name"],)
    ).fetchall()
    listed: dict[int, int] = {}
    for row in rows:
        if group["unique"] and group["details"].get("float_value") is not None and row["float_value"] is not None \
                and not _same_float(row["float_value"], group["details"]["float_value"]):
            continue
        listed[row["unit"]] = listed.get(row["unit"], 0) + row["quantity"]
    best = None
    for uid, qty_listed in sorted(listed.items(), key=lambda x: -x[1]):
        if counts_known and uid in deltas:
            out = -deltas[uid] - taken.get(uid, 0)
            if out > 0:
                return uid, min(group["quantity"], qty_listed, out), True
            if anything_left and best is None:
                best = (uid, min(group["quantity"], qty_listed), False)   # in and out in one go: maybe
        elif not counts_known and group["unique"] and any(
                _same_float(r["float_value"], group["details"].get("float_value")) for r in rows if r["unit"] == uid):
            return uid, min(group["quantity"], qty_listed), False
    return best


# ---------------------------------------------------------------------------
# Reviewing and confirming
# ---------------------------------------------------------------------------
def storage_units() -> list[dict[str, Any]]:
    """Every top-level storage unit, with how many items are listed in it and Steam's own count."""
    rows = db.query(
        "SELECT u.id, u.market_hash_name, u.name_tag, u.source, u.steam_count, "
        "(SELECT COALESCE(SUM(c.quantity), 0) FROM items c WHERE c.container_id = u.id) AS listed "
        "FROM items u WHERE u.category = 'container' AND u.container_id IS NULL "
        "AND (u.is_storage = 1 OR EXISTS (SELECT 1 FROM items c WHERE c.container_id = u.id)) ORDER BY u.id"
    )
    for row in rows:
        row["label"] = row["name_tag"] or row["market_hash_name"]
    return rows


def pending() -> dict[str, Any]:
    """Changes waiting for you, and the storage units they could go into."""
    from .valuation import Valuator

    rows = db.query("SELECT * FROM inventory_changes WHERE status = 'pending' ORDER BY kind DESC, detected_at DESC, id")
    units = storage_units()
    labels = {u["id"]: u["label"] for u in units}
    best = Valuator().best_net if rows else {}
    found = images.lookup({r["market_hash_name"] for r in rows})
    changes = []
    for row in rows:
        details = json.loads(row["details"])
        unit_price = best.get(row["market_hash_name"])
        changes.append({
            "id": row["id"], "kind": row["kind"], "detected_at": row["detected_at"],
            "market_hash_name": row["market_hash_name"], "name": details.get("name") or row["market_hash_name"],
            "quantity": row["quantity"], "exterior": details.get("exterior"), "float_value": details.get("float_value"),
            "name_tag": details.get("name_tag"), "stickers": [s["name"] for s in details.get("stickers") or []],
            "icon": images.icon_src(details.get("icon_url") or found.get(row["market_hash_name"])),
            "value": unit_price * row["quantity"] if unit_price is not None else None,
            "suggestion": row["suggestion"], "container_id": row["container_id"],
            "container_label": labels.get(row["container_id"]), "unit_before": row["unit_before"],
            "unit_after": row["unit_after"], "sure": bool(row["sure"]),
        })
    return {"changes": changes, "units": units}


def pending_count() -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM inventory_changes WHERE status = 'pending'")
    return int(row["n"]) if row else 0


def apply(change_id: int, action: str, container_id: int | None = None) -> dict[str, Any]:
    """Confirm one change: put it in a storage unit / it was sold or traded (items that left),
    or take it off a unit's list / keep it listed (items that came back)."""
    now = db.utcnow_iso()
    with db.connect() as conn:
        change = conn.execute("SELECT * FROM inventory_changes WHERE id = ?", (change_id,)).fetchone()
        if not change:
            raise ValueError("That change isn't there any more - reload the page.")
        if change["status"] != "pending":
            raise ValueError("That change has already been dealt with.")
        if action not in ACTIONS[change["kind"]]:
            raise ValueError("That isn't something you can do with this change.")
        details = json.loads(change["details"])
        unit = int(container_id) if container_id else change["container_id"]
        if action == "store":
            if not unit:
                raise ValueError("Choose the storage unit they went into.")
            _store(conn, change, details, unit, now)
        elif action == "take_out":
            _take_out(conn, change, details, unit)
        done = action in ("store", "take_out")
        conn.execute(
            "UPDATE inventory_changes SET status = ?, resolution = ?, container_id = ?, resolved_at = ? WHERE id = ?",
            ("applied" if done else "dismissed", action, unit if done else change["container_id"], now, change_id),
        )
    if action == "store":
        images.ensure([change["market_hash_name"]])
    return {"id": change_id, "action": action, "container_id": unit}


def apply_suggested() -> dict[str, Any]:
    """Confirm every change that has a suggestion, as suggested."""
    applied, problems = 0, []
    for row in db.query("SELECT id, suggestion FROM inventory_changes WHERE status = 'pending' AND suggestion IS NOT NULL "
                        "ORDER BY id"):
        try:
            apply(row["id"], row["suggestion"])
            applied += 1
        except ValueError as error:
            problems.append(str(error))
    return {"applied": applied, "problems": problems, "pending": pending_count()}


def _store(conn, change, details: dict, unit: int, now: str) -> None:
    target = conn.execute("SELECT category, is_storage, container_id FROM items WHERE id = ?", (unit,)).fetchone()
    if not target or target["category"] != "container" or not target["is_storage"]:
        raise ValueError("Choose one of your storage units.")
    name = change["market_hash_name"]
    values: dict[str, Any] = {
        "source": "manual", "market_hash_name": name, "name": details.get("name") or name,
        "category": details.get("category") or categorize(name), "quantity": change["quantity"],
        "container_id": unit, "marketable": 1, "tradable": 1, "created_at": now, "updated_at": now,
    }
    for field in KEPT_FIELDS:
        if field not in values and details.get(field) is not None:
            values[field] = details[field]
    storage.check_placement(conn, None, values, unit)
    storage.check_capacity(conn, unit, change["quantity"])
    stickers = [{"slot": s.get("slot") if s.get("slot") is not None else i, "kind": s["kind"], "name": s["name"],
                 "scrape": s.get("scrape") or 0} for i, s in enumerate(details.get("stickers") or [])]
    storage.add_item(conn, values, stickers)


def _take_out(conn, change, details: dict, unit: int | None) -> None:
    if not unit:
        raise ValueError("Choose the storage unit they came out of.")
    float_value = details.get("float_value")
    rows = conn.execute(
        "SELECT * FROM items WHERE container_id = ? AND market_hash_name = ? AND source = 'manual' "
        "ORDER BY (float_value IS NOT NULL AND ABS(float_value - ?) < 1e-9) DESC, (float_value IS NULL) DESC, id",
        (unit, change["market_hash_name"], float_value if float_value is not None else -1.0),
    ).fetchall()
    if not rows:
        raise ValueError(f"{change['market_hash_name']} isn't listed in that storage unit any more.")
    remaining = change["quantity"]
    for row in rows:
        if remaining <= 0:
            break
        take = min(row["quantity"], remaining)
        storage.remove(conn, dict(row), take)
        remaining -= take
