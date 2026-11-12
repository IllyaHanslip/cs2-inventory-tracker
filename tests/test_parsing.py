"""Parsing helpers: money strings, categories, SteamIDs and Steam inventory JSON."""
import pytest

from backend.classify import applied_hash_name, categorize, exterior_from_float
from backend.markets.base import parse_money
from backend.steam_inventory import parse_asset, resolve_steam_id
from tests.conftest import FAKE_STEAM_ID


@pytest.mark.parametrize("text, expected", [
    ("$1,234.56", 1234.56),
    ("1.234,56€", 1234.56),
    ("0,03€", 0.03),
    ("¥ 12.30", 12.30),
    ("$0.29", 0.29),
    ("1,234", 1234.0),
    ("--", None),
    (None, None),
])
def test_parse_money(text, expected):
    assert parse_money(text) == expected


@pytest.mark.parametrize("name, expected", [
    ("AK-47 | Case Hardened (Field-Tested)", "weapon_skin"),
    ("★ Karambit", "weapon_skin"),
    ("Recoil Case", "container"),
    ("Paris 2023 Mirage Souvenir Package", "container"),
    ("Storage Unit", "container"),
    ("Chroma Case Key", "other"),
    ("Sticker | Crown (Foil)", "sticker"),
    ("Sticker Slab | IEM (Gold) | Rio 2022", "sticker_slab"),
    ("Patch | Phoenix", "other"),
    ("Sir Bloody Miami Darryl | The Professionals", "other"),
])
def test_categorize(name, expected):
    assert categorize(name) == expected


def test_type_tag_wins_over_name():
    assert categorize("Some Future Case Item", "CSGO_Type_Rifle") == "weapon_skin"


def test_exterior_from_float():
    assert exterior_from_float(0.01) == "Factory New"
    assert exterior_from_float(0.07) == "Minimal Wear"
    assert exterior_from_float(0.2) == "Field-Tested"
    assert exterior_from_float(0.44) == "Well-Worn"
    assert exterior_from_float(0.9) == "Battle-Scarred"


def test_applied_hash_name():
    assert applied_hash_name("sticker", "Crown (Foil)") == "Sticker | Crown (Foil)"
    assert applied_hash_name("sticker", "Sticker | Crown (Foil)") == "Sticker | Crown (Foil)"
    assert applied_hash_name("charm", "Lil' Squirt") == "Charm | Lil' Squirt"


def test_resolve_steam_id(app, monkeypatch):
    from backend import steam_inventory

    assert resolve_steam_id(FAKE_STEAM_ID) == FAKE_STEAM_ID
    assert resolve_steam_id(f"https://steamcommunity.com/profiles/{FAKE_STEAM_ID}/inventory/") == FAKE_STEAM_ID

    class Resp:
        status_code = 200

        def __init__(self, text):
            self.text = text

    calls = []

    def fake_get(url, params=None, headers=None):  # stands in for Steam's public profile XML
        calls.append((url, params))
        if "/id/someone/" in url:
            return Resp(f"<profile><steamID64>{FAKE_STEAM_ID}</steamID64></profile>")
        return Resp("<response><error><![CDATA[The specified profile could not be found.]]></error></response>")

    monkeypatch.setattr(steam_inventory._profile_client, "get", fake_get)
    assert resolve_steam_id("https://steamcommunity.com/id/someone") == FAKE_STEAM_ID
    assert calls[0] == ("https://steamcommunity.com/id/someone/", {"xml": 1})   # no API key involved
    with pytest.raises(ValueError, match="Could not find"):
        resolve_steam_id("nobody-here")
    with pytest.raises(ValueError):
        resolve_steam_id("not a steam id!")


# A fictional item in the shape returned by steamcommunity.com/inventory/<id>/730/2
STICKER_HTML = (
    '<br><div id="sticker_info" name="sticker_info" title="Sticker" style="border: 2px solid rgb(102, 102, 102);">'
    '<center><img width=64 height=48 src="https://example.invalid/a.png" title="Sticker: Crown (Foil)">'
    '<img width=64 height=48 src="https://example.invalid/b.png" title="Sticker: Team &quot;Q&quot; | Test 2030">'
    '<br>Sticker: Crown (Foil), Team "Q" | Test 2030</center></div>'
)
DESCRIPTION = {
    "classid": "1", "instanceid": "2",
    "market_hash_name": "AK-47 | Redline (Field-Tested)",
    "market_name": "AK-47 | Redline (Field-Tested)",
    "icon_url": "abc123",
    "tradable": 1, "marketable": 1,
    "descriptions": [{"type": "html", "value": "Exterior: Field-Tested"}, {"type": "html", "value": STICKER_HTML}],
    "fraudwarnings": ["Name Tag: ''Old <Faithful>''"],
    "actions": [{"name": "Inspect in Game...",
                 "link": "steam://rungame/730/76561202255233023/+csgo_econ_action_preview%20S%owner_steamid%A%assetid%D123"}],
    "tags": [
        {"category": "Type", "internal_name": "CSGO_Type_Rifle", "localized_tag_name": "Rifle"},
        {"category": "Weapon", "internal_name": "weapon_ak47", "localized_tag_name": "AK-47"},
        {"category": "Exterior", "internal_name": "WearCategory2", "localized_tag_name": "Field-Tested"},
        {"category": "Rarity", "internal_name": "Rarity_Legendary", "localized_tag_name": "Classified", "color": "d32ce6"},
    ],
}


def test_parse_asset():
    asset = {"assetid": "999", "classid": "1", "instanceid": "2", "amount": "1"}
    props = [{"propertyid": 2, "float_value": "0.2213", "name": "Wear Rating"},
             {"propertyid": 1, "int_value": "661", "name": "Pattern Template"}]
    item = parse_asset(asset, DESCRIPTION, props, FAKE_STEAM_ID)
    assert item["category"] == "weapon_skin"
    assert item["exterior"] == "Field-Tested"
    assert item["rarity_color"] == "#d32ce6"
    assert item["name_tag"] == "Old <Faithful>"
    assert item["float_value"] == pytest.approx(0.2213)
    assert item["paint_seed"] == 661
    assert item["inspect_link"].endswith(f"S{FAKE_STEAM_ID}A999D123")
    assert [s["name"] for s in item["stickers"]] == ["Crown (Foil)", 'Team "Q" | Test 2030']
    assert [s["slot"] for s in item["stickers"]] == [0, 1]


def test_parse_asset_without_description_is_skipped():
    assert parse_asset({"assetid": "1"}, None, None, FAKE_STEAM_ID) is None
