"""Sub-agent spawning and orchestration.

Allows Zeus to spawn isolated sub-agents that run in parallel,
communicate via events, and return results.

Each sub-agent is an independent module with its own context
and lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Callable

from zeus.module import Module, EventBus, ModuleManager, Event, USER_OUTPUT

logger = logging.getLogger(__name__)


class SubAgentInstance:
    """A running sub-agent with its own context and lifecycle."""

    def __init__(
        self,
        agent_id: str,
        goal: str,
        task: Callable,
        parent_bus: EventBus,
    ):
        self.id = agent_id
        self.goal = goal
        self.task = task
        self._parent_bus = parent_bus
        self._result: str | None = None
        self._error: str | None = None
        self._done = False
        self._started_at: float | None = None
        self._finished_at: float | None = None

    async def run(self):
        """Execute the sub-agent's task."""
        self._started_at = __import__("time").time()
        logger.info("Sub-agent %s started: %s", self.id, self.goal[:60])

        try:
            if asyncio.iscoroutinefunction(self.task):
                result = await self.task()
            else:
                result = self.task()

            self._result = str(result) if result is not None else ""
            self._done = True
            logger.info("Sub-agent %s completed", self.id)
        except Exception as e:
            self._error = str(e)
            self._done = True
            logger.error("Sub-agent %s failed: %s", self.id, e)

        self._finished_at = __import__("time").time()

        # Emit result to parent bus
        await self._parent_bus.publish(Event("sub_agent.completed", {
            "agent_id": self.id,
            "goal": self.goal,
            "result": self._result,
            "error": self._error,
            "duration_ms": (self._finished_at - self._started_at) * 1000,
        }))

    @property
    def done(self) -> bool:
        return self._done

    @property
    def result(self) -> str | None:
        return self._result

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def duration_ms(self) -> float:
        if self._started_at and self._finished_at:
            return (self._finished_at - self._started_at) * 1000
        return 0.0

    def status(self) -> dict:
        return {
            "id": self.id,
            "goal": self.goal,
            "done": self._done,
            "has_result": self._result is not None,
            "has_error": self._error is not None,
            "duration_ms": self.duration_ms,
        }


class SubAgentManager(Module):
    """Manages the lifecycle of sub-agents.

    Spawns isolated agents, tracks their progress,
    and aggregates results.
    """

    def __init__(self, bus=None):
        super().__init__(
            name="sub_agent_manager",
            description="Spawns and orchestrates parallel sub-agents",
            bus=bus,
        )
        self._agents: dict[str, SubAgentInstance] = {}
        self._max_concurrent = 3

    async def start(self):
        await super().start()
        self.subscribe("sub_agent.spawn_request", self._handle_spawn_request)

    async def stop(self):
        await super().stop()

    async def _handle_spawn_request(self, event: Event):
        """Handle a spawn request from the pipeline or other modules."""
        goal = event.data.get("goal", "")
        task_data = event.data.get("task", "")
        task_type = event.data.get("task_type", "llm_call")

        if not goal:
            return

        agent_id = self.spawn(goal=goal, task_data=task_data, task_type=task_type)
        if agent_id:
            await self.emit("sub_agent.spawned", {
                "agent_id": agent_id,
                "goal": goal[:60],
            })

    def spawn(
        self,
        goal: str,
        task_data: str,
        task_type: str = "llm_call",
    ) -> str | None:
        """Spawn a new sub-agent.

        Args:
            goal: What the sub-agent should accomplish
            task_data: The data/task for the agent
            task_type: 'llm_call', 'terminal', 'search', or custom

        Returns:
            Agent ID or None if at capacity.
        """
        if len(self._agents) >= self._max_concurrent:
            logger.warning("Max sub-agents reached (%d)", self._max_concurrent)
            return None

        agent_id = f"sub_{uuid.uuid4().hex[:8]}"

        # Create the task based on type
        if task_type == "llm_call":
            task = self._make_llm_task(goal, task_data)
        elif task_type == "terminal":
            task = self._make_terminal_task(task_data)
        elif task_type == "search":
            task = self._make_search_task(task_data)
        else:
            task = self._make_llm_task(goal, task_data)

        agent = SubAgentInstance(
            agent_id=agent_id,
            goal=goal,
            task=task,
            parent_bus=self.bus,
        )
        self._agents[agent_id] = agent

        # Start in background
        asyncio.create_task(agent.run())

        return agent_id

    def _make_llm_task(self, goal: str, task_data: str):
        """Create an LLM-based sub-agent task."""
        async def run_llm():
            from zeus.llm import make_llm_call
            from zeus.providers import _discover_providers

            _discover_providers()
            llm = make_llm_call()

            response = llm(
                messages=[
                    {"role": "system", "content": f"Ти — субагент Zeus. Твоя мета: {goal}. Відповідай коротко і точно."},
                    {"role": "user", "content": task_data},
                ],
            )
            return response.strip()

        return run_llm

    def _make_terminal_task(self, command: str):
        """Create a terminal-based sub-agent task."""
        def run_terminal():
            import subprocess
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=30
            )
            output = result.stdout.strip()
            if result.stderr:
                output += f"\n(stderr: {result.stderr[:200]})"
            return output

        return run_terminal

    def _make_search_task(self, query: str):
        """Create a search-based sub-agent task."""
        async def run_search():
            from zeus.tools.web import execute as web_search
            return web_search({"query": query})

        return run_search

    def get_result(self, agent_id: str) -> str | None:
        """Get a sub-agent's result (or None if not done yet)."""
        agent = self._agents.get(agent_id)
        if agent and agent.done:
            return agent.result
        return None

    def list_agents(self) -> list[dict]:
        """List all sub-agents with their status."""
        return [a.status() for a in self._agents.values()]

    def clean_finished(self):
        """Remove finished agents from tracking."""
        self._agents = {
            aid: a for aid, a in self._agents.items()
            if not a.done
        }

    def status(self) -> dict:
        active = sum(1 for a in self._agents.values() if not a.done)
        finished = sum(1 for a in self._agents.values() if a.done)
        return {
            "name": self.name,
            "running": self._running,
            "active_agents": active,
            "finished_agents": finished,
            "max_concurrent": self._max_concurrent,
        }