"""MCP Context Module — reads project context (files, git, issues)
and injects it into the pipeline for better decision-making.

Inspired by Model Context Protocol: provides context to the agent
without blocking the main execution flow.

Subscribes to: pipeline.request, context.request
Emits:         context.result, mcp.context_ready

Runs in parallel with the pipeline — context fetching is async.
"""

from __future__ import annotations
import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from zeus.module import Module, Event, CONTEXT_REQUEST, CONTEXT_RESULT

logger = logging.getLogger(__name__)


class MCPModule(Module):
    """Project context provider.

    Reads:
      - Project files (README, AGENTS.md, CLAUDE.md, cursorrules)
      - Git log (recent commits, branch)
      - Directory structure
      - File summaries

    Provides context to the pipeline for better planning.
    """

    def __init__(self, bus=None, project_dir: str | None = None):
        super().__init__(
            name="mcp",
            description="Project context provider (files, git, structure)",
            bus=bus,
        )
        self._project_dir = project_dir or os.getcwd()
        self._cache: dict[str, tuple[str, float]] = {}
        self._cache_ttl = 30  # seconds

    async def start(self):
        await super().start()
        self.subscribe(CONTEXT_REQUEST, self._handle_context_request)
        self.subscribe("pipeline.request", self._handle_pipeline_request)

    async def _handle_context_request(self, event: Event):
        """Handle explicit context request."""
        query = event.data.get("query", "")
        if not query:
            query = "general"

        context = self._get_relevant_context(query)
        if context:
            await self.emit(CONTEXT_RESULT, {
                "query": query,
                "context": context,
                "source": "mcp",
                "event_id": event.id,
            })

    async def _handle_pipeline_request(self, event: Event):
        """When pipeline starts, provide project context."""
        context = self._get_project_context()
        if context:
            # Inject context as event data for the pipeline
            await self.emit("mcp.context_ready", {
                "context": context,
                "event_id": event.id,
            })

    # ── Context gathering ─────────────────────────────────

    def _get_project_context(self) -> str:
        """Get full project context."""
        parts = []

        # Project name from directory
        project = Path(self._project_dir).name
        parts.append(f"Project: {project}")
        parts.append(f"Directory: {self._project_dir}")

        # README or similar
        readme = self._read_file_if_exists("README.md")
        if readme:
            parts.append(f"\nREADME:\n{readme[:300]}")

        # AGENTS.md / CLAUDE.md
        agents_md = self._read_file_if_exists("AGENTS.md") or self._read_file_if_exists("CLAUDE.md")
        if agents_md:
            parts.append(f"\nAgent instructions:\n{agents_md[:300]}")

        # Git context
        git_info = self._get_git_context()
        if git_info:
            parts.append(f"\nGit: {git_info}")

        # Directory structure (top level)
        structure = self._get_directory_structure()
        if structure:
            parts.append(f"\nStructure:\n{structure}")

        return "\n\n".join(parts)

    def _get_relevant_context(self, query: str) -> str:
        """Get context relevant to a specific query."""
        parts = []

        # Git recent changes
        git_log = self._get_git_log(limit=3)
        if git_log:
            parts.append(f"Recent changes:\n{git_log}")

        # Search for relevant files
        q_lower = query.lower()
        keywords = [w for w in q_lower.split() if len(w) > 3]
        for kw in keywords[:3]:
            files = self._find_files_with_keyword(kw, limit=3)
            if files:
                parts.append(f"Files matching '{kw}': {', '.join(files)}")

        return "\n\n".join(parts)

    # ── Helpers ────────────────────────────────────────────

    def _read_file_if_exists(self, filename: str) -> str | None:
        """Read a file from the project directory."""
        path = Path(self._project_dir) / filename
        return self._cached_read(path)

    def _cached_read(self, path: Path) -> str | None:
        """Read with caching."""
        now = __import__("time").time()
        path_str = str(path)

        # Check cache
        if path_str in self._cache:
            content, timestamp = self._cache[path_str]
            if now - timestamp < self._cache_ttl:
                return content

        if not path.exists() or not path.is_file():
            return None

        try:
            content = path.read_text(errors="replace")[:500]
            self._cache[path_str] = (content, now)
            return content
        except Exception:
            return None

    def _get_git_context(self) -> str | None:
        """Get git branch and status."""
        try:
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                capture_output=True, text=True, timeout=5,
                cwd=self._project_dir,
            ).stdout.strip()

            status = subprocess.run(
                ["git", "status", "--short"],
                capture_output=True, text=True, timeout=5,
                cwd=self._project_dir,
            ).stdout.strip()

            lines = status.count("\n") if status else 0
            modified = f" ({lines} modified)" if lines else ""
            return f"branch: {branch}{modified}"
        except Exception:
            return None

    def _get_git_log(self, limit: int = 5) -> str | None:
        """Get recent git log."""
        try:
            result = subprocess.run(
                ["git", "log", f"--max-count={limit}", "--oneline"],
                capture_output=True, text=True, timeout=5,
                cwd=self._project_dir,
            )
            return result.stdout.strip() or None
        except Exception:
            return None

    def _get_directory_structure(self) -> str | None:
        """Get top-level directory structure."""
        path = Path(self._project_dir)
        items = []
        try:
            for item in sorted(path.iterdir()):
                if item.name.startswith("."):
                    continue
                if item.name.startswith("__"):
                    continue
                suffix = "/" if item.is_dir() else ""
                items.append(f"  {item.name}{suffix}")
            if items:
                return "\n".join(items[:20])  # Limit to 20 items
        except Exception:
            pass
        return None

    def _find_files_with_keyword(self, keyword: str, limit: int = 5) -> list[str]:
        """Find files containing a keyword (using grep)."""
        try:
            result = subprocess.run(
                ["grep", "-rl", keyword, "--include=*.py", "--include=*.md",
                 "--include=*.yaml", "--include=*.yml", "--include=*.json",
                 "--include=*.txt", self._project_dir],
                capture_output=True, text=True, timeout=5,
            )
            files = [f for f in result.stdout.strip().split("\n") if f]
            return [Path(f).name for f in files[:limit]]
        except Exception:
            return []

    def status(self) -> dict:
        return {
            "name": self.name,
            "running": self._running,
            "project": Path(self._project_dir).name,
            "dir": self._project_dir,
        }