"""Windows notifications, with no extra packages: the toast is handed to Windows' own notification
API through PowerShell. Used by the background update (``app.py --update``) to say what it caught.

scripts/install-daily-update.ps1 registers an app identity (so notifications say "CS2 Inventory
Intelligence" rather than "Windows PowerShell") and a ``cs2-inventory:`` link, so clicking a
notification opens the dashboard. Without them, notifications still show, just less nicely.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from xml.sax.saxutils import escape, quoteattr

from config import Config

log = logging.getLogger(__name__)

APP_ID = "CS2InventoryIntelligence"
POWERSHELL_APP_ID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"
OPEN_LINK = "cs2-inventory:open"

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml($env:CS2T_TOAST_XML)
$toast = [Windows.UI.Notifications.ToastNotification]::new($doc)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:CS2T_TOAST_APP).Show($toast)
"""


def _registered(path: str) -> bool:
    """True if HKEY_CURRENT_USER\\Software\\Classes\\<path> exists (set up by the installer script)."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"Software\\Classes\\{path}"):
            return True
    except OSError:
        return False


def toast_xml(title: str, lines: list[str], link: str | None = None) -> str:
    texts = "".join(f"<text>{escape(t)}</text>" for t in [title, *lines[:2]])
    launch = f" activationType=\"protocol\" launch={quoteattr(link)}" if link else ""
    return f"<toast{launch}><visual><binding template=\"ToastGeneric\">{texts}</binding></visual></toast>"


def toast(title: str, lines: list[str]) -> bool:
    """Show a Windows notification. Returns False (and does nothing) off Windows or when switched off."""
    if sys.platform != "win32" or not Config.NOTIFICATIONS:
        return False
    app_id = APP_ID if _registered(f"AppUserModelId\\{APP_ID}") else POWERSHELL_APP_ID
    link = OPEN_LINK if _registered("cs2-inventory") else None
    env = {**os.environ, "CS2T_TOAST_XML": toast_xml(title, lines, link), "CS2T_TOAST_APP": app_id}
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _SCRIPT],
            env=env, capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("Couldn't show a notification (%s)", exc)
        return False
    if result.returncode != 0:
        log.warning("Couldn't show a notification: %s", (result.stderr or "").strip()[:300])
        return False
    return True
