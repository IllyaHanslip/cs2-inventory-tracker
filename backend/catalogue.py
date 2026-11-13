"""Every CS2 item name, for autocomplete - including items nobody is selling right now.

Market price lists only contain what is listed today, so on their own they miss
rare or brand-new items (a Sticker Slab no one has put up for sale, say).
CSFloat publishes a public item schema (no API key needed, ~250 KB, cached for a
week) describing every skin with its float range and StatTrak/Souvenir
availability, plus every sticker, patch, charm, case, agent and music kit.
From it we build the exact Steam market names - each skin in every wear it can
actually have, and a Sticker Slab for every sticker - and merge them with the
names on the cached market price lists, which add graffiti, keys and passes.
"""
from __future__ import annotations

import heapq
import logging
import re
import threading
import time
from typing import Any, Iterable

from config import Config

from . import cache, db
from .http_client import ApiError
from .markets import SOURCES

log = logging.getLogger(__name__)

SCHEMA_URL = "https://csfloat.com/api/v1/schema"
SCHEMA_CACHE_SECONDS = 7 * 24 * 3600
RETRY_AFTER_FAILURE = 600     # seconds before trying the download again after it failed
NAMES_KEY = "item_catalogue"
STICKERS_KEY = "csfloat_sticker_names"
PRICE_LISTS = ("skinport", "marketcsgo", "waxpeer")

WEARS = (("Factory New", 0.0, 0.07), ("Minimal Wear", 0.07, 0.15), ("Field-Tested", 0.15, 0.38),
         ("Well-Worn", 0.38, 0.45), ("Battle-Scarred", 0.45, 1.0))
# CSFloat names each Doppler phase separately; on Steam they all share one name.
_PHASE_RE = re.compile(r"^((?:Gamma )?Doppler) \([^)]*\)$")

_fetch_lock = threading.Lock()
_retry_at = 0.0
_memo: tuple[Any, list[tuple[str, str]]] | None = None


# ---------------------------------------------------------------------------
# Building names from the schema
# ---------------------------------------------------------------------------
def build_names(schema: dict) -> list[str]:
    """Steam market names for everything in a CSFloat schema."""
    names: set[str] = set()
    for weapon in schema.get("weapons", {}).values():
        base, kind = weapon["name"], weapon.get("type")
        star = "★ " if kind in ("Knives", "Gloves") else ""
        if kind == "Knives":                       # plain ("vanilla") knives have no paint or wear
            names.update((f"★ {base}", f"★ StatTrak™ {base}"))
        for paint in weapon.get("paints", {}).values():
            finish = _PHASE_RE.sub(r"\1", paint["name"])
            wears = [w for w, low, high in WEARS if paint["min"] < high and paint["max"] > low]
            prefixes = ["", *(["StatTrak™ "] if paint.get("stattrak") else []),
                        *(["Souvenir "] if paint.get("souvenir") else [])]
            names.update(f"{star}{prefix}{base} | {finish} ({wear})" for prefix in prefixes for wear in wears)
    for sticker in schema.get("stickers", {}).values():
        name = sticker.get("market_hash_name")
        if name:
            names.add(name)
            if name.startswith("Sticker | "):     # any sticker can be sealed into a slab
                names.add("Sticker Slab | " + name[len("Sticker | "):])
    for group in ("keychains", "highlight_reels", "collectibles", "containers", "agents"):
        names.update(v["market_hash_name"] for v in schema.get(group, {}).values() if v.get("market_hash_name"))
    for kit in schema.get("music_kits", {}).values():
        if kit.get("market_hash_name"):
            names.add(kit["market_hash_name"])
            if kit.get("stattrak_price") is not None:
                names.add("StatTrak™ " + kit["market_hash_name"])
    return sorted(names)


def sticker_names_from(schema: dict) -> dict[str, str]:
    """CSFloat sticker/patch ID (as text) -> market name."""
    return {str(k): v["market_hash_name"] for k, v in schema["stickers"].items() if v.get("market_hash_name")}


def refresh_schema() -> dict[str, Any]:
    """Download CSFloat's public item schema and cache the parts the app uses. Raises ApiError."""
    resp = SOURCES["csfloat"].client.get(SCHEMA_URL)
    if resp.status_code != 200:
        raise ApiError(f"CSFloat's item list returned HTTP {resp.status_code}", resp.status_code)
    try:
        schema = resp.json()
        stickers = sticker_names_from(schema)
        names = build_names(schema)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ApiError(f"CSFloat's item list had an unexpected format ({exc})") from None
    if not names or not stickers:
        raise ApiError("CSFloat's item list was empty")
    cache.set_cached(STICKERS_KEY, stickers)
    cache.set_cached(NAMES_KEY, names)
    return {"stickers": stickers, "names": names}


def catalogue_names(allow_fetch: bool = True) -> list[str]:
    """Names built from the CSFloat schema: cached for a week, downloaded when missing or old."""
    global _retry_at
    cached = cache.get_cached(NAMES_KEY, SCHEMA_CACHE_SECONDS)
    if cached is not None or not allow_fetch or time.monotonic() < _retry_at:
        return cached if cached is not None else cache.get_stale(NAMES_KEY) or []
    with _fetch_lock:                          # several keystrokes at once -> one download
        cached = cache.get_cached(NAMES_KEY, SCHEMA_CACHE_SECONDS)
        if cached is not None:
            return cached
        try:
            return refresh_schema()["names"]
        except ApiError as exc:
            _retry_at = time.monotonic() + RETRY_AFTER_FAILURE
            log.warning("Couldn't download the item list for autocomplete (%s)", exc)
            return cache.get_stale(NAMES_KEY) or []


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def _price_list_keys() -> list[str]:
    return [SOURCES[key]._cache_key() for key in PRICE_LISTS]


def known_names(allow_fetch: bool = True) -> list[tuple[str, str]]:
    """Every known item name as (lower-case, name): the catalogue plus every cached price list.

    Rebuilt only when one of the underlying caches changes."""
    global _memo
    catalogue = catalogue_names(allow_fetch)
    keys = [NAMES_KEY, *_price_list_keys()]
    marks = db.query(f"SELECT cache_key, fetched_at FROM api_cache WHERE cache_key IN ({','.join('?' * len(keys))})", keys)
    version = (str(Config.DATABASE_PATH), tuple(sorted((r["cache_key"], r["fetched_at"]) for r in marks)), len(catalogue))
    if _memo is not None and _memo[0] == version:
        return _memo[1]
    names = dict.fromkeys(catalogue)
    for key in keys[1:]:
        names.update(dict.fromkeys(row[0] for row in cache.get_stale(key) or [] if row and row[0]))
    entries = [(name.casefold(), name) for name in names]
    _memo = (version, entries)
    return entries


def canonical(name: str) -> str:
    """Steam's exact spelling of a typed item name. Names are matched exactly everywhere ("NiKo"
    and "niko" are different items), so a name typed in the wrong case would never get a price.
    It's corrected only when exactly one known item has that name ignoring case; otherwise it's
    kept as typed. Never downloads anything."""
    if not name:
        return name
    matches = {known for lower, known in known_names(allow_fetch=False) if lower == name.casefold()}
    if name in matches or len(matches) != 1:
        return name
    return next(iter(matches))


def _wear_split(lower: str) -> tuple[str, int]:
    """('ak-47 | redline', 2) for 'ak-47 | redline (field-tested)'; names without a wear rank first."""
    for rank, (wear, _, _) in enumerate(WEARS, start=1):
        suffix = f" ({wear.lower()})"
        if lower.endswith(suffix):
            return lower[:-len(suffix)], rank
    return lower, 0


def search(query: str, owned: Iterable[str] = (), prefix: str = "", limit: int = 25,
           allow_fetch: bool = True) -> list[str]:
    """Names containing every word of the query, best first: items you own, then names where each
    word typed starts a word ('ak' -> AK-47 before StatTrak), names starting with what you typed,
    then shorter names (plain before StatTrak), each skin's wears from Factory New to Battle-Scarred."""
    typed = query.strip()
    query = typed.casefold()
    terms = query.split()
    exact_terms = typed.split()   # as typed, for ranking exact-case matches first ("NiKo" before "niko")
    prefix = prefix.lower()
    if not terms:
        return []
    word_starts = [re.compile(r"(?<!\w)" + re.escape(t)) for t in terms]
    owned = {name for name in owned if name}
    candidates = [(name.casefold(), name) for name in owned] + known_names(allow_fetch)
    seen: set[str] = set()
    matches = []
    for lower, name in candidates:
        if name in seen or (prefix and not lower.startswith(prefix)) or not all(t in lower for t in terms):
            continue
        seen.add(name)
        stem, wear = _wear_split(lower)
        mid_word = not all(p.search(lower) for p in word_starts)
        other_case = not all(t in name for t in exact_terms)
        matches.append(((name not in owned, other_case, mid_word, not lower.startswith(query), len(stem), stem, wear),
                        name))
    return [name for _, name in heapq.nsmallest(limit, matches)]
