"""Conversation history — dialog buffer + semantic search.

Two parts:
1. ConversationBuffer — in-memory queue of recent exchanges
2. HistorySearcher — smart search across all sessions (time + semantic)

Usage:
    from zeus.memory.history import ConversationBuffer, HistorySearcher

    buf = ConversationBuffer(max_turns=20)
    buf.add("user", "hello")
    buf.add("assistant", "hi")
    print(buf.context_prompt())  # formatted recent history

    searcher = HistorySearcher()
    results = searcher.smart_search("що ми вчора робили на github?")
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from zeus.memory.session import SessionStore

logger = logging.getLogger(__name__)


# ── Conversation Buffer ───────────────────────────────────

@dataclass
class Turn:
    """A single conversation turn."""
    role: str          # user | assistant | system
    content: str
    timestamp: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()


class ConversationBuffer:
    """In-memory ring buffer of recent conversation turns.

    Keeps the last N messages and formats them for LLM context injection.
    """

    def __init__(self, max_turns: int = 20):
        self._turns: list[Turn] = []
        self._max_turns = max_turns
        self._session_id: int | None = None

    def add(self, role: str, content: str):
        """Add a turn to the buffer.

        Args:
            role: 'user', 'assistant', or 'system'
            content: Message text
        """
        self._turns.append(Turn(role=role, content=content))
        if len(self._turns) > self._max_turns:
            # Remove oldest pair (user+assistant) to stay within budget
            removed = 0
            while len(self._turns) > self._max_turns and removed < 2:
                self._turns.pop(0)
                removed += 1

    def extend(self, turns: list[Turn]):
        """Add multiple turns at once."""
        for t in turns:
            self._turns.append(t)
        while len(self._turns) > self._max_turns:
            self._turns.pop(0)

    def context_prompt(self, max_chars: int = 3000) -> str:
        """Format recent history as a context block for LLM prompts.

        Returns:
            Formatted string: "Previous conversation:\nUser: ...\nAssistant: ..."
        """
        if not self._turns:
            return ""

        lines = ["Previous conversation:"]
        chars = 0
        # Take from the start (oldest first)
        for turn in self._turns:
            label = "User" if turn.role == "user" else "Assistant" if turn.role == "assistant" else "System"
            line = f"\n{label}: {turn.content}"[:500]
            if chars + len(line) > max_chars:
                break
            lines.append(line)
            chars += len(line)

        return "\n".join(lines)

    def to_api_messages(self, system_prompt: str = "") -> list[dict]:
        """Format buffer as OpenAI-style message list.

        Returns:
            [{"role": "system", ...}, {"role": "user", ...}, ...]
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        for turn in self._turns:
            if turn.role == "system":
                continue  # skip system turns (already at top)
            messages.append({
                "role": "user" if turn.role == "user" else "assistant",
                "content": turn.content,
            })

        return messages

    @property
    def turns(self) -> list[Turn]:
        return list(self._turns)

    @property
    def turn_count(self) -> int:
        return len(self._turns)

    def clear(self):
        """Clear the buffer."""
        self._turns.clear()

    def last_user_message(self) -> str:
        """Get the most recent user message."""
        for turn in reversed(self._turns):
            if turn.role == "user":
                return turn.content
        return ""

    def last_assistant_message(self) -> str:
        """Get the most recent assistant message."""
        for turn in reversed(self._turns):
            if turn.role == "assistant":
                return turn.content
        return ""


# ── Time Expressions ──────────────────────────────────────

def parse_time_expression(text: str) -> dict:
    """Parse time-related expressions from a query.

    Detects words like: вчора, сьогодні, 2 дні тому, минулого тижня
    вчера, сегодня, на днях, позавчера, earlier, yesterday, last week

    Returns:
        dict with 'since' (timestamp), 'query' (remaining text).
    """
    now = time.time()
    text_lower = text.lower().strip()

    patterns = [
        # Ukrainian
        (r"\bвчора\b", timedelta(days=1)),
        (r"\bпозавчора\b", timedelta(days=2)),
        (r"\bсьогодні\b", timedelta(hours=0)),
        (r"\bминулого тижня\b", timedelta(days=7)),
        (r"\bна днях\b", timedelta(days=3)),
        (r"\bнещодавно\b", timedelta(days=1)),
        (r"\bщойно\b", timedelta(hours=1)),
        (r"\bза\s+останні\s+(\d+)\s+(днів|дні|дня|день|годин|години|хвилин|хвилини)\b", None),
        (r"\b(\d+)\s*(днів|дні|дня|день|годин|години|годину|хвилин|хвилини)\s*(тому|назад)\b", None),

        # Russian
        (r"\bвчера\b", timedelta(days=1)),
        (r"\bпозавчера\b", timedelta(days=2)),
        (r"\bсегодня\b", timedelta(hours=0)),
        (r"\bна днях\b", timedelta(days=3)),

        # English
        (r"\byesterday\b", timedelta(days=1)),
        (r"\btoday\b", timedelta(hours=0)),
        (r"\blast week\b", timedelta(days=7)),
        (r"\bday before yesterday\b", timedelta(days=2)),
        (r"\brecently\b", timedelta(days=1)),
        (r"\bjust now\b", timedelta(hours=1)),
        (r"\b(\d+)\s*(days?|hours?|minutes?)\s*(ago)\b", None),
    ]

    since = None

    for pattern, delta in patterns:
        match = re.search(pattern, text_lower)
        if match:
            if delta is not None:
                since = now - delta.total_seconds()
            else:
                # "2 days ago" or "last 2 days" pattern
                try:
                    num = int(match.group(1))
                    unit = match.group(2)
                    if unit.startswith("дн") or unit.startswith("day"):
                        since = now - num * 86400
                    elif unit.startswith("год") or unit.startswith("hour"):
                        since = now - num * 3600
                    elif unit.startswith("хв") or unit.startswith("min"):
                        since = now - num * 60
                except (IndexError, ValueError):
                    since = None

            # Remove the time expression from the query
            text_lower = re.sub(pattern, "", text_lower, count=1).strip()
            break

    return {
        "since": since,
        "query": text_lower,
    }


# ── History Searcher ──────────────────────────────────────

class HistorySearcher:
    """Smart search across all conversation sessions.

    Combines:
      - Time expression parsing ("вчора", "2 дні тому")
      - FTS5 keyword search
      - Session enumeration by date
    """

    def __init__(self, store: SessionStore | None = None):
        self._store = store or SessionStore()

    def smart_search(
        self,
        query: str,
        limit: int = 15,
    ) -> dict:
        """Search across sessions with time + semantic awareness.

        Interprets natural language like:
          - "що ми вчора робили?"
          - "знайди коли ми обговорювали github"
          - "покажи всі сесії за останні 2 дні"
          - "що там з self-review?"

        Returns:
            dict with:
              - time_range: parsed time info
              - sessions: matching sessions with messages
              - messages: matching individual messages
        """
        time_info = parse_time_expression(query)
        search_query = time_info["query"] or query
        since = time_info["since"]

        result: dict[str, Any] = {
            "time_range": {
                "since": since,
                "since_str": datetime.fromtimestamp(since).strftime("%Y-%m-%d %H:%M") if since else "anytime",
                "original_query": query,
            },
            "sessions": [],
            "messages": [],
        }

        try:
            if not search_query or search_query in ("все", "всі", "all", "everything"):
                # Just time-based — show sessions
                sessions = self._store.list_sessions(limit=limit)
                if since:
                    # Filter by time
                    sessions = [s for s in sessions if s.get("created_at", 0) >= since]

                for s in sessions:
                    msgs = self._store.get_session_messages(s["id"])
                    if msgs:
                        result["sessions"].append({
                            "id": s["id"],
                            "title": s.get("title", f"Session {s['id']}"),
                            "created_at": s.get("created_at", 0),
                            "created_str": datetime.fromtimestamp(s["created_at"]).strftime("%Y-%m-%d %H:%M") if s.get("created_at") else "",
                            "messages": [
                                {"role": m["role"], "content": m["content"][:200]}
                                for m in msgs[-10:]  # last 10 per session
                            ],
                            "message_count": len(msgs),
                        })

            else:
                # Search FTS5 with the query
                fts_results = self._store.search(search_query, limit=limit * 3)

                # Filter by time if specified
                if since:
                    fts_results = [
                        r for r in fts_results
                        if r.get("created_at", 0) >= since
                    ]

                result["messages"] = fts_results[:limit]

                # Group by session
                seen_sessions: set[int] = set()
                for msg in fts_results:
                    sid = msg.get("session_id") or msg.get("id", 0) // 1000
                    if sid not in seen_sessions:
                        msgs = self._store.get_session_messages(sid)
                        if msgs:
                            seen_sessions.add(sid)
                            result["sessions"].append({
                                "id": sid,
                                "messages": [
                                    {"role": m["role"], "content": m["content"][:200]}
                                    for m in msgs[-10:]
                                ],
                                "message_count": len(msgs),
                            })

        except Exception as e:
            logger.error("History search failed: %s", e)
            result["error"] = str(e)

        return result

    def format_result(self, result: dict) -> str:
        """Format search result as human-readable text.

        Args:
            result: Output from smart_search()

        Returns:
            Formatted string for display.
        """
        parts = []
        time_str = result.get("time_range", {}).get("since_str", "anytime")
        sessions = result.get("sessions", [])
        messages = result.get("messages", [])

        if result.get("error"):
            return f"⚠ Search error: {result['error']}"

        if not sessions and not messages:
            if "вчора" in str(result) or "yesterday" in str(result):
                return "За вчора нічого не знайдено."
            return "Нічого не знайдено за вашим запитом."

        if sessions:
            if len(sessions) == 1 and time_str != "anytime":
                parts.append(f"📅 Знайдено 1 сесію ({time_str}):\n")
            elif len(sessions) > 1:
                parts.append(f"📅 Знайдено {len(sessions)} сесій ({time_str}):\n")

            for i, s in enumerate(sessions[:5], 1):
                title = s.get("title", f"Сесія #{s['id']}")
                created = s.get("created_str", "")
                count = s.get("message_count", 0)
                parts.append(f"  {i}. **{title}** ({created}, {count} повідомлень)")

                # Show key messages
                msgs = s.get("messages", [])
                for m in msgs[-6:]:  # last 6
                    role_icon = "👤" if m["role"] == "user" else "🤖"
                    content = m.get("content", "")[:150]
                    if content:
                        parts.append(f"     {role_icon} {content}")
                parts.append("")

        if messages:
            if not sessions:
                parts.append(f"📝 Знайдено {len(messages)} повідомлень:\n")
            for i, m in enumerate(messages[:5], 1):
                role_icon = "👤" if m.get("role") == "user" else "🤖"
                content = m.get("content", "")[:200]
                if content:
                    parts.append(f"  {i}. {role_icon} {content}")
            parts.append("")

        return "\n".join(parts)


# Convenience
def search_history(query: str) -> str:
    """Quick one-shot search across history."""
    searcher = HistorySearcher()
    result = searcher.smart_search(query)
    return searcher.format_result(result)
