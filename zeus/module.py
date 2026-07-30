"""Module system for Zeus — event-driven, independent, parallel modules.

Architecture:
  - Each component is a Module with its own lifecycle (start/stop/handle)
  - Modules communicate via EventBus (typed events)
  - ModuleManager orchestrates startup/shutdown
  - Events are processed asynchronously (parallel when independent)

Event flow:
  UserInput → Classifier → Classification → Router → TaskRequest → Planner
  → TaskDAG → Runtime → TaskResults → Synthesizer → UserOutput

Parallel modules (can run simultaneously):
  - Memory module (reads/writes facts during any phase)
  - Proactive module (scheduler runs independently)
  - Gateway module (handles I/O without blocking pipeline)
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from collections import defaultdict
from enum import Enum, auto
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


# ── Events ─────────────────────────────────────────────────

class EventPriority(Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


class Event:
    """A typed event that flows between modules.

    Attributes:
        type: Event type string (e.g. "user.input", "classification.result")
        data: Payload dict
        source: Name of the module that emitted the event
        id: Unique event ID for tracing
        priority: Event priority (determines queue ordering)
        created_at: Timestamp
    """

    def __init__(
        self,
        type: str,
        data: dict | None = None,
        source: str = "",
        priority: EventPriority = EventPriority.NORMAL,
    ):
        self.id = uuid.uuid4().hex[:12]
        self.type = type
        self.data = data or {}
        self.source = source
        self.priority = priority
        self.created_at = time.time()
        # Optional: set by the bus when routing
        self._consumed = False
        self._response: Any = None

    def __repr__(self):
        return f"Event({self.type}, source={self.source}, id={self.id})"

    def respond(self, data: Any):
        """Set a response on this event (for request-response pattern)."""
        self._response = data
        self._consumed = True


# ── Event types (convention: <module>.<action>) ────────────

# Input/Output
USER_INPUT = "user.input"
USER_OUTPUT = "user.output"

# Pipeline
CLASSIFICATION_RESULT = "classification.result"
ROUTE_RESULT = "route.result"
TASK_DAG_CREATED = "task.dag_created"
TASK_NODE_COMPLETED = "task.node_completed"
TASK_COMPLETED = "task.completed"
TASK_FAILED = "task.failed"

# Memory
MEMORY_SAVE = "memory.save"
MEMORY_SEARCH = "memory.search"
MEMORY_RESULT = "memory.result"

# System
MODULE_STARTED = "module.started"
MODULE_STOPPED = "module.stopped"
MODULE_ERROR = "module.error"
SYSTEM_SHUTDOWN = "system.shutdown"

# Reflection
REFLECTION_TOOL_CREATED = "reflection.tool_created"
REFLECTION_PATTERN_DETECTED = "reflection.pattern_detected"
REFLECTION_TASK_FAILED = "reflection.task_failed"

# Proactive
PROACTIVE_TRIGGER = "proactive.trigger"
PROACTIVE_TICK = "proactive.tick"


# ── EventBus ───────────────────────────────────────────────

Handler = Callable[[Event], Coroutine | Any] | Callable[[Event], None]


class EventBus:
    """In-process event bus with async dispatch.

    Supports:
      - Publish/subscribe (fire-and-forget)
      - Request/response (await response)
      - Priority queuing
      - Wildcard subscriptions ("task.*" matches "task.completed")
      - Module isolation (events scoped to subscribed modules)

    Thread-safe for both sync and async handlers.
    """

    def __init__(self):
        self._subscriptions: dict[str, list[Handler]] = defaultdict(list)
        self._async_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._worker_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── Subscription ───────────────────────────────────────

    def subscribe(self, event_type: str, handler: Handler):
        """Subscribe a handler to an event type.

        Supports glob patterns: "task.*" matches "task.completed", "task.failed", etc.
        """
        self._subscriptions[event_type].append(handler)
        logger.debug("Subscribed to %s: %s", event_type, handler.__name__ if hasattr(handler, '__name__') else type(handler).__name__)

    def unsubscribe(self, event_type: str, handler: Handler):
        """Remove a handler subscription."""
        if event_type in self._subscriptions:
            self._subscriptions[event_type] = [
                h for h in self._subscriptions[event_type] if h is not handler
            ]

    def unsubscribe_module(self, module_name: str):
        """Remove all subscriptions from a module (used on module stop)."""
        for event_type in list(self._subscriptions.keys()):
            self._subscriptions[event_type] = [
                h for h in self._subscriptions[event_type]
                if not (hasattr(h, '__self__') and getattr(h.__self__, 'name', None) == module_name)
            ]

    # ── Publishing ─────────────────────────────────────────

    async def publish(self, event: Event):
        """Publish an event to all matching subscribers.

        Events are dispatched asynchronously — handlers run in parallel.
        """
        matched = self._find_matched(event.type)
        if not matched:
            logger.debug("No handlers for %s", event.type)
            return

        tasks = []
        for handler in matched:
            if inspect.iscoroutinefunction(handler):
                tasks.append(handler(event))
            else:
                # Sync handler — run in executor to avoid blocking
                if self._loop:
                    tasks.append(self._loop.run_in_executor(None, handler, event))
                else:
                    handler(event)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def publish_sync(self, event: Event):
        """Synchronous publish — for use in non-async code.

        Runs handlers in the calling thread.
        """
        matched = self._find_matched(event.type)
        for handler in matched:
            try:
                if inspect.iscoroutinefunction(handler):
                    # Can't run async in sync context — log warning
                    logger.warning("Async handler %s called from sync context", handler.__name__)
                else:
                    handler(event)
            except Exception as e:
                logger.error("Handler %s failed: %s", handler, e)

    async def request(self, event: Event, timeout: float = 10.0) -> Any:
        """Publish an event and wait for a response.

        The handler should call event.respond(data) to set the response.
        """
        await self.publish(event)

        # Wait for response (with timeout)
        start = time.time()
        while time.time() - start < timeout:
            if event._consumed:
                return event._response
            await asyncio.sleep(0.01)

        logger.warning("Request timed out: %s", event.type)
        return None

    # ── Matching ───────────────────────────────────────────

    def _find_matched(self, event_type: str) -> list[Handler]:
        """Find all handlers that match an event type.

        Supports exact match and glob patterns (e.g. "task.*").
        """
        handlers = list(self._subscriptions.get(event_type, []))

        # Check wildcard patterns
        parts = event_type.split(".")
        if len(parts) > 1:
            wildcard = parts[0] + ".*"
            handlers.extend(self._subscriptions.get(wildcard, []))

        # Global wildcard
        handlers.extend(self._subscriptions.get("*", []))

        return handlers

    # ── Lifecycle ──────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop | None = None):
        """Start the event bus async worker."""
        if self._running:
            return
        self._running = True
        self._loop = loop or asyncio.get_event_loop()
        logger.info("EventBus started")

    def stop(self):
        """Stop the event bus."""
        self._running = False
        logger.info("EventBus stopped")

    @property
    def subscribers(self) -> dict[str, int]:
        """Return subscriber counts per event type."""
        return {t: len(h) for t, h in self._subscriptions.items()}


# ── Module Base ────────────────────────────────────────────

class Module:
    """Base class for all Zeus modules.

    Each module:
      - Has a name and optional description
      - Has its own lifecycle (start → running → stop)
      - Communicates via EventBus (subscribe to events, publish events)
      - Can run independently and in parallel
    """

    def __init__(self, name: str, description: str = "", bus: EventBus | None = None):
        self.name = name
        self.description = description
        self.bus = bus
        self._running = False

    # ── Lifecycle ──────────────────────────────────────────

    async def start(self):
        """Start the module. Subscribe to events here."""
        self._running = True
        logger.info("Module started: %s", self.name)

    async def stop(self):
        """Stop the module. Clean up resources."""
        self._running = False
        if self.bus:
            self.bus.unsubscribe_module(self.name)
        logger.info("Module stopped: %s", self.name)

    # ── Event helpers ──────────────────────────────────────

    def subscribe(self, event_type: str, handler: Handler):
        """Subscribe to an event on the bus."""
        if self.bus:
            self.bus.subscribe(event_type, handler)

    async def emit(self, event_type: str, data: dict | None = None, priority: EventPriority = EventPriority.NORMAL):
        """Emits an event to the bus (async)."""
        if self.bus:
            event = Event(event_type, data, source=self.name, priority=priority)
            await self.bus.publish(event)

    def emit_sync(self, event_type: str, data: dict | None = None):
        """Emits an event to the bus (sync)."""
        if self.bus:
            event = Event(event_type, data, source=self.name)
            self.bus.publish_sync(event)

    def on(self, event_type: str):
        """Decorator to register an event handler.

        Usage:
            class MyModule(Module):
                @on("user.input")
                async def handle_input(self, event):
                    ...
        """
        def decorator(func):
            self.subscribe(event_type, func)
            return func
        return decorator

    # ── Status ─────────────────────────────────────────────

    def status(self) -> dict:
        """Return module status info."""
        return {
            "name": self.name,
            "description": self.description,
            "running": self._running,
        }


# ── Module Manager ─────────────────────────────────────────

class ModuleManager:
    """Orchestrates module lifecycle.

    Starts/stops modules, manages their dependencies,
    provides unified status reporting.
    """

    def __init__(self, bus: EventBus | None = None):
        self.bus = bus or EventBus()
        self._modules: dict[str, Module] = {}
        self._parallel_groups: list[list[str]] = []

    def register(self, module: Module):
        """Register a module with the manager.

        Automatically assigns the event bus.
        """
        module.bus = self.bus
        self._modules[module.name] = module
        logger.info("Registered module: %s", module.name)

    async def start_all(self):
        """Start all registered modules in parallel."""
        tasks = [module.start() for module in self._modules.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("All modules started (%d)", len(self._modules))

    async def start_group(self, names: list[str]):
        """Start a specific group of modules."""
        tasks = [self._modules[n].start() for n in names if n in self._modules]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def stop_all(self):
        """Stop all modules."""
        tasks = [module.stop() for module in self._modules.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("All modules stopped")

    async def stop_module(self, name: str):
        """Stop a specific module."""
        if name in self._modules:
            await self._modules[name].stop()

    def get(self, name: str) -> Module | None:
        """Get a module by name."""
        return self._modules.get(name)

    def status_all(self) -> dict[str, dict]:
        """Get status of all modules."""
        return {n: m.status() for n, m in self._modules.items()}

    def bus_status(self) -> dict:
        """Get event bus subscriber info."""
        return self.bus.subscribers