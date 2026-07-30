"""Reflection module — analyzes completed tasks, detects patterns,
and autonomously creates tools for repeatable workflows.

Subscribes to: task.completed, task.failed
Emits:         reflection.tool_created, reflection.pattern_detected
"""

from __future__ import annotations
import logging
import re
from collections import defaultdict
from typing import Any

from zeus.module import Module, Event, TASK_COMPLETED, TASK_FAILED

logger = logging.getLogger(__name__)


class ReflectionModule(Module):
    """Analyzes task execution patterns and creates tools.

    Tracks:
      - What tasks were run (goals, tools used)
      - Which patterns repeat (same task type done 2+ times)
      - What errors occurred (for self-improvement)

    When a pattern repeats, creates a tool automatically.
    """

    def __init__(self, bus=None, tool_registry=None, llm_call=None, memory_module=None):
        super().__init__(
            name="reflection",
            description="Task pattern analysis and auto-tool creation",
            bus=bus,
        )
        self._tool_registry = tool_registry
        self._llm_call = llm_call
        self._memory = memory_module
        self._task_history: list[dict] = []
        self._pattern_counts: dict[str, int] = defaultdict(int)

    async def start(self):
        await super().start()
        self.subscribe(TASK_COMPLETED, self._handle_task_completed)
        self.subscribe(TASK_FAILED, self._handle_task_failed)

    async def _handle_task_completed(self, event: Event):
        """Analyze a completed task and look for patterns."""
        goal = event.data.get("goal", "")
        results = event.data.get("results", [])
        tool_names = [r.get("node_id", "") for r in results if r.get("success")]

        if not goal:
            return

        # Save to history
        record = {
            "goal": goal,
            "tool_names": tool_names,
            "success": True,
            "timestamp": event.created_at,
        }
        self._task_history.append(record)
        if len(self._task_history) > 100:
            self._task_history = self._task_history[-100:]

        # Detect pattern by extracting keywords from goal
        pattern_key = self._extract_pattern(goal)
        self._pattern_counts[pattern_key] += 1
        count = self._pattern_counts[pattern_key]

        logger.debug("Pattern '%s' count: %d", pattern_key, count)

        # If pattern repeated 2+ times, create a tool
        if count >= 2 and self._llm_call and self._tool_registry:
            if pattern_key not in ("", "unknown"):
                await self._maybe_create_tool(pattern_key, goal, tool_names)

    async def _handle_task_failed(self, event: Event):
        """Analyze a failed task — log error for later improvement."""
        goal = event.data.get("goal", "")
        error = event.data.get("error", "unknown")
        results = event.data.get("results", [])

        failed_nodes = [r for r in results if not r.get("success")]
        errors = []
        for n in failed_nodes:
            errors.append(f"{n.get('node_id', '?')}: {n.get('error', 'unknown')}")

        record = {
            "goal": goal,
            "success": False,
            "errors": errors,
            "timestamp": event.created_at,
        }
        self._task_history.append(record)

        logger.info("Task failed: %s — %s", goal[:60], "; ".join(errors))

        # Emit failure event for other modules to react
        await self.emit("reflection.task_failed", {
            "goal": goal,
            "errors": errors,
            "history_count": len(self._task_history),
        })

    # ── Pattern detection ──────────────────────────────────

    def _extract_pattern(self, goal: str) -> str:
        """Extract a pattern key from a task goal.

        E.g. "convert 257 USD to PLN" → "currency_converter"
        "search for latest python version" → "web_search"
        """
        g = goal.lower()

        # Currency conversion
        if re.search(r'\d+\s+(\w{3})\s+(to|in|->)\s+(\w{3})', g):
            return "currency_converter"

        # Web search
        if re.search(r'(search|find|look\s+up|what\s+is|latest)', g):
            return "web_search"

        # File operations
        if re.search(r'(read|write|create|delete|list)\s+file', g):
            return "file_operation"

        # Code
        if re.search(r'(code|script|function|class|implement|refactor)', g):
            return "code_generation"

        # Default
        return "unknown"

    def _check_tool_exists(self, name: str) -> bool:
        """Check if a tool already exists in the registry."""
        if self._tool_registry:
            return name in self._tool_registry.names()
        return False

    async def _maybe_create_tool(self, pattern: str, goal: str, tool_names: list[str]):
        """Create a tool for a repeatable pattern if one doesn't exist."""
        tool_name_map = {
            "currency_converter": "currency_converter",
            "web_search": "web_search",
            "file_operation": "file_tool",
            "code_generation": "code_writer",
        }

        tool_name = tool_name_map.get(pattern)

        # Skip if tool already exists or is a built-in
        if not tool_name or self._check_tool_exists(tool_name):
            return

        # Skip built-in patterns (they already have tools)
        if pattern == "web_search":
            return  # Built-in, no need to create

        if pattern == "currency_converter":
            # Already have one, but just in case
            if self._check_tool_exists("currency_converter"):
                return

        # Create the tool
        logger.info("Reflection: creating tool for pattern '%s' (from: %s)", pattern, goal[:40])

        if pattern == "currency_converter":
            from zeus.tools.dynamic import create_tool
            desc = (
                "currency converter that fetches live exchange rates from open.er-api.com. "
                "Parameters: 'amount' (number, required), "
                "'from_currency' (string, required) - source currency code (e.g. USD, EUR, PLN, UAH), "
                "'to_currency' (string, required) - target currency code."
            )
            result = create_tool(desc, self._llm_call)
            if result["success"]:
                # Reload tools
                from zeus.tools.dynamic import discover_custom_tools
                for name, tool in discover_custom_tools().items():
                    self._tool_registry.register(name, tool["schema"], tool["handler"])
                await self.emit("reflection.tool_created", {
                    "name": tool_name,
                    "pattern": pattern,
                    "description": f"Auto-created for repeatable pattern: {pattern}",
                })
                logger.info("Reflection: created tool '%s'", tool_name)

    # ── Status ──────────────────────────────────────────────

    def get_history(self, limit: int = 10) -> list[dict]:
        """Get recent task history."""
        return self._task_history[-limit:]

    def get_patterns(self) -> dict:
        """Get detected patterns with counts."""
        return dict(self._pattern_counts)

    def status(self) -> dict:
        return {
            "name": self.name,
            "running": self._running,
            "history_count": len(self._task_history),
            "patterns": dict(self._pattern_counts),
        }