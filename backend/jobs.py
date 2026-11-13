"""Background jobs (Steam sync, price refresh) and the daily snapshot scheduler."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from datetime import time as clock_time
from typing import Any, Callable

from config import Config

from .http_client import SHUTDOWN, ApiError, CancelledError

log = logging.getLogger(__name__)


class JobBusy(Exception):
    pass


class Job:
    def __init__(self, kind: str, label: str):
        self.id = uuid.uuid4().hex[:8]
        self.kind = kind
        self.label = label
        self.status = "running"
        self.message = "Starting…"
        self.progress = 0
        self.total = 0
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.error: str | None = None
        self.result: Any = None
        self.warnings: list[str] = []
        self.cancelled = False
        # Markets fetched one item at a time, side by side: each one's own progress.
        self.steps: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def update(self, message: str | None = None, progress: int | None = None, total: int | None = None) -> None:
        with self._lock:
            if message is not None:
                self.message = message
            if progress is not None:
                self.progress = progress
            if total is not None:
                self.total = total

    def step(self, key: str, label: str | None = None, done: int | None = None, total: int | None = None,
             note: str | None = None, finished: bool = False) -> None:
        """Progress of one part of the job (e.g. one market): done of total, and what it's doing."""
        with self._lock:
            entry = self.steps.setdefault(key, {"label": label or key, "done": 0, "total": 0, "note": "",
                                                "started": time.monotonic(), "finished": False})
            if label is not None:
                entry["label"] = label
            if done is not None:
                entry["done"] = done
            if total is not None:
                entry["total"] = total
            if note is not None:
                entry["note"] = note
            entry["finished"] = entry["finished"] or finished

    def _steps(self) -> list[dict[str, Any]]:
        out = []
        for entry in self.steps.values():
            left = None
            elapsed = time.monotonic() - entry["started"]
            if not entry["finished"] and entry["done"] and entry["total"] > entry["done"] and elapsed > 5:
                left = round(elapsed / entry["done"] * (entry["total"] - entry["done"]))
            out.append({"label": entry["label"], "done": entry["done"], "total": entry["total"],
                        "note": entry["note"], "finished": entry["finished"], "seconds_left": left})
        return out

    def warn(self, message: str) -> None:
        with self._lock:
            self.warnings.append(message)

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "label": self.label,
                "status": self.status,
                "message": self.message,
                "progress": self.progress,
                "total": self.total,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "error": self.error,
                "result": self.result,
                "warnings": list(self.warnings),
                "steps": self._steps(),
            }


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current: Job | None = None

    def busy(self) -> bool:
        return self.current is not None and self.current.status == "running"

    def start(self, kind: str, label: str, fn: Callable[..., Any], *args, **kwargs) -> Job:
        with self._lock:
            if self.busy():
                raise JobBusy(f"'{self.current.label}' is still running. Wait for it to finish or cancel it.")
            job = Job(kind, label)
            self.current = job

        def runner() -> None:
            try:
                job.result = fn(job, *args, **kwargs)
                job.status = "cancelled" if job.cancelled else "done"
                job.message = "Cancelled" if job.cancelled else "Finished"
            except CancelledError:
                job.status, job.message = "cancelled", "Cancelled"
            except (ApiError, ValueError) as exc:
                job.status, job.error = "error", str(exc)
                log.warning("Job %s failed: %s", label, exc)
            except Exception as exc:  # noqa: BLE001 - surface unexpected failures to the UI
                job.status, job.error = "error", f"Unexpected error: {exc}"
                log.exception("Job %s crashed", label)
            finally:
                job.finished_at = time.time()

        threading.Thread(target=runner, name=f"job-{kind}", daemon=True).start()
        return job

    def cancel(self) -> bool:
        if self.busy():
            self.current.cancelled = True
            self.current.update(message="Cancelling…")
            return True
        return False

    def status(self) -> dict[str, Any] | None:
        return self.current.to_dict() if self.current else None


jobs = JobManager()


# ---------------------------------------------------------------------------
# Job bodies
# ---------------------------------------------------------------------------
def sync_job(job: Job, steam_id: str) -> dict[str, Any]:
    from .steam_inventory import sync_inventory

    result = sync_inventory(steam_id, job)
    if not job.cancelled:
        job.update(message="Inventory saved. Fetching prices for new items…")
        from .refresh import refresh_prices

        result["prices"] = refresh_prices(job)
    return result


def refresh_job(job: Job, force: bool = False) -> dict[str, Any]:
    from .refresh import refresh_prices

    return refresh_prices(job, force=force)


def daily_job(job: Job) -> dict[str, Any]:
    """Re-sync the saved Steam inventory (if any), refresh prices and snapshot. Counts as today's
    daily update only if fresh prices arrived; otherwise it's tried again next hour."""
    from . import settings
    from .refresh import refresh_prices

    result: dict[str, Any] = {}
    steam_id = settings.get("steam_id")
    if Config.AUTO_SYNC_STEAM and steam_id:
        from .steam_inventory import sync_inventory

        try:
            result["sync"] = sync_inventory(steam_id, job)
        except (ApiError, ValueError) as exc:
            job.warn(f"Steam sync skipped: {exc}")
    result["prices"] = refresh_prices(job)
    result["complete"] = bool(result["prices"].get("complete", True))
    if result["complete"]:
        settings.set_value("last_daily_update", _now().astimezone(timezone.utc).replace(microsecond=0).isoformat())
    else:
        job.warn("No market could be reached (is the internet down?), so the daily update will be tried again next hour.")
    return result


# ---------------------------------------------------------------------------
# When the daily update runs
# ---------------------------------------------------------------------------
def _now() -> datetime:
    """Local time now (a function, so tests can set the clock)."""
    return datetime.now()


def daily_update_start(now: datetime | None = None) -> datetime:
    """The earliest the *automatic* (background) update may run today: DAILY_UPDATE_TIME (default
    12:00), local time. Opening the dashboard updates straight away, whatever the time."""
    now = now or _now()
    try:
        at = datetime.strptime(Config.DAILY_UPDATE_TIME.strip(), "%H:%M").time()
    except ValueError:
        at = clock_time(12, 0)
    return datetime.combine(now.date(), at)


def daily_update_due(now: datetime | None = None, automatic: bool = False) -> bool:
    """Whether today's daily update still needs to run. Once one has worked today - at any time,
    including when you opened the dashboard in the morning - it isn't due until tomorrow. Until
    then it stays due, so a run that couldn't reach the markets is retried next hour.
    ``automatic`` (the background task): also never before DAILY_UPDATE_TIME."""
    from . import settings

    now = now or _now()
    if automatic and now < daily_update_start(now):
        return False
    last = settings.get("last_daily_update")
    if last:
        try:
            done = datetime.fromisoformat(last).astimezone().replace(tzinfo=None)
        except (TypeError, ValueError):
            done = None
        if done is not None and done.date() == now.date():
            return False
    return True


def scheduled_action(now: datetime | None = None) -> tuple[str, str] | None:
    """What the open dashboard's hourly check should start: ("daily", why), ("refresh", why) or None.
    The dashboard updates whenever it's open (no 12:00 rule): the daily update if today's hasn't
    happened yet, then a refresh when prices are over 12 hours old, plus one-off catch-ups."""
    from . import db
    from .refresh import refresh_reasons

    if db.query_one("SELECT 1 FROM items LIMIT 1") is None or jobs.busy() or background_update_running():
        return None
    if daily_update_due(now):
        return "daily", "the daily update is due"
    reasons = refresh_reasons()
    return ("refresh", "; ".join(reasons)) if reasons else None


def seconds_to_next_hour(now: datetime | None = None) -> float:
    """Seconds until just after the next full hour, so checks happen at 12:00, 13:00, ..."""
    now = now or _now()
    following = (now + timedelta(hours=1)).replace(minute=0, second=5, microsecond=0)
    return max((following - now).total_seconds(), 30.0)


UPDATE_MARKER_MAX_AGE = 2 * 3600   # a marker older than this is left over from a crash


def _update_marker():
    return Config.DATA_DIR / ".update-running"


def background_update_running() -> bool:
    """True while ``app.py --update`` (the scheduled daily update) is working."""
    marker = _update_marker()
    try:
        return time.time() - marker.stat().st_mtime < UPDATE_MARKER_MAX_AGE
    except OSError:
        return False


def background_update() -> dict[str, Any]:
    """The daily update without the dashboard open (``app.py --update``, run by the Windows
    scheduled task from scripts/install-daily-update.ps1). Runs only when it's due: not before
    DAILY_UPDATE_TIME, not if today's update already happened (e.g. you opened the dashboard), and
    again next hour if the markets couldn't be reached. Checking uses no internet at all."""
    from . import db, images

    if db.query_one("SELECT 1 FROM items LIMIT 1") is None:
        return {"skipped": "no items yet"}
    now = _now()
    start = daily_update_start(now)
    if now < start:
        return {"skipped": f"it's before {start:%H:%M}, when the daily update starts"}
    if not daily_update_due(now, automatic=True):
        return {"skipped": "today's daily update is already done"}
    marker = _update_marker()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(str(time.time()), encoding="utf-8")
    try:
        images.backfill()
        job = Job("daily", "Daily update")
        result = daily_job(job)
        _notify_update(result["complete"])
        return {"ran": "daily update", "complete": result["complete"], "result": result, "warnings": job.warnings}
    finally:
        marker.unlink(missing_ok=True)


def _notify_update(complete: bool) -> None:
    """A Windows notification saying what the background update found (or that it'll try again)."""
    from . import insights, notify, portfolio
    from .valuation import Valuator

    try:
        if complete:
            valuator = Valuator()
            frame = portfolio.build_frame(valuator, "total")
            summary = portfolio.summarize(frame, valuator, "total")
            title, lines = insights.notification_text(insights.update_summary(frame, summary, valuator.base))
        else:
            following = _now() + timedelta(hours=1)
            when = f"at {following:%H}:00" if following.date() == _now().date() else "tomorrow"
            title = "Couldn't update your CS2 inventory"
            lines = [f"The markets couldn't be reached (is the internet down?). Trying again {when}."]
        notify.toast(title, lines)
    except Exception:  # noqa: BLE001 - a notification must never break the update
        log.exception("Couldn't prepare the update notification")


def find_pictures_now(timeout: float) -> None:
    """Find pictures for items added by hand, waiting at most ``timeout`` seconds. A slow download
    carries on in the background (later page loads pick it up), so start-up is never held long."""
    from . import images

    def run() -> None:
        try:
            images.backfill()
            from . import catalogue

            catalogue.item_meta(allow_fetch=True)   # item details for grouping, if missing (weekly)
        except Exception:  # noqa: BLE001
            log.exception("Finding item pictures failed")

    worker = threading.Thread(target=run, name="item-pictures", daemon=True)
    worker.start()
    worker.join(timeout)


def start_scheduler() -> None:
    """While the dashboard is open: a check at start-up and then on every full hour (see
    scheduled_action). The 12:00 rule only applies to the background task."""
    if not Config.AUTO_DAILY_SNAPSHOT:
        log.info("Daily snapshot scheduler disabled")
        return

    def loop() -> None:
        from . import images

        if SHUTDOWN.wait(10):  # give the web server a moment to start
            return
        while not SHUTDOWN.is_set():
            try:
                images.backfill()  # pictures for items added by hand (cheap when there's nothing to do)
            except Exception:  # noqa: BLE001
                log.exception("Finding item pictures failed")
            try:
                action = scheduled_action()
                if action:
                    kind, why = action
                    log.info("Starting the %s: %s", "daily update" if kind == "daily" else "price refresh", why)
                    if kind == "daily":
                        jobs.start("daily", "Daily update", daily_job)
                    else:
                        jobs.start("refresh", "Price refresh", refresh_job)
            except JobBusy:
                pass
            except Exception:  # noqa: BLE001
                log.exception("Scheduler tick failed")
            SHUTDOWN.wait(seconds_to_next_hour())

    threading.Thread(target=loop, name="scheduler", daemon=True).start()
