"""File tool — read, write, search files."""

from __future__ import annotations
import os
import glob


SCHEMA = {
    "name": "file",
    "description": "Read, write, or search files on the local filesystem",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "write", "search", "list"],
                "description": "What to do with the file",
            },
            "path": {
                "type": "string",
                "description": "File path (for read/write) or directory (for list/search)",
            },
            "content": {
                "type": "string",
                "description": "Content to write (for write action)",
            },
            "pattern": {
                "type": "string",
                "description": "Glob pattern to search for (for search action, e.g. '*.py')",
            },
        },
        "required": ["action", "path"],
    },
}


def execute(params: dict) -> str:
    """Execute a file operation.

    Args:
        params: {"action": str, "path": str, "content": str, "pattern": str}

    Returns:
        Operation result.
    """
    action = params["action"]
    path = os.path.expanduser(params["path"])

    if action == "read":
        return _read_file(path)

    elif action == "write":
        content = params.get("content", "")
        return _write_file(path, content)

    elif action == "search":
        pattern = params.get("pattern", "*")
        return _search_files(path, pattern)

    elif action == "list":
        return _list_files(path)

    return f"Unknown action: {action}"


def _read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        lines = content.count("\n") + 1
        return f"{path} ({lines} lines, {len(content)} chars):\n\n{content}"
    except FileNotFoundError:
        return f"File not found: {path}"
    except Exception as e:
        return f"Error reading {path}: {e}"


def _write_file(path: str, content: str) -> str:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Written {len(content)} chars to {path}"
    except Exception as e:
        return f"Error writing to {path}: {e}"


def _search_files(directory: str, pattern: str) -> str:
    try:
        matches = glob.glob(os.path.join(directory, pattern), recursive=True)
        if not matches:
            return f"No files matching '{pattern}' in {directory}"
        result = [f"Found {len(matches)} file(s):"]
        for m in sorted(matches):
            size = os.path.getsize(m) if os.path.isfile(m) else 0
            result.append(f"  {m} ({size} bytes)")
        return "\n".join(result)
    except Exception as e:
        return f"Error searching: {e}"


def _list_files(directory: str) -> str:
    try:
        entries = os.listdir(directory)
        result = [f"{directory}/ ({len(entries)} entries):"]
        for e in sorted(entries):
            full = os.path.join(directory, e)
            kind = "📁" if os.path.isdir(full) else "📄"
            result.append(f"  {kind} {e}")
        return "\n".join(result)
    except Exception as e:
        return f"Error listing {directory}: {e}"