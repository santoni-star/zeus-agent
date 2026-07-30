"""Session store — SQLite with FTS5 for conversation history."""

from __future__ import annotations
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any


_DB_PATH = Path(os.environ.get("ZEUS_HOME", Path.home() / ".zeus")) / "memory.db"


def get_conn() -> sqlite3.Connection:
    """Get a SQLite connection, creating tables on first use."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    _init_tables(conn)
    return conn


def _init_tables(conn: sqlite3.Connection):
    """Create tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            metadata TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system', 'tool')),
            content TEXT NOT NULL,
            tool_calls TEXT,
            created_at REAL NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            content, tokenize='unicode61'
        );

        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            category TEXT NOT NULL DEFAULT 'general',
            content TEXT NOT NULL,
            entities TEXT DEFAULT '[]',
            trust REAL NOT NULL DEFAULT 0.5,
            created_at REAL NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        CREATE INDEX IF NOT EXISTS idx_messages_session
            ON messages(session_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_facts_category
            ON facts(category, trust DESC);
    """)


class SessionStore:
    """Persistent conversation store with FTS5 search."""

    def __init__(self):
        self.conn = get_conn()
        self._current_session_id: int | None = None

    # ── Session management ─────────────────────────────────

    def new_session(self, title: str = "") -> int:
        """Create a new session and return its ID."""
        now = time.time()
        cur = self.conn.execute(
            "INSERT INTO sessions (title, created_at, updated_at) VALUES (?, ?, ?)",
            (title, now, now),
        )
        self.conn.commit()
        self._current_session_id = cur.lastrowid
        return cur.lastrowid

    def current_session(self) -> int:
        """Return current session ID, creating one if needed."""
        if self._current_session_id is None:
            return self.new_session()
        return self._current_session_id

    def list_sessions(self, limit: int = 20) -> list[dict]:
        """List recent sessions."""
        rows = self.conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Message storage ────────────────────────────────────

    def add_message(self, role: str, content: str, tool_calls: Any = None) -> int:
        """Add a message to the current session."""
        now = time.time()
        sid = self.current_session()
        tc = json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None

        cur = self.conn.execute(
            "INSERT INTO messages (session_id, role, content, tool_calls, created_at) VALUES (?, ?, ?, ?, ?)",
            (sid, role, content, tc, now),
        )
        mid = cur.lastrowid

        # Update session timestamp
        self.conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (now, sid),
        )

        # Add to FTS index
        try:
            self.conn.execute(
                "INSERT INTO messages_fts (rowid, content) VALUES (?, ?)",
                (mid, content),
            )
        except sqlite3.IntegrityError:
            pass  # already indexed

        self.conn.commit()
        return mid

    def get_session_messages(self, session_id: int | None = None) -> list[dict]:
        """Get all messages in a session."""
        sid = session_id or self._current_session_id
        if sid is None:
            return []
        rows = self.conn.execute(
            "SELECT id, role, content, tool_calls, created_at FROM messages WHERE session_id = ? ORDER BY created_at",
            (sid,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Search ─────────────────────────────────────────────

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Full-text search across all messages."""
        try:
            rows = self.conn.execute(
                """SELECT m.id, m.session_id, m.role, m.content, m.created_at,
                          s.title as session_title
                   FROM messages_fts fts
                   JOIN messages m ON m.id = fts.rowid
                   JOIN sessions s ON s.id = m.session_id
                   WHERE messages_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (query, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    # ── Facts ──────────────────────────────────────────────

    def save_fact(self, content: str, category: str = "general", entities: list | None = None):
        """Save a durable fact."""
        now = time.time()
        sid = self._current_session_id
        self.conn.execute(
            "INSERT INTO facts (session_id, category, content, entities, created_at) VALUES (?, ?, ?, ?, ?)",
            (sid, category, content, json.dumps(entities or [], ensure_ascii=False), now),
        )
        self.conn.commit()

    def get_facts(self, category: str | None = None, limit: int = 50) -> list[dict]:
        """Retrieve facts, optionally filtered by category."""
        if category:
            rows = self.conn.execute(
                "SELECT * FROM facts WHERE category = ? ORDER BY trust DESC, created_at DESC LIMIT ?",
                (category, limit),
            )
        else:
            rows = self.conn.execute(
                "SELECT * FROM facts ORDER BY trust DESC, created_at DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in rows.fetchall()]

    def close(self):
        self.conn.close()