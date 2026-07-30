"""L0 Proactive Engine — scheduler, triggers, and background tasks.

Runs scheduled jobs and pattern-based triggers in the background.
Jobs persist across sessions via SQLite (using the memory store).

Schedule syntax:
  - ``every 30m`` — every 30 minutes
  - ``every 2h`` — every 2 hours  
  - ``daily at 9:00`` — once per day at specified time
  - ``once in 5m`` — single execution after 5 minutes

Usage:
    from zeus.proactive import Scheduler
    s = Scheduler()
    s.start()
    s.schedule_every(300, "search latest python news")
    ...
    s.stop()
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from typing import Any, Callable

logger = logging.getLogger(__name__)


class Scheduler:
    """Lightweight in-process task scheduler.

    Runs a background thread that checks every second for ready tasks.
    Can execute shell commands or Zeus tasks (via a provided callback).
    """

    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()
        self._task_callback: Callable | None = None

    def set_task_callback(self, cb: Callable):
        """Set callback for Zeus task execution.

        Callback signature: cb(text: str) -> str
        """
        self._task_callback = cb

    # ── Job scheduling ─────────────────────────────────────

    def schedule_every(
        self, interval_seconds: int, task: str,
        background: bool = False, job_id: str | None = None,
    ) -> str:
        """Schedule a task to run every N seconds.

        Args:
            interval_seconds: Interval in seconds
            task: Shell command or Zeus task text
            background: If True, run as shell command (no LLM)
            job_id: Optional custom ID

        Returns:
            Job ID
        """
        job_id = job_id or f"job_{int(time.time())}_{len(self._jobs)}"
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "kind": "interval",
                "interval": interval_seconds,
                "task": task,
                "background": background,
                "active": True,
                "last_run": 0.0,
                "next_run": time.time() + interval_seconds,
                "created_at": time.time(),
                "run_count": 0,
            }
        logger.info("Scheduled job %s every %ss: %s", job_id, interval_seconds, task[:60])
        return job_id

    def schedule_watchdog(
            self,
            interval: int,
            check_fn: callable,
            task: str,
            name: str | None = None,
        ) -> str:
            """Schedule a watchdog: checks a condition every 'interval' seconds.

            If check_fn() returns truthy (and wasn't true last check), fire 'task'.

            Args:
                interval: Check interval in seconds
                check_fn: Function that returns True/False for condition
                task: Task to execute when condition triggers
                name: Optional job name

            Returns:
                Job ID
            """
            jid = f"wd_{uuid.uuid4().hex[:8]}"
            next_run = time.time() + interval
            self._jobs[jid] = {
                "id": jid,
                "kind": "watchdog",
                "task": task,
                "interval": interval,
                "next_run": next_run,
                "active": True,
                "run_count": 0,
                "created_at": time.time(),
                "last_triggered": False,  # Track to avoid re-triggering
                "check_fn": check_fn,
            }
            if name:
                self._jobs[jid]["name"] = name
            return jid

    def schedule_memory_trigger(
        self,
        fact_pattern: str,
        task: str,
        interval: int = 300,
        name: str | None = None,
    ) -> str:
        """Schedule a memory trigger: checks memory for a fact pattern.

        Searches memory for 'fact_pattern' every 'interval' seconds.
        If found AND not previously found, fires 'task'.

        Args:
            fact_pattern: Search query for memory
            task: Task to execute when trigger fires
            interval: Check interval in seconds
            name: Optional job name

        Returns:
            Job ID
        """
        jid = f"mem_{uuid.uuid4().hex[:8]}"
        self._jobs[jid] = {
            "id": jid,
            "kind": "memory_trigger",
            "task": task,
            "interval": interval,
            "next_run": time.time() + interval,
            "active": True,
            "run_count": 0,
            "created_at": time.time(),
            "last_found": False,
            "fact_pattern": fact_pattern,
        }
        if name:
            self._jobs[jid]["name"] = name
        return jid

    def schedule_cron(
        background: bool = False, job_id: str | None = None,
    ) -> str:
        """Schedule a task using a simple cron-like expression.

        Supports:
        - ``*/N * * * *`` — every N minutes
        - ``0 * * * *`` — every hour
        - ``0 9 * * *`` — daily at 9:00
        - ``0 9 * * 1-5`` — weekdays at 9:00
        """
        job_id = job_id or f"cron_{int(time.time())}_{len(self._jobs)}"
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron expression: {cron_expr}. Use 'min hour dom mon dow'")

        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "kind": "cron",
                "cron": cron_expr,
                "cron_parts": parts,
                "task": task,
                "background": background,
                "active": True,
                "last_run": 0.0,
                "next_run": self._calc_cron_next(parts),
                "created_at": time.time(),
                "run_count": 0,
            }
        logger.info("Scheduled cron job %s: %s → %s", job_id, cron_expr, task[:60])
        return job_id

    def schedule_once(
        self, delay_seconds: int, task: str,
        background: bool = False, job_id: str | None = None,
    ) -> str:
        """Schedule a one-shot task after a delay."""
        job_id = job_id or f"once_{int(time.time())}_{len(self._jobs)}"
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "kind": "once",
                "task": task,
                "background": background,
                "active": True,
                "delay": delay_seconds,
                "last_run": 0.0,
                "next_run": time.time() + delay_seconds,
                "created_at": time.time(),
                "run_count": 0,
            }
        logger.info("Scheduled one-shot job %s in %ss: %s", job_id, delay_seconds, task[:60])
        return job_id

    # ── Job management ─────────────────────────────────────

    def cancel(self, job_id: str) -> bool:
        """Cancel a scheduled job."""
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["active"] = False
                return True
            return False

    def list_jobs(self) -> list[dict]:
        """List all jobs with their status."""
        with self._lock:
            now = time.time()
            result = []
            for j in self._jobs.values():
                entry = dict(j)
                if entry["active"]:
                    entry["next_in"] = max(0, entry["next_run"] - now)
                result.append(entry)
            return sorted(result, key=lambda x: x.get("next_run", 0))

    def pause(self, job_id: str) -> bool:
        """Pause a job without removing it."""
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["active"] = False
                return True
            return False

    def resume(self, job_id: str) -> bool:
        """Resume a paused job."""
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["active"] = True
                self._jobs[job_id]["next_run"] = time.time() + self._jobs[job_id].get("interval", 300)
                return True
            return False

    # ── Engine ──────────────────────────────────────────────

    def start(self):
        """Start the scheduler background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Scheduler started")

    def stop(self):
        """Stop the scheduler."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("Scheduler stopped")

    def _run_loop(self):
        """Background loop — check every second for ready tasks."""
        while self._running:
            self._tick()
            time.sleep(1)

    def _tick(self):
        """Check for ready tasks and execute them."""
        now = time.time()
        ready: list[dict] = []

        with self._lock:
            for job_id, job in self._jobs.items():
                if not job["active"]:
                    continue
                if now >= job["next_run"]:
                    ready.append(job)
                    # Update next run
                    if job["kind"] == "interval":
                        job["next_run"] = now + job["interval"]
                    elif job["kind"] == "cron":
                        job["next_run"] = self._calc_cron_next(job["cron_parts"])
                    elif job["kind"] == "once":
                        job["active"] = False  # one-shot, disable after first run
                    job["last_run"] = now
                    job["run_count"] += 1

        # Execute outside lock
        for job in ready:
            self._execute_job(job)

    def _execute_job(self, job: dict):
        """Execute a single job."""
        task = job["task"]
        bg = job.get("background", False)
        logger.info("Executing job %s: %s", job["id"], task[:60])

        try:
            if bg or self._task_callback is None:
                # Run as shell command
                import subprocess, os
                subprocess.run(
                    task, shell=True, timeout=300,
                    capture_output=True,
                    cwd=os.path.expanduser("~"),
                )
            else:
                # Run as Zeus task via callback
                self._task_callback(task)
        except Exception as e:
            logger.error("Job %s failed: %s", job["id"], e)

    # ── Cron parsing (minimal) ─────────────────────────────

    def _calc_cron_next(self, parts: list[str]) -> float:
        """Calculate next execution time from cron parts.

        Supports: minute, hour, day-of-month, month, day-of-week.
        Each field supports: *, */N, N, N-M, N,M,O
        """
        import datetime

        now = datetime.datetime.now()
        cron_min, cron_hour, cron_dom, cron_mon, cron_dow = parts

        # Start from next minute
        t = now.replace(second=0) + datetime.timedelta(minutes=1)

        for _ in range(525600):  # max 1 year lookahead
            if not self._cron_match(cron_mon, t.month):
                t += datetime.timedelta(days=1)
                t = t.replace(hour=0, minute=0)
                continue
            if not self._cron_match(cron_dom, t.day):
                t += datetime.timedelta(days=1)
                t = t.replace(hour=0, minute=0)
                continue
            if not self._cron_match(cron_dow, (t.weekday() + 1) % 7):
                t += datetime.timedelta(days=1)
                t = t.replace(hour=0, minute=0)
                continue
            if not self._cron_match(cron_hour, t.hour):
                t += datetime.timedelta(hours=1)
                t = t.replace(minute=0)
                continue
            if not self._cron_match(cron_min, t.minute):
                t += datetime.timedelta(minutes=1)
                continue
            return t.timestamp()

        return time.time() + 86400  # fallback: 1 day

    @staticmethod
    def _cron_match(pattern: str, value: int) -> bool:
        """Check if a value matches a cron field pattern."""
        if pattern == "*":
            return True
        if "/" in pattern:
            base, step = pattern.split("/", 1)
            base = 0 if base == "*" else int(base)
            return (value - base) % int(step) == 0
        if "-" in pattern:
            lo, hi = (int(x) for x in pattern.split("-", 1))
            return lo <= value <= hi
        if "," in pattern:
            return value in (int(x) for x in pattern.split(","))
        try:
            return int(pattern) == value
        except ValueError:
            return False

    # ── Persistence ────────────────────────────────────────

    def save_state(self, filepath: str | None = None) -> None:
        """Save job state to JSON for persistence across restarts."""
        import json
        from pathlib import Path

        path = Path(filepath or Path.home() / ".zeus" / "scheduler.json")
        path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            # Only save active interval/cron jobs (not oneshots or inactive)
            saveable = {
                jid: j for jid, j in self._jobs.items()
                if j["active"] and j["kind"] in ("interval", "cron")
            }
        with open(path, "w") as f:
            json.dump(saveable, f, indent=2, ensure_ascii=False)

    def load_state(self, filepath: str | None = None, callback: Callable | None = None) -> int:
        """Load jobs from JSON state file.

        Returns:
            Number of jobs restored.
        """
        import json
        from pathlib import Path

        path = Path(filepath or Path.home() / ".zeus" / "scheduler.json")
        if not path.exists():
            return 0

        with open(path) as f:
            jobs = json.load(f)

        restored = 0
        for jid, job in jobs.items():
            # Recalculate next_run from now
            if job.get("kind") == "interval":
                job["next_run"] = time.time() + job.get("interval", 300)
            elif job.get("kind") == "cron":
                job["next_run"] = self._calc_cron_next(job.get("cron_parts", ["*"] * 5))
            job["last_run"] = 0
            job["run_count"] = 0

            with self._lock:
                if jid not in self._jobs:
                    self._jobs[jid] = job
                    restored += 1

        return restored