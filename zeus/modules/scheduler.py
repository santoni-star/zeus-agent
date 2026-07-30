"""Scheduler module — cron scheduling as an EventBus module.

Converts the legacy proactive.py (background thread) into a proper
EventBus module with persistent SQLite storage.

Subscribes to: scheduler.add, scheduler.remove, scheduler.list, scheduler.pause, scheduler.resume
Emits:         scheduler.tick, scheduler.result

Usage:
    from zeus.modules.scheduler import SchedulerModule
    module = SchedulerModule(bus=bus)
    await module.schedule_every(300, "check weather")
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from zeus.module import Module, Event
from zeus.proactive import Scheduler

logger = logging.getLogger(__name__)


class SchedulerModule(Module):
    """Cron-style scheduler as an EventBus module.

    Runs scheduled jobs and pattern-based triggers in the background.
    Jobs are persisted in SQLite and survive agent restarts.

    Supports:
      - Interval jobs ("every 30m")
      - Watchdog triggers (condition-based)
      - Memory triggers (fact-pattern-based)
      - Persistent storage in SQLite
      - EventBus integration (subscribe to scheduler.*, emit results)
    """

    def __init__(
        self,
        bus=None,
        db_path: str | None = None,
        legacy_scheduler: Scheduler | None = None,
    ):
        super().__init__(
            name="scheduler",
            description="Cron scheduling: periodic tasks, watchdogs, memory triggers",
            bus=bus,
        )
        # Use existing Scheduler if provided (backward compat)
        if legacy_scheduler:
            self._scheduler = legacy_scheduler
            self._own_scheduler = False
        else:
            self._scheduler = Scheduler()
            self._own_scheduler = True

        self._db_path = Path(
            db_path or os.environ.get("ZEUS_JOBS_DB", "~/.zeus/jobs.db")
        ).expanduser()
        self._db_conn: sqlite3.Connection | None = None
        self._polling_task: asyncio.Task | None = None
        self._loop_interval = 1.0  # check jobs every second

    async def start(self):
        """Start the module: init DB, load persisted jobs, start polling."""
        await super().start()
        self._init_db()
        self._load_jobs()

        # Subscribe to scheduler commands
        self.subscribe("scheduler.add", self._handle_add)
        self.subscribe("scheduler.remove", self._handle_remove)
        self.subscribe("scheduler.list", self._handle_list)
        self.subscribe("scheduler.pause", self._handle_pause)
        self.subscribe("scheduler.resume", self._handle_resume)

        # Start polling loop
        self._polling_task = asyncio.create_task(self._polling_loop())
        logger.info("SchedulerModule: started (%d jobs loaded)", len(self._scheduler._jobs))

    async def stop(self):
        """Stop the module: cancel polling, persist jobs, close DB."""
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None
        self._save_jobs()
        self._close_db()
        await super().stop()
        logger.info("SchedulerModule: stopped")

    # ── Database ──────────────────────────────────────────

    def _init_db(self):
        """Initialize SQLite database for persistent job storage."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_conn = sqlite3.connect(str(self._db_path))
        self._db_conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                task TEXT NOT NULL,
                interval INTEGER,
                background INTEGER DEFAULT 0,
                created_at REAL,
                active INTEGER DEFAULT 1,
                run_count INTEGER DEFAULT 0,
                last_run REAL,
                config TEXT
            )
        """)
        self._db_conn.commit()

    def _close_db(self):
        """Close the database connection."""
        if self._db_conn:
            self._db_conn.close()
            self._db_conn = None

    def _save_jobs(self):
        """Save current jobs to database."""
        if not self._db_conn:
            return
        try:
            self._db_conn.execute("DELETE FROM jobs")
            with self._scheduler._lock:
                for jid, job in self._scheduler._jobs.items():
                    config = {}
                    if "check_fn" in job:
                        config["has_check_fn"] = True
                    if "fact_pattern" in job:
                        config["fact_pattern"] = job["fact_pattern"]
                    if "name" in job:
                        config["name"] = job["name"]

                    self._db_conn.execute(
                        """INSERT INTO jobs (id, kind, task, interval, background,
                           created_at, active, run_count, last_run, config)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            jid,
                            job.get("kind", "interval"),
                            job.get("task", ""),
                            job.get("interval", 0),
                            1 if job.get("background", False) else 0,
                            job.get("created_at", time.time()),
                            1 if job.get("active", True) else 0,
                            job.get("run_count", 0),
                            job.get("last_run", 0),
                            json.dumps(config),
                        ),
                    )
            self._db_conn.commit()
            logger.debug("Saved %d jobs to DB", len(self._scheduler._jobs))
        except Exception as e:
            logger.error("Failed to save jobs: %s", e)

    def _load_jobs(self):
        """Load persisted jobs from database."""
        if not self._db_conn:
            return
        try:
            cursor = self._db_conn.execute("SELECT * FROM jobs WHERE active=1")
            loaded = 0
            for row in cursor.fetchall():
                jid = row[0]
                kind = row[1]
                task = row[2]
                interval = row[3] or 300
                active = bool(row[6])
                run_count = row[7] or 0
                last_run = row[8] or 0
                config_str = row[9]

                config = {}
                if config_str:
                    try:
                        config = json.loads(config_str)
                    except json.JSONDecodeError:
                        pass

                if kind == "watchdog":
                    # Can't restore watchdog without check_fn — skip
                    if not config.get("has_check_fn"):
                        continue
                elif kind == "memory_trigger":
                    # Restore memory trigger
                    fact_pattern = config.get("fact_pattern", "")
                    name = config.get("name", "")
                    with self._scheduler._lock:
                        self._scheduler._jobs[jid] = {
                            "id": jid,
                            "kind": kind,
                            "task": task,
                            "interval": interval,
                            "next_run": time.time(),
                            "active": active,
                            "run_count": run_count,
                            "created_at": row[5] or time.time(),
                            "last_found": False,
                            "fact_pattern": fact_pattern,
                            "name": name,
                        }
                    loaded += 1
                else:
                    # Regular interval job
                    with self._scheduler._lock:
                        self._scheduler._jobs[jid] = {
                            "id": jid,
                            "kind": kind,
                            "task": task,
                            "interval": interval,
                            "background": config.get("background", False),
                            "active": active,
                            "next_run": time.time(),
                            "last_run": last_run,
                            "run_count": run_count,
                            "created_at": row[5] or time.time(),
                        }
                    loaded += 1
            logger.info("Loaded %d jobs from DB", loaded)
        except Exception as e:
            logger.error("Failed to load jobs: %s", e)

    # ── Polling loop ──────────────────────────────────────

    async def _polling_loop(self):
        """Background loop that checks and fires ready jobs."""
        while True:
            try:
                now = time.time()
                ready_jobs = []

                with self._scheduler._lock:
                    for jid, job in list(self._scheduler._jobs.items()):
                        if not job.get("active", True):
                            continue
                        next_run = job.get("next_run", 0)
                        if now >= next_run:
                            ready_jobs.append((jid, job))

                for jid, job in ready_jobs:
                    await self._execute_job(jid, job)

                await asyncio.sleep(self._loop_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("SchedulerModule: polling error: %s", e)
                await asyncio.sleep(5.0)

    async def _execute_job(self, jid: str, job: dict):
        """Execute a single job and update its state."""
        kind = job.get("kind", "interval")
        task = job.get("task", "")
        interval = job.get("interval", 300)

        if kind == "watchdog":
            # Check condition
            check_fn = job.get("check_fn")
            if check_fn:
                try:
                    triggered = check_fn()
                    if triggered and not job.get("last_triggered", False):
                        job["last_triggered"] = True
                        await self._fire_task(jid, task)
                    elif not triggered:
                        job["last_triggered"] = False
                except Exception as e:
                    logger.error("Watchdog check failed: %s", e)
            # Update next run
            job["next_run"] = time.time() + interval
            return

        elif kind == "memory_trigger":
            # Would need memory module access — for now, just log
            logger.debug("Memory trigger %s: skipping (no memory access)", jid)
            job["next_run"] = time.time() + interval
            return

        # Regular interval job
        job["run_count"] = job.get("run_count", 0) + 1
        job["last_run"] = time.time()
        job["next_run"] = time.time() + interval

        logger.info("SchedulerModule: firing job %s: %s", jid, task[:80])

        # Emit event
        await self.emit("scheduler.tick", {
            "job_id": jid,
            "kind": kind,
            "task": task,
        })

        # Execute task via callback or EventBus
        await self._fire_task(jid, task)

        # Auto-save periodically
        if job["run_count"] % 10 == 0:
            self._save_jobs()

    async def _fire_task(self, jid: str, task: str):
        """Fire a task: try EventBus first, fall back to callback."""
        # Try to emit as user input for processing
        if self.bus:
            await self.emit("scheduler.task", {
                "job_id": jid,
                "task": task,
            })

        # Also try legacy callback
        try:
            if self._scheduler._task_callback:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, self._scheduler._task_callback, task
                )
                await self.emit("scheduler.result", {
                    "job_id": jid,
                    "task": task,
                    "result": result,
                })
        except Exception as e:
            logger.error("SchedulerModule: task %s failed: %s", jid, e)
            await self.emit("scheduler.result", {
                "job_id": jid,
                "task": task,
                "error": str(e),
            })

    # ── EventBus handlers ─────────────────────────────────

    async def _handle_add(self, event: Event):
        """Handle scheduler.add event."""
        data = event.data or {}
        kind = data.get("kind", "interval")
        task = data.get("task", "")
        interval = data.get("interval", 300)
        jid = data.get("id")

        if kind == "watchdog":
            logger.warning("Watchdog jobs can't be added via events (need check_fn)")
            return

        if kind == "memory_trigger":
            fact_pattern = data.get("fact_pattern", "")
            jid = self._scheduler.schedule_memory_trigger(
                fact_pattern=fact_pattern,
                task=task,
                interval=interval,
            )
        else:
            jid = self._scheduler.schedule_every(
                interval_seconds=interval,
                task=task,
                job_id=jid,
            )

        self._save_jobs()
        logger.info("SchedulerModule: added job %s (%s)", jid, kind)

    async def _handle_remove(self, event: Event):
        """Handle scheduler.remove event."""
        jid = (event.data or {}).get("job_id", "")
        with self._scheduler._lock:
            self._scheduler._jobs.pop(jid, None)
        self._save_jobs()

    async def _handle_list(self, event: Event):
        """Handle scheduler.list event — emit status."""
        status = []
        with self._scheduler._lock:
            for jid, job in self._scheduler._jobs.items():
                status.append({
                    "id": jid,
                    "kind": job.get("kind", "interval"),
                    "task": job.get("task", "")[:80],
                    "active": job.get("active", True),
                    "interval": job.get("interval", 0),
                    "run_count": job.get("run_count", 0),
                    "next_run": datetime.fromtimestamp(
                        job.get("next_run", 0)
                    ).isoformat() if job.get("next_run") else "",
                })

        await self.emit("scheduler.status", {"jobs": status})

    async def _handle_pause(self, event: Event):
        """Handle scheduler.pause event."""
        jid = (event.data or {}).get("job_id", "")
        with self._scheduler._lock:
            if jid in self._scheduler._jobs:
                self._scheduler._jobs[jid]["active"] = False
        self._save_jobs()

    async def _handle_resume(self, event: Event):
        """Handle scheduler.resume event."""
        jid = (event.data or {}).get("job_id", "")
        with self._scheduler._lock:
            if jid in self._scheduler._jobs:
                self._scheduler._jobs[jid]["active"] = True
                self._scheduler._jobs[jid]["next_run"] = time.time()
        self._save_jobs()

    # ── Public API (for CLI / /schedule commands) ──────────

    def schedule_every(self, interval: int, task: str, **kwargs) -> str:
        """Schedule a periodic task. Returns job ID."""
        jid = self._scheduler.schedule_every(interval, task, **kwargs)
        self._save_jobs()
        return jid

    def schedule_watchdog(self, interval: int, check_fn: Callable, task: str, **kwargs) -> str:
        """Schedule a watchdog trigger."""
        jid = self._scheduler.schedule_watchdog(interval, check_fn, task, **kwargs)
        self._save_jobs()
        return jid

    def remove(self, jid: str) -> bool:
        """Remove a scheduled job."""
        with self._scheduler._lock:
            if jid in self._scheduler._jobs:
                del self._scheduler._jobs[jid]
                self._save_jobs()
                return True
        return False

    def list_jobs(self) -> list[dict]:
        """Get all jobs."""
        jobs = []
        with self._scheduler._lock:
            for jid, job in self._scheduler._jobs.items():
                jobs.append({
                    "id": jid,
                    "kind": job.get("kind", "interval"),
                    "task": job.get("task", "")[:80],
                    "active": job.get("active", True),
                    "interval": job.get("interval", 0),
                    "run_count": job.get("run_count", 0),
                })
        return jobs

    @property
    def job_count(self) -> int:
        """Number of active jobs."""
        with self._scheduler._lock:
            return len(self._scheduler._jobs)
