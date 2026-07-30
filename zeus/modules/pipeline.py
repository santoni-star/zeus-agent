"""Pipeline module — executes tasks via Planner → Runtime → Synthesizer.

Subscribes to: pipeline.request, task.node_completed
Emits:         task.completed, task.failed, user.output

This is the main execution engine — processes complex tasks
through the full DAG pipeline.
"""

from __future__ import annotations
import asyncio
import time
from zeus.module import Module, Event, USER_OUTPUT
from zeus.planner import plan
from zeus.runtime import execute_dag
from zeus.synthesizer import synthesize


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

        # 2. Plan with context
        tools_schemas = self._tool_registry.schemas() if self._tool_registry else []
        dag = plan(text=text, tools=tools_schemas, llm_call=self._llm_call)

        if not dag:
            await self.emit(USER_OUTPUT, {
                "text": "Planner не зміг створити план для цієї задачі.",
                "source": "pipeline",
            })
            return

        # 2. Validate & Execute
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