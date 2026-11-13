"""Build the read-only live demo (GitHub Pages) from the made-up demo inventory.

    python scripts/build_demo_site.py --out site            # what the "Live demo" workflow runs
    python scripts/build_demo_site.py --out site --offline  # no item pictures / item details

It runs the real app on a fresh demo database in a temporary folder, asks it every question
the dashboard can ask, and saves the answers as JSON next to a copy of the page and its static
files. static/js/demo-site.js answers from those files when the page is served without the app.
Nothing from your own data folder is touched or included.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RANGES = ("7d", "30d", "90d", "1y", "all")
PLAN_TARGETS = (5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100, 125, 150, 200, 250, 300, 400, 500, 600, 750,
                1000, 1250, 1500, 2000, 2500, 3000, 3500, 4000, 5000)
# The page's security policy, as the app sends it (a meta tag can't carry frame-ancestors).
CSP = ("default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: https://community.steamstatic.com https://*.steamstatic.com; "
       "connect-src 'self'; base-uri 'self'; form-action 'self'")


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
    client = app.test_client()

    def get(path: str):
        response = client.get(path)
        if response.status_code != 200:
            raise SystemExit(f"{path} -> HTTP {response.status_code}: {response.get_data(as_text=True)[:200]}")
        return response.get_json()

    def save(name: str, data) -> None:
        (out / "data" / name).write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")

    save("status.json", get("/api/status"))
    for scope in ("public", "total"):
        save(f"items-{scope}.json", get(f"/api/items?scope={scope}"))
        save(f"market-{scope}.json", get(f"/api/market-comparison?scope={scope}"))
        (out / "data" / f"export-{scope}.csv").write_bytes(client.get(f"/api/export.csv?scope={scope}").get_data())
        for range_key in RANGES:
            save(f"history-{scope}-{range_key}.json", get(f"/api/history/portfolio?scope={scope}&range={range_key}"))
            save(f"insights-{scope}-{range_key}.json", get(f"/api/insights?scope={scope}&range={range_key}"))

    containers = get("/api/containers")
    save("containers.json", containers)
    for container in containers["containers"]:
        save(f"container-{container['id']}.json", get(f"/api/containers/{container['id']}"))

    items = get("/api/items?scope=total")["items"]
    names = sorted({i["market_hash_name"] for i in items})
    save("item-index.json", {name: n for n, name in enumerate(names)})
    from urllib.parse import quote

    for n, name in enumerate(names):
        for range_key in RANGES:
            save(f"item-history-{n}-{range_key}.json", get(f"/api/history/item?name={quote(name)}&range={range_key}"))
    for item in items:
        save(f"quotes-{item['id']}.json", get(f"/api/quotes?name={quote(item['market_hash_name'])}&item={item['id']}"))

    for kind, wallet in (("cash", 0), ("wallet", 1)):
        plans = [get(f"/api/sell-plan?target={t}&wallet={wallet}") for t in PLAN_TARGETS]
        save(f"plans-{kind}.json", plans)

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
    count = sum(1 for _ in (out / "data").iterdir())
    print(f"Demo site written to {out} ({count} data files)")


if __name__ == "__main__":
    main()
