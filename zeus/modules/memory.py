"""Memory module — handles memory operations independently.

Cross-session memory: saves facts from every interaction,
searches for relevant context on each user input,
and provides context to the pipeline.

Subscribes to: user.input, user.output, memory.save, memory.search, task.*, context.request
Emits:         memory.result, context.result, memory.save
"""

from __future__ import annotations
from zeus.module import (
    Module, Event,
    USER_INPUT, USER_OUTPUT,
    MEMORY_SAVE, MEMORY_SEARCH, MEMORY_RESULT,
    TASK_COMPLETED, TASK_FAILED,
    CONTEXT_REQUEST, CONTEXT_RESULT,
)
from zeus.memory import SessionStore, extract_facts


class MemoryModule(Module):
    """Persistent memory with SQLite+FTS5.

    Cross-session:
      - Saves facts from every user interaction
      - Searches for relevant context before each user input
      - Provides context to pipeline via context.result events
    """

    def __init__(self, bus=None):
        super().__init__(
            name="memory",
            description="Cross-session memory with FTS5 search and context injection",
            bus=bus,
        )
        self._store: SessionStore | None = None

    async def start(self):
        await super().start()
        self._store = SessionStore()
        self.subscribe(USER_INPUT, self._handle_user_input)
        self.subscribe(USER_OUTPUT, self._handle_user_output)
        self.subscribe(MEMORY_SAVE, self._handle_save)
        self.subscribe(MEMORY_SEARCH, self._handle_search)
        self.subscribe(CONTEXT_REQUEST, self._handle_context_request)
        self.subscribe(TASK_COMPLETED, self._handle_task_completed)
        self.subscribe(TASK_FAILED, self._handle_task_failed)

    async def stop(self):
        if self._store:
            self._store.close()
        await super().stop()

    async def _handle_user_input(self, event: Event):
        """When user sends input, save it and search for relevant context."""
        if not self._store:
            return

        text = event.data.get("text", "")
        if not text:
            return

        # Save the user input as a fact (limited length)
        self._store.save_fact(text[:200], "user_input", entities=["user"])

        # Search for relevant context from past sessions
        # Extract keywords from the input
        words = [w for w in text.lower().split() if len(w) > 3]
        relevant_facts = []
        for word in words[:5]:  # Search with top 5 keywords
            results = self._store.search(word, limit=3)
            for r in results:
                if r not in relevant_facts:
                    relevant_facts.append(r)

        if relevant_facts:
            # Emit context for the pipeline
            context_text = "\n".join(f"- {f['content'][:100]}" for f in relevant_facts[:5])
            await self.emit(CONTEXT_RESULT, {
                "query": text,
                "context": context_text,
                "facts_count": len(relevant_facts[:5]),
                "event_id": event.id,
            })

    async def _handle_user_output(self, event: Event):
        """Save key information from assistant responses."""
        if not self._store:
            return

        text = event.data.get("text", "")
        source = event.data.get("source", "")

        if source == "error":
            self._store.save_fact(text[:200], "error", entities=["error"])
        elif source == "chat":
            pass  # Chat responses aren't useful to remember
        elif text and len(text) > 20 and not text.startswith("⚡"):
            # Save substantial responses as facts
            self._store.save_fact(text[:200], "assistant_response", entities=["assistant"])

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

    async def _handle_context_request(self, event: Event):
        """Handle explicit context request from pipeline/router."""
        if not self._store:
            return
        query = event.data.get("query", "")
        limit = event.data.get("limit", 5)
        if not query:
            return
        results = self._store.search(query, limit=limit)
        context_parts = []
        for r in results:
            content = r.get("content", "")[:150]
            cat = r.get("category", "")
            context_parts.append(f"[{cat}] {content}")

        context = "\n".join(context_parts) if context_parts else ""

        await self.emit(CONTEXT_RESULT, {
            "query": query,
            "context": context,
            "facts_count": len(context_parts),
            "event_id": event.id,
        })

    async def _handle_task_completed(self, event: Event):
        """After a task completes, save task info to memory."""
        if not self._store:
            return
        goal = event.data.get("goal", "")
        results = event.data.get("results", [])
        if goal:
            self._store.save_fact(goal[:200], "task_goal", entities=["task"])
        for r in results:
            if r.get("success"):
                node_id = r.get("node_id", "?")
                self._store.save_fact(f"{node_id}: completed", "task_step", entities=["task", node_id])

    async def _handle_task_failed(self, event: Event):
        """Save failed task info."""
        if not self._store:
            return
        goal = event.data.get("goal", "")
        error = event.data.get("error", "unknown")
        if goal:
            self._store.save_fact(f"Failed: {goal[:100]} - {error[:50]}", "task_failed", entities=["task", "error"])

    # ── Direct access ─────────────────────────────────────

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

    def close(self):
        """Close the store."""
        if self._store:
            self._store.close()