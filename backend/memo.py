"""Reusing expensive results until the data changes.

Building the valuation and checking every saved day's prices for unrealistic listings takes a good
part of a second on a big inventory, and the dashboard asks for it several times per page (each tab,
the movers, an item's details). The results only change when the data does, so they are kept, keyed
on what they depend on and on db.data_version() - which moves on with every write (and while the
scheduled background update is writing) - and on today's date (windows like "the last 7 weeks" move
daily).

Cached results are shared: callers read them, never change them in place.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from datetime import date
from typing import Any, Callable

from . import db

MAX_ENTRIES = 24
_lock = threading.RLock()     # one computation at a time: requests arriving together wait and reuse it
_store: "OrderedDict[tuple, Any]" = OrderedDict()


def cached(key: tuple, compute: Callable[[], Any]) -> Any:
    """``compute()``'s result for ``key``, worked out once per version of the data."""
    full = (db.data_version(), date.today().isoformat(), key)
    with _lock:
        if full in _store:
            _store.move_to_end(full)
            return _store[full]
        value = compute()
        _store[full] = value
        while len(_store) > MAX_ENTRIES:
            _store.popitem(last=False)
        return value


def clear() -> None:
    with _lock:
        _store.clear()
