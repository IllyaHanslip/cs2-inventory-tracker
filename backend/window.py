"""The app's own small window, in place of a console window: our icon on the taskbar, what the app
is doing, and Open dashboard / Stop buttons. Closing it stops the app, as closing the console did.

Used by the Windows app and by launch.bat. The web server runs on a thread beside it; Tk only ever
runs on the main thread (the start-up thread just sets ``state``, which the window reads).
"""
from __future__ import annotations

import base64
import logging
import struct
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from config import BASE_DIR

# Loaded here, before start-up begins on its own thread, so the window never waits on a module that
# start-up is in the middle of loading.
from . import db
from .jobs import jobs

log = logging.getLogger(__name__)

APP_ID = "CS2InventoryIntelligence"
TITLE = "CS2 Inventory Intelligence"
COLORS = {
    "bg": "#171a21", "text": "#eef0f3", "muted": "#8a919c", "accent": "#3987e5", "accent_hover": "#5598e7",
    "button": "#262b36", "button_hover": "#323846", "error": "#ef6461",
}


def icon_path() -> Path:
    """static/icon.ico: next to app.py, or unpacked from the Windows app."""
    return Path(getattr(sys, "_MEIPASS", None) or BASE_DIR) / "static" / "icon.ico"


def icon_png(size: int) -> bytes | None:
    """One size of the icon as a PNG (static/icon.ico holds PNG images)."""
    try:
        data = icon_path().read_bytes()
        for i in range(struct.unpack_from("<H", data, 4)[0]):
            width, _h, _c, _r, _p, _b, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
            blob = data[offset:offset + length]
            if (width or 256) == size and blob[:4] == b"\x89PNG":
                return blob
    except (OSError, struct.error):
        pass
    return None


def _ago(iso: str | None) -> str | None:
    """"just now", "5 minutes ago", "3 hours ago", "2 days ago"."""
    try:
        then = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    minutes = int((datetime.now(timezone.utc) - then).total_seconds() // 60)
    if minutes < 2:
        return "just now"
    if minutes < 90:
        return f"{minutes} minutes ago"
    if minutes < 36 * 60:
        return f"{round(minutes / 60)} hours ago"
    return f"{round(minutes / 1440)} days ago"


def status_line() -> str:
    """What the app is doing: a running job and its progress, else when prices were last updated."""
    job = jobs.status()
    if job and job["status"] == "running":
        working = [s for s in job.get("steps") or [] if not s["finished"] and s["total"]]
        if working:
            step = working[0]
            return f"{job['label']}: {step['label']} {step['done']:,} of {step['total']:,}"
        return f"{job['label']}: {job['message']}"
    row = db.query_one("SELECT MAX(fetched_at) AS last FROM market_prices WHERE is_manual = 0")
    when = _ago(row["last"] if row else None)
    return f"Prices updated {when}." if when else "No prices yet: open the dashboard and sync your Steam inventory."


def _own_taskbar_button() -> None:
    """From source (pythonw), a taskbar button of its own with our icon, not Python's."""
    if sys.platform == "win32" and not getattr(sys, "frozen", False):
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (AttributeError, OSError):
            pass


def _dark_title_bar(root) -> None:
    """Windows 10/11: a dark title bar to match the window."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        on = ctypes.c_int(1)
        for attribute in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (Windows 11 / older Windows 10)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(on), ctypes.sizeof(on)) == 0:
                break
    except (AttributeError, OSError):
        pass


class AppWindow:
    def __init__(self, url: str, demo: bool, state: dict[str, Any]):
        import tkinter as tk
        from tkinter import messagebox

        self.tk = tk
        self.messagebox = messagebox
        self.url = url
        self.state = state
        self.root = root = tk.Tk()
        root.title(TITLE + (" (demo data)" if demo else ""))
        try:
            root.iconbitmap(default=str(icon_path()))
        except tk.TclError:
            pass
        root.configure(bg=COLORS["bg"], padx=22, pady=18)
        root.resizable(False, False)
        font = ("Segoe UI", 10)

        head = tk.Frame(root, bg=COLORS["bg"])
        head.pack(fill="x")
        png = icon_png(48)
        if png:
            self.image = tk.PhotoImage(data=base64.b64encode(png))
            tk.Label(head, image=self.image, bg=COLORS["bg"]).pack(side="left", padx=(0, 14))
        text = tk.Frame(head, bg=COLORS["bg"])
        text.pack(side="left", fill="x")
        tk.Label(text, text=TITLE + (" - demo data" if demo else ""), font=("Segoe UI Semibold", 13),
                 fg=COLORS["text"], bg=COLORS["bg"]).pack(anchor="w")
        link = tk.Label(text, text=url, font=font, fg=COLORS["accent"], bg=COLORS["bg"], cursor="hand2")
        link.pack(anchor="w")
        link.bind("<Button-1>", lambda _e: self.open_dashboard())

        self.status = tk.Label(root, text="Starting…", font=font, fg=COLORS["muted"], bg=COLORS["bg"],
                               justify="left", anchor="w", wraplength=400)
        self.status.pack(fill="x", pady=(14, 14))

        buttons = tk.Frame(root, bg=COLORS["bg"])
        buttons.pack(fill="x")
        self.open_button = self._button(buttons, "Open dashboard", self.open_dashboard, primary=True)
        self.open_button.pack(side="left")
        self._button(buttons, "Stop", self.close).pack(side="left", padx=(10, 0))
        tk.Label(root, text="Closing this window stops the app.", font=("Segoe UI", 9), fg=COLORS["muted"],
                 bg=COLORS["bg"], anchor="w").pack(fill="x", pady=(12, 0))

        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Return>", lambda _e: self.open_dashboard())
        _dark_title_bar(root)
        root.update_idletasks()
        width, height = max(root.winfo_reqwidth(), 440), root.winfo_reqheight()
        root.geometry(f"{width}x{height}+{(root.winfo_screenwidth() - width) // 2}+{(root.winfo_screenheight() - height) // 3}")
        self.tick()

    def _button(self, parent, label: str, command: Callable[[], None], primary: bool = False):
        tk = self.tk
        bg, hover = (COLORS["accent"], COLORS["accent_hover"]) if primary else (COLORS["button"], COLORS["button_hover"])
        button = tk.Button(parent, text=label, command=command, font=("Segoe UI Semibold", 10), fg="#ffffff",
                           bg=bg, activebackground=hover, activeforeground="#ffffff", relief="flat", bd=0,
                           padx=16, pady=7, cursor="hand2", highlightthickness=0)
        button.bind("<Enter>", lambda _e: button.configure(bg=hover))
        button.bind("<Leave>", lambda _e: button.configure(bg=bg))
        return button

    def tick(self) -> None:
        """Every couple of seconds: what the app is doing."""
        if self.state.get("error"):
            self.status.configure(text=f"Couldn't start: {self.state['error']}", fg=COLORS["error"])
            return
        if self.state.get("ready"):
            try:
                self.status.configure(text=status_line())
            except Exception:  # noqa: BLE001 - a status line must never break the window
                log.exception("Couldn't read the app's status")
        self.root.after(2000 if self.state.get("ready") else 300, self.tick)

    def open_dashboard(self) -> None:
        if self.state.get("ready"):
            webbrowser.open(self.url)

    def close(self) -> None:
        if self.state.get("ready") and jobs.busy():
            job = jobs.status() or {}
            if not self.messagebox.askyesno(TITLE, f"{job.get('label', 'An update')} is still running. Stop the app anyway?",
                                            parent=self.root):
                return
        self.root.destroy()


def run(start: Callable[[], Any], *, host: str, port: int, url: str, demo: bool,
        on_ready: Callable[[], None] | None = None) -> None:
    """Show the window at once, start the app (``start`` returns the Flask app) and its web server
    beside it, and stop everything when the window closes."""
    from werkzeug.serving import make_server

    from .http_client import SHUTDOWN

    state: dict[str, Any] = {}

    def boot() -> None:
        try:
            server = make_server(host, port, start(), threaded=True)
        except Exception as exc:  # noqa: BLE001 - shown in the window
            log.exception("The app couldn't start")
            state["error"] = str(exc) or exc.__class__.__name__
            return
        state["server"] = server
        threading.Thread(target=server.serve_forever, name="web-server", daemon=True).start()
        state["ready"] = True
        if on_ready:
            on_ready()

    _own_taskbar_button()
    window = AppWindow(url, demo, state)
    threading.Thread(target=boot, name="start-up", daemon=True).start()
    try:
        window.root.mainloop()
    finally:
        SHUTDOWN.set()
        if state.get("server"):
            state["server"].shutdown()
