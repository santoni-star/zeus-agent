"""Pipeline module — executes tasks via Planner → Runtime → Synthesizer.

Subscribes to: pipeline.request, task.node_completed
Emits:         task.completed, task.failed, user.output

This is the main execution engine — processes complex tasks
through the full DAG pipeline.
"""
from __future__ import annotations
import asyncio
import logging
import time

from zeus.module import Module, Event, USER_INPUT, USER_OUTPUT
from zeus.planner import plan
from zeus.runtime import execute_dag
from zeus.synthesizer import synthesize

logger = logging.getLogger(__name__)


class PipelineModule(Module):
    """Executes tasks via Planner → Runtime → Synthesizer.

    Runs as an independent module. Subscribes to pipeline.request
    events and emits user.output when done.
    """

    def __init__(self, bus=None, tool_registry=None, llm_call=None):
        super().__init__(
            name="pipeline",
            description="Task execution: Planner → Runtime → Synthesizer",
            bus=bus,
        )
        self._tool_registry = tool_registry
        self._llm_call = llm_call

    async def start(self):
        await super().start()
        self.subscribe("pipeline.request", self._handle_request)

    async def _handle_request(self, event: Event):
        """Process a pipeline request.

        Optionally requests context from memory before planning.
        """
        text = event.data.get("text", "")
        if not text:
            return

        start = time.time()

        # 1. Request context from memory (doesn't block if no memory module)
        context = ""
        if self.bus:
            ctx_event = Event("context.request", {
                "query": text,
                "limit": 3,
            }, source="pipeline")
            await self.bus.publish(ctx_event)
            # Give memory a moment to respond
            await asyncio.sleep(0.1)

        # Check if context result was emitted (relayed via out-of-band)
        # For now, we just proceed without it - memory context is optional

        # 2. Plan with context (filter tools by query relevance)
        tools_schemas = self._tool_registry.schemas(filter_query=text) if self._tool_registry else []
        dag = plan(text=text, tools=tools_schemas, llm_call=self._llm_call)

        if not dag:
            await self.emit("user.output", {"text": "Не вдалося створити план.", "source": "error", "event_id": event.id})
            return

        # Validate tool nodes against available tools
        tool_names = set(self._tool_registry.names()) if self._tool_registry else set()
        bad_nodes = [
            n for n in dag.nodes
            if n.type == "tool" and n.tool not in tool_names
        ]
        if bad_nodes:
            bad_tools = [n.tool for n in bad_nodes]
            logger.warning("Planner made up tools: %s — falling back to find_api", bad_tools)
            if tool_names and "find_api" in tool_names:
                try:
                    from zeus.tools.find_api import execute as find_api_execute
                    result_text = find_api_execute({
                        "action": "call",
                        "query": text,
                        "no_auth": False,
                        "https_only": True,
                    })
                    if not result_text.startswith("❌"):
                        await self.emit("user.output", {"text": result_text, "source": "find_api", "event_id": event.id})
                        return
                except Exception as fe:
                    logger.debug("find_api fallback failed: %s", fe)
            await self.emit("user.output", {
                "text": f"Не можу відповісти: планувальник створив неіснуючі інструменти {bad_tools}. "
                        f"Доступно: {', '.join(sorted(tool_names)[:10])}",
                "source": "error",
                "event_id": event.id,
            })
            return

        # 3. Validate and Execute
        results = execute_dag(dag, self._tool_registry, llm_call=self._llm_call)

        # 3. Synthesize
        if self._llm_call:
            final = synthesize(goal=dag.goal, results=results, llm_call=self._llm_call)
        else:
            # Fallback: concat outputs
            parts = [str(r.output) for r in results if r.success and r.output]
            final = "\n\n".join(parts) if parts else "Виконано."

        duration = (time.time() - start) * 1000

        # Show DAG info
        dag_info = []
        for r in results:
            icon = "✅" if r.success else "❌"
            dag_info.append(f"{icon} {r.node_id} ({r.duration_ms:.0f}ms)")

        dag_summary = f"⚡ DAG: {len(results)} nodes, {duration:.0f}ms\n" + "\n".join(f"   {d}" for d in dag_info)

        # Emit both DAG info and final output
        await self.emit(USER_OUTPUT, {"text": dag_summary + "\n\n" + final, "source": "pipeline"})

        # Emit task completed for memory
        await self.emit("task.completed", {
            "goal": dag.goal,
            "results": [{
                "node_id": r.node_id,
                "success": r.success,
                "duration_ms": r.duration_ms,
            } for r in results],
        })