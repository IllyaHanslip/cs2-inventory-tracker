"""Item categorisation helpers shared by Steam sync, manual entry and filters."""
from __future__ import annotations

import re

CATEGORIES = {
    "weapon_skin": "Weapon Skin",
    "sticker": "Sticker",
    "sticker_slab": "Sticker Slab",
    "container": "Container",
    "other": "Other",
}

EXTERIORS = ("Factory New", "Minimal Wear", "Field-Tested", "Well-Worn", "Battle-Scarred")

APPLIED_KINDS = {"sticker": "Sticker | ", "patch": "Patch | ", "charm": "Charm | "}

# Steam "Type" tag internal names.
_WEAPON_TYPES = {
    "CSGO_Type_Pistol",
    "CSGO_Type_SMG",
    "CSGO_Type_Rifle",
    "CSGO_Type_SniperRifle",
    "CSGO_Type_Shotgun",
    "CSGO_Type_Machinegun",
    "CSGO_Type_Knife",
    "Type_Hands",
}
_CONTAINER_TYPES = {"CSGO_Type_WeaponCase"}
_CONTAINER_RE = re.compile(
    r"\b(case|capsule|package|storage unit|patch pack|graffiti box|music kit box|souvenir package)\b",
    re.IGNORECASE,
)
_EXTERIOR_RE = re.compile(r"\((" + "|".join(re.escape(e) for e in EXTERIORS) + r")\)\s*$")


def categorize(market_hash_name: str, type_internal: str | None = None) -> str:
    name = (market_hash_name or "").strip()
    lower = name.lower()
    if lower.startswith("sticker slab |"):
        return "sticker_slab"
    if type_internal == "CSGO_Tool_Sticker" or lower.startswith("sticker |"):
        return "sticker"
    if type_internal in _WEAPON_TYPES:
        return "weapon_skin"
    if type_internal in _CONTAINER_TYPES:
        return "container"
    # Weapon checks come before the container regex so "Case Hardened" skins stay skins.
    if name.startswith("★") or _EXTERIOR_RE.search(name):
        return "weapon_skin"
    if lower.endswith(" key") or lower.startswith(("patch |", "charm |", "sealed graffiti |")):
        return "other"
    if _CONTAINER_RE.search(name):
        return "container"
    return "other"


def exterior_from_name(market_hash_name: str) -> str | None:
    match = _EXTERIOR_RE.search(market_hash_name or "")
    return match.group(1) if match else None


def exterior_from_float(float_value: float | None) -> str | None:
    if float_value is None:
        return None
    for limit, label in ((0.07, "Factory New"), (0.15, "Minimal Wear"), (0.38, "Field-Tested"), (0.45, "Well-Worn")):
        if float_value < limit:
            return label
    return "Battle-Scarred"


def applied_hash_name(kind: str, name: str) -> str:
    """Market name of a loose sticker/patch/charm, e.g. 'Sticker | Titan (Holo) | Katowice 2014'."""
    prefix = APPLIED_KINDS.get(kind, "Sticker | ")
    name = (name or "").strip()
    return name if name.lower().startswith(prefix.lower()) else prefix + name
