"""Review store — persistent storage for self-review proposals.

Each review is a structured proposal to improve Zeus's code.
User reviews, approves, or rejects them via /review commands.

Schema:
  - id: UUID
  - target_file: path to file being reviewed
  - module_name: name of the module
  - issue_type: duplication | error_handling | complexity | sync_async | performance
  - severity: low | medium | high | critical
  - title: short description
  - description: detailed analysis
  - line_range: [start, end] in target file
  - old_code: existing code snippet
  - new_code: proposed replacement
  - status: pending | approved | rejected | applied | failed
  - created_at: timestamp
  - applied_at: timestamp
  - llm_analysis: full LLM response for audit
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REVIEW_DB_PATH = "~/.zeus/reviews.db"


@dataclass
class ReviewProposal:
    """A single review proposal — one suggested code change."""

    target_file: str
    module_name: str
    issue_type: str  # duplication | error_handling | complexity | sync_async | performance | architecture | other
    severity: str    # low | medium | high | critical
    title: str
    description: str
    old_code: str = ""
    new_code: str = ""
    line_range: list[int] | None = None
    status: str = "pending"  # pending | approved | rejected | applied | failed
    id: str = ""
    created_at: float = 0.0
    applied_at: float = 0.0
    llm_analysis: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = f"rev_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = time.time()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "target_file": str(self.target_file),
            "module_name": self.module_name,
            "issue_type": self.issue_type,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "old_code": self.old_code,
            "new_code": self.new_code,
            "line_range": self.line_range or [],
            "status": self.status,
            "created_at": self.created_at,
            "applied_at": self.applied_at,
            "llm_analysis": self.llm_analysis,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ReviewProposal":
        d.setdefault("line_range", [])
        return cls(**d)

    def __str__(self) -> str:
        icon = {"pending": "⏳", "approved": "✅", "rejected": "❌", "applied": "🔄", "failed": "⚠"}.get(self.status, "📝")
        return (
            f"{icon} `{self.id}` — {self.title}\n"
            f"   File: {self.target_file}:{self.line_range[0] if self.line_range else '?'}\n"
            f"   Type: {self.issue_type} | Severity: {self.severity}\n"
            f"   Created: {datetime.fromtimestamp(self.created_at).strftime('%Y-%m-%d %H:%M')}\n"
        )


class ReviewStore:
    """Persistent storage for code review proposals.

    Uses SQLite for reliable storage with search capability.
    """

    def __init__(self, db_path: str = REVIEW_DB_PATH):
        self._db_path = Path(db_path).expanduser()
        self._init_db()

    def _init_db(self):
        """Initialize the database schema."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reviews (
                    id TEXT PRIMARY KEY,
                    target_file TEXT NOT NULL,
                    module_name TEXT NOT NULL,
                    issue_type TEXT NOT NULL,
                    severity TEXT NOT NULL DEFAULT 'medium',
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    old_code TEXT DEFAULT '',
                    new_code TEXT DEFAULT '',
                    line_range TEXT DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at REAL NOT NULL,
                    applied_at REAL DEFAULT 0,
                    llm_analysis TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_reviews_status
                ON reviews(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_reviews_created
                ON reviews(created_at)
            """)
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        """Get a database connection."""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, proposal: ReviewProposal) -> str:
        """Save a new proposal. Returns its ID."""
        conn = self._conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO reviews
                   (id, target_file, module_name, issue_type, severity,
                    title, description, old_code, new_code, line_range,
                    status, created_at, applied_at, llm_analysis)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    proposal.id,
                    str(proposal.target_file),
                    proposal.module_name,
                    proposal.issue_type,
                    proposal.severity,
                    proposal.title,
                    proposal.description,
                    proposal.old_code,
                    proposal.new_code,
                    json.dumps(proposal.line_range or []),
                    proposal.status,
                    proposal.created_at,
                    proposal.applied_at,
                    proposal.llm_analysis,
                ),
            )
            conn.commit()
            return proposal.id
        finally:
            conn.close()

    def get(self, review_id: str) -> ReviewProposal | None:
        """Get a proposal by ID."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM reviews WHERE id = ?", (review_id,)
            ).fetchone()
            if row:
                return self._row_to_proposal(row)
            return None
        finally:
            conn.close()

    def list(
        self,
        status: str | None = None,
        module_name: str | None = None,
        limit: int = 20,
    ) -> list[ReviewProposal]:
        """List proposals, optionally filtered."""
        conn = self._conn()
        try:
            query = "SELECT * FROM reviews"
            params: list[Any] = []
            conditions = []

            if status:
                conditions.append("status = ?")
                params.append(status)
            if module_name:
                conditions.append("module_name = ?")
                params.append(module_name)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [self._row_to_proposal(r) for r in rows]
        finally:
            conn.close()

    def update_status(self, review_id: str, new_status: str) -> bool:
        """Update the status of a proposal.

        Args:
            review_id: Proposal ID
            new_status: pending | approved | rejected | applied | failed

        Returns:
            True if updated.
        """
        conn = self._conn()
        try:
            extra = {}
            if new_status == "applied":
                extra["applied_at"] = time.time()

            if extra:
                conn.execute(
                    f"UPDATE reviews SET status = ?, applied_at = ? WHERE id = ?",
                    (new_status, extra["applied_at"], review_id),
                )
            else:
                conn.execute(
                    "UPDATE reviews SET status = ? WHERE id = ?",
                    (new_status, review_id),
                )
            conn.commit()
            return conn.total_changes > 0
        finally:
            conn.close()

    def pending_count(self) -> int:
        """Count pending proposals."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM reviews WHERE status = 'pending'"
            ).fetchone()
            return row["cnt"] if row else 0
        finally:
            conn.close()

    def _row_to_proposal(self, row: sqlite3.Row) -> ReviewProposal:
        """Convert a DB row to a ReviewProposal."""
        return ReviewProposal(
            id=row["id"],
            target_file=row["target_file"],
            module_name=row["module_name"],
            issue_type=row["issue_type"],
            severity=row["severity"],
            title=row["title"],
            description=row["description"],
            old_code=row["old_code"],
            new_code=row["new_code"],
            line_range=json.loads(row["line_range"]) if row["line_range"] else [],
            status=row["status"],
            created_at=row["created_at"],
            applied_at=row["applied_at"],
            llm_analysis=row["llm_analysis"],
        )


def load_store() -> ReviewStore:
    """Quick access to the review store."""
    return ReviewStore()
