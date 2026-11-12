"""Small thread-safe TTL cache for reusable agent metadata.

Only route/tool metadata should use long TTLs. Live NinjaOne response data must
not be stored here by default.
"""
from __future__ import annotations

from threading import RLock
from time import monotonic
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    def __init__(self, ttl_seconds: float, max_entries: int = 256):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self.ttl_seconds = float(ttl_seconds)
        self.max_entries = int(max_entries)
        self._items: dict[str, tuple[float, T]] = {}
        self._lock = RLock()

    def get(self, key: str) -> T | None:
        now = monotonic()
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= now:
                self._items.pop(key, None)
                return None
            return value

    def set(self, key: str, value: T) -> None:
        with self._lock:
            now = monotonic()
            expired = [item_key for item_key, (expiry, _) in self._items.items()
                       if expiry <= now]
            for item_key in expired:
                self._items.pop(item_key, None)
            if key not in self._items and len(self._items) >= self.max_entries:
                oldest = min(self._items, key=lambda item_key: self._items[item_key][0])
                self._items.pop(oldest, None)
            self._items[key] = (now + self.ttl_seconds, value)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        now = monotonic()
        with self._lock:
            expired = [key for key, (expiry, _) in self._items.items()
                       if expiry <= now]
            for key in expired:
                self._items.pop(key, None)
            return len(self._items)
