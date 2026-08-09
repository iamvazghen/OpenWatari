"""Afon's L4 hot-cache — front the slow paths so repeated questions are near-instant.

Two tiers, both optional and graceful:

* **In-process TTL cache** (always on): a small bounded dict with per-entry expiry. Costs
  nothing, needs no service, and makes a repeated lookup inside one session essentially free.
* **Redis** (when ``AFON_REDIS_URL`` is set and ``redis`` is installed): the same cache, but
  shared across processes and surviving a brain restart — so "what's the BTC price?" asked again
  tomorrow can still hit a warm value (within its TTL).

Everything degrades: no Redis URL → in-process only; Redis unreachable → fall back to in-process
and log once; cache miss/any error → the caller's real function runs as if no cache existed. The
cache is therefore *never* a source of failure, only of speed.

Usage (see ``tools/utility.py`` and ``tools/web.py``):

    from afon.brain.cache import CACHE
    val = await CACHE.cached("weather", key=f"{lat},{lon}", ttl=600, factory=lambda: _fetch())
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Awaitable, Callable

from loguru import logger

from afon.config import settings


class _InProcessTTL:
    """A tiny bounded TTL cache. Newest-used kept; oldest evicted past ``max_items``."""

    def __init__(self, max_items: int = 512) -> None:
        self._max = max_items
        self._data: OrderedDict[str, tuple[float, str]] = OrderedDict()

    def get(self, key: str) -> str | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if expires < time.time():
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)  # mark as recently used
        return value

    def set(self, key: str, value: str, ttl: int) -> None:
        self._data[key] = (time.time() + ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)  # evict least-recently-used

    def clear(self) -> None:
        self._data.clear()


_UNSET = object()  # distinguishes "argument omitted" from an explicit None/"" (= force no Redis)


class Cache:
    """L4 hot-cache: in-process always, Redis too when configured. Async, fail-open."""

    def __init__(self, redis_url: str | None = _UNSET) -> None:  # type: ignore[assignment]
        # Omitted -> use the configured URL; explicit None/"" -> force in-process only (tests).
        self._url = settings.redis_url if redis_url is _UNSET else redis_url
        self._local = _InProcessTTL()
        self._redis = None            # lazily connected redis.asyncio client
        self._redis_tried = False     # connect attempted (success or give-up)
        self._redis_down = False      # connect/op failed once -> stop trying this run

    @property
    def backend(self) -> str:
        """'redis' if a Redis client is live, else 'memory'. For self-health/diagnostics."""
        return "redis" if self._redis is not None else "memory"

    async def _ensure_redis(self):
        if not self._url or self._redis_down:
            return None
        if self._redis is not None:
            return self._redis
        if self._redis_tried:
            return None
        self._redis_tried = True
        try:
            import redis.asyncio as redis  # type: ignore

            client = redis.from_url(self._url, decode_responses=True)
            await client.ping()
            self._redis = client
            logger.info(f"L4 cache: connected to Redis at {self._url}")
            return client
        except Exception as e:  # noqa: BLE001
            self._redis_down = True
            logger.warning(f"L4 cache: Redis unavailable ({type(e).__name__}); using in-process only")
            return None

    @staticmethod
    def _k(namespace: str, key: str) -> str:
        return f"afon:{namespace}:{key}"

    async def get(self, namespace: str, key: str) -> str | None:
        full = self._k(namespace, key)
        # In-process first (cheapest); it also caches Redis hits for the session.
        local = self._local.get(full)
        if local is not None:
            return local
        client = await self._ensure_redis()
        if client is not None:
            try:
                val = await client.get(full)
                if val is not None:
                    return val
            except Exception as e:  # noqa: BLE001
                self._redis_down = True
                logger.warning(f"L4 cache get failed ({type(e).__name__}); in-process only from now")
        return None

    async def set(self, namespace: str, key: str, value: str, ttl: int) -> None:
        full = self._k(namespace, key)
        self._local.set(full, value, ttl)
        client = await self._ensure_redis()
        if client is not None:
            try:
                await client.set(full, value, ex=ttl)
            except Exception as e:  # noqa: BLE001
                self._redis_down = True
                logger.warning(f"L4 cache set failed ({type(e).__name__}); in-process only from now")

    async def cached(
        self,
        namespace: str,
        key: str,
        ttl: int,
        factory: Callable[[], Awaitable[str]],
    ) -> str:
        """Return the cached value for (namespace, key), or run ``factory`` and cache its result.

        A blank/falsey factory result is NOT cached (don't pin transient errors or empties).
        Any cache error falls through to a plain ``factory()`` call — caching never blocks work.
        """
        try:
            hit = await self.get(namespace, key)
            if hit is not None:
                return hit
        except Exception:  # noqa: BLE001
            pass  # fail open: behave as a cache miss
        value = await factory()
        if value:
            try:
                await self.set(namespace, key, value, ttl)
            except Exception:  # noqa: BLE001
                pass
        return value

    def clear(self) -> None:
        """Drop the in-process tier (used by tests). Redis is left intact."""
        self._local.clear()


# Process-wide cache used by tools.
CACHE = Cache()
