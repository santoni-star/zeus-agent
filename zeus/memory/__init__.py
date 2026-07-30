"""Zeus Memory — multi-layer persistent memory system.

Phase 0:
- L1: Ephemeral (session context via SQLite)
- L3: Semantic (facts via SQLite)
- FTS5 full-text search across all messages

Phase 1+:
- L0: Proactive (triggers, patterns)
- L2: Episodic (past sessions with embedding similarity)
"""

from zeus.memory.session import SessionStore, get_conn
from zeus.memory.extractor import extract_facts, save_task_result

__all__ = [
    "SessionStore",
    "get_conn",
    "extract_facts",
    "save_task_result",
]