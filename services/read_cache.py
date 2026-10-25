"""Small in-process cache for read-only aggregate and reference data."""

from copy import deepcopy
from threading import Lock
import time

MAX_ENTRIES = 16
_entries = {}
_lock = Lock()


def cached(key, ttl_seconds, load):
    """Return a copy of the fresh cached value, or call load() and remember it.

    Failures raised by load() propagate and are never stored.
    """
    now = time.monotonic()
    with _lock:
        entry = _entries.get(key)
        if entry and entry[0] > now:
            return deepcopy(entry[1])
    value = load()
    with _lock:
        for stale in [k for k, (expires, _) in _entries.items() if expires <= now]:
            del _entries[stale]
        while len(_entries) >= MAX_ENTRIES:
            del _entries[min(_entries, key=lambda k: _entries[k][0])]
        _entries[key] = (now + ttl_seconds, deepcopy(value))
    return value


def clear():
    with _lock:
        _entries.clear()
