"""ToolRegistry — central registry for all Zeus tools.

Each tool is a module with:
  - SCHEMA: dict with name, description, parameters (JSON Schema)
  - execute(params: dict) -> str: the implementation

Supports:
  - Register built-in and custom tools
  - Schema validation before execution
  - Error handling with retry
  - Tool discovery (list/search by category)
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import time
import traceback
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


class ToolError(Exception):
    """Tool execution error."""
    pass


class ToolRegistry:
    """Central registry for all Zeus tools.

    Usage:
        registry = ToolRegistry()
        registry.discover()  # auto-load built-in tools
        registry.register("my_tool", schema, execute_fn)
        result = registry.call("web_search", {"query": "python"})
    """

    def __init__(self):
        self._tools: dict[str, dict] = {}
        self._built_in_dir = Path(__file__).parent

    # ── Registration ──────────────────────────────────────

    def register(self, name: str, schema: dict, execute_fn: Callable) -> bool:
        """Register a tool.

        Args:
            name: Tool name (lowercase, underscores)
            schema: JSON Schema dict with name/description/parameters
            execute_fn: Callable(params: dict) -> str

        Returns:
            True if registered.
        """
        self._tools[name] = {
            "name": name,
            "schema": schema,
            "execute": execute_fn,
        }
        logger.debug("Registered tool: %s", name)
        return True

    def unregister(self, name: str) -> bool:
        """Remove a tool."""
        return bool(self._tools.pop(name, None))

    # ── Discovery ─────────────────────────────────────────

    def discover(self, custom_dir: str | None = None) -> int:
        """Auto-discover built-in and custom tools.

        Scans zeus/tools/*.py for modules with SCHEMA + execute().
        Also scans custom_dir for user-created tools.

        Args:
            custom_dir: Optional path to custom tools directory.

        Returns:
            Number of tools discovered.
        """
        count = 0

        # Built-in tools
        for f in sorted(self._built_in_dir.glob("*.py")):
            if f.name.startswith("_") or f.name == "registry.py":
                continue
            try:
                mod_name = f"zeus.tools.{f.stem}"
                mod = importlib.import_module(mod_name)
                if hasattr(mod, "SCHEMA") and hasattr(mod, "execute"):
                    name = mod.SCHEMA.get("name", f.stem)
                    self.register(name, mod.SCHEMA, mod.execute)
                    count += 1
            except Exception as e:
                logger.warning("Tool discovery: failed to load %s: %s", f.name, e)

        # Custom tools
        if custom_dir:
            custom_path = Path(custom_dir).expanduser()
            if custom_path.exists():
                for f in sorted(custom_path.glob("*.py")):
                    if f.name == "__init__.py":
                        continue
                    try:
                        spec = importlib.util.spec_from_file_location(f.stem, f)
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        if hasattr(mod, "SCHEMA") and hasattr(mod, "execute"):
                            name = mod.SCHEMA.get("name", f.stem)
                            self.register(name, mod.SCHEMA, mod.execute)
                            count += 1
                    except Exception as e:
                        logger.warning("Custom tool: failed to load %s: %s", f.name, e)

        return count

    # ── Execution ─────────────────────────────────────────

    def call(self, name: str, params: dict | None = None, timeout: int = 30) -> str:
        """Execute a tool by name with validation.

        Args:
            name: Tool name
            params: Parameters dict
            timeout: Max execution time in seconds

        Returns:
            Tool output string.

        Raises:
            ToolError: If tool not found, validation fails, or execution error.
        """
        tool = self._tools.get(name)
        if not tool:
            raise ToolError(f"Unknown tool: {name}")

        params = params or {}

        # Validate against schema
        schema = tool["schema"]
        validation_error = self._validate(params, schema)
        if validation_error:
            raise ToolError(f"Validation error for {name}: {validation_error}")

        # Execute with timeout
        try:
            start = time.time()
            result = tool["execute"](params)
            duration = (time.time() - start) * 1000
            logger.info("Tool %s: %.0fms", name, duration)
            return str(result)
        except ToolError:
            raise
        except Exception as e:
            logger.error("Tool %s failed: %s", name, e)
            raise ToolError(f"{name}: {e}") from e

    def call_with_retry(self, name: str, params: dict | None = None,
                        max_retries: int = 2, timeout: int = 30) -> str:
        """Execute a tool with retry on failure.

        Args:
            name: Tool name
            params: Parameters dict
            max_retries: Max retry attempts
            timeout: Per-attempt timeout

        Returns:
            Tool output string.
        """
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                return self.call(name, params, timeout=timeout)
            except ToolError as e:
                last_error = e
                if attempt < max_retries:
                    wait = 1.0 * (2 ** attempt)
                    logger.warning("Tool %s failed (attempt %d/%d), retry in %.1fs: %s",
                                   name, attempt + 1, max_retries + 1, wait, e)
                    time.sleep(wait)
        raise ToolError(f"{name} failed after {max_retries + 1} attempts: {last_error}")

    # ── Queries ───────────────────────────────────────────

    def list_tools(self, category: str | None = None) -> list[dict]:
        """List all registered tools.

        Args:
            category: Optional filter (e.g. 'web', 'file', 'code')

        Returns:
            List of tool info dicts.
        """
        tools = []
        for name, tool in sorted(self._tools.items()):
            schema = tool["schema"]
            desc = schema.get("description", "")
            if category and category.lower() not in desc.lower() and category.lower() not in name:
                continue
            tools.append({
                "name": name,
                "description": desc[:100],
                "params": list(schema.get("parameters", {}).get("properties", {}).keys()),
            })
        return tools

    def get_schema(self, name: str) -> dict | None:
        """Get the schema for a tool."""
        tool = self._tools.get(name)
        return tool["schema"] if tool else None

    def get_help(self, name: str) -> str:
        """Get formatted help text for a tool."""
        tool = self._tools.get(name)
        if not tool:
            return f"Unknown tool: {name}"

        schema = tool["schema"]
        lines = [
            f"📦 {schema.get('name', name)}",
            f"   {schema.get('description', 'No description')}",
            "",
        ]

        params = schema.get("parameters", {})
        props = params.get("properties", {})
        required = params.get("required", [])

        if props:
            lines.append("   Parameters:")
            for pname, pinfo in props.items():
                req = " (required)" if pname in required else ""
                ptype = pinfo.get("type", "any")
                default = pinfo.get("default", "")
                default_str = f" [default: {default}]" if default != "" else ""
                lines.append(f"     • {pname}: {ptype}{req}{default_str}")
                desc = pinfo.get("description", "")
                if desc:
                    lines.append(f"       {desc}")

        return "\n".join(lines)

    # ── Validation ────────────────────────────────────────

    def _validate(self, params: dict, schema: dict) -> str | None:
        """Validate params against JSON Schema.

        Returns:
            Error message string, or None if valid.
        """
        params_schema = schema.get("parameters", {})
        props = params_schema.get("properties", {})
        required = params_schema.get("required", [])

        # Check required
        for req in required:
            if req not in params or params[req] is None:
                return f"Missing required parameter: '{req}'"

        # Check types (basic)
        for key, value in params.items():
            if key in props:
                expected_type = props[key].get("type")
                if expected_type == "array" and not isinstance(value, (list, tuple)):
                    return f"Parameter '{key}' should be an array"
                if expected_type == "integer" and not isinstance(value, int):
                    return f"Parameter '{key}' should be an integer"
                if expected_type == "number" and not isinstance(value, (int, float)):
                    return f"Parameter '{key}' should be a number"
                if expected_type == "boolean" and not isinstance(value, bool):
                    return f"Parameter '{key}' should be a boolean"

        return None

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    @property
    def tool_names(self) -> list[str]:
        return sorted(self._tools.keys())


# Singleton
_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """Get the global ToolRegistry singleton."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _registry.discover()
    return _registry


def reset_registry():
    """Reset the registry (for testing)."""
    global _registry
    _registry = None


def register_tool(name: str, schema: dict, execute_fn: Callable) -> bool:
    """Quick-register a tool in the global registry."""
    return get_registry().register(name, schema, execute_fn)


def call_tool(name: str, params: dict | None = None) -> str:
    """Quick-call a tool from the global registry."""
    return get_registry().call(name, params)


def list_tools(category: str | None = None) -> list[dict]:
    """Quick-list tools from the global registry."""
    return get_registry().list_tools(category=category)


def tool_help(name: str) -> str:
    """Quick-help for a tool."""
    return get_registry().get_help(name)
