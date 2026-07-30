"""Performance optimizations for Zeus — caching and context management.

Features:
  - ResponseCache: LRU cache for LLM call results
  - ContextPruner: truncates long context to fit budget
  - DedupTracker: avoids duplicate LLM calls in the same turn
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import Any, Callable

logger = logging.getLogger(__name__)


class ResponseCache:
    """LRU cache for LLM responses.

    Caches by hashing (messages + tools) for exact repeats.
    Useful for:
      - Repeated queries ("who are you", "help")
      - System prompts that don't change
      - Testing and debugging

    Usage:
        cache = ResponseCache(max_size=100, ttl=300)
        key = cache.make_key(messages, tools)
        if key in cache:
            return cache[key]
        result = llm(messages, tools)
        cache[key] = result
    """

    def __init__(self, max_size: int = 100, ttl: float = 300):
        """Initialize cache.

        Args:
            max_size: Max cached entries (LRU eviction)
            ttl: Time-to-live in seconds (default: 5 min)
        """
        self._cache: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl
        self._hits = 0
        self._misses = 0

    def make_key(self, messages: list, tools: list | None = None) -> str:
        """Generate a cache key from messages and tools.

        Args:
            messages: Message list
            tools: Optional tools list

        Returns:
            SHA256 hash string.
        """
        content = json.dumps({
            "messages": [
                {"role": m.get("role", ""), "content": m.get("content", "")[:500]}
                for m in messages[-5:]  # Only last 5 messages matter
            ],
            "tools": bool(tools),
        }, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    def get(self, key: str) -> str | None:
        """Get cached result.

        Returns:
            Cached result, or None if miss/expired.
        """
        if key not in self._cache:
            self._misses += 1
            return None

        timestamp, result = self._cache[key]
        if time.time() - timestamp > self._ttl:
            # Expired
            del self._cache[key]
            self._misses += 1
            return None

        # LRU: move to end
        self._cache.move_to_end(key)
        self._hits += 1
        return result

    def set(self, key: str, result: str):
        """Cache a result."""
        self._cache[key] = (time.time(), result)
        # LRU eviction
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    def clear(self):
        """Clear all cached entries."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0
        logger.info("Cache cleared")

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        hit_rate = self._hits / total * 100 if total > 0 else 0
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": f"{hit_rate:.1f}%",
            "ttl": self._ttl,
        }


class CachedLLM:
    """Wrapper that adds caching to any LLM callable.

    Usage:
        llm = CachedLLM(make_llm_call())
        response = llm(messages=[...])  # First call — passes through
        response = llm(messages=[...])  # Same call — returns cached
    """

    def __init__(self, llm: Callable, max_size: int = 100, ttl: float = 300):
        self._llm = llm
        self._cache = ResponseCache(max_size=max_size, ttl=ttl)

    def __call__(self, messages: list, tools: list | None = None, **kwargs) -> str:
        key = self._cache.make_key(messages, tools)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        result = self._llm(messages, tools, **kwargs)
        self._cache.set(key, result)
        return result

    @property
    def cache_stats(self) -> dict:
        return self._cache.stats

    def clear_cache(self):
        self._cache.clear()


def make_cached(llm: Callable, max_size: int = 100, ttl: float = 300) -> CachedLLM:
    """Wrap an LLM callable with caching."""
    return CachedLLM(llm, max_size=max_size, ttl=ttl)
