"""
In-Memory TTL Caching Module
Provides thread-safe caching with time-to-live expiration and
atomic write-invalidation for high-throughput dashboard analytics (B8).
"""

import threading
import time
from typing import Any


class SimpleTTLCache:
    """Thread-safe in-memory cache with TTL expiration and invalidation."""

    def __init__(self, default_ttl_seconds: int = 180):
        self.default_ttl = default_ttl_seconds
        self._store = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        """Retrieve a value if key exists and has not expired."""
        with self._lock:
            if key in self._store:
                val, expires_at = self._store[key]
                if time.time() < expires_at:
                    return val
                # Expired: clean up
                del self._store[key]
            return None

    def set(self, key: str, value: Any, ttl_seconds: int | None = None):
        """Set a key with specific or default TTL."""
        with self._lock:
            ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
            self._store[key] = (value, time.time() + ttl)

    def invalidate(self, key: str | None = None):
        """Invalidate a specific key or clear all cached entries."""
        with self._lock:
            if key is not None:
                self._store.pop(key, None)
            else:
                self._store.clear()

    def has(self, key: str) -> bool:
        """Check if active unexpired key exists."""
        return self.get(key) is not None


# Global dashboard analytics cache instance (180s / 3-minute default TTL, Phase 6)
dashboard_cache = SimpleTTLCache(default_ttl_seconds=180)
