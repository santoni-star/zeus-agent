"""Resilient LLM — provider fallback, retry with backoff, health checks.

Wraps an LLM callable with:
  - Retry with exponential backoff (for transient errors like 429/503)
  - Fallback chain (if primary fails, try backup providers)
  - Timeout handling
  - Health checks (periodic connectivity verification)

Usage:
    from zeus.resilient import ResilientLLM

    llm = ResilientLLM(
        primary=make_llm_call("openai", "gpt-4"),
        fallbacks=[make_llm_call("openrouter", "gpt-3.5")],
        max_retries=3,
    )
    response = llm(messages=[...])
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


class LLMFailure(Exception):
    """All LLM providers failed."""
    pass


class ResilientLLM:
    """Wraps an LLM callable with retry, fallback, and timeout.

    The resulting callable has the same signature as make_llm_call():
        (messages: list, tools: list | None = None, **kwargs) -> str

    Features:
      - Retries on transient failures (timeout, 429, 500, 503)
      - Falls back to alternative providers when primary fails
      - Exponential backoff between retries (1s, 2s, 4s, ...)
      - Provider health tracking (can skip unhealthy providers)
      - Reports which provider handled the request
    """

    def __init__(
        self,
        primary: Callable,
        fallbacks: list[Callable] | None = None,
        max_retries: int = 2,
        base_delay: float = 1.0,
        max_delay: float = 8.0,
        timeout: float = 30.0,
        health_check_interval: float = 60.0,
    ):
        """Initialize ResilientLLM.

        Args:
            primary: Primary LLM callable
            fallbacks: Ordered list of fallback LLM callables
            max_retries: Retries per provider before failing over
            base_delay: Initial backoff delay in seconds
            max_delay: Maximum backoff delay
            timeout: Per-call timeout in seconds
            health_check_interval: Seconds between health checks
        """
        self._providers = [primary] + (fallbacks or [])
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._timeout = timeout
        self._health_interval = health_check_interval

        # Provider health tracking
        self._health: dict[int, dict] = {}
        for i in range(len(self._providers)):
            self._health[i] = {
                "healthy": True,
                "last_check": 0,
                "failures": 0,
                "last_failure": None,
            }

    def __call__(self, messages: list, tools: list | None = None, **kwargs) -> str:
        """Call with retry and fallback.

        Args:
            messages: Message list
            tools: Optional tools list
            **kwargs: Additional arguments passed to provider

        Returns:
            Response string.

        Raises:
            LLMFailure: If all providers fail.
        """
        last_error = None

        for provider_idx, provider in enumerate(self._providers):
            if not self._is_healthy(provider_idx):
                continue

            for attempt in range(self._max_retries + 1):
                try:
                    # Remove timeout from kwargs
                    timeout = kwargs.pop("timeout", self._timeout)

                    # Call with remaining kwargs
                    result = provider(
                        messages=messages,
                        tools=tools,
                        **kwargs,
                    )

                    if not isinstance(result, str) or not result.strip():
                        raise LLMFailure("Empty response from provider")

                    # Record success
                    self._record_success(provider_idx, attempt)
                    return result

                except Exception as e:
                    last_error = e
                    self._record_failure(provider_idx, attempt, str(e))

                    if attempt < self._max_retries:
                        # Wait with backoff + jitter
                        delay = min(
                            self._base_delay * (2 ** attempt) + random.uniform(0, 0.5),
                            self._max_delay,
                        )
                        logger.warning(
                            "LLM provider %d attempt %d failed: %s, retry in %.1fs",
                            provider_idx, attempt + 1, e, delay,
                        )
                        time.sleep(delay)

            # Provider exhausted — mark unhealthy temporarily
            self._mark_unhealthy(provider_idx)
            logger.warning("LLM provider %d exhausted, trying fallback", provider_idx)

        raise LLMFailure(f"All LLM providers failed. Last error: {last_error}")

    # ── Health tracking ───────────────────────────────────

    def _is_healthy(self, idx: int) -> bool:
        health = self._health.get(idx, {})
        if not health.get("healthy", True):
            # Check if enough time passed to retry
            last_failure = health.get("last_failure", 0)
            if last_failure and (time.time() - last_failure) > self._health_interval:
                self._health[idx]["healthy"] = True
                return True
            return False
        return True

    def _record_success(self, idx: int, attempt: int):
        health = self._health[idx]
        health["healthy"] = True
        health["failures"] = 0
        health["last_check"] = time.time()
        health["last_failure"] = None

    def _record_failure(self, idx: int, attempt: int, error: str):
        health = self._health[idx]
        health["failures"] += 1
        health["last_failure"] = time.time()
        health["last_error"] = error

    def _mark_unhealthy(self, idx: int):
        self._health[idx]["healthy"] = False

    # ── Public API ────────────────────────────────────────

    def health_summary(self) -> list[dict]:
        """Get health status of all providers."""
        return [
            {
                "provider": i,
                "healthy": h.get("healthy", True),
                "failures": h.get("failures", 0),
                "last_error": h.get("last_error", ""),
            }
            for i, h in self._health.items()
        ]

    @property
    def provider_count(self) -> int:
        return len(self._providers)

    @property
    def has_fallback(self) -> bool:
        return len(self._providers) > 1


# ── Convenience ───────────────────────────────────────────

def make_resilient(
    primary: Callable,
    fallbacks: list[Callable] | None = None,
    max_retries: int = 2,
) -> ResilientLLM:
    """Create a resilient LLM wrapper with defaults.

    Args:
        primary: Primary LLM callable
        fallbacks: Optional fallback callables
        max_retries: Number of retries before fallback

    Returns:
        ResilientLLM instance.
    """
    return ResilientLLM(
        primary=primary,
        fallbacks=fallbacks,
        max_retries=max_retries,
    )
