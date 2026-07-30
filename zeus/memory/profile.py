"""User profile — persistent user preferences and identity.

Equivalent to Hermes USER.md in memory.
Stores:
  - Language preference
  - Communication style
  - Environment details
  - Recurring preferences
  - Conventions

Auto-injected into system prompts for consistent behavior.
Auto-updated based on user corrections and patterns.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

USER_DB_PATH = "~/.zeus/user.db"


class UserProfile:
    """Persistent user profile with auto-update capability.

    Usage:
        profile = UserProfile()
        profile.set("language", "uk")
        profile.set("style", "concise")
        print(profile.to_prompt())  # "User profile: language=uk, style=concise"
    """

    def __init__(self, db_path: str = USER_DB_PATH):
        self._db_path = Path(db_path).expanduser()
        self._cache: dict[str, str] = {}
        self._init_db()
        self._load()

    def _init_db(self):
        """Initialize the database."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS profile (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    source TEXT DEFAULT 'auto'  -- 'manual' | 'auto' | 'correction'
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _load(self):
        """Load all profile entries into cache."""
        conn = self._conn()
        try:
            rows = conn.execute("SELECT key, value FROM profile").fetchall()
            for row in rows:
                self._cache[row["key"]] = row["value"]
        finally:
            conn.close()

    def get(self, key: str, default: str = "") -> str:
        """Get a profile value."""
        return self._cache.get(key, default)

    def set(self, key: str, value: str, source: str = "auto"):
        """Set a profile value.

        Args:
            key: Profile key (e.g. 'language', 'style')
            value: Value to store
            source: 'manual', 'auto', or 'correction'
        """
        self._cache[key] = value
        conn = self._conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO profile (key, value, updated_at, source)
                   VALUES (?, ?, ?, ?)""",
                (key, value, time.time(), source),
            )
            conn.commit()
        finally:
            conn.close()

    def update_from_text(self, text: str, source: str = "auto"):
        """Extract profile info from user text.

        Detects patterns like:
          - Language: uk, en, ru
          - Style preferences: concise, detailed
          - Corrections: "не так, а ось так"
          - Recurring topics
        """
        text_lower = text.lower()

        # Detect language preferences
        if any(word in text_lower for word in ["говори українською", "відповідай українською", "ua", "укр"]):
            self.set("language", "uk", source)
        elif any(word in text_lower for word in ["speak english", "in english"]):
            self.set("language", "en", source)

        # Detect style
        if any(word in text_lower for word in ["коротко", "стисло", "без води", "short", "concise"]):
            self.set("style", "concise", source)
        elif any(word in text_lower for word in ["детально", "розгорнуто", "detailed", "details"]):
            self.set("style", "detailed", source)

        # Detect environment
        if "termux" in text_lower:
            self.set("environment", "termux/android", source)
        if any(word in text_lower for word in ["mac", "macos", "darwin"]):
            self.set("environment", "macos", source)

        # Detect corrections (user correcting the agent)
        correction_patterns = [
            r"не\s+(\w+),\s*а\s+(\w+)",
            r"не\s+(\w+)\s*,?\s*а",
            r"краще\s+(\w+)",
            r"не\s+треба\s+(\w+)",
        ]
        for pattern in correction_patterns:
            match = re.search(pattern, text_lower)
            if match:
                # Found a correction pattern — note it
                self.set("last_correction", text[:100], source)
                break

    def delete(self, key: str):
        """Remove a profile entry."""
        self._cache.pop(key, None)
        conn = self._conn()
        try:
            conn.execute("DELETE FROM profile WHERE key = ?", (key,))
            conn.commit()
        finally:
            conn.close()

    def to_prompt(self) -> str:
        """Format profile as context prompt for LLM injection.

        Returns:
            Formatted string like "User preferences: language=uk, style=concise"
        """
        if not self._cache:
            return ""

        parts = ["User profile:"]
        for key, value in sorted(self._cache.items()):
            parts.append(f"  {key}: {value}")

        return "\n".join(parts)

    def to_dict(self) -> dict[str, str]:
        """Get all profile entries as dict."""
        return dict(self._cache)

    @property
    def language(self) -> str:
        return self._cache.get("language", "uk")

    @property
    def style(self) -> str:
        return self._cache.get("style", "balanced")

    @property
    def environment(self) -> str:
        return self._cache.get("environment", "unknown")


class FactStore:
    """Persistent fact storage with entity resolution and trust scoring.

    Equivalent to Hermes fact_store.
    Each fact has:
      - Content: what the fact is
      - Entity: what/who it's about
      - Category: general | user_pref | project | tool
      - Trust: 0.0-1.0 (higher = more reliable)
      - Tags: for grouping
      - Source: auto | manual | correction

    Usage:
        store = FactStore()
        store.add("User prefers Ukrainian language", entity="user", category="user_pref")
        results = store.search("ukrainian")
        facts = store.probe("user")  # All facts about "user"
    """

    def __init__(self, db_path: str = USER_DB_PATH):
        self._db_path = Path(db_path).expanduser()
        self._init_db()

    def _init_db(self):
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    entity TEXT DEFAULT '',
                    category TEXT DEFAULT 'general',
                    tags TEXT DEFAULT '',
                    trust REAL DEFAULT 0.5,
                    source TEXT DEFAULT 'auto',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_facts_entity
                ON facts(entity)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_facts_category
                ON facts(category)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_facts_trust
                ON facts(trust DESC)
            """)
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def add(self, content: str, entity: str = "",
            category: str = "general", tags: str = "",
            trust: float = 0.5, source: str = "auto") -> int:
        """Add or update a fact.

        If a similar fact exists for the same entity, updates it
        (increases trust).

        Args:
            content: Fact text
            entity: Entity this fact is about (e.g. 'user', 'project')
            category: Category string
            tags: Comma-separated tags
            trust: Trust confidence 0.0-1.0
            source: 'auto' | 'manual' | 'correction'

        Returns:
            Fact ID.
        """
        now = time.time()
        conn = self._conn()
        try:
            # Check for existing similar fact
            existing = conn.execute(
                "SELECT id, trust, content FROM facts WHERE entity = ? AND category = ? AND content LIKE ? LIMIT 1",
                (entity, category, content[:50] + "%"),
            ).fetchone()

            if existing:
                # Update: increase trust
                new_trust = min(1.0, existing["trust"] + 0.1)
                conn.execute(
                    "UPDATE facts SET content = ?, trust = ?, updated_at = ?, source = ? WHERE id = ?",
                    (content, new_trust, now, source, existing["id"]),
                )
                return existing["id"]
            else:
                cur = conn.execute(
                    """INSERT INTO facts (content, entity, category, tags, trust, source, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (content, entity, category, tags, trust, source, now, now),
                )
                conn.commit()
                return cur.lastrowid
        finally:
            conn.close()

    def search(self, query: str, min_trust: float = 0.3, limit: int = 10) -> list[dict]:
        """Search facts by keyword.

        Args:
            query: Search keywords
            min_trust: Minimum trust threshold
            limit: Max results

        Returns:
            List of fact dicts.
        """
        conn = self._conn()
        try:
            rows = conn.execute(
                """SELECT id, content, entity, category, tags, trust, source, created_at
                   FROM facts
                   WHERE (content LIKE ? OR entity LIKE ?) AND trust >= ?
                   ORDER BY trust DESC, updated_at DESC
                   LIMIT ?""",
                (f"%{query}%", f"%{query}%", min_trust, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def probe(self, entity: str, category: str | None = None,
              min_trust: float = 0.3) -> list[dict]:
        """Get all facts about an entity.

        Args:
            entity: Entity name
            category: Optional category filter
            min_trust: Minimum trust threshold

        Returns:
            List of fact dicts.
        """
        conn = self._conn()
        try:
            query = "SELECT * FROM facts WHERE entity = ? AND trust >= ?"
            params: list[Any] = [entity, min_trust]

            if category:
                query += " AND category = ?"
                params.append(category)

            query += " ORDER BY trust DESC, updated_at DESC"

            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def related(self, entity: str, max_distance: int = 2) -> list[dict]:
        """Find facts related to an entity (shared tags or category).

        Args:
            entity: Entity name
            max_distance: How far to traverse relationships

        Returns:
            List of related fact dicts.
        """
        conn = self._conn()
        try:
            # Find entities that share tags or category with this entity
            entity_facts = self.probe(entity)
            if not entity_facts:
                return []

            # Get all tags and categories from this entity
            tags = set()
            for f in entity_facts:
                for t in f.get("tags", "").split(","):
                    if t.strip():
                        tags.add(t.strip())

            # Find other entities with same tags
            related = []
            for tag in tags:
                rows = conn.execute(
                    """SELECT * FROM facts
                       WHERE entity != ? AND tags LIKE ? AND trust >= 0.3
                       ORDER BY trust DESC LIMIT 5""",
                    (entity, f"%{tag}%"),
                ).fetchall()
                for r in rows:
                    d = dict(r)
                    if d not in related:
                        d["_relation"] = f"shared tag: {tag}"
                        related.append(d)

            return related[:10]
        finally:
            conn.close()

    def reason(self, entities: list[str]) -> list[dict]:
        """Find facts connected to MULTIPLE entities simultaneously.

        Args:
            entities: List of entity names

        Returns:
            Facts that reference all listed entities.
        """
        if not entities:
            return []

        conn = self._conn()
        try:
            placeholders = ",".join("?" * len(entities))
            rows = conn.execute(
                f"""SELECT content, entity, category, tags, trust
                    FROM facts
                    WHERE entity IN ({placeholders}) AND trust >= 0.3
                    ORDER BY trust DESC
                    LIMIT 20""",
                entities,
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def contradict(self) -> list[dict]:
        """Find facts that contradict each other (same entity, opposing claims).

        Returns:
            List of conflicting fact pairs.
        """
        conn = self._conn()
        try:
            # Simple heuristic: same entity + category, different trust levels
            rows = conn.execute("""
                SELECT a.id as id1, a.content as c1, b.id as id2, b.content as c2,
                       a.entity, a.category
                FROM facts a
                JOIN facts b ON a.entity = b.entity AND a.category = b.category
                WHERE a.id < b.id AND abs(a.trust - b.trust) > 0.5
                LIMIT 10
            """).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_relevant_context(self, query: str, limit: int = 5) -> str:
        """Get relevant facts formatted as context prompt.

        Searches across all fields, returns best matches.
        Used for auto-injection into LLM context.

        Args:
            query: Current user query or context
            limit: Max facts to include

        Returns:
            Formatted string of relevant facts.
        """
        facts = self.search(query, min_trust=0.3, limit=limit)

        if not facts:
            return ""

        lines = ["Knowledge:"]
        for f in facts:
            entity = f.get("entity", "")
            content = f.get("content", "")[:200]
            trust = f.get("trust", 0.5)
            trust_icon = "✓" if trust > 0.7 else "○"
            if entity:
                lines.append(f"  [{entity}] {trust_icon} {content}")
            else:
                lines.append(f"  {trust_icon} {content}")

        return "\n".join(lines)

    def merge(self, source: "FactStore"):
        """Import facts from another FactStore."""
        conn = self._conn()
        try:
            rows = source._conn().execute("SELECT * FROM facts").fetchall()
            for row in rows:
                self.add(
                    content=row["content"],
                    entity=row["entity"],
                    category=row["category"],
                    tags=row["tags"],
                    trust=row["trust"],
                    source=row["source"],
                )
        finally:
            conn.close()

    def cleanup(self, min_trust: float = 0.1):
        """Remove low-trust facts."""
        conn = self._conn()
        try:
            deleted = conn.execute(
                "DELETE FROM facts WHERE trust < ?", (min_trust,)
            ).rowcount
            if deleted:
                logger.info("FactStore: cleaned %d low-trust facts", deleted)
            conn.commit()
        finally:
            conn.close()

    def count(self, category: str | None = None) -> int:
        """Count facts."""
        conn = self._conn()
        try:
            if category:
                row = conn.execute(
                    "SELECT COUNT(*) as c FROM facts WHERE category = ?", (category,)
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) as c FROM facts").fetchone()
            return row["c"] if row else 0
        finally:
            conn.close()
