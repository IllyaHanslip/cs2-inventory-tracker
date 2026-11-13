"""Build the read-only live demo (GitHub Pages) from the made-up demo inventory.

    python scripts/build_demo_site.py --out site            # what the "Live demo" workflow runs
    python scripts/build_demo_site.py --out site --offline  # no item pictures / item details

It runs the real app on a fresh demo database in a temporary folder, asks it every question
the dashboard can ask, and saves the answers as JSON next to a copy of the page and its static
files. static/js/demo-site.js answers from those files when the page is served without the app.
Nothing from your own data folder is touched or included.

The demo also lets you try confirming the changes a Steam sync noticed (Containers tab). Each
combination of confirmed changes is worked out here, on its own copy of the database, and only
the answers that differ are saved (data/states/<ids>/), listed in data/states.json.
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RANGES = ("7d", "30d", "90d", "1y", "all")
PLAN_TARGETS = (5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100, 125, 150, 200, 250, 300, 400, 500, 600, 750,
                1000, 1250, 1500, 2000, 2500, 3000, 3500, 4000, 5000)
# The page's security policy, as the app sends it (a meta tag can't carry frame-ancestors).
CSP = ("default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: https://community.steamstatic.com https://*.steamstatic.com; "
       "connect-src 'self'; base-uri 'self'; form-action 'self'")
HEADERS = {"X-Requested-With": "cs2-tracker"}


def dump(data) -> bytes:
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


def answers(client, item_index: dict[str, int]) -> dict[str, bytes]:
    """Every answer the dashboard can ask for (item price histories only for names not in
    ``item_index`` yet - they don't depend on what you hold). New names are added to it."""
    files: dict[str, bytes] = {}

    def get(path: str):
        response = client.get(path)
        if response.status_code != 200:
            raise SystemExit(f"{path} -> HTTP {response.status_code}: {response.get_data(as_text=True)[:200]}")
        return response.get_json()

    def save(name: str, data) -> None:
        files[name] = dump(data)

    save("status.json", get("/api/status"))
    save("changes.json", get("/api/changes"))
    for scope in ("public", "total"):
        save(f"items-{scope}.json", get(f"/api/items?scope={scope}"))
        save(f"market-{scope}.json", get(f"/api/market-comparison?scope={scope}"))
        files[f"export-{scope}.csv"] = client.get(f"/api/export.csv?scope={scope}").get_data()
        for range_key in RANGES:
            save(f"history-{scope}-{range_key}.json", get(f"/api/history/portfolio?scope={scope}&range={range_key}"))
            save(f"insights-{scope}-{range_key}.json", get(f"/api/insights?scope={scope}&range={range_key}"))

    containers = get("/api/containers")
    save("containers.json", containers)
    for container in containers["containers"]:
        save(f"container-{container['id']}.json", get(f"/api/containers/{container['id']}"))

    items = get("/api/items?scope=total")["items"]
    for name in sorted({i["market_hash_name"] for i in items}):
        if name in item_index:
            continue
        item_index[name] = len(item_index)
        for range_key in RANGES:
            save(f"item-history-{item_index[name]}-{range_key}.json",
                 get(f"/api/history/item?name={quote(name)}&range={range_key}"))
    save("item-index.json", item_index)
    for item in items:
        save(f"quotes-{item['id']}.json", get(f"/api/quotes?name={quote(item['market_hash_name'])}&item={item['id']}"))
    for kind, wallet in (("cash", 0), ("wallet", 1)):
        save(f"plans-{kind}.json", [get(f"/api/sell-plan?target={t}&wallet={wallet}") for t in PLAN_TARGETS])
    return files


def event_sets(client) -> dict[str, bytes]:
    """"From an event": the list of events and each one's items (from the item catalogue)."""
    sets = client.get("/api/sets").get_json()["sets"]
    files = {}
    for n, entry in enumerate(sets):
        entry["file"] = n
        items = client.get(f"/api/sets/items?event={quote(entry['event'])}").get_json()
        files[f"set-{n}.json"] = dump(items)
    files["sets.json"] = dump({"sets": sets})
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="site", help="output folder (replaced)")
    parser.add_argument("--offline", action="store_true", help="skip downloading item pictures and details")
    args = parser.parse_args()

    out = Path(args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    (out / "data").mkdir(parents=True)

    from config import Config

    work = Path(tempfile.mkdtemp(prefix="cs2-demo-site-"))
    Config.DATA_DIR = work
    Config.AUTO_DAILY_SNAPSHOT = False
    Config.NOTIFICATIONS = False
    Config.ITEM_IMAGES = not args.offline
    Config.CSFLOAT_API_KEY = ""          # never use a key, even if a .env is around

    from app import create_app

    app = create_app(demo=True, reset_demo=True)
    if not args.offline:
        from backend import catalogue, images

        images.backfill()
        catalogue.item_meta(allow_fetch=True)
    Config.ITEM_IMAGES = False           # everything is fetched by now: nothing more to download
    client = app.test_client()
    base_db = Config.DATABASE_PATH

    def write(name: str, data: bytes, folder: Path) -> None:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    item_index: dict[str, int] = {}
    base = answers(client, item_index)
    base.update(event_sets(client) if not args.offline else {"sets.json": dump({"sets": []})})

    # Confirming changes in the demo: every combination of the ones that change what you hold.
    changes = json.loads(base["changes.json"])["changes"]
    doing = [c for c in changes if c["suggestion"] in ("store", "take_out")]
    states: dict[str, list[str]] = {}
    shared: dict[str, bytes] = {}
    for size in range(1, len(doing) + 1):
        for combo in itertools.combinations(doing, size):
            key = "-".join(str(c["id"]) for c in sorted(combo, key=lambda c: c["id"]))
            copy = work / f"state-{key}.db"
            with sqlite3.connect(base_db) as source, sqlite3.connect(copy) as target:
                source.backup(target)          # a consistent copy, including recent writes
            Config.DATABASE_PATH = copy
            for change in combo:
                response = client.post(f"/api/changes/{change['id']}", json={"action": change["suggestion"]}, headers=HEADERS)
                if response.status_code != 200:
                    raise SystemExit(f"Couldn't confirm demo change {change['id']}: {response.get_data(as_text=True)}")
            found = answers(client, item_index)
            different = {}
            for name, data in found.items():
                if name.startswith("item-history-"):
                    shared[name] = data           # by item name: the same in every state
                elif base.get(name) != data:
                    different[name] = data
            for name, data in different.items():
                write(name, data, out / "data" / "states" / key)
            states[key] = sorted(different)
    Config.DATABASE_PATH = base_db
    base["item-index.json"] = dump(item_index)   # includes names only some states have
    for name, data in {**base, **shared}.items():
        write(name, data, out / "data")
    write("states.json", dump({"changes": [c["id"] for c in doing], "states": states}), out / "data")

    # The page itself: relative links (the site lives under /<repo>/), the demo switch and the CSP.
    html = client.get("/").get_data(as_text=True)
    html = html.replace('href="/static/', 'href="static/').replace('src="/static/', 'src="static/')
    html = html.replace('<html lang="en">', '<html lang="en" data-demo-site="1">', 1)
    html = html.replace('<meta charset="utf-8">',
                        f'<meta charset="utf-8">\n  <meta http-equiv="Content-Security-Policy" content="{CSP}">', 1)
    html = re.sub(r"<title>[^<]*</title>", "<title>CS2 Inventory Intelligence - live demo</title>", html, count=1)
    (out / "index.html").write_text(html, encoding="utf-8")
    shutil.copytree(ROOT / "static", out / "static")
    (out / ".nojekyll").write_text("", encoding="utf-8")   # serve files as they are
    shutil.rmtree(work, ignore_errors=True)
    count = sum(1 for p in (out / "data").rglob("*") if p.is_file())
    print(f"Demo site written to {out} ({count} data files, {len(states)} confirm-a-change states)")


if __name__ == "__main__":
    main()
