"""Telemetry module — performance monitoring for EventBus modules.

Subscribes to all task/user events, records timing and success metrics,
and provides architecture insights based on real usage data.

Note: Subscribes to events AFTER start, so it won't interfere with
normal module startup.

Usage:
    from zeus.modules.telemetry import TelemetryModule
    module = TelemetryModule(bus=bus)
    manager.register(module)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from zeus.module import Module, Event
from zeus.module import (
    USER_INPUT, USER_OUTPUT,
    TASK_COMPLETED, TASK_FAILED,
    MEMORY_SAVE, MEMORY_SEARCH,
    CONTEXT_REQUEST,
)
from zeus.telemetry import TelemetryStore

logger = logging.getLogger(__name__)


class TelemetryModule(Module):
    """Performance monitoring — records timing for all module events.

    Subscribes to lifecycle events and tracks:
      - Per-module execution time
      - LLM call count per pipeline run
      - Success/failure rates
      - Event throughput

    Architecture Insights:
      Generates suggestions based on telemetry data:
      - Which modules are bottlenecks (high avg duration)
      - Which modules are overused (high event count)
      - Which modules fail most often
      - LLM call efficiency
    """

    def __init__(self, bus=None, telemetry_store: TelemetryStore | None = None):
        super().__init__(
            name="telemetry",
            description="Performance monitoring: module timing, success rates, architecture insights",
            bus=bus,
        )
        self._store = telemetry_store or TelemetryStore()
        self._active_timers: dict[str, float] = {}
        self._insights_cache: list[dict] = []
        self._last_insight_time: float = 0

    async def start(self):
        """Start the module and subscribe to all monitored events."""
        await super().start()

        # Subscribe to all lifecycle events
        events = [
            USER_INPUT, USER_OUTPUT,
            TASK_COMPLETED, TASK_FAILED,
            MEMORY_SAVE, MEMORY_SEARCH,
            CONTEXT_REQUEST,
        ]
        for evt in events:
            self.subscribe(evt, self._on_event)

        # Also subscribe to wildcard patterns for unknown event types
        self.subscribe("pipeline.request", self._on_event)
        self.subscribe("pipeline.result", self._on_event)
        self.subscribe("planner.result", self._on_event)
        self.subscribe("router.*", self._on_event)
        self.subscribe("classifier.*", self._on_event)
        self.subscribe("scheduler.tick", self._on_event)
        self.subscribe("scheduler.result", self._on_event)
        self.subscribe("review.*", self._on_event)
        self.subscribe("task.*", self._on_event)

        logger.info("TelemetryModule: started")
        self._store.record_call("telemetry", "module.start", success=True)

    async def stop(self):
        """Stop the module."""
        self._store.record_call("telemetry", "module.stop", success=True)
        await super().stop()
        logger.info("TelemetryModule: stopped")

    async def _on_event(self, event: Event):
        """Record timing for published events.

        Uses a simple timer pattern:
        - On user.input: start a pipeline timer
        - On user.output: stop it, record duration
        - On task.completed/failed: record result
        """
        evt_type = event.type
        source = event.data.get("source", "unknown") if event.data else "unknown"

        # Track pipeline timing
        if evt_type == USER_INPUT:
            self._active_timers["pipeline"] = time.time()

        elif evt_type == USER_OUTPUT:
            start = self._active_timers.pop("pipeline", None)
            duration = (time.time() - start) * 1000 if start else 0

            # Count LLM calls in this event
            text = event.data.get("text", "") if event.data else ""
            llm_calls = 1 if "Planner" in text or "Synthesizer" in text else 0

            self._store.record_call(
                module_name=source or "pipeline",
                event_type=evt_type,
                duration_ms=duration,
                llm_calls=llm_calls,
                success=True,
            )

        elif evt_type == TASK_FAILED:
            error = event.data.get("error", "") if event.data else ""
            node_id = event.data.get("node_id", "") if event.data else ""
            self._store.record_call(
                module_name="pipeline",
                event_type=evt_type,
                success=False,
                error=f"{node_id}: {error}",
            )

        elif evt_type == TASK_COMPLETED:
            duration = event.data.get("duration_ms", 0) if event.data else 0
            llm_calls = event.data.get("llm_calls", 0) if event.data else 0
            self._store.record_call(
                module_name="pipeline",
                event_type=evt_type,
                duration_ms=duration,
                llm_calls=llm_calls,
                success=True,
            )

        elif evt_type == MEMORY_SEARCH:
            # Record memory access timing
            self._store.record_call(
                module_name="memory",
                event_type=evt_type,
                success=True,
            )

        elif evt_type == CONTEXT_REQUEST:
            self._store.record_call(
                module_name="memory",
                event_type=evt_type,
                success=True,
            )

        # Generic recording for router/classifier events
        elif evt_type.startswith("router.") or evt_type.startswith("classifier."):
            module = evt_type.split(".")[0]
            duration = event.data.get("duration_ms", 0) if event.data else 0
            self._store.record_call(
                module_name=module,
                event_type=evt_type,
                duration_ms=duration,
                success=True,
            )

    # ── Architecture Insights ─────────────────────────────

    async def _generate_insights(self) -> list[dict]:
        """Generate architecture improvement suggestions from telemetry.

        Analyzes:
          - Module bottlenecks (high avg duration)
          - Module error rates
          - LLM call efficiency
          - Event volume per module

        Returns:
            List of insight dicts with title, severity, description.
        """
        insights = []
        summary = self._store.module_summary(hours=48)
        errors = self._store.error_summary(hours=48)
        stats = self._store.stats()

        if not summary:
            return []

        # 1. Bottleneck detection
        avg_all = sum(m["avg_duration_ms"] for m in summary) / len(summary) if summary else 0
        for mod in summary:
            if mod["avg_duration_ms"] > avg_all * 2 and mod["events"] > 5:
                insights.append({
                    "title": f"Bottleneck: `{mod['module']}`",
                    "severity": "medium",
                    "description": (
                        f"Module `{mod['module']}` averages {mod['avg_duration_ms']}ms "
                        f"({avg_all:.0f}ms average across all modules). "
                        f"Consider optimization or fast-path for common cases."
                    ),
                    "module": mod["module"],
                    "avg_duration_ms": mod["avg_duration_ms"],
                })

        # 2. High error rate
        if errors:
            for err in errors[:3]:
                if err["error_count"] >= 3:
                    insights.append({
                        "title": f"High error rate: `{err['module_name']}/{err['event_type']}`",
                        "severity": "high",
                        "description": (
                            f"Module `{err['module_name']}` had {err['error_count']} errors "
                            f"on `{err['event_type']}` events. Last: {err.get('last_error', '')[:200]}"
                        ),
                        "module": err["module_name"],
                    })

        # 3. LLM efficiency
        total_llm = sum(m["total_llm_calls"] for m in summary)
        total_events = sum(m["events"] for m in summary)
        if total_llm > 0 and total_events > 0:
            llm_per_event = total_llm / total_events
            if llm_per_event > 2:
                insights.append({
                    "title": "High LLM call ratio",
                    "severity": "medium",
                    "description": (
                        f"Average {llm_per_event:.1f} LLM calls per event. "
                        f"Consider caching, fast-path, or shorter prompts to reduce costs."
                    ),
                    "llm_per_event": round(llm_per_event, 1),
                })

        # 4. Most active modules
        sorted_by_events = sorted(summary, key=lambda m: m["events"], reverse=True)
        if sorted_by_events:
            top = sorted_by_events[0]
            second = sorted_by_events[1] if len(sorted_by_events) > 1 else None
            if top["events"] > 100 and second and top["events"] > second["events"] * 3:
                insights.append({
                    "title": f"Traffic imbalance: {top['module']} dominates",
                    "severity": "low",
                    "description": (
                        f"Module `{top['module']}` handles {top['events']} events "
                        f"({top['events']/total_events*100:.0f}% of all traffic). "
                        f"Consider load distribution if it becomes a bottleneck."
                    ),
                })

        # 5. Module load suggestions
        for mod in summary:
            if mod["avg_duration_ms"] > 500 and mod["events"] > 20:
                insights.append({
                    "title": f"High latency module: `{mod['module']}`",
                    "severity": "low",
                    "description": (
                        f"Module `{mod['module']}` has high average latency "
                        f"({mod['avg_duration_ms']}ms) with {mod['events']} events. "
                        f"Consider moving to a separate process for isolation."
                    ),
                    "module": mod["module"],
                    "avg_duration_ms": mod["avg_duration_ms"],
                })

        return insights

    async def insights(self, refresh: bool = False) -> list[dict]:
        """Get architecture insights.

        Args:
            refresh: Force regeneration

        Returns:
            List of insight dicts.
        """
        if refresh or not self._insights_cache or (time.time() - self._last_insight_time) > 3600:
            self._insights_cache = await self._generate_insights()
            self._last_insight_time = time.time()
        return self._insights_cache

    # ── Public API ────────────────────────────────────────

    def summary(self, hours: int = 24, module_name: str | None = None) -> list[dict]:
        """Get module summary from telemetry."""
        return self._store.module_summary(hours=hours, module_name=module_name)

    def errors(self, hours: int = 48) -> list[dict]:
        """Get error summary."""
        return self._store.error_summary(hours=hours)

    def timeline(self, module_name: str | None = None, hours: int = 24) -> list[dict]:
        """Get event timeline."""
        return self._store.event_timeline(module_name=module_name, hours=hours)

    def slowest(self, limit: int = 10) -> list[dict]:
        """Get slowest events."""
        return self._store.slowest_events(limit=limit)

    def stats(self) -> dict:
        """Get quick stats."""
        return self._store.stats()

    def get_store(self) -> TelemetryStore:
        """Get the underlying store for direct queries."""
        return self._store
