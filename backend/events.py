"""Everything from one tournament - stickers, slabs, capsules, souvenir packages, patches,
charms - for adding many items at once ("Add from an event" in a container).

Built from the item catalogue, so it includes items nobody is selling right now. The names are
Steam's exact ones, so whatever you pick is exactly what gets added.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from . import catalogue, images
from .classify import categorize
from .tags import event_of, type_of

KIND_ORDER = ("Sticker", "Sticker slab", "Capsule", "Souvenir package", "Patch", "Charm", "Graffiti", "Collectible")
VARIANT_ORDER = ("Paper", "Glitter", "Holo", "Foil", "Lenticular", "Embroidered", "Gold", "Champion")
_OLD_CAPSULE_RE = re.compile(r"\b(Legends|Challengers|Contenders|Champions|Autographs?)\b")
MIN_ITEMS = 20          # fewer than this isn't a tournament (a one-off collaboration sticker, say)
_PARTS_RE = re.compile(r"^[^|]+\|\s*(?P<subject>.+?)\s*(?:\((?P<variant>[^)]+)\))?\s*\|\s*[^|]+$")
_memo: tuple[Any, dict[str, list[str]]] | None = None


def _by_event() -> dict[str, list[str]]:
    """Every known item name that belongs to an event, by event (rebuilt when the catalogue changes)."""
    global _memo
    names = catalogue.known_names(allow_fetch=True)
    version = (id(names), len(names))
    if _memo is not None and _memo[0] == version:
        return _memo[1]
    found: dict[str, list[str]] = {}
    for _lower, name in names:
        event = event_of(name, categorize(name))
        if event:
            found.setdefault(event, []).append(name)
    _memo = (version, found)
    return found


def _year(event: str) -> int:
    match = re.search(r"(?:19|20)\d{2}", event)
    return int(match.group(0)) if match else 0


def event_list() -> list[dict[str, Any]]:
    """Tournaments, newest first, with how many items each has."""
    latest = date.today().year + 1
    events = {e: names for e, names in _by_event().items() if len(names) >= MIN_ITEMS and _year(e) <= latest}
    return [{"event": event, "count": len(names)}
            for event, names in sorted(events.items(), key=lambda kv: (-_year(kv[0]), kv[0]))]


def describe(name: str, event: str) -> dict[str, Any]:
    """What kind of item it is, who or what it shows, and which version (Gold, Holo...)."""
    category = categorize(name)
    kind = type_of(name, category, None, None, set())
    if kind == "Other" and _OLD_CAPSULE_RE.search(name):
        kind = "Capsule"            # older capsules: "Berlin 2019 Legends (Holo-Foil)"
    subject, variant = name, None
    match = _PARTS_RE.match(name)
    if match and kind in ("Sticker", "Sticker slab", "Patch", "Charm", "Graffiti"):
        subject = match.group("subject")
        variant = match.group("variant") or ("Paper" if kind in ("Sticker", "Sticker slab") else None)
    elif event in name:
        subject = " ".join(name.replace(event, "").split())
    return {"name": name, "kind": kind, "subject": subject, "variant": variant}


def event_items(event: str) -> list[dict[str, Any]]:
    names = _by_event().get(event, [])
    icons = images.lookup(names)
    items = [{**describe(name, event), "icon": images.icon_src(icons.get(name))} for name in names]

    def order(item):
        kind = KIND_ORDER.index(item["kind"]) if item["kind"] in KIND_ORDER else len(KIND_ORDER)
        variant = VARIANT_ORDER.index(item["variant"]) if item["variant"] in VARIANT_ORDER else len(VARIANT_ORDER)
        return kind, item["subject"].casefold(), variant, item["name"]

    return sorted(items, key=order)
