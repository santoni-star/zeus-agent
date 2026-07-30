"""Telemetry store — performance metrics for Zeus modules.

Records per-module metrics: execution time, LLM calls, success rate,
event throughput. Data is used for architecture insights and self-tuning.

Schema:
  module_name | event_type | duration_ms | llm_calls | success | timestamp
  execution_log: detailed per-event log
  module_stats: aggregated hourly/daily stats
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TELEMETRY_DB_PATH = "~/.zeus/telemetry.db"


@dataclass
class TelemetryEvent:
    """A single telemetry record for a module execution."""

    module_name: str
    event_type: str           # user.input, task.completed, pipeline.request, etc.
    duration_ms: float = 0.0
    llm_calls: int = 0
    success: bool = True
    error: str = ""
    metadata: str = ""        # JSON string with extra context
    timestamp: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()


class TelemetryStore:
    """Persistent telemetry storage with aggregation queries."""

    def __init__(self, db_path: str = TELEMETRY_DB_PATH):
        self._db_path = Path(db_path).expanduser()
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    module_name TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    duration_ms REAL DEFAULT 0,
                    llm_calls INTEGER DEFAULT 0,
                    success INTEGER DEFAULT 1,
                    error TEXT DEFAULT '',
                    metadata TEXT DEFAULT '{}',
                    timestamp REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_telemetry_module
                ON events(module_name, timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_telemetry_ts
                ON events(timestamp)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS module_stats (
                    module_name TEXT NOT NULL,
                    period TEXT NOT NULL,  -- 'hourly' | 'daily'
                    period_start REAL NOT NULL,
                    event_count INTEGER DEFAULT 0,
                    avg_duration_ms REAL DEFAULT 0,
                    total_llm_calls INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    error_count INTEGER DEFAULT 0,
                    PRIMARY KEY (module_name, period, period_start)
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def record(self, event: TelemetryEvent):
        """Record a telemetry event."""
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO events
                   (module_name, event_type, duration_ms, llm_calls,
                    success, error, metadata, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.module_name,
                    event.event_type,
                    event.duration_ms,
                    event.llm_calls,
                    1 if event.success else 0,
                    event.error[:500],
                    event.metadata[:2000],
                    event.timestamp,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def record_call(
        self,
        module_name: str,
        event_type: str,
        duration_ms: float = 0.0,
        llm_calls: int = 0,
        success: bool = True,
        error: str = "",
        metadata: dict | None = None,
    ):
        """Quick record a telemetry event."""
        ev = TelemetryEvent(
            module_name=module_name,
            event_type=event_type,
            duration_ms=duration_ms,
            llm_calls=llm_calls,
            success=success,
            error=error,
            metadata=json.dumps(metadata or {}),
        )
        self.record(ev)

    # ── Queries ───────────────────────────────────────────

    def module_summary(
        self,
        hours: int = 24,
        module_name: str | None = None,
    ) -> list[dict]:
        """Get aggregated metrics per module for the last N hours.

        Returns:
            List of dicts with keys: module_name, event_count,
            avg_duration, total_llm_calls, success_rate, error_count.
        """
        conn = self._conn()
        try:
            cutoff = time.time() - (hours * 3600)
            query = """
                SELECT
                    module_name,
                    COUNT(*) as event_count,
                    AVG(duration_ms) as avg_duration,
                    SUM(llm_calls) as total_llm_calls,
                    SUM(success) as success_count,
                    SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) as error_count
                FROM events
                WHERE timestamp > ?
            """
            params: list[Any] = [cutoff]

            if module_name:
                query += " AND module_name = ?"
                params.append(module_name)

            query += " GROUP BY module_name ORDER BY avg_duration DESC"

            rows = conn.execute(query, params).fetchall()
            results = []
            for row in rows:
                total = row["event_count"] or 1
                results.append({
                    "module": row["module_name"],
                    "events": row["event_count"],
                    "avg_duration_ms": round(row["avg_duration"] or 0, 1),
                    "total_llm_calls": row["total_llm_calls"] or 0,
                    "success_rate": round((row["success_count"] or 0) / total * 100, 1),
                    "errors": row["error_count"] or 0,
                })
            return results
        finally:
            conn.close()

    def slowest_events(self, limit: int = 10) -> list[dict]:
        """Get the slowest events."""
        conn = self._conn()
        try:
            rows = conn.execute(
                """SELECT module_name, event_type, duration_ms, llm_calls,
                          success, error, timestamp
                   FROM events
                   ORDER BY duration_ms DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def most_llm_expensive(self, limit: int = 5) -> list[dict]:
        """Get events with most LLM calls."""
        conn = self._conn()
        try:
            rows = conn.execute(
                """SELECT module_name, event_type, llm_calls, duration_ms, timestamp
                   FROM events
                   ORDER BY llm_calls DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def event_timeline(
        self,
        module_name: str | None = None,
        hours: int = 24,
    ) -> list[dict]:
        """Get event count per hour for a timeline view."""
        conn = self._conn()
        try:
            cutoff = time.time() - (hours * 3600)
            query = """
                SELECT
                    CAST(strftime('%s', datetime(timestamp, 'unixepoch')) / 3600 AS INTEGER) * 3600 as hour_bucket,
                    COUNT(*) as event_count,
                    AVG(duration_ms) as avg_duration
                FROM events
                WHERE timestamp > ?
            """
            params: list[Any] = [cutoff]
            if module_name:
                query += " AND module_name = ?"
                params.append(module_name)
            query += " GROUP BY hour_bucket ORDER BY hour_bucket"

            rows = conn.execute(query, params).fetchall()
            return [
                {
                    "hour": datetime.fromtimestamp(r["hour_bucket"]).strftime("%Y-%m-%d %H:00"),
                    "events": r["event_count"],
                    "avg_duration_ms": round(r["avg_duration"] or 0, 1),
                }
                for r in rows
            ]
        finally:
            conn.close()

    def error_summary(self, hours: int = 48) -> list[dict]:
        """Get error summary by module and event type."""
        conn = self._conn()
        try:
            cutoff = time.time() - (hours * 3600)
            rows = conn.execute(
                """SELECT module_name, event_type,
                          COUNT(*) as error_count,
                          MAX(error) as last_error
                   FROM events
                   WHERE success = 0 AND timestamp > ?
                   GROUP BY module_name, event_type
                   ORDER BY error_count DESC
                   LIMIT 10""",
                (cutoff,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def stats(self) -> dict:
        """Quick overall stats."""
        conn = self._conn()
        try:
            total = conn.execute("SELECT COUNT(*) as c FROM events").fetchone()["c"]
            last_hour = conn.execute(
                "SELECT COUNT(*) as c FROM events WHERE timestamp > ?",
                (time.time() - 3600,),
            ).fetchone()["c"]
            modules = conn.execute(
                "SELECT COUNT(DISTINCT module_name) as c FROM events"
            ).fetchone()["c"]
            avg_dur = conn.execute(
                "SELECT AVG(duration_ms) as avg FROM events WHERE timestamp > ?",
                (time.time() - 3600 * 24,),
            ).fetchone()["avg"] or 0
            return {
                "total_events": total,
                "events_last_hour": last_hour,
                "active_modules": modules,
                "avg_duration_24h_ms": round(avg_dur, 1),
            }
        finally:
            conn.close()

    def cleanup(self, max_age_days: int = 30):
        """Delete events older than max_age_days."""
        conn = self._conn()
        try:
            cutoff = time.time() - (max_age_days * 86400)
            deleted = conn.execute(
                "DELETE FROM events WHERE timestamp < ?", (cutoff,)
            ).rowcount
            if deleted:
                logger.info("Telemetry: cleaned %d old events", deleted)
            conn.commit()
        finally:
            conn.close()


def load_telemetry() -> TelemetryStore:
    """Quick access to telemetry store."""
    return TelemetryStore()
