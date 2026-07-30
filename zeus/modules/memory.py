"""Memory module — handles memory operations independently.

Subscribes to: memory.save, memory.search, task.*
Emits:         memory.result

Runs in parallel with the pipeline. Saves facts asynchronously
without blocking the main execution flow.
"""

from __future__ import annotations
from zeus.module import Module, Event, MEMORY_SAVE, MEMORY_SEARCH, MEMORY_RESULT, TASK_COMPLETED
from zeus.memory import SessionStore, extract_facts


class MemoryModule(Module):
    """Persistent memory with SQLite+FTS5.

    Saves facts and session data asynchronously.
    Can be searched at any time without blocking other modules.
    """

    def __init__(self, bus=None):
        super().__init__(
            name="memory",
            description="Persistent session store with FTS5 search",
            bus=bus,
        )
        self._store: SessionStore | None = None

    async def start(self):
        await super().start()
        self._store = SessionStore()
        self.subscribe(MEMORY_SAVE, self._handle_save)
        self.subscribe(MEMORY_SEARCH, self._handle_search)
        self.subscribe(TASK_COMPLETED, self._handle_task_completed)

    async def stop(self):
        if self._store:
            self._store.close()
        await super().stop()

    async def _handle_save(self, event: Event):
        """Save a fact to memory."""
        if not self._store:
            return
        content = event.data.get("content", "")
        category = event.data.get("category", "general")
        entities = event.data.get("entities")
        self._store.save_fact(content, category, entities)

    async def _handle_search(self, event: Event):
        """Search memory and emit results."""
        if not self._store:
            return
        query = event.data.get("query", "")
        results = self._store.search(query)
        await self.emit(MEMORY_RESULT, {
            "query": query,
            "results": results,
            "request_id": event.id,
        })

    async def _handle_task_completed(self, event: Event):
        """After a task completes, save task info to memory."""
        if not self._store:
            return
        goal = event.data.get("goal", "")
        results = event.data.get("results", [])
        # Save the goal
        self._store.save_fact(goal[:200], "task_goal", entities=["task"])
        # Save tool results
        for r in results:
            if r.get("success"):
                node_id = r.get("node_id", "?")
                self._store.save_fact(f"{node_id}: completed", "task_step", entities=["task", node_id])

    def search(self, query: str) -> list[dict]:
        """Direct search access (for CLI commands)."""
        if self._store:
            return self._store.search(query)
        return []

    def get_facts(self, category: str | None = None, limit: int = 10) -> list[dict]:
        """Direct facts access (for CLI commands)."""
        if self._store:
            return self._store.get_facts(category, limit)
        return []

    def list_sessions(self, limit: int = 5) -> list[dict]:
        """Direct session list (for CLI commands)."""
        if self._store:
            return self._store.list_sessions(limit)
        return []