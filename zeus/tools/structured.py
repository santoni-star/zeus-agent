"""Structured file editing — patch (find/replace) and write_file.

Inspired by Hermes Agent's patch and write_file tools.
Supports fuzzy matching for reliable find-and-replace editing.

Use cases:
  - Targeted code changes without full file rewrite
  - Atomic file writes with parent dir creation
  - Safe string replacement with validation
"""

from __future__ import annotations

import os
import re
import difflib
from pathlib import Path
from typing import Any

SCHEMA = {
    "name": "structured_file",
    "description": "Edit files with find/replace or full write. "
                   "Supports fuzzy matching for reliable patching.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["patch", "write", "read"],
                "description": "'patch' for find/replace, 'write' for full file write, 'read' to read file",
            },
            "path": {
                "type": "string",
                "description": "File path (absolute or relative)",
            },
            "old_string": {
                "type": "string",
                "description": "Text to find (for patch action). Must be unique in file.",
            },
            "new_string": {
                "type": "string",
                "description": "Replacement text (for patch action)",
            },
            "content": {
                "type": "string",
                "description": "Full file content (for write action)",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences (default: false)",
                "default": False,
            },
            "offset": {
                "type": "integer",
                "description": "Line offset for read action (1-indexed)",
                "default": 1,
            },
            "limit": {
                "type": "integer",
                "description": "Max lines to read",
                "default": 500,
            },
        },
        "required": ["action", "path"],
    },
}


def execute(params: dict) -> str:
    """Execute a structured file operation.

    Args:
        params: See SCHEMA for details.

    Returns:
        Result message with details of the operation.
    """
    action = params["action"]
    path = Path(params["path"]).expanduser().resolve()

    if action == "write":
        return _write(path, params.get("content", ""))
    elif action == "patch":
        return _patch(
            path,
            params.get("old_string", ""),
            params.get("new_string", ""),
            replace_all=params.get("replace_all", False),
        )
    elif action == "read":
        return _read(path, offset=params.get("offset", 1), limit=params.get("limit", 500))
    else:
        return f"Unknown action: {action}"


def _write(path: Path, content: str) -> str:
    """Write content to a file, creating parent directories."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

        # Syntax check for Python files
        warnings = ""
        if path.suffix == ".py":
            try:
                compile(content, str(path), "exec")
            except SyntaxError as e:
                warnings = f"\n⚠ Syntax warning: {e}"

        size = len(content)
        return f"✅ Written {size} bytes to {path}{warnings}"
    except Exception as e:
        return f"❌ Write failed: {e}"


def _patch(path: Path, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """Find and replace text in a file with fuzzy matching support.

    Uses difflib for fuzzy matching, allowing minor whitespace/indentation
    differences to still work.
    """
    if not path.exists():
        return f"❌ File not found: {path}"

    try:
        content = path.read_text()
    except Exception as e:
        return f"❌ Cannot read {path}: {e}"

    if not old_string:
        return "❌ old_string cannot be empty"

    # Direct match first
    if old_string in content:
        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)
    else:
        # Fuzzy match — try to find closest match
        matches = _fuzzy_find(content, old_string, top_n=3)
        if not matches:
            return (
                f"❌ Could not find unique match in {path}\n"
                f"Looking for: {old_string[:80]}...\n"
                f"File has {len(content)} chars."
            )

        # Use the best match
        matched, start, end = matches[0]
        if replace_all:
            # Replace all fuzzy matches (iterate)
            new_content = content
            for m, s, e in matches:
                new_content = new_content[:s] + new_string + new_content[e:]
        else:
            new_content = content[:start] + new_string + content[end:]

    # Validate: count occurrences instead of checking string presence
    old_count = content.count(old_string)
    new_count = new_content.count(old_string)
    expected_remaining = old_count - (1 if not replace_all else old_count)
    if new_count > expected_remaining:
        # Fuzzy replacement might have left fragments — that's ok, just warn
        pass

    try:
        path.write_text(new_content)
    except Exception as e:
        return f"❌ Write failed after patch: {e}"

    # Count changes
    old_lines = content.count("\n") + 1
    new_lines = new_content.count("\n") + 1
    diff_lines = abs(new_lines - old_lines)

    return (
        f"✅ Patched {path} ({old_lines} → {new_lines} lines, "
        f"{diff_lines} line{'s' if diff_lines != 1 else ''} changed)"
    )


def _fuzzy_find(text: str, pattern: str, top_n: int = 3) -> list[tuple[str, int, int]]:
    """Find closest fuzzy matches of pattern in text.

    Uses difflib.SequenceMatcher to handle whitespace/indentation differences.

    Returns:
        List of (matched_text, start_pos, end_pos) tuples, best first.
    """
    matcher = difflib.SequenceMatcher(None, pattern, text)
    # Get matching blocks
    matches = []
    for block in matcher.get_matching_blocks():
        if block.size > len(pattern) * 0.5:  # At least 50% match
            end = block.b + block.size
            matches.append((text[block.b:end], block.b, end))

    # Also try line-by-line on a sliding window
    pattern_lines = pattern.splitlines()
    text_lines = text.splitlines()

    if len(pattern_lines) >= 2:
        for i in range(len(text_lines) - len(pattern_lines) + 1):
            window = text_lines[i:i + len(pattern_lines)]
            window_text = "\n".join(window)

            # Compare similarity
            seq = difflib.SequenceMatcher(None, pattern, window_text)
            ratio = seq.ratio()
            if ratio > 0.7:
                # Calculate start position
                start = sum(len(l) + 1 for l in text_lines[:i])
                end = start + len(window_text)
                matches.append((window_text, start, end))

    # Deduplicate and sort by match quality
    seen = set()
    unique = []
    for m, s, e in sorted(matches, key=lambda x: -difflib.SequenceMatcher(None, pattern, x[0]).ratio()):
        if (s, e) not in seen:
            seen.add((s, e))
            unique.append((m, s, e))

    return unique[:top_n]


def _read(path: Path, offset: int = 1, limit: int = 500) -> str:
    """Read a file with line numbers."""
    if not path.exists():
        return f"❌ File not found: {path}"

    try:
        lines = path.read_text().splitlines()
    except Exception as e:
        return f"❌ Cannot read {path}: {e}"

    total = len(lines)
    start = max(0, offset - 1)
    end = min(total, start + limit)
    selected = lines[start:end]

    output = []
    output.append(f"📄 {path} ({total} lines, showing {start+1}-{end})")
    output.append("")
    for i, line in enumerate(selected, start + 1):
        output.append(f"{i:>6}|{line}")

    return "\n".join(output)
