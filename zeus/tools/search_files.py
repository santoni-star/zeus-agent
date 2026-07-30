"""File search tool — enhanced search with context lines.

Uses ripgrep (rg) if available, falls back to Python's re.
Searches by content (regex) or filename (glob).

Examples:
  - search_files("def hello", path=".", file_glob="*.py")
  - search_files("*config*", target="files")
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

SCHEMA = {
    "name": "search_files",
    "description": "Search file contents or find files by name. "
                   "Supports regex patterns and glob matching.",
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern (content search) or glob (file search)",
            },
            "target": {
                "type": "string",
                "enum": ["content", "files"],
                "description": "'content' searches inside files, 'files' finds by name",
                "default": "content",
            },
            "path": {
                "type": "string",
                "description": "Directory to search in",
                "default": ".",
            },
            "file_glob": {
                "type": "string",
                "description": "Only search files matching this glob (e.g. '*.py')",
                "default": "",
            },
            "context": {
                "type": "integer",
                "description": "Context lines before/after match",
                "default": 0,
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results to return",
                "default": 20,
            },
        },
        "required": ["pattern"],
    },
}


def execute(params: dict) -> str:
    """Search files by content or name.

    Args:
        params: See SCHEMA.

    Returns:
        Formatted search results.
    """
    pattern = params["pattern"]
    target = params.get("target", "content")
    path = Path(params.get("path", ".")).expanduser().resolve()
    file_glob = params.get("file_glob", "")
    context = params.get("context", 0)
    max_results = min(params.get("max_results", 20), 100)

    if not path.exists():
        return f"❌ Path not found: {path}"

    if target == "files":
        return _search_files(pattern, path, max_results)
    else:
        return _search_content(pattern, path, file_glob, context, max_results)


def _search_files(glob_pattern: str, path: Path, max_results: int) -> str:
    """Find files by glob pattern."""
    try:
        matches = list(path.rglob(glob_pattern))
    except Exception:
        # Fallback to simple name match
        matches = [p for p in path.rglob("*") if glob_pattern.lower() in p.name.lower()]

    if not matches:
        return f"No files matching '{glob_pattern}' in {path}"

    matches = sorted(matches)[:max_results]
    lines = [f"📁 Found {len(matches)} file(s) matching '{glob_pattern}':\n"]
    for m in matches:
        rel = m.relative_to(path) if m != path else m
        size = m.stat().st_size if m.is_file() else 0
        if m.is_dir():
            lines.append(f"  📁 {rel}/")
        else:
            lines.append(f"  📄 {rel} ({size:,} bytes)")

    return "\n".join(lines)


def _search_content(pattern: str, path: Path, file_glob: str,
                    context: int, max_results: int) -> str:
    """Search file contents using regex."""
    # Try ripgrep first (much faster)
    try:
        return _search_with_rg(pattern, path, file_glob, context, max_results)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback to Python
    return _search_with_python(pattern, path, file_glob, context, max_results)


def _search_with_rg(pattern: str, path: Path, file_glob: str,
                    context: int, max_results: int) -> str:
    """Use ripgrep for fast search."""
    cmd = ["rg", "-n", "--no-heading", "--color", "never", "-m", "5"]

    if context > 0:
        cmd.extend(["-C", str(context)])

    if file_glob:
        cmd.extend(["--glob", file_glob])

    cmd.extend([pattern, str(path)])

    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=30,
    )

    if result.returncode not in (0, 1):  # 1 = no matches
        raise Exception(result.stderr)

    output = result.stdout.strip()
    if not output:
        # Try Python fallback
        return _search_with_python(pattern, path, file_glob, context, max_results)

    lines = output.split("\n")
    if len(lines) > max_results * 2:
        lines = lines[:max_results * 2]
        lines.append("... (truncated)")

    return "\n".join(lines)


def _search_with_python(pattern: str, path: Path, file_glob: str,
                        context: int, max_results: int) -> str:
    """Pure Python fallback for file content search."""
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"❌ Invalid regex: {e}"

    results = []
    files_scanned = 0

    for f in path.rglob("*"):
        if not f.is_file():
            continue
        if file_glob and not f.match(file_glob):
            continue

        files_scanned += 1
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue

        for i, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                rel = f.relative_to(path)
                prefix = f"{rel}:{i}"
                if context > 0:
                    lines = text.splitlines()
                    start = max(0, i - 1 - context)
                    end = min(len(lines), i + context)
                    ctx_lines = []
                    for ci in range(start, end):
                        marker = ">" if ci == i - 1 else " "
                        ctx_lines.append(f"{marker} {lines[ci]}")
                    results.append(f"{prefix}\n" + "\n".join(ctx_lines))
                else:
                    results.append(f"{prefix}: {line.strip()[:200]}")

                if len(results) >= max_results:
                    break

        if len(results) >= max_results:
            break

    if not results:
        return f"No matches for '{pattern}' in {path} (scanned {files_scanned} files)"

    return "\n".join(results)
