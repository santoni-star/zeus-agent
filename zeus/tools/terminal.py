"""Terminal tool — execute shell commands."""

from __future__ import annotations
import subprocess
import shlex


SCHEMA = {
    "name": "terminal",
    "description": "Execute a shell command and return its output",
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute",
            },
            "timeout": {
                "type": "integer",
                "description": "Max seconds to wait (default: 30)",
                "default": 30,
            },
            "workdir": {
                "type": "string",
                "description": "Working directory (default: current)",
                "default": None,
            },
        },
        "required": ["command"],
    },
}


def execute(params: dict) -> str:
    """Execute a shell command.

    Args:
        params: {"command": str, "timeout": int, "workdir": str}

    Returns:
        Command output (stdout + stderr).
    """
    command = params["command"]
    timeout = params.get("timeout", 30)
    workdir = params.get("workdir")

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workdir,
        )
        output = result.stdout
        if result.stderr:
            if output:
                output += "\n" + result.stderr
            else:
                output = result.stderr
        if result.returncode != 0:
            output = f"Exit code {result.returncode}\n{output}"
        return output.strip()
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s: {command[:100]}..."
    except Exception as e:
        return f"Error executing command: {e}"