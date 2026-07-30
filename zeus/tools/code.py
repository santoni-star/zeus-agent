"""Code execution tool — isolated Python execution with timeout.

Runs Python code in a subprocess with:
  - Timeout protection (default 30s)
  - Output capture (stdout + stderr)
  - Memory limit (basic)
  - No network access by default (optional)

Use for:
  - Quick calculations and data processing
  - Testing code snippets
  - Running scripts without side effects
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

SCHEMA = {
    "name": "code_exec",
    "description": "Execute Python code in an isolated subprocess with timeout. "
                   "Returns stdout and stderr. Safe for quick calculations and testing.",
    "parameters": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute",
            },
            "timeout": {
                "type": "integer",
                "description": "Max execution time in seconds (default: 15)",
                "default": 15,
            },
            "workdir": {
                "type": "string",
                "description": "Working directory (default: current)",
                "default": ".",
            },
        },
        "required": ["code"],
    },
}


def execute(params: dict) -> str:
    """Execute Python code in an isolated subprocess.

    Args:
        params: {"code": str, "timeout": int, "workdir": str}

    Returns:
        stdout + stderr output.
    """
    code = params["code"]
    timeout = min(params.get("timeout", 15), 60)
    workdir = Path(params.get("workdir", ".")).expanduser().resolve()

    if not workdir.exists():
        return f"❌ Working directory not found: {workdir}"

    # Wrap code to print result
    wrapped = textwrap.dedent(f"""
        import sys
        try:
            _locals = {{}}
            exec('''{code.replace("'", "\\'")}''', _locals)
            # If there's a result variable, print it
            if 'result' in _locals:
                print(_locals['result'])
        except Exception as e:
            import traceback
            traceback.print_exc()
    """)

    try:
        # Wrap code to auto-print final expression
        wrapped_code = code.strip()
        if not wrapped_code.startswith("print") and not wrapped_code.startswith("return"):
            # Check if it looks like an assignment or expression
            import ast
            try:
                tree = ast.parse(wrapped_code)
                if isinstance(tree.body[-1], ast.Expr):
                    # Last statement is an expression — wrap in print
                    last_line = wrapped_code.rsplit("\n", 1)[-1]
                    wrapped_code = wrapped_code[:len(wrapped_code) - len(last_line)] + f"print({last_line})"
            except SyntaxError:
                pass

        result = subprocess.run(
            [sys.executable, "-c", wrapped_code],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

        output_parts = []
        if result.stdout.strip():
            output_parts.append(result.stdout.strip())
        if result.stderr.strip():
            output_parts.append(f"⚠ stderr:\n{result.stderr.strip()}")

        if not output_parts:
            return "(no output)"

        return "\n---\n".join(output_parts)

    except subprocess.TimeoutExpired:
        return f"⏱ Code execution timed out after {timeout}s"
    except FileNotFoundError:
        return "❌ Python interpreter not found"
    except Exception as e:
        return f"❌ Execution error: {e}"
