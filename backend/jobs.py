"""Background jobs (Steam sync, price refresh) and the daily snapshot scheduler."""
from __future__ import annotations

import logging
import threading
import time
import uuid
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
        self._lock = threading.Lock()

    def update(self, message: str | None = None, progress: int | None = None, total: int | None = None) -> None:
        with self._lock:
            if message is not None:
                self.message = message
            if progress is not None:
                self.progress = progress
            if total is not None:
                self.total = total

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
    """Re-sync the saved Steam inventory (if any), refresh prices and snapshot."""
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
    return result


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
    scheduled task from scripts/install-daily-update.ps1). Does nothing if today's snapshot is
    already saved and prices are fresh."""
    from . import db, images
    from .history import has_snapshot_today
    from .refresh import refresh_reasons

    if db.query_one("SELECT 1 FROM items LIMIT 1") is None:
        return {"skipped": "no items yet"}
    snapshot_done = has_snapshot_today()
    reasons = refresh_reasons()
    if snapshot_done and not reasons:
        return {"skipped": "already up to date today"}
    marker = _update_marker()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(str(time.time()), encoding="utf-8")
    try:
        images.backfill()
        job = Job("daily", "Daily update")
        result = refresh_job(job) if snapshot_done else daily_job(job)
        return {"ran": "price refresh" if snapshot_done else "daily update", "reasons": reasons,
                "result": result, "warnings": job.warnings}
    finally:
        marker.unlink(missing_ok=True)


def find_pictures_now(timeout: float) -> None:
    """Find pictures for items added by hand, waiting at most ``timeout`` seconds. A slow download
    carries on in the background (later page loads pick it up), so start-up is never held long."""
    from . import images

    def run() -> None:
        try:
            images.backfill()
        except Exception:  # noqa: BLE001
            log.exception("Finding item pictures failed")

    worker = threading.Thread(target=run, name="item-pictures", daemon=True)
    worker.start()
    worker.join(timeout)


def start_scheduler() -> None:
    """Checks at start-up and then hourly: runs the daily update once a day, and refreshes
    prices sooner when a market has no prices yet (e.g. just enabled, or a key just added)
    or the prices have gone stale."""
    if not Config.AUTO_DAILY_SNAPSHOT:
        log.info("Daily snapshot scheduler disabled")
        return

    def loop() -> None:
        from . import db, images
        from .history import has_snapshot_today
        from .refresh import refresh_reasons

        if SHUTDOWN.wait(10):  # give the web server a moment to start
            return
        while not SHUTDOWN.is_set():
            try:
                images.backfill()  # pictures for items added by hand (cheap when there's nothing to do)
            except Exception:  # noqa: BLE001
                log.exception("Finding item pictures failed")
            try:
                has_items = db.query_one("SELECT 1 FROM items LIMIT 1") is not None
                # Skip while the scheduled background update (app.py --update) is doing the same work.
                if has_items and not jobs.busy() and not background_update_running():
                    reasons = refresh_reasons()
                    if not has_snapshot_today():
                        log.info("No snapshot for today yet - starting the daily update")
                        jobs.start("daily", "Daily update", daily_job)
                    elif reasons:
                        log.info("Refreshing prices: %s", "; ".join(reasons))
                        jobs.start("refresh", "Price refresh", refresh_job)
            except JobBusy:
                pass
            except Exception:  # noqa: BLE001
                log.exception("Scheduler tick failed")
            SHUTDOWN.wait(Config.SCHEDULER_INTERVAL_MINUTES * 60)

    threading.Thread(target=loop, name="scheduler", daemon=True).start()
