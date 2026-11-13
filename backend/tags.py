"""Tags for grouping and filtering: event, collection, type, weapon and rarity.

Worked out from the item's name, Steam's own tags (synced items) and CSFloat's item list
(catalogue.item_meta), so they work for items you add by hand too.
"""
from __future__ import annotations

import re
from typing import Any

WEAPON_CLASSES = {
    "Rifle": ("AK-47", "M4A4", "M4A1-S", "FAMAS", "Galil AR", "AUG", "SG 553"),
    "Sniper rifle": ("AWP", "SSG 08", "SCAR-20", "G3SG1"),
    "Pistol": ("Glock-18", "USP-S", "P2000", "P250", "Five-SeveN", "Tec-9", "CZ75-Auto", "Desert Eagle",
               "Dual Berettas", "R8 Revolver"),
    "SMG": ("MAC-10", "MP9", "MP7", "MP5-SD", "UMP-45", "P90", "PP-Bizon"),
    "Shotgun": ("Nova", "XM1014", "Sawed-Off", "MAG-7"),
    "Machine gun": ("M249", "Negev"),
    "Zeus": ("Zeus x27",),
}
WEAPON_CLASS = {weapon: cls for cls, weapons in WEAPON_CLASSES.items() for weapon in weapons}

_WEAR = re.compile(r" \((?:Factory New|Minimal Wear|Field-Tested|Well-Worn|Battle-Scarred)\)$")
# Third-party names for Doppler phases ("Doppler Phase 2", "Gamma Doppler Emerald") -> the finish.
_PHASE = re.compile(r"^((?:Gamma )?Doppler)(?: Phase \d| Ruby| Sapphire| Black Pearl| Emerald)$")
# "Budapest 2025", "ESL One Cologne 2014", "2020 RMR": capitalised words then a year, or a year + RMR.
_EVENT = re.compile(r"(\b\d{4} RMR\b|(?:\b[A-Z][\w'.&-]*\s)+(?:19|20)\d{2}\b)")
_COLLECTIBLE = re.compile(r"\b(?:coin|medal|pin|badge|trophy)\b", re.IGNORECASE)

KINDS = ("event", "collection", "type", "weapon", "rarity")


def split_skin(name: str) -> tuple[str, str | None]:
    """'★ StatTrak™ Karambit | Doppler (Factory New)' -> ('Karambit', 'Doppler')."""
    base = name.removeprefix("★ ").removeprefix("StatTrak™ ").removeprefix("Souvenir ")
    base = _WEAR.sub("", base)
    weapon, _, finish = base.partition(" | ")
    finish = _PHASE.sub(r"\1", finish.strip()) if finish else None
    return weapon.strip(), finish or None


def event_of(name: str, category: str) -> str | None:
    """The tournament or major an item belongs to, from its name (stickers, slabs, patches,
    capsules, souvenir packages, charms, coins…). Weapon skins don't carry it in their names."""
    if category == "weapon_skin":
        return None
    match = _EVENT.search(name)
    return match.group(1).strip() if match else None


def type_of(name: str, category: str, weapon: str | None, weapon_type: str | None, agents: set[str]) -> str:
    lower = name.lower()
    if category == "weapon_skin":
        if weapon_type == "Gloves" or "gloves" in lower or "hand wraps" in lower:
            return "Gloves"
        if weapon_type == "Knives" or name.startswith("★"):
            return "Knife"
        return WEAPON_CLASS.get(weapon or "", "Other weapon")
    if category == "sticker":
        return "Sticker"
    if category == "sticker_slab":
        return "Sticker slab"
    if lower.startswith("patch |"):
        return "Patch"
    if lower.startswith(("charm |", "souvenir charm |")):
        return "Charm"
    if lower.startswith(("sealed graffiti |", "graffiti |")):
        return "Graffiti"
    if "music kit |" in lower:
        return "Music kit"
    if category == "container":
        if name == "Storage Unit":
            return "Storage unit"
        if "capsule" in lower:
            return "Capsule"
        if "souvenir" in lower and "package" in lower:
            return "Souvenir package"
        return "Case" if "case" in lower else "Container"
    if lower.endswith(" key"):
        return "Key"
    if name in agents:
        return "Agent"
    if _COLLECTIBLE.search(name):
        return "Collectible"
    return "Other"


def item_tags(name: str, category: str, steam_rarity: Any, meta: dict[str, Any]) -> dict[str, str | None]:
    weapon = finish = None
    rarity = steam_rarity if isinstance(steam_rarity, str) and steam_rarity else None
    collection = None
    if category == "weapon_skin":
        weapon, finish = split_skin(name)
        details = meta.get("paints", {}).get(f"{weapon}|{finish}") if finish else None
        if details:
            rarity = rarity or details[0]
            collection = details[1][0] if details[1] else None
        elif name.startswith("★"):
            rarity = rarity or "Covert"   # plain knives
    weapon_type = meta.get("weapons", {}).get(weapon) if weapon else None
    return {
        "event": event_of(name, category),
        "collection": collection,
        "type": type_of(name, category, weapon, weapon_type, meta.get("agents") or set()),
        "weapon": weapon,
        "rarity": rarity,
    }
