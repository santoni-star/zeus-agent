"""Delegation — spawn child agents for parallel or isolated work.

Each child agent runs in its own subprocess with:
  - A specific task (goal + context)
  - Own LLM context (no contamination)
  - Limited tools (file, terminal, web)
  - Timeout protection
  - Result collection

Usage:
    from zeus.delegate import DelegateManager
    
    dm = DelegateManager()
    result = dm.run_task("Search for Python 3.14 news", timeout=120)
    print(result)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DelegateResult:
    """Result from a delegated task."""

    def __init__(self, task_id: str, task: str, output: str,
                 duration: float, success: bool, error: str = ""):
        self.task_id = task_id
        self.task = task
        self.output = output
        self.duration = duration
        self.success = success
        self.error = error

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task": self.task[:100],
            "output": self.output[:500],
            "duration": round(self.duration, 1),
            "success": self.success,
            "error": self.error[:200] if self.error else "",
        }

    def __str__(self) -> str:
        status = "✅" if self.success else "❌"
        return f"{status} {self.task[:60]} ({self.duration:.1f}s): {self.output[:200]}"


class DelegateManager:
    """Manages delegation of tasks to child agent processes.

    Each child runs Zeus in single-query mode with isolated context.
    """

    def __init__(self, max_workers: int = 3):
        self._max_workers = max_workers
        self._results: dict[str, DelegateResult] = {}

    def run_task(self, task: str, context: str = "",
                 timeout: int = 120, tools: list[str] | None = None) -> DelegateResult:
        """Run a single task in a child agent process.

        The child agent:
          - Receives the task as a direct query
          - Has no access to parent conversation history
          - Has limited tools (terminal, file, web)
          - Returns output via stdout

        Args:
            task: Task description for the child
            context: Additional context (file paths, constraints)
            timeout: Max execution time in seconds
            tools: Tool restrictions (default: all)

        Returns:
            DelegateResult with output or error.
        """
        task_id = uuid.uuid4().hex[:12]
        start = time.time()

        # Build child agent command
        cmd = [
            sys.executable, "-m", "zeus",
            "--no-interactive",
        ]

        # Add tool restrictions if specified
        if tools:
            cmd.extend(["--tools", ",".join(tools)])

        # Full prompt with context
        if context:
            prompt = f"CONTEXT: {context}\n\nTASK: {task}"
        else:
            prompt = task

        cmd.append(prompt)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "ZEUS_AGENT_MODE": "child"},
                cwd=os.getcwd(),
            )

            output = result.stdout.strip()
            error = result.stderr.strip()

            session_output = DelegateResult(
                task_id=task_id,
                task=task,
                output=output or "[no output]",
                duration=time.time() - start,
                success=result.returncode == 0,
                error=error if error else "",
            )

        except subprocess.TimeoutExpired:
            session_output = DelegateResult(
                task_id=task_id,
                task=task,
                output="",
                duration=time.time() - start,
                success=False,
                error=f"Timed out after {timeout}s",
            )
        except Exception as e:
            session_output = DelegateResult(
                task_id=task_id,
                task=task,
                output="",
                duration=time.time() - start,
                success=False,
                error=str(e),
            )

        self._results[task_id] = session_output
        logger.info("Delegate %s: %s (%.1fs)", task_id,
                     "success" if session_output.success else "failed",
                     session_output.duration)
        return session_output

    def run_parallel(self, tasks: list[tuple[str, str]]) -> list[DelegateResult]:
        """Run multiple tasks in parallel (limited by max_workers).

        Args:
            tasks: List of (task_prompt, context) tuples

        Returns:
            List of DelegateResult objects.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = []
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(self.run_task, task, ctx, timeout=120):
                (task, ctx)
                for task, ctx in tasks
            }
            for future in as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    task, ctx = futures[future]
                    results.append(DelegateResult(
                        task_id=uuid.uuid4().hex[:12],
                        task=task,
                        output="",
                        duration=0,
                        success=False,
                        error=str(e),
                    ))

        return sorted(results, key=lambda r: r.duration)

    def get_result(self, task_id: str) -> DelegateResult | None:
        """Get a completed task result."""
        return self._results.get(task_id)

    def list_results(self) -> list[dict]:
        """List all task results."""
        return [r.to_dict() for r in self._results.values()]

    @property
    def completed_count(self) -> int:
        return len(self._results)

    @property
    def success_rate(self) -> float:
        if not self._results:
            return 0.0
        successes = sum(1 for r in self._results.values() if r.success)
        return successes / len(self._results) * 100


# Convenience
_delegate_manager: DelegateManager | None = None


def get_delegate() -> DelegateManager:
    global _delegate_manager
    if _delegate_manager is None:
        _delegate_manager = DelegateManager()
    return _delegate_manager


def delegate_task(task: str, context: str = "", timeout: int = 120) -> str:
    """Quick one-shot delegation. Returns formatted result."""
    dm = get_delegate()
    result = dm.run_task(task, context=context, timeout=timeout)
    return str(result)
