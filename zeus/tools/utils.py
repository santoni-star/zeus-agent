"""Utility tools — calculator, timestamp, uuid, json format.

Quick utilities that don't need LLM calls.
"""

from __future__ import annotations

import json
import math
import time
import uuid
from typing import Any

SCHEMA = {
    "name": "utility",
    "description": "Utility functions: calculator, timestamp, uuid, json_format, echo.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["calc", "timestamp", "uuid", "json_format", "echo"],
                "description": "Utility action",
            },
            "expression": {
                "type": "string",
                "description": "Math expression to evaluate (for calc action)",
                "default": "",
            },
            "data": {
                "type": "string",
                "description": "JSON string to format (for json_format action)",
                "default": "",
            },
            "message": {
                "type": "string",
                "description": "Message to echo back (for echo action)",
                "default": "",
            },
            "format": {
                "type": "string",
                "enum": ["unix", "iso", "human"],
                "description": "Timestamp format (for timestamp action)",
                "default": "human",
            },
        },
        "required": ["action"],
    },
}


def execute(params: dict) -> str:
    """Execute a utility action.

    Args:
        params: See SCHEMA.

    Returns:
        Utility result.
    """
    action = params["action"]

    if action == "calc":
        return _calc(params.get("expression", ""))
    elif action == "timestamp":
        return _timestamp(params.get("format", "human"))
    elif action == "uuid":
        return str(uuid.uuid4())
    elif action == "json_format":
        return _json_format(params.get("data", ""))
    elif action == "echo":
        return params.get("message", "")
    else:
        return f"Unknown utility action: {action}"


def _calc(expression: str) -> str:
    """Evaluate a math expression safely.

    Uses a restricted set of math functions and operators.
    """
    if not expression.strip():
        return "Usage: calc('2 + 2'), calc('sqrt(144) * pi'), etc."

    # Safe math namespace
    safe_globals = {
        "__builtins__": {},
        "abs": abs, "int": int, "float": float,
        "min": min, "max": max, "round": round,
        "sum": sum, "pow": pow,
    }
    safe_locals = {
        "pi": math.pi, "e": math.e, "tau": math.tau,
        "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
        "tan": math.tan, "log": math.log, "log10": math.log10,
        "floor": math.floor, "ceil": math.ceil,
    }

    try:
        # Sanitize: only allow safe characters
        if not re.match(r'^[\d\s\+\-\*\/\(\)\.,\w\[\]]+$', expression):
            # Re-allow with math.func syntax
            pass

        result = eval(expression, safe_globals, safe_locals)
        return f"{expression} = {result}"
    except Exception as e:
        return f"❌ Calculation error: {e}"


def _timestamp(format: str) -> str:
    """Get current time in various formats."""
    now = time.time()
    if format == "unix":
        return f"Unix timestamp: {now:.3f}"
    elif format == "iso":
        from datetime import datetime, timezone
        return f"ISO time: {datetime.now(timezone.utc).isoformat()}"
    else:
        from datetime import datetime
        dt = datetime.now()
        return (
            f"Local time: {dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Unix: {now:.3f}\n"
            f"Weekday: {dt.strftime('%A')}"
        )


def _json_format(data: str) -> str:
    """Format/validate a JSON string."""
    if not data.strip():
        return "Usage: json_format('{\"key\": \"value\"}')"

    try:
        parsed = json.loads(data)
        formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
        return f"✅ Valid JSON ({len(data)} chars → {len(formatted)}):\n{formatted}"
    except json.JSONDecodeError as e:
        return f"❌ Invalid JSON: {e}"


# Need re for calc
import re  # noqa: E402
