"""Zeus Agent — CLI entry point (modular architecture).

Usage:
    python -m zeus "your request here"
    python -m zeus --interactive
    python -m zeus --provider openrouter --model deepseek/deepseek-v4-flash-free "search for latest python"
"""

from __future__ import annotations
import argparse
import asyncio
import os
import sys
import re

from zeus.models.types import ToolRegistry
from zeus.tools.terminal import execute as terminal_execute, SCHEMA as terminal_schema
from zeus.tools.file import execute as file_execute, SCHEMA as file_schema
from zeus.tools.web import execute as web_execute, SCHEMA as web_schema
from zeus.llm import make_llm_call, configure_from_env, list_providers
import zeus.llm as _llm_mod

from zeus.memory import SessionStore
from zeus.proactive import Scheduler
from zeus.tools.dynamic import (
    discover_custom_tools,
    create_tool as create_dynamic_tool,
    delete_tool as delete_custom_tool,
)

# Modular imports
from zeus.module import EventBus, ModuleManager, Event, USER_INPUT, USER_OUTPUT
from zeus.modules.classifier import ClassifierModule
from zeus.modules.memory import MemoryModule
from zeus.modules.router import RouterModule
from zeus.modules.pipeline import PipelineModule
from zeus.modules.reflection import ReflectionModule

_llm_call = None
_tool_registry = None


def setup_tools() -> ToolRegistry:
    """Initialize the tool registry with all available tools.

    Loads built-in tools + any custom tools from ~/.zeus/custom_tools/.
    """
    registry = ToolRegistry()
    registry.register("terminal", terminal_schema, terminal_execute)
    registry.register("file", file_schema, file_execute)
    registry.register("web_search", web_schema, web_execute)

    custom = discover_custom_tools()
    for name, tool in custom.items():
        registry.register(name, tool["schema"], tool["handler"])

    return registry


def show_providers():
    """Display available LLM providers."""
    providers = list_providers()
    if providers:
        print("\nAvailable providers:")
        for p in providers[:30]:
            name = p.get("name", "?")
            display = p.get("display_name") or p.get("description", "")
            print(f"  \u2022 {name}{' \u2014 ' + display if display else ''}")
    else:
        print("\n\u26a0 No providers found. Install hermes-agent or configure manually.")
    print()


def _show_facts(store):
    """Display saved facts."""
    facts = store.get_facts(limit=10)
    if not facts:
        print("  No facts stored yet.")
        return
    print(f"  Facts ({len(facts)}):")
    for f in facts:
        cat = f["category"]
        content = f["content"][:80]
        trust = f["trust"]
        print(f"  \u2022 [{cat}] {content} (trust: {trust})")


def _show_sessions(store):
    """Display recent sessions."""
    sessions = store.list_sessions(limit=5)
    if not sessions:
        print("  No sessions yet.")
        return
    for s in sessions:
        import datetime
        ts = datetime.datetime.fromtimestamp(s["created_at"]).strftime("%Y-%m-%d %H:%M")
        print(f"  #{s['id']} {s['title'] or '(no title)'} \u2014 {ts}")


def _show_jobs(scheduler):
    """Display scheduled jobs."""
    jobs = scheduler.list_jobs()
    if not jobs:
        print("  No scheduled jobs.")
        return
    print(f"  Jobs ({len(jobs)}):")
    for j in jobs:
        status = "\u25b6" if j["active"] else "\u23f8"
        kind = j["kind"]
        task = j["task"][:50]
        runs = j["run_count"]
        next_str = f"next in {j['next_in']:.0f}s" if (j["active"] and j.get("next_in", 0) > 0) else ("now" if j["active"] else "paused")
        print(f"  {status} {j['id']}: {kind} \u2014 \"{task}\" ({runs} runs, {next_str})")


def _handle_schedule_cmd(text: str, scheduler):
    """Handle /schedule commands."""
    cmd = text[len("/schedule "):].strip()
    m = re.match(r"every\s+(\d+)\s*(s|sec|m|min|h|hr)?\s+run\s+(.+)", cmd, re.IGNORECASE)
    if m:
        num = int(m.group(1))
        unit = (m.group(2) or "m").lower()
        task = m.group(3).strip()
        multipliers = {"s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600, "hr": 3600}
        interval = num * multipliers.get(unit, 60)
        jid = scheduler.schedule_every(interval, task)
        next_time = scheduler._jobs[jid]["next_run"]
        import datetime
        ts = datetime.datetime.fromtimestamp(next_time).strftime("%H:%M:%S")
        print(f"  \u2705 Scheduled every {num}{unit}: \"{task[:50]}\" (next: {ts})")
        return

    m = re.match(r"cancel\s+(.+)", cmd, re.IGNORECASE)
    if m:
        jid = m.group(1).strip()
        if scheduler.cancel(jid):
            print(f"  \u2705 Cancelled job: {jid}")
        else:
            print(f"  \u26a0 Job not found: {jid}")
        return

    print("  \u26a0 Usage: /schedule every <N> <unit> run <task>")
    print("    e.g.: /schedule every 30m run search latest python news")
    print("    e.g.: /schedule cancel job_123")


def _handle_tools_cmd(text: str):
    """Handle /tools commands."""
    global _llm_call, _tool_registry
    cmd = text[len("/tools"):].strip()

    if not cmd or cmd == "list":
        names = _tool_registry.names() if _tool_registry else []
        print(f"  Tools ({len(names)}):")
        for n in names:
            is_custom = n not in ("terminal", "file", "web_search")
            print(f"  {'\u26a1' if is_custom else '\u2022'} {n}")
        return

    if cmd.startswith("create "):
        if not _llm_call:
            print("  \u26a0 LLM not configured. Cannot create tools.")
            return
        desc = cmd[7:].strip()
        print(f"  Generating tool: \"{desc}\"...")
        result = create_dynamic_tool(desc, _llm_call)
        if result["success"]:
            name = result["name"]
            schema = result["schema"]
            desc_text = schema.get("description", "")
            params = list(schema.get("parameters", {}).get("properties", {}).keys())
            print(f"  \u2705 Created tool: {name}")
            print(f"     Description: {desc_text}")
            print(f"     Parameters: {', '.join(params)}")
            print(f"     Saved: {result['path']}")
            _tool_registry = setup_tools()
        else:
            print(f"  \u26a0 Failed: {result.get('error', 'unknown error')}")
        return

    if cmd.startswith("delete "):
        name = cmd[7:].strip()
        if delete_custom_tool(name):
            _tool_registry = setup_tools()
            print(f"  \u2705 Deleted tool: {name}")
        else:
            print(f"  \u26a0 Tool not found: {name}")
        return

    print("  Usage: /tools                    \u2014 list tools")
    print("         /tools list               \u2014 list tools")
    print("         /tools create <desc>      \u2014 create a tool from description")
    print("         /tools delete <name>      \u2014 delete a custom tool")
    print("         /tools inspect <name>     \u2014 show tool details")


# ── Modular CLI ─────────────────────────────────────────────

def _run_async(coro):
    """Helper to run async code from sync CLI."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return asyncio.ensure_future(coro)
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def process_via_modules(text: str) -> str:
    """Process a query through the modular pipeline.

    Creates a temporary EventBus + modules, publishes user.input,
    waits for user.output, returns the response.

    Returns:
        Response string.
    """
    async def _run():
        bus = EventBus()
        bus.start()

        outputs = []
        bus.subscribe(USER_OUTPUT, lambda e: outputs.append(e.data))

        manager = ModuleManager(bus=bus)
        manager.register(ClassifierModule(bus=bus))
        manager.register(RouterModule(bus=bus, tool_registry=_tool_registry, llm_call=_llm_call))
        manager.register(PipelineModule(bus=bus, tool_registry=_tool_registry, llm_call=_llm_call))
        manager.register(ReflectionModule(bus=bus, tool_registry=_tool_registry, llm_call=_llm_call))

        await manager.start_all()
        await bus.publish(Event(USER_INPUT, {"text": text}, source="cli"))

        import time
        deadline = time.time() + 30
        while time.time() < deadline:
            if outputs:
                result = outputs[-1].get("text", "")
                await manager.stop_all()
                return result
            await asyncio.sleep(0.2)

        await manager.stop_all()
        return "Timeout: no response within 30s."

    return asyncio.run(_run())


# ── Main ────────────────────────────────────────────────────

def main():
    global _llm_call, _tool_registry

    parser = argparse.ArgumentParser(
        description="Zeus Agent \u2014 next-gen AI agent framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python -m zeus "hello"
  python -m zeus "list files in current dir"
  python -m zeus --interactive
  python -m zeus --provider openrouter "search python 3.13"
  python -m zeus --providers          # list available providers
        """,
    )
    parser.add_argument("query", nargs="?", help="Single query (non-interactive)")
    parser.add_argument("-i", "--interactive", action="store_true", help="Interactive mode")
    parser.add_argument("--provider", default=None, help="LLM provider")
    parser.add_argument("--model", default=None, help="LLM model")
    parser.add_argument("--api-key", default=None, help="API key (default: env ZEUS_LLM_API_KEY)")
    parser.add_argument("--base-url", default=None, help="Base URL override")
    parser.add_argument("--providers", action="store_true", help="List available LLM providers and exit")
    parser.add_argument("--doctor", action="store_true", help="Check Zeus configuration")

    args = parser.parse_args()

    if args.providers:
        show_providers()
        return

    if args.doctor:
        show_doctor()
        return

    # Initialize core
    _tool_registry = setup_tools()
    _store = SessionStore()

    # Scheduler (background thread, kept for now)
    _scheduler = Scheduler()
    _scheduler.start()

    # Initialize LLM
    if args.provider or args.model or args.api_key:
        _llm_call = make_llm_call(
            provider=args.provider,
            model=args.model,
            api_key=args.api_key,
            base_url=args.base_url,
        )
    elif _llm_mod._DEFAULT_API_KEY:
        _llm_call = make_llm_call(
            provider=_llm_mod._DEFAULT_PROVIDER,
            model=_llm_mod._DEFAULT_MODEL,
            api_key=_llm_mod._DEFAULT_API_KEY,
        )
        if args.query or args.interactive:
            provider_name = _llm_mod._DEFAULT_PROVIDER
            model_name = _llm_mod._DEFAULT_MODEL
            print(f"\u26a1 Auto-configured: {provider_name}/{model_name}")
    elif os.environ.get("ZEUS_LLM_API_KEY"):
        _llm_call = configure_from_env()

    # Single query via modular pipeline
    if args.query:
        result = process_via_modules(args.query)
        print(result)

    # Interactive mode with modules
    elif args.interactive:
        # Build persistent module system
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        bus = EventBus()
        bus.start(loop=loop)

        outputs = []
        bus.subscribe(USER_OUTPUT, lambda e: outputs.append(e.data))

        manager = ModuleManager(bus=bus)
        manager.register(ClassifierModule(bus=bus))
        manager.register(RouterModule(bus=bus, tool_registry=_tool_registry, llm_call=_llm_call))
        manager.register(PipelineModule(bus=bus, tool_registry=_tool_registry, llm_call=_llm_call))
        manager.register(ReflectionModule(bus=bus, tool_registry=_tool_registry, llm_call=_llm_call))
        loop.run_until_complete(manager.start_all())

        print("\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557")
        print("\u2551          Zeus Agent — Modular                \u2551")
        print("\u2551      'exit', '/quit' to quit               \u2551")
        if _llm_call:
            print("\u2551      LLM: \u2705 configured                    \u2551")
        else:
            print("\u2551      LLM: \u26a0 not set (env ZEUS_LLM_API_KEY) \u2551")
        print("\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d")
        print()

        while True:
            try:
                text = input("\u26a1 ").strip()
                if text in ("exit", "quit", "/quit", "/exit"):
                    break
                if not text:
                    continue
                if text == "/providers":
                    show_providers()
                    continue
                if text == "/memory":
                    _show_facts(_store)
                    continue
                if text == "/sessions":
                    _show_sessions(_store)
                    continue
                if text == "/jobs" or text == "/schedule list":
                    _show_jobs(_scheduler)
                    continue
                if text.startswith("/schedule "):
                    _handle_schedule_cmd(text, _scheduler)
                    continue
                if text.startswith("/tools"):
                    _handle_tools_cmd(text)
                    continue

                # Process via modular EventBus
                outputs.clear()
                loop.run_until_complete(bus.publish(Event(USER_INPUT, {"text": text}, source="cli")))

                # Wait for response
                import time
                deadline = time.time() + 30
                last_len = 0
                while time.time() < deadline:
                    if len(outputs) > last_len:
                        for i in range(last_len, len(outputs)):
                            print()
                            print(outputs[i].get("text", ""))
                            print()
                        last_len = len(outputs)
                        break
                    loop.run_until_complete(asyncio.sleep(0.1))

            except KeyboardInterrupt:
                print()
                break
            except EOFError:
                break
            except Exception as e:
                print(f"\u26a0 Error: {e}")
                import traceback
                traceback.print_exc()

        loop.run_until_complete(manager.stop_all())
        loop.close()
        _scheduler.stop()
    else:
        parser.print_help()


def show_doctor():
    """Check system configuration."""
    print("\n\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557")
    print("\u2551          Zeus Agent — Doctor                \u2551")
    print("\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d")

    print(f"\n\U0001f40d Python: {sys.version}")
    api_key_set = bool(_llm_mod._DEFAULT_API_KEY)
    print(f"\U0001f511 LLM API Key: {'✅ set' if api_key_set else '❌ not set'}")
    print(f"   Provider: {_llm_mod._DEFAULT_PROVIDER}")
    print(f"   Model: {_llm_mod._DEFAULT_MODEL}")
    print(f"   Status: {'✅ configured and ready' if _llm_call else ('⚠ key found — use --interactive or pass a query' if api_key_set else '❌ no API key found')}")

    if _tool_registry:
        print(f"\U0001f527 Tools: {', '.join(_tool_registry.names())}")

    hermes_path = os.path.expanduser("~/.hermes")
    if os.path.exists(hermes_path):
        print(f"\U0001f4e6 Hermes: ✅ found at {hermes_path}")
    else:
        print(f"\U0001f4e6 Hermes: ❌ not found")

    try:
        import providers
        print(f"\U0001f50c Provider system: ✅ available")
    except ImportError:
        print(f"\U0001f50c Provider system: ❌ not available (install hermes-agent)")

    print()


if __name__ == "__main__":
    main()