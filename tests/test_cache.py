import time

import pytest

from ninjaone_agent.cache import TTLCache


def test_ttl_cache_stores_and_returns_values():
    cache = TTLCache[str](ttl_seconds=1)
    cache.set("organizations", "GET /v2/organizations")
    assert cache.get("organizations") == "GET /v2/organizations"


def test_ttl_cache_expires_values():
    cache = TTLCache[str](ttl_seconds=0.01)
    cache.set("route", "GET /v2/devices")
    time.sleep(0.02)
    assert cache.get("route") is None


def test_ttl_cache_evicts_oldest_entry_at_capacity():
    cache = TTLCache[str](ttl_seconds=10, max_entries=1)
    cache.set("first", "one")
    cache.set("second", "two")
    assert cache.get("first") is None
    assert cache.get("second") == "two"


def test_ttl_cache_rejects_invalid_configuration():
    with pytest.raises(ValueError):
        TTLCache(ttl_seconds=0)
    with pytest.raises(ValueError):
        TTLCache(ttl_seconds=1, max_entries=0)
