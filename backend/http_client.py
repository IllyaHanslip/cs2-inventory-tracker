"""HTTP client with per-source throttling, retries and HTTP 429 handling.

Every external service gets its own ``RateLimitedClient`` so that, for example,
a Steam Market cool-down never slows down Skinport requests.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from email.utils import parsedate_to_datetime

import requests

from config import Config

log = logging.getLogger(__name__)

# Set on shutdown so sleeping background threads wake up immediately.
SHUTDOWN = threading.Event()


class ApiError(Exception):
    """An external API failed in a way the caller should report."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class RateLimitError(ApiError):
    """The API kept answering HTTP 429 (Too Many Requests)."""


class AuthError(ApiError):
    """The API rejected our credentials (HTTP 401/403)."""


class CancelledError(ApiError):
    """The background job using this client was cancelled while waiting."""


def interruptible_sleep(seconds: float, should_cancel=None) -> None:
    """Sleep in short slices so shutdown or a job cancel interrupts long back-offs."""
    end = time.monotonic() + max(0.0, seconds)
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        if SHUTDOWN.wait(min(remaining, 0.5)):
            raise ApiError("Application is shutting down")
        if should_cancel and should_cancel():
            raise CancelledError("Cancelled")


def _retry_after_seconds(resp: requests.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
        return max(0.0, when.timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class RateLimitedClient:
    """A ``requests.Session`` wrapper that spaces out calls and backs off politely."""

    def __init__(
        self,
        name: str,
        min_interval: float,
        *,
        base_backoff: float = 5.0,
        max_retries: int | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.name = name
        self.min_interval = min_interval
        self.base_backoff = base_backoff
        self.max_retries = Config.MAX_RETRIES if max_retries is None else max_retries
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": Config.USER_AGENT, "Accept": "application/json"})
        if headers:
            self.session.headers.update(headers)
        self._lock = threading.Lock()
        self._next_slot = 0.0
        self._cooldown_until = 0.0
        # Optional hooks set by a background job: progress messages and cancellation.
        self.listener = None
        self.should_cancel = None

    def _notify(self, message: str) -> None:
        if self.listener:
            self.listener(message)

    def _sleep(self, seconds: float) -> None:
        interruptible_sleep(seconds, self.should_cancel)

    # -- throttling ----------------------------------------------------------
    def _wait_for_slot(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot, self._cooldown_until)
            self._next_slot = slot + self.min_interval
        if slot - now > 5:
            self._notify(f"{self.name}: cooling down, next request in {int(slot - now)}s…")
        self._sleep(slot - now)

    def _backoff_delay(self, attempt: int) -> float:
        return min(Config.MAX_BACKOFF_SECONDS, self.base_backoff * (2 ** attempt)) + random.uniform(0, 1)

    def cooldown_remaining(self) -> float:
        return max(0.0, self._cooldown_until - time.monotonic())

    # -- requests ------------------------------------------------------------
    def get(self, url: str, *, params: dict | None = None, headers: dict | None = None) -> requests.Response:
        attempt = 0
        while True:
            self._wait_for_slot()
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=Config.REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                # Deliberately not echoing the exception text: it can contain the full
                # URL, and some URLs (Steam Web API) carry an API key as a parameter.
                if attempt >= self.max_retries:
                    raise ApiError(f"{self.name}: network error ({type(exc).__name__}). Check your connection.") from None
                delay = self._backoff_delay(attempt)
                log.warning("%s: network error (%s), retrying in %.0fs", self.name, type(exc).__name__, delay)
                self._notify(f"{self.name}: network error, retrying in {int(delay)}s…")
                self._sleep(delay)
                attempt += 1
                continue

            if resp.status_code == 429:
                delay = _retry_after_seconds(resp)
                if delay is None:
                    delay = self._backoff_delay(attempt)
                with self._lock:
                    self._cooldown_until = max(self._cooldown_until, time.monotonic() + delay)
                if attempt >= self.max_retries or delay > Config.MAX_BACKOFF_SECONDS:
                    raise RateLimitError(
                        f"{self.name} is rate-limiting requests from this computer (HTTP 429). "
                        f"It was left alone for now; try again in a few minutes.",
                        status=429,
                    )
                log.warning("%s: HTTP 429, cooling down for %.0fs (attempt %d)", self.name, delay, attempt + 1)
                self._notify(f"{self.name} asked us to slow down (HTTP 429) - waiting {int(delay)}s…")
                attempt += 1
                continue

            if resp.status_code in (502, 503, 504) and attempt < self.max_retries:
                delay = self._backoff_delay(attempt)
                log.warning("%s: HTTP %d, retrying in %.0fs", self.name, resp.status_code, delay)
                self._notify(f"{self.name}: server error {resp.status_code}, retrying in {int(delay)}s…")
                self._sleep(delay)
                attempt += 1
                continue

            return resp
