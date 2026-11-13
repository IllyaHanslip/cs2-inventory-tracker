"""Items moved into / out of storage units, noticed by Steam syncs (moves.py)."""
from __future__ import annotations

from backend import db, moves, steam_inventory
from tests.conftest import FAKE_STEAM_ID, HEADERS


def make(asset_id, name, **extra):
    unit = name == "Storage Unit"
    return {
        "asset_id": asset_id, "steam_id": FAKE_STEAM_ID, "market_hash_name": name, "name": name,
        "category": "container" if unit or "Case" in name else "weapon_skin", "item_type": None, "weapon": None,
        "rarity": None, "rarity_color": None, "exterior": None, "float_value": None, "paint_seed": None,
        "name_tag": "Long Holds" if unit else None, "quantity": 1, "icon_url": None, "inspect_link": None,
        "tradable": 1, "marketable": 0 if unit else 1, "stickers": [], "storage_count": None, **extra,
    }


def pending():
    return moves.pending()["changes"]


def unit_id():
    return db.query_one("SELECT id FROM items WHERE market_hash_name = 'Storage Unit'")["id"]


def listed(name):
    row = db.query_one("SELECT SUM(quantity) AS n FROM items WHERE container_id IS NOT NULL AND market_hash_name = ?", (name,))
    return row["n"] or 0


def test_storage_count_from_the_description():
    assert steam_inventory.parse_unit_count([{"value": " "}, {"value": "Number of Items: 1,000"}]) == 1000
    assert steam_inventory.parse_unit_count([{"value": "<span>Number of Items: 84</span>"}]) == 84
    assert steam_inventory.parse_unit_count([{"value": "Modification Date: today"}]) is None


def test_exact_subset():
    assert moves._exact_subset([15, 1, 3], 16) == ([0, 1], True)
    assert moves._exact_subset([1, 1], 1)[1] is False                 # two ways: a best guess
    assert moves._exact_subset([5, 5], 3) == (None, False)


def test_moves_in_and_out_of_a_storage_unit(client, steam):
    awp = {"float_value": 0.25, "paint_seed": 7}
    base = [make(f"case-{i}", "Recoil Case") for i in (1, 2, 3)] + [make("awp-1", "AWP | Atheris (Field-Tested)", **awp)]
    first = steam(make("unit-1", "Storage Unit", storage_count=5), *base, make("ak-1", "AK-47 | Slate (Field-Tested)"))
    assert first["changes"] == 0 and not pending()                    # nothing to compare with yet
    client.post(f"/api/containers/{unit_id()}/items", json={"items": [{"market_hash_name": "Fracture Case", "quantity": 5}]},
                headers=HEADERS)

    # 3 cases and the AWP go into the unit (5 -> 9); exactly those add up to 4.
    result = steam(make("unit-1", "Storage Unit", storage_count=9), make("ak-1", "AK-47 | Slate (Field-Tested)"))
    assert result["changes"] == 2
    changes = {c["name"]: c for c in pending()}
    assert changes["Recoil Case"]["quantity"] == 3 and changes["Recoil Case"]["suggestion"] == "store"
    assert changes["AWP | Atheris (Field-Tested)"]["container_id"] == unit_id()
    assert all(c["sure"] and (c["unit_before"], c["unit_after"]) == (5, 9) for c in changes.values())
    assert client.post("/api/changes/apply-suggested", headers=HEADERS).get_json()["applied"] == 2
    assert listed("Recoil Case") == 3
    stored_awp = db.query_one("SELECT float_value, paint_seed, container_id FROM items WHERE market_hash_name LIKE 'AWP%'")
    assert (stored_awp["float_value"], stored_awp["paint_seed"], stored_awp["container_id"]) == (0.25, 7, unit_id())
    box = client.get("/api/containers").get_json()["containers"][0]
    assert box["contents_count"] == box["steam_count"] == 9

    # The AK is sold: it left while the unit didn't grow.
    steam(make("unit-1", "Storage Unit", storage_count=9))
    (sold,) = pending()
    assert sold["suggestion"] == "gone" and sold["sure"]
    client.post(f"/api/changes/{sold['id']}", json={"action": "gone"}, headers=HEADERS)
    assert not pending()

    # Two cases come back out (9 -> 7): they're new Steam items, and still on the unit's list.
    steam(make("unit-1", "Storage Unit", storage_count=7), make("case-4", "Recoil Case"), make("case-5", "Recoil Case"))
    (back,) = pending()
    assert (back["kind"], back["quantity"], back["suggestion"], back["container_id"]) == ("back", 2, "take_out", unit_id())
    r = client.post(f"/api/changes/{back['id']}", json={"action": "take_out"}, headers=HEADERS)
    assert r.status_code == 200 and r.get_json()["pending"] == 0
    assert listed("Recoil Case") == 1
    assert client.get("/api/containers").get_json()["containers"][0]["contents_count"] == 7


def test_reissued_items_keep_your_notes_and_returning_items_cancel(client, steam):
    ak = {"float_value": 0.1234, "paint_seed": 99}
    steam(make("unit-1", "Storage Unit", storage_count=0), make("ak-1", "AK-47 | Redline (Field-Tested)", **ak),
          make("case-1", "Recoil Case"))
    ak_id = db.query_one("SELECT id FROM items WHERE asset_id = 'ak-1'")["id"]
    client.put(f"/api/items/{ak_id}", json={"notes": "keeper", "purchase_price": 20, "purchase_currency": "USD"},
               headers=HEADERS)
    # A sticker is applied: Steam gives the AK a new asset id. Same float, so it's the same AK.
    steam(make("unit-1", "Storage Unit", storage_count=0), make("case-1", "Recoil Case"),
          make("ak-2", "AK-47 | Redline (Field-Tested)", **ak, stickers=[{"slot": 0, "kind": "sticker", "name": "Crown (Foil)", "scrape": 0}]))
    assert not pending()
    new_ak = db.query_one("SELECT notes, purchase_price FROM items WHERE asset_id = 'ak-2'")
    assert (new_ak["notes"], new_ak["purchase_price"]) == ("keeper", 20)

    # The case leaves, then comes back before you've said where it went: the change is dropped.
    steam(make("unit-1", "Storage Unit", storage_count=0), make("ak-2", "AK-47 | Redline (Field-Tested)", **ak))
    assert [c["name"] for c in pending()] == ["Recoil Case"]
    steam(make("unit-1", "Storage Unit", storage_count=0), make("ak-2", "AK-47 | Redline (Field-Tested)", **ak),
          make("case-9", "Recoil Case"))
    assert not pending()


def test_without_counts_from_steam_you_choose(client, steam):
    steam(make("unit-1", "Storage Unit"), make("case-1", "Recoil Case"), make("case-2", "Recoil Case"))
    steam(make("unit-1", "Storage Unit"))
    (change,) = pending()
    assert change["suggestion"] is None and change["quantity"] == 2
    assert client.post("/api/changes/apply-suggested", headers=HEADERS).get_json()["pending"] == 1
    r = client.post(f"/api/changes/{change['id']}", json={"action": "store"}, headers=HEADERS)
    assert r.status_code == 400 and "Choose the storage unit" in r.get_json()["error"]
    r = client.post(f"/api/changes/{change['id']}", json={"action": "store", "container_id": unit_id()}, headers=HEADERS)
    assert r.status_code == 200 and listed("Recoil Case") == 2
    assert client.post(f"/api/changes/{change['id']}", json={"action": "gone"}, headers=HEADERS).status_code == 400


# --------------------------------------------------------------------------- "From an event"
NAMES = [
    "Sticker | NiKo (Gold) | Paris 2023", "Sticker | NiKo | Paris 2023", "Sticker | Vitality (Holo) | Paris 2023",
    "Sticker Slab | 3DMAX (Gold) | Budapest 2025", "Paris 2023 Legends Sticker Capsule",
    "Paris 2023 Mirage Souvenir Package", "Sticker | Natus Vincere (Foil) | ESL One Cologne 2015",
    "AK-47 | Redline (Field-Tested)", "Recoil Case", "Sticker | BF 2042", "Berlin 2019 Legends (Holo-Foil)",
    "Sticker | Team Dignitas | DreamHack 2014", "DreamHack Winter 2014 Legends (Foil)",
]


def test_event_sets(client, monkeypatch):
    from backend import catalogue, events

    monkeypatch.setattr(catalogue, "known_names", lambda allow_fetch=True: [(n.casefold(), n) for n in NAMES])
    monkeypatch.setattr(events, "MIN_ITEMS", 1)
    events._memo = None
    sets = client.get("/api/sets").get_json()["sets"]
    # Newest first; "BF 2042" (a collaboration, dated in the future) isn't a tournament.
    assert [s["event"] for s in sets] == ["Budapest 2025", "Paris 2023", "Berlin 2019", "Cologne 2015", "DreamHack 2014"]
    assert {s["event"]: s["count"] for s in sets}["DreamHack 2014"] == 2          # "DreamHack Winter 2014" too
    old = client.get("/api/sets/items?event=Berlin 2019").get_json()["items"][0]
    assert old["kind"] == "Capsule"
    items = client.get("/api/sets/items?event=Paris 2023").get_json()["items"]
    assert [(i["kind"], i["subject"], i["variant"]) for i in items] == [
        ("Sticker", "NiKo", "Paper"), ("Sticker", "NiKo", "Gold"), ("Sticker", "Vitality", "Holo"),
        ("Capsule", "Legends Sticker Capsule", None), ("Souvenir package", "Mirage Souvenir Package", None),
    ]
    assert items[1]["name"] == "Sticker | NiKo (Gold) | Paris 2023"          # Steam's exact name
    slab = client.get("/api/sets/items?event=Budapest 2025").get_json()["items"][0]
    assert (slab["kind"], slab["subject"], slab["variant"]) == ("Sticker slab", "3DMAX", "Gold")
    assert client.get("/api/sets/items").status_code == 400


def test_first_sync_after_updating_asks_instead_of_guessing_sold(client, steam):
    """Counts saved for the first time: an item that left can't be called "sold" yet."""
    steam(make("unit-1", "Storage Unit"), make("case-1", "Recoil Case"))          # older version: no count saved
    steam(make("unit-1", "Storage Unit", storage_count=1))                        # case moved in meanwhile
    (change,) = pending()
    assert change["suggestion"] is None
    # From then on the counts are compared: a brand-new unit starts from zero.
    steam(make("unit-1", "Storage Unit", storage_count=1), make("unit-2", "Storage Unit", storage_count=0,
                                                                name_tag="New"), make("case-2", "Recoil Case"))
    steam(make("unit-1", "Storage Unit", storage_count=1), make("unit-2", "Storage Unit", storage_count=1, name_tag="New"))
    new = [c for c in pending() if c["id"] != change["id"]]
    assert len(new) == 1 and new[0]["suggestion"] == "store" and new[0]["container_label"] == "New"


# --------------------------------------------------------------------------- public inventories hide storage units
def manual_unit(client, tag, **contents):
    unit = client.post("/api/items", json={"market_hash_name": "Storage Unit", "category": "container", "name_tag": tag,
                                           "is_storage": True}, headers=HEADERS).get_json()["item"]["id"]
    if contents:
        client.post(f"/api/containers/{unit}/items", json={"items": [{"market_hash_name": n.replace("_", " "), "quantity": q}
                                                                     for n, q in contents.items()]}, headers=HEADERS)
    return unit


def test_without_storage_units_in_steam_the_unit_holding_the_item_is_suggested(client, steam):
    stash = manual_unit(client, "Stash", Recoil_Case=40)
    manual_unit(client, "Other", Fracture_Case=3)
    steam(make("case-1", "Recoil Case"), make("case-2", "Recoil Case"), make("p250", "P250 | Sand Dune (Field-Tested)"))
    steam(make("p250", "P250 | Sand Dune (Field-Tested)"))                         # 2 cases went into storage
    (change,) = pending()
    assert (change["suggestion"], change["container_id"], change["sure"], change["unit_holds"]) == ("store", stash, False, 40)
    client.post("/api/changes/apply-suggested", headers=HEADERS)                  # Stash now lists 42
    # Three cases come back: still listed in Stash, so they'd be counted twice - suggested to take off.
    steam(make("p250", "P250 | Sand Dune (Field-Tested)"), *[make(f"case-{i}", "Recoil Case") for i in (7, 8, 9)])
    back = next(c for c in pending() if c["kind"] == "back")
    assert (back["quantity"], back["suggestion"], back["container_id"]) == (3, "take_out", stash)
