"""Dynamic tool creation and management system.

Allows Zeus to create, store, and use custom tools at runtime.
Tools are Python files stored in ~/.zeus/custom_tools/ and auto-loaded.

Each tool has:
  - SCHEMA: JSON Schema for parameters (name, description, parameters)
  - execute(params: dict) -> str: the tool implementation
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

CUSTOM_TOOLS_DIR = Path.home() / ".zeus" / "custom_tools"
TOOL_TEMPLATE = '''"""Dynamic tool: {name} — {description}"""

from __future__ import annotations

SCHEMA = {schema}

def execute(params: dict) -> str:
    """Execute the {name} tool."""
{body}
'''


def ensure_tools_dir():
    """Create the custom tools directory if it doesn't exist."""
    CUSTOM_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    (CUSTOM_TOOLS_DIR / "__init__.py").touch(exist_ok=True)


def generate_tool_code(
    name: str,
    description: str,
    parameters: dict,
    implementation: str,
) -> str:
    """Generate a Python tool file from components.

    Args:
        name: Tool name (lowercase, underscores)
        description: Human-readable description
        parameters: JSON Schema dict for parameters
        implementation: Python code for the execute function body (indented)

    Returns:
        Complete Python source code for the tool.
    """
    schema = {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": parameters,
            "required": list(parameters.keys()),
        },
    }
    return TOOL_TEMPLATE.format(
        name=name,
        description=description,
        schema=json.dumps(schema, indent=2, ensure_ascii=False),
        body=implementation,
    )


def save_tool(name: str, source_code: str) -> Path:
    """Save a tool file to the custom tools directory.

    Args:
        name: Tool name (used as filename)
        source_code: Complete Python source

    Returns:
        Path to the saved file.
    """
    ensure_tools_dir()
    filepath = CUSTOM_TOOLS_DIR / f"{name}.py"
    filepath.write_text(source_code)
    logger.info("Saved dynamic tool: %s", filepath)
    return filepath


def delete_tool(name: str) -> bool:
    """Delete a custom tool.

    Args:
        name: Tool name

    Returns:
        True if deleted, False if not found.
    """
    filepath = CUSTOM_TOOLS_DIR / f"{name}.py"
    if filepath.exists():
        filepath.unlink()
        logger.info("Deleted dynamic tool: %s", name)
        return True
    return False


def list_custom_tools() -> list[dict]:
    """List all custom tools with their schemas.

    Returns:
        List of dicts with name, description, path.
    """
    ensure_tools_dir()
    tools = []
    for f in CUSTOM_TOOLS_DIR.glob("*.py"):
        if f.name == "__init__.py":
            continue
        module = _load_tool_module(f)
        if module and hasattr(module, "SCHEMA"):
            schema = module.SCHEMA
            tools.append({
                "name": schema.get("name", f.stem),
                "description": schema.get("description", ""),
                "path": str(f),
                "parameters": schema.get("parameters", {}),
            })
    return tools


def discover_custom_tools() -> dict[str, dict]:
    """Discover and load all custom tools.

    Returns:
        Dict mapping tool name → {schema, handler}
    """
    ensure_tools_dir()
    tools = {}

    for f in sorted(CUSTOM_TOOLS_DIR.glob("*.py")):
        if f.name == "__init__.py":
            continue
        module = _load_tool_module(f)
        if module is None:
            continue

        schema = getattr(module, "SCHEMA", None)
        handler = getattr(module, "execute", None)
        if schema and handler:
            name = schema.get("name", f.stem)
            tools[name] = {"schema": schema, "handler": handler}
            logger.debug("Loaded custom tool: %s", name)

    return tools


def _load_tool_module(filepath: Path):
    """Dynamically import a Python file as a module."""
    try:
        module_name = f"custom_tools_{filepath.stem}"
        spec = importlib.util.spec_from_file_location(module_name, filepath)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            # Add to sys.modules so imports within the tool work
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            return module
    except Exception as e:
        logger.warning("Failed to load custom tool %s: %s", filepath.name, e)
    return None


# ── LLM-based tool generation ───────────────────────────────

TOOL_GENERATOR_PROMPT = """Ти — генератор інструментів для Zeus Agent.

Користувач описує інструмент, який він хоче створити.
Твоя задача — згенерувати PYTHON КОД для цього інструмента.

Формат тулзи:
```python
\"\"\"Dynamic tool: <name> — <description>\"\"\"

from __future__ import annotations

SCHEMA = {{
    "name": "<tool_name>",
    "description": "<human-readable description>",
    "parameters": {{
        "type": "object", 
        "properties": {{
            "<param1>": {{
                "type": "<string|number|boolean|array>",
                "description": "<what this param does>"
            }},
            ...
        }},
        "required": ["<param1>"]
    }}
}}

def execute(params: dict) -> str:
    \"\"\"Execute the tool.\"\"\"
    <implementation>
```

ПРАВИЛА:
1. Ім'я тулзи — lowercase_underscore
2. Параметри — зрозумілі, з описами
3. execute() завжди повертає str
4. Код має бути безпечним (не виконувати шкідливі операції)
5. Використовувати тільки стандартну бібліотеку Python
6. Відповідь — ТІЛЬКИ код, без додаткового тексту
"""


def generate_tool_from_description(
    description: str,
    llm_call: Callable,
) -> tuple[str, str] | None:
    """Generate a tool from a natural language description.

    Args:
        description: User's description of the tool
        llm_call: LLM function to call

    Returns:
        Tuple of (tool_name, source_code) or None on failure.
    """
    response = llm_call(
        messages=[
            {"role": "system", "content": TOOL_GENERATOR_PROMPT},
            {"role": "user", "content": f"Створи інструмент: {description}"},
        ],
    )

    # Extract code from response (handle markdown code blocks)
    code = response.strip()
    if "```python" in code:
        code = code.split("```python")[1]
        if "```" in code:
            code = code.split("```")[0]
    elif "```" in code:
        code = code.split("```")[1]
        if "```" in code:
            code = code.split("```")[0]

    code = code.strip()

    # Extract tool name from SCHEMA
    import re
    match = re.search(r'"name"\s*:\s*"([^"]+)"', code)
    if not match:
        logger.error("Could not extract tool name from generated code")
        return None

    name = match.group(1)
    return name, code


def create_tool(
    description: str,
    llm_call: Callable,
    name_override: str | None = None,
) -> dict:
    """Create a tool from description and save it.

    Args:
        description: What the tool should do
        llm_call: LLM function
        name_override: Optional override for tool name

    Returns:
        Dict with result status and tool info.
    """
    result = generate_tool_from_description(description, llm_call)
    if result is None:
        return {"success": False, "error": "Failed to generate tool code"}

    name, code = result
    if name_override:
        name = name_override
        # Fix the name in the schema
        code = code.replace(f'"name": "{result[0]}"', f'"name": "{name}"')

    path = save_tool(name, code)

    # Try to import to validate
    try:
        module = _load_tool_module(path)
        if module and hasattr(module, "SCHEMA") and hasattr(module, "execute"):
            return {
                "success": True,
                "name": name,
                "path": str(path),
                "schema": module.SCHEMA,
            }
        else:
            return {
                "success": False,
                "error": "Generated tool missing SCHEMA or execute()",
                "name": name,
                "path": str(path),
            }
    except Exception as e:
        return {
            "success": False,
            "error": f"Generated tool failed to load: {e}",
            "name": name,
            "path": str(path),
        }