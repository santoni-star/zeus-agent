"""Shared types and configuration for Zeus."""

from __future__ import annotations
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any, Optional


# Intent types the classifier can return
INTENT_TYPES = [
    "simple_chat",     # Casual conversation, greeting, thanks
    "simple_question", # Factual question (no tools needed)
    "command",         # Direct terminal command ("cd projects", "ls -la")
    "task_complex",    # Multi-step task → Task Runtime
    "task_simple",     # Single-step task → direct tool call
    "skill_search",    # Find/install a skill
    "system",          # System control (config, status, etc.)
]


@dataclass
class Intent:
    type: str
    confidence: float
    raw_input: str
    metadata: dict = field(default_factory=dict)


@dataclass
class ClassificationResult:
    intent: Intent
    entities: dict[str, Any] = field(default_factory=dict)  # extracted: tool names, URLs, etc.


@dataclass
class ExecutionResult:
    success: bool
    output: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    dag_result: Optional[list] = None  # per-node results if DAG was used


@dataclass
class ToolRegistry:
    """Registry of available tools with their schemas."""
    tools: dict[str, dict] = field(default_factory=dict)

    def register(self, name: str, schema: dict, handler: callable):
        self.tools[name] = {
            "schema": schema,
            "handler": handler,
        }

    def execute(self, name: str, params: dict) -> Any:
        if name not in self.tools:
            raise ValueError(f"Unknown tool: '{name}'. Available: {list(self.tools.keys())}")
        return self.tools[name]["handler"](params)

    def schemas(self) -> list[dict]:
        return [t["schema"] for t in self.tools.values()]

    def names(self) -> list[str]:
        return list(self.tools.keys())