"""CS2 Inventory & Multi-Market Intelligence Platform - entry point.

    python app.py              # run with your real data (data/cs2_tracker.db)
    python app.py --demo       # run with synthetic demo data (data/demo.db)
    python app.py --demo --reset-demo   # regenerate the demo data
    python app.py --open       # also open the dashboard in your browser (used by launch.bat)
    python app.py --update     # daily update without the dashboard, then exit (scheduled task)
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

from flask import Flask, jsonify, render_template, request

from config import Config

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_HEADER = ("X-Requested-With", "cs2-tracker")

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https://community.steamstatic.com https://*.steamstatic.com; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def create_app(*, demo: bool = False, reset_demo: bool = False) -> Flask:
    if demo:
        Config.DEMO = True
        Config.DATABASE_PATH = Config.DATA_DIR / "demo.db"
        Config.AUTO_DAILY_SNAPSHOT = False
        if reset_demo:
            for suffix in ("", "-wal", "-shm"):
                path = Config.DATABASE_PATH.with_name(Config.DATABASE_PATH.name + suffix)
                if path.exists():
                    path.unlink()

    from backend import db, settings
    from backend.routes import bp

    db.init_db()
    added = settings.enable_new_markets()
    if added:
        logging.getLogger(__name__).info("New price sources switched on: %s", ", ".join(added))
    if demo:
        from backend.demo import seed_if_empty

        if seed_if_empty():
            logging.getLogger(__name__).info("Demo database created at %s", Config.DATABASE_PATH)
    try:
        from backend.history import convert_market_snapshots

        convert_market_snapshots()  # history saved before values were after fees (one-off)
    except Exception:  # noqa: BLE001 - never stop the app starting over this
        logging.getLogger(__name__).exception("Couldn't convert older portfolio history to after-fee values")

    app = Flask(__name__)
    app.json.sort_keys = False
    app.register_blueprint(bp)

    @app.before_request
    def guard_requests():
        # DNS-rebinding protection: when bound to localhost, only answer localhost.
        host = (request.host or "").rsplit(":", 1)[0].strip("[]").lower()
        if Config.HOST in LOCAL_HOSTS and host not in LOCAL_HOSTS:
            return jsonify(error="Host not allowed"), 403
        # CSRF protection: browsers can't add custom headers to cross-site form posts.
        if request.method in MUTATING_METHODS and request.headers.get(CSRF_HEADER[0]) != CSRF_HEADER[1]:
            return jsonify(error="Missing request header"), 403
        return None

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    @app.get("/")
    def index():
        return render_template("index.html", demo=Config.DEMO)

    return app


def _background_update(url: str) -> int:
    """``--update``: the daily update with no window (run by the scheduled task), logged to
    data/update.log. If the dashboard is open, it keeps itself up to date, so this does nothing."""
    Config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(Config.DATA_DIR / "update.log", maxBytes=256_000,
                                                   backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    log = logging.getLogger("update")
    if _running_instance(url) is not None:
        log.info("The dashboard is open and updates itself - nothing to do.")
        return 0
    try:
        create_app()
        from backend.jobs import background_update

        outcome = background_update()
    except Exception:  # noqa: BLE001 - log everything, there's no window to show it in
        log.exception("Background update failed")
        return 1
    if outcome.get("skipped"):
        log.info("Background update skipped: %s", outcome["skipped"])
    else:
        log.info("Background update done (%s; %s)", outcome["ran"], "; ".join(outcome["reasons"]) or "daily snapshot")
        for warning in outcome.get("warnings", []):
            log.warning(warning)
    return 0


def _running_instance(url: str) -> dict | None:
    """The /api/status payload if this app is already answering at ``url``, else None."""
    try:
        with urllib.request.urlopen(url + "api/status", timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "settings" in data else None


def _port_is_free(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _open_browser_when_ready(url: str) -> None:
    from backend.http_client import SHUTDOWN

    def wait_then_open() -> None:
        for _ in range(120):  # up to a minute
            if SHUTDOWN.is_set():
                return
            if _running_instance(url) is not None:
                webbrowser.open(url)
                return
            time.sleep(0.5)

    threading.Thread(target=wait_then_open, name="open-browser", daemon=True).start()


def _alert(message: str, popup: bool) -> None:
    """Print a problem, and when started from the desktop shortcut also show a dialog box
    (the console window is minimised, so a printed message alone would go unseen)."""
    print(f"\n  {message}\n")
    if popup and sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "CS2 Inventory Intelligence", 0x10)
        except Exception:  # noqa: BLE001 - the printed message is enough
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="CS2 Inventory & Multi-Market Intelligence Platform")
    parser.add_argument("--demo", action="store_true", help="use synthetic demo data (separate database)")
    parser.add_argument("--reset-demo", action="store_true", help="regenerate the demo database")
    parser.add_argument("--host", help=f"interface to bind (default {Config.HOST})")
    parser.add_argument("--port", type=int, help=f"port (default {Config.PORT})")
    parser.add_argument("--open", action="store_true",
                        help="open the dashboard in your browser (if already running, just open it)")
    parser.add_argument("--update", action="store_true",
                        help="refresh prices and save today's snapshot without the dashboard, then exit "
                             "(used by the scheduled daily update)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    if args.host:
        Config.HOST = args.host
    port = args.port or Config.PORT
    demo = args.demo or args.reset_demo
    shown_host = "127.0.0.1" if Config.HOST in ("0.0.0.0", "::") else Config.HOST
    url = f"http://{shown_host}:{port}/"

    if args.update:
        sys.exit(_background_update(url))

    running = _running_instance(url)
    if running is not None:
        if args.open:
            if bool(running.get("demo")) != demo:
                print("  Note: the copy already running uses "
                      f"{'demo' if running.get('demo') else 'your real'} data. Close it first to switch.")
            print(f"  Already running - opening {url}")
            webbrowser.open(url)
            return
        _alert(f"CS2 Inventory Intelligence is already running at {url}", popup=False)
        sys.exit(1)
    if not _port_is_free(Config.HOST, port):
        _alert(f"Port {port} is being used by another program, so the dashboard can't start.\n\n"
               f"Add a line such as PORT=5001 to the .env file and try again.", popup=args.open)
        sys.exit(1)

    app = create_app(demo=demo, reset_demo=args.reset_demo)

    from backend.http_client import SHUTDOWN
    from backend.jobs import find_pictures_now, start_scheduler

    # Pictures for items added by hand, before the page first loads (it would otherwise show
    # placeholders until it next reloaded). Waits a few seconds at most.
    find_pictures_now(timeout=8)
    start_scheduler()
    print(f"\n  CS2 Inventory Intelligence running at {url}"
          f"{'  (DEMO DATA)' if Config.DEMO else ''}\n"
          f"  Keep this window open while you use the dashboard; close it (or press Ctrl+C) to stop.\n")
    if Config.LEGACY_STEAM_API_KEY_SET:
        print("  NOTE: STEAM_API_KEY in your .env is no longer used. Delete that line, and revoke the key at\n"
              "        https://steamcommunity.com/dev/apikey so it can't be misused.\n")
    if args.open:
        _open_browser_when_ready(url)
    try:
        app.run(host=Config.HOST, port=port, debug=Config.DEBUG, use_reloader=False, threaded=True)
    finally:
        SHUTDOWN.set()


if __name__ == "__main__":
    main()
