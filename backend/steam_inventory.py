"""Public Steam inventory synchronisation for CS2 (app 730, context 2).

Uses the public community endpoint ``/inventory/<steamid64>/730/2`` - the same
data Steam shows on a public inventory page. No login or cookies are used, so
only inventories whose privacy setting is "Public" can be read.
"""
from __future__ import annotations

import html
import logging
import re
from typing import Any

from config import Config

from . import db, moves, settings
from .classify import categorize
from .http_client import ApiError, RateLimitedClient

log = logging.getLogger(__name__)

INVENTORY_URL = "https://steamcommunity.com/inventory/{steam_id}/730/2"
# Public profile data as XML - resolves /id/<name> links without any API key.
PROFILE_XML_URL = "https://steamcommunity.com/id/{vanity}/"
_STEAMID_XML_RE = re.compile(r"<steamID64>\s*(\d{17})\s*</steamID64>")
PAGE_SIZE = 2000
MAX_PAGES = 25

STEAMID64_RE = re.compile(r"^7656119\d{10}$")
_PROFILE_RE = re.compile(r"steamcommunity\.com/profiles/(\d{17})")
_VANITY_RE = re.compile(r"steamcommunity\.com/id/([^/?#]+)")
_APPLIED_TITLE_RE = re.compile(r'title="(Sticker|Patch|Charm):\s*([^"]+)"', re.IGNORECASE)
_APPLIED_TEXT_RE = re.compile(r"<br>\s*(Sticker|Patch|Charm)s?:\s*(.+?)\s*</center>", re.IGNORECASE | re.DOTALL)
_NAME_TAG_RE = re.compile(r"Name Tag:\s*''(.*)''")
# A storage unit's description includes how many items it holds, e.g. "Number of Items: 84".
_UNIT_COUNT_RE = re.compile(r"Number of Items:\s*([\d,.\s]+)", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_PLACEHOLDER_RE = re.compile(r"%[a-z_]+(?::\d+)?%")

_inventory_client = RateLimitedClient(
    "Steam inventory", min_interval=Config.STEAM_INVENTORY_MIN_INTERVAL, base_backoff=30, max_retries=2
)
_profile_client = RateLimitedClient("Steam profile lookup", min_interval=2.0, base_backoff=30, max_retries=1)


# ---------------------------------------------------------------------------
# SteamID handling
# ---------------------------------------------------------------------------
def resolve_steam_id(value: str) -> str:
    """Accept a SteamID64, a /profiles/<id> URL or a /id/<vanity> URL/name."""
    text = (value or "").strip()
    if STEAMID64_RE.match(text):
        return text
    match = _PROFILE_RE.search(text)
    if match and STEAMID64_RE.match(match.group(1)):
        return match.group(1)

    match = _VANITY_RE.search(text)
    vanity = match.group(1) if match else (text if re.fullmatch(r"[A-Za-z0-9_-]{2,32}", text) else None)
    if not vanity or not re.fullmatch(r"[A-Za-z0-9_-]{2,32}", vanity):
        raise ValueError("Enter a SteamID64 (17 digits starting with 7656119) or a Steam profile URL.")
    resp = _profile_client.get(PROFILE_XML_URL.format(vanity=vanity), params={"xml": 1})
    if resp.status_code != 200:
        raise ApiError(f"Steam returned HTTP {resp.status_code} while looking up that profile. "
                       "Try pasting your 17-digit SteamID64 instead.", resp.status_code)
    found = _STEAMID_XML_RE.search(resp.text)
    if found and STEAMID64_RE.match(found.group(1)):
        return found.group(1)
    raise ValueError(f"Could not find a Steam profile called '{vanity}'. Check the link, or paste your SteamID64.")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def parse_applied(descriptions: list[dict] | None) -> list[dict[str, Any]]:
    """Extract applied stickers, patches and charms from an item's description HTML."""
    found: list[tuple[str, str]] = []
    for desc in descriptions or []:
        value = desc.get("value") or ""
        if not any(marker in value for marker in ("sticker_info", "keychain_info", "patch_info")) and desc.get(
            "name"
        ) not in ("sticker_info", "keychain_info"):
            continue
        titles = _APPLIED_TITLE_RE.findall(value)
        if titles:
            found.extend(titles)
            continue
        text = _APPLIED_TEXT_RE.search(value)
        if text:
            found.extend((text.group(1), part) for part in text.group(2).split(", ") if part.strip())

    applied, slots = [], {}
    for kind, name in found:
        kind = kind.lower()
        slot = slots.get(kind, 0)
        slots[kind] = slot + 1
        applied.append({"kind": kind, "name": html.unescape(name).strip(), "slot": slot, "scrape": 0.0})
    return applied


def _parse_properties(props: list[dict] | None) -> tuple[float | None, int | None]:
    """Read wear (float) and pattern seed from ``asset_properties`` when Steam supplies them."""
    float_value, seed = None, None
    for prop in props or []:
        name = str(prop.get("name") or "").lower()
        pid = str(prop.get("propertyid", ""))
        if "wear" in name or "float" in name or (not name and pid == "2"):
            try:
                value = float(prop.get("float_value", prop.get("string_value")))
                if 0 <= value <= 1:
                    float_value = value
            except (TypeError, ValueError):
                pass
        elif "pattern" in name or "seed" in name or (not name and pid == "1"):
            try:
                seed = int(float(prop.get("int_value", prop.get("string_value"))))
            except (TypeError, ValueError):
                pass
    return float_value, seed


def parse_unit_count(descriptions: list[dict] | None) -> int | None:
    """How many items a storage unit holds, when Steam includes it in the description."""
    for desc in descriptions or []:
        text = _TAG_RE.sub(" ", html.unescape(str(desc.get("value") or "")))
        match = _UNIT_COUNT_RE.search(text)
        if match:
            digits = re.sub(r"\D", "", match.group(1))
            if digits:
                return int(digits)
    return None


def _parse_name_tag(warnings: list[str] | None) -> str | None:
    for warning in warnings or []:
        match = _NAME_TAG_RE.search(warning)
        if match:
            return html.unescape(match.group(1))
    return None


def _inspect_link(desc: dict, asset: dict, steam_id: str) -> str | None:
    for action in desc.get("actions") or []:
        link = action.get("link") or ""
        if link.startswith("steam://"):
            link = link.replace("%owner_steamid%", steam_id).replace("%assetid%", str(asset.get("assetid")))
            return None if _PLACEHOLDER_RE.search(link) else link
    return None


def parse_asset(asset: dict, desc: dict | None, props: list | None, steam_id: str) -> dict[str, Any] | None:
    if not desc:
        return None
    market_hash_name = desc.get("market_hash_name") or desc.get("market_name") or desc.get("name")
    if not market_hash_name:
        return None
    tags = {tag.get("category"): tag for tag in desc.get("tags") or []}
    type_tag = tags.get("Type", {})
    rarity_tag = tags.get("Rarity", {})
    float_value, seed = _parse_properties(props)
    color = rarity_tag.get("color")
    return {
        "asset_id": str(asset["assetid"]),
        "steam_id": steam_id,
        "market_hash_name": market_hash_name,
        "name": desc.get("market_name") or desc.get("name") or market_hash_name,
        "category": categorize(market_hash_name, type_tag.get("internal_name")),
        "item_type": type_tag.get("localized_tag_name"),
        "weapon": tags.get("Weapon", {}).get("localized_tag_name"),
        "rarity": rarity_tag.get("localized_tag_name"),
        "rarity_color": f"#{color}" if color and re.fullmatch(r"[0-9A-Fa-f]{6}", color) else None,
        "exterior": tags.get("Exterior", {}).get("localized_tag_name"),
        "float_value": float_value,
        "paint_seed": seed,
        "name_tag": _parse_name_tag(desc.get("fraudwarnings")),
        "quantity": max(1, int(asset.get("amount", 1) or 1)),
        "icon_url": desc.get("icon_url_large") or desc.get("icon_url"),
        "inspect_link": _inspect_link(desc, asset, steam_id),
        "tradable": int(bool(desc.get("tradable"))),
        "marketable": int(bool(desc.get("marketable"))),
        "stickers": parse_applied(desc.get("descriptions")),
        "storage_count": parse_unit_count(desc.get("descriptions")) if market_hash_name == "Storage Unit" else None,
    }


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
def fetch_inventory(steam_id: str, job=None) -> list[dict[str, Any]]:
    assets: list[dict] = []
    descriptions: dict[tuple[str, str], dict] = {}
    properties: dict[str, list] = {}
    start_assetid = None

    for page in range(MAX_PAGES):
        if job:
            job.update(message=f"Downloading Steam inventory (page {page + 1})…")
        params: dict[str, Any] = {"l": "english", "count": PAGE_SIZE}
        if start_assetid:
            params["start_assetid"] = start_assetid
        resp = _inventory_client.get(
            INVENTORY_URL.format(steam_id=steam_id),
            params=params,
            headers={"Referer": f"https://steamcommunity.com/profiles/{steam_id}/inventory"},
        )
        if resp.status_code == 403:
            raise ApiError(
                "Steam returned 403 Forbidden: this profile or its inventory is private. "
                "Set Steam > Edit Profile > Privacy Settings > Inventory to Public, then try again.",
                status=403,
            )
        if resp.status_code in (400, 404):
            raise ApiError("Steam could not find an inventory for that SteamID.", status=resp.status_code)
        if resp.status_code != 200:
            raise ApiError(f"Steam inventory request failed (HTTP {resp.status_code}). Try again later.", resp.status_code)
        try:
            data = resp.json()
        except ValueError:
            raise ApiError("Steam returned an unreadable response. Try again later.") from None
        if not data:
            raise ApiError("Steam returned no inventory data (the inventory may be private).")
        if not data.get("success", 1):
            raise ApiError(str(data.get("error") or "Steam reported an error loading the inventory."))
        if not data.get("total_inventory_count") and not data.get("assets"):
            break

        assets.extend(data.get("assets") or [])
        for desc in data.get("descriptions") or []:
            descriptions[(str(desc.get("classid")), str(desc.get("instanceid", "0")))] = desc
        for entry in data.get("asset_properties") or []:
            properties[str(entry.get("assetid"))] = entry.get("asset_properties") or []

        if data.get("more_items") and data.get("last_assetid"):
            start_assetid = data["last_assetid"]
        else:
            break

    items = []
    for asset in assets:
        key = (str(asset.get("classid")), str(asset.get("instanceid", "0")))
        parsed = parse_asset(asset, descriptions.get(key), properties.get(str(asset.get("assetid"))), steam_id)
        if parsed:
            items.append(parsed)
    return items


# ---------------------------------------------------------------------------
# Persisting
# ---------------------------------------------------------------------------
_STEAM_COLUMNS = (
    "market_hash_name", "name", "category", "item_type", "weapon", "rarity", "rarity_color",
    "exterior", "name_tag", "quantity", "icon_url", "inspect_link", "tradable", "marketable",
)


def sync_inventory(steam_id_input: str, job=None) -> dict[str, Any]:
    """Download a public inventory and reconcile it with the local database."""
    steam_id = resolve_steam_id(steam_id_input)
    if job:
        _inventory_client.listener = lambda message: job.update(message=message)
        _inventory_client.should_cancel = lambda: job.cancelled
    try:
        fetched = fetch_inventory(steam_id, job)
    finally:
        _inventory_client.listener = _inventory_client.should_cancel = None
    if job:
        job.update(message=f"Saving {len(fetched)} Steam items…")
    now = db.utcnow_iso()
    added = updated = removed = rehomed = 0

    with db.connect() as conn:
        existing = {
            row["asset_id"]: dict(row)
            for row in conn.execute("SELECT * FROM items WHERE source = 'steam' AND steam_id = ?", (steam_id,))
        }
        if not fetched and existing:
            # Steam occasionally answers with an empty inventory during outages.
            raise ApiError(
                "Steam returned an empty inventory, so your saved items were kept. "
                "If this keeps happening, check the inventory is still public."
            )
        seen: set[str] = set()
        new_containers: list[dict] = []
        fetched_ids = {item["asset_id"] for item in fetched}
        arrived: list[dict] = []         # new in Steam since the last sync
        units: list[dict] = []           # storage units, with Steam's item count before and after

        for item in fetched:
            seen.add(item["asset_id"])
            values = [item[col] for col in _STEAM_COLUMNS]
            is_storage = int(item["market_hash_name"] == "Storage Unit")
            row = existing.get(item["asset_id"])
            count = item.get("storage_count")
            if row:
                item_id = row["id"]
                assignments = ", ".join(f"{col} = ?" for col in _STEAM_COLUMNS)
                conn.execute(
                    f"UPDATE items SET {assignments}, float_value = COALESCE(?, float_value), "
                    "paint_seed = COALESCE(?, paint_seed), is_storage = MAX(is_storage, ?), steam_count = ?, "
                    "updated_at = ? WHERE id = ?",
                    (*values, item["float_value"], item["paint_seed"], is_storage, count, now, item_id),
                )
                updated += 1
            else:
                cols = ("source", "steam_id", "asset_id", *_STEAM_COLUMNS, "float_value", "paint_seed",
                        "is_storage", "steam_count", "created_at", "updated_at")
                item_id = conn.execute(
                    f"INSERT INTO items ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    ("steam", steam_id, item["asset_id"], *values, item["float_value"], item["paint_seed"],
                     is_storage, count, now, now),
                ).lastrowid
                added += 1
                if item["category"] == "container":
                    new_containers.append({"id": item_id, **item})
                if not is_storage:
                    arrived.append({"id": item_id, **item})
            if is_storage:
                # Its count before: this unit's last sync, or the unit it replaced (Steam can re-issue
                # a storage unit's asset id), matched by name tag.
                before = row if row else next(
                    (r for a, r in existing.items() if a not in fetched_ids and r["is_storage"]
                     and r["market_hash_name"] == item["market_hash_name"] and r["name_tag"] == item["name_tag"]), None)
                # A storage unit you've only just got starts empty.
                units.append({"id": item_id, "name_tag": item["name_tag"], "after": count,
                              "before": before["steam_count"] if before else (0 if count is not None else None)})

            conn.execute("DELETE FROM item_stickers WHERE item_id = ?", (item_id,))
            conn.executemany(
                "INSERT INTO item_stickers (item_id, slot, kind, name, scrape) VALUES (?, ?, ?, ?, ?)",
                [(item_id, s["slot"], s["kind"], s["name"], s["scrape"]) for s in item["stickers"]],
            )

        left: list[dict] = []            # gone from Steam since the last sync
        for asset_id, row in existing.items():
            if asset_id in seen:
                continue
            if not row["is_storage"]:
                row["stickers"] = [dict(r) for r in conn.execute(
                    "SELECT slot, kind, name, scrape FROM item_stickers WHERE item_id = ? ORDER BY slot", (row["id"],))]
                left.append(row)
            contents = conn.execute("SELECT COUNT(*) FROM items WHERE container_id = ?", (row["id"],)).fetchone()[0]
            if contents:
                # Steam can re-issue asset IDs (e.g. when a storage unit changes). Move any
                # manually-tracked contents to the matching new container instead of losing them.
                match = next(
                    (c for c in new_containers
                     if c["market_hash_name"] == row["market_hash_name"] and c["name_tag"] == row["name_tag"]),
                    None,
                )
                conn.execute(
                    "UPDATE items SET container_id = ?, updated_at = ? WHERE container_id = ?",
                    (match["id"] if match else None, now, row["id"]),
                )
                rehomed += contents
            conn.execute("DELETE FROM items WHERE id = ?", (row["id"],))
            removed += 1

        # What moved in or out of storage units (or was sold / traded): suggestions for you to
        # confirm on the Containers tab. Nothing to compare against on the very first sync.
        changes = moves.detect(conn, left, arrived, units, now) if existing else 0

    settings.set_value("steam_id", steam_id)
    settings.set_value("last_steam_sync", now)
    log.info("Steam sync: %d fetched, %d added, %d updated, %d removed", len(fetched), added, updated, removed)
    return {
        "steam_id": steam_id,
        "fetched": len(fetched),
        "added": added,
        "updated": updated,
        "removed": removed,
        "contents_moved": rehomed,
        "changes": changes,
    }
