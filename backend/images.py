"""Pictures for items you add by hand.

Items synced from Steam come with their picture. For items added manually, the name is
looked up in ByMykel's CSGO-API (https://github.com/ByMykel/CSGO-API, MIT licence), a
catalogue built from the game files that gives the Steam image of every item - including
ones nobody is selling. Only the file for the kind of item needed is downloaded (about
1 MB compressed for skins, stickers, slabs or cases; the others are small). The
name -> picture pairs are kept in the ``item_images`` table, so a file is fetched once and
re-checked at most once a day, and only while an item still has no picture.
The pictures themselves load from Steam's image servers, exactly like synced items.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Iterable
from urllib.parse import urlparse

from config import Config

from . import cache, db
from .classify import categorize
from .http_client import ApiError, RateLimitedClient

log = logging.getLogger(__name__)

BASE_URL = "https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/en/{file}.json"
RECHECK_SECONDS = 24 * 3600
RETRY_AFTER_FAILURE = 600
SMALL_FILES = ("agents", "collectibles", "tools", "keychains", "graffiti", "highlights", "music_kits",
               "patches", "keys")
_PREFIX_FILES = (("patch |", "patches"), ("charm |", "keychains"), ("souvenir charm |", "highlights"),
                 ("sealed graffiti |", "graffiti"), ("music kit |", "music_kits"),
                 ("stattrak™ music kit |", "music_kits"))
# Several Doppler phases share one Steam name; prefer an ordinary phase's picture over a gem's.
_GEMS = {"Ruby", "Sapphire", "Black Pearl", "Emerald"}
_STEAM_ECONOMY = re.compile(r"^https://[a-z0-9.-]+\.steamstatic\.com/economy/image/([A-Za-z0-9_-]+)")

# No retries: this can run while you wait for an item to be added, so fail fast and try again later.
client = RateLimitedClient("Item pictures", min_interval=0.5, base_backoff=10, max_retries=0)
_lock = threading.Lock()
_retry_at = 0.0


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------
def icon_from_url(url: object) -> str | None:
    """A Steam image hash (stored like a synced item's ``icon_url``), a full Steam CDN URL, or None.
    Anything not hosted by Steam is ignored, so the page only ever loads pictures from Steam."""
    if not isinstance(url, str):
        return None
    match = _STEAM_ECONOMY.match(url)
    if match:
        return match.group(1)
    parts = urlparse(url)
    if parts.scheme == "https" and (parts.hostname or "").endswith(".steamstatic.com"):
        return url
    return None


def icon_src(icon: object) -> str | None:
    """Browser URL for a stored icon (Steam image hash or full Steam CDN URL)."""
    if not isinstance(icon, str) or not icon:
        return None
    return icon if icon.startswith("https://") else f"{Config.STEAM_IMAGE_CDN}{icon}/128fx128f"


# ---------------------------------------------------------------------------
# Catalogue files
# ---------------------------------------------------------------------------
def files_for(name: str) -> tuple[str, ...]:
    """Which catalogue file(s) can hold this item's picture, most likely first."""
    category = categorize(name)
    if category == "weapon_skin":
        return ("skins_not_grouped",)
    if category == "sticker":
        return ("stickers",)
    if category == "sticker_slab":
        return ("sticker_slabs",)
    lower = name.lower()
    for prefix, file in _PREFIX_FILES:
        if lower.startswith(prefix):
            return (file,)
    if lower.endswith(" key"):
        return ("keys",)
    if category == "container":
        return ("crates", "tools")
    return (*SMALL_FILES, "crates")


def parse(file: str, data: object) -> dict[str, str]:
    """Market name -> icon for one catalogue file."""
    if not isinstance(data, list):
        raise ApiError(f"Item pictures: {file} had an unexpected format")
    entries = sorted((e for e in data if isinstance(e, dict)), key=lambda e: e.get("phase") in _GEMS)
    found: dict[str, str] = {}
    for entry in entries:
        icon = icon_from_url(entry.get("image"))
        if not icon:
            continue
        for name in (entry.get("market_hash_name"), entry.get("name")):
            if isinstance(name, str) and name:
                found.setdefault(name, icon)
                if file == "music_kits" and name.startswith("Music Kit | "):
                    found.setdefault("StatTrak™ " + name, icon)
    return found


def _download(file: str) -> int:
    resp = client.get(BASE_URL.format(file=file))
    if resp.status_code != 200:
        raise ApiError(f"Item pictures: {file} returned HTTP {resp.status_code}", resp.status_code)
    try:
        found = parse(file, resp.json())
    except ValueError:
        raise ApiError(f"Item pictures: {file} wasn't valid JSON") from None
    if not found:
        raise ApiError(f"Item pictures: {file} was empty")
    db.executemany(
        "INSERT OR REPLACE INTO item_images (market_hash_name, icon, source) VALUES (?, ?, ?)",
        [(name, icon, file) for name, icon in found.items()],
    )
    cache.set_cached(f"item_images:{file}", len(found))
    log.info("Item pictures: %d from %s", len(found), file)
    return len(found)


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
def lookup(names: Iterable[str]) -> dict[str, str]:
    """Known pictures for these names (no network)."""
    wanted = sorted({n for n in names if isinstance(n, str) and n})
    found: dict[str, str] = {}
    for i in range(0, len(wanted), 500):
        chunk = wanted[i:i + 500]
        rows = db.query(
            f"SELECT market_hash_name, icon FROM item_images WHERE market_hash_name IN ({','.join('?' * len(chunk))})",
            chunk,
        )
        found.update((r["market_hash_name"], r["icon"]) for r in rows)
    return found


def _synced(names: set[str]) -> set[str]:
    """Names a Steam-synced item already has a picture for (manual copies borrow it)."""
    wanted = sorted(names)
    found: set[str] = set()
    for i in range(0, len(wanted), 500):
        chunk = wanted[i:i + 500]
        rows = db.query(
            "SELECT DISTINCT market_hash_name FROM items WHERE COALESCE(icon_url, '') != '' "
            f"AND market_hash_name IN ({','.join('?' * len(chunk))})",
            chunk,
        )
        found.update(r["market_hash_name"] for r in rows)
    return found


def ensure(names: Iterable[str]) -> set[str]:
    """Download pictures for any of these names we don't have yet. Never raises.
    Returns the names that still have no picture."""
    global _retry_at
    names = {n for n in names if isinstance(n, str) and n}
    missing = names - set(lookup(names)) - _synced(names)
    if not missing or not Config.ITEM_IMAGES:
        return missing
    with _lock:                                  # one download at a time, even from several requests
        for file in dict.fromkeys(f for name in sorted(missing) for f in files_for(name)):
            if not missing:
                break
            if not any(file in files_for(n) for n in missing):
                continue
            if cache.get_cached(f"item_images:{file}", RECHECK_SECONDS) is not None:
                continue                         # fetched recently: the name just isn't in it
            if time.monotonic() < _retry_at:
                break
            try:
                _download(file)
            except ApiError as exc:
                _retry_at = time.monotonic() + RETRY_AFTER_FAILURE
                log.warning("Couldn't download item pictures (%s); will try again later", exc)
                break
            missing -= set(lookup(missing))
    return missing


def backfill() -> set[str]:
    """Find pictures for items that have none (and no Steam-synced twin to borrow one from)."""
    rows = db.query("SELECT DISTINCT market_hash_name FROM items WHERE COALESCE(icon_url, '') = ''")
    return ensure(r["market_hash_name"] for r in rows)
