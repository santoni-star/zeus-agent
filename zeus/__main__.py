"""Zeus Agent — CLI entry point (modular architecture).

Usage:
    python -m zeus "your request here"
    python -m zeus --interactive
    python -m zeus --provider openrouter --model deepseek/deepseek-v4-flash-free "search for latest python"
"""

from __future__ import annotations
import argparse
import asyncio
import logging
import os
import sys
import re

logger = logging.getLogger(__name__)

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
from zeus.memory.history import ConversationBuffer, HistorySearcher, search_history
from zeus.memory.profile import UserProfile, FactStore
from zeus.skills import SkillManager, get_skill_manager
from zeus.sync import GitSync, get_sync, auto_sync
from zeus.actions import ActionsManager, get_actions, auto_generate_workflows
from zeus.mcp_client import MCPClientManager, get_mcp_manager, MCP_AVAILABLE
from zeus.modules.classifier import ClassifierModule
from zeus.modules.memory import MemoryModule
from zeus.modules.router import RouterModule
from zeus.modules.pipeline import PipelineModule
from zeus.modules.reflection import ReflectionModule
from zeus.modules.gateway import GatewayModule
from zeus.modules.self_review import SelfReviewModule
from zeus.modules.telemetry import TelemetryModule
from zeus.config import ZeusConfig, show_config

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
        deadline = time.time() + 60
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
    parser.add_argument("--config", action="store_true", help="Show current configuration")
    parser.add_argument("--config-path", default=None, help="Path to zeus.yaml config")
    parser.add_argument("--gateway", action="store_true", help="Enable Telegram gateway module")
    parser.add_argument("--gateway-token", default=None, help="Telegram bot token")
    parser.add_argument("--gateway-chat", default=None, help="Telegram chat ID")

    args = parser.parse_args()

    if args.providers:
        show_providers()
        return

    if args.doctor:
        show_doctor()
        return

    # Load configuration
    _config = ZeusConfig.load(args.config_path)
    if args.config:
        show_config()
        return
    if args.config_path:
        print(f"⚡ Config loaded from: {args.config_path}")

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

        # Shared conversation history buffer
        _history = ConversationBuffer(max_turns=20)
        _searcher = HistorySearcher()
        _profile = UserProfile()
        _facts = FactStore()
        _skills = get_skill_manager()
        _sync = get_sync()
        _actions = get_actions()
        _mcp = get_mcp_manager()
        # Auto-setup sync if token is available
        if _config.get("sync.enabled", False):
            try:
                token = os.environ.get(_config.get("sync.token_env", "GITHUB_TOKEN"), "")
                if token:
                    _sync.setup(
                        token=token,
                        repo=_config.get("sync.repo", ""),
                        branch=_config.get("sync.branch", "agent-state"),
                    )
                    # Auto-generate GitHub Actions workflows
                    if _config.get("sync.actions.enabled", True):
                        generated = auto_generate_workflows()
                        if generated:
                            logger.info("Auto-generated workflows: %s", generated)
            except Exception as e:
                logger.debug("Sync auto-setup: %s", e)

        manager = ModuleManager(bus=bus)
        manager.register(ClassifierModule(bus=bus))
        manager.register(RouterModule(
            bus=bus, tool_registry=_tool_registry,
            llm_call=_llm_call, history=_history,
        ))
        manager.register(PipelineModule(bus=bus, tool_registry=_tool_registry, llm_call=_llm_call))
        manager.register(ReflectionModule(bus=bus, tool_registry=_tool_registry, llm_call=_llm_call))
        # Register enabled modules from config
        if _config.get("memory.enabled", True):
            from zeus.modules.memory import MemoryModule
            manager.register(MemoryModule(bus=bus))
        if _config.get("scheduler.enabled", True):
            # Scheduler already initialized above
            pass

        # Configure MCP servers (lazy — no connect)
        mcp_servers_config = _config.get("mcp_servers", {})
        if mcp_servers_config:
            _mcp.set_servers_config(
                {k: v for k, v in mcp_servers_config.items() if isinstance(v, dict)}
            )
            logger.info("MCP: %d server(s) configured (lazy)", len(mcp_servers_config))

        _gateway_mod = None
        enable_gateway = args.gateway or _config.get("gateway.enabled", False)
        if enable_gateway:
            token = args.gateway_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
            chat_id = args.gateway_chat or os.environ.get("TELEGRAM_CHAT_ID", "")
            if token:
                _gateway_mod = GatewayModule(bus=bus, token=token, chat_id=chat_id)
                manager.register(_gateway_mod)
            else:
                print("⚠ Gateway enabled but TELEGRAM_BOT_TOKEN not set.")

        # Optional SelfReviewModule
        _self_review = SelfReviewModule(bus=bus, llm_call=_llm_call)
        if _config.module_enabled("self_review"):
            manager.register(_self_review)

        # Optional TelemetryModule
        _telemetry = TelemetryModule(bus=bus)
        if _config.module_enabled("telemetry"):
            manager.register(_telemetry)

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
                if text == "/tools list":
                    _handle_tools_cmd(text)
                    continue
                if text.startswith("/tools"):
                    _handle_tools_cmd(text)
                    continue
                if text == "/gateway" or text == "/gw":
                    if _gateway_mod:
                        if _gateway_mod.is_connected:
                            print(f"✅ Gateway active (chat: {_gateway_mod.configured_chat})")
                        else:
                            print("⚠ Gateway: no token configured. Set TELEGRAM_BOT_TOKEN env var.")
                    else:
                        print("⚠ Gateway module not loaded. Restart with --gateway flag.")
                    continue

                # ── Review commands ────────────────────────
                if text.startswith("/review scan"):
                    parts = text.split(maxsplit=2)
                    if len(parts) >= 3:
                        # Scan specific file
                        result = _self_review.scan_file(parts[2])
                        if result:
                            for p in result:
                                print(p)
                        else:
                            print("ℹ No issues found in that file.")
                    else:
                        # Scan next unscanned
                        print("⏳ Scanning next file... (use --interactive with LLM)")
                    continue
                if text == "/review list" or text == "/reviews":
                    proposals = _self_review.list_proposals()
                    if proposals:
                        print(f"\n📋 Pending reviews ({len(proposals)}):\n")
                        for p in proposals:
                            print(p)
                    else:
                        print("✅ No pending reviews.")
                    continue
                if text.startswith("/review show"):
                    parts = text.split(maxsplit=2)
                    if len(parts) >= 3:
                        p = _self_review.get_proposal(parts[2])
                        if p:
                            d = p.to_dict()
                            print(f"\n{'='*60}")
                            print(f"📝 {d['title']}")
                            print(f"{'='*60}")
                            print(f"  File: {d['target_file']}")
                            print(f"  Lines: {d['line_range']}")
                            print(f"  Type: {d['issue_type']} | Severity: {d['severity']}")
                            print(f"  Status: {d['status']}")
                            print(f"\n  Description: {d['description']}")
                            if d['old_code']:
                                print(f"\n  ┌─ Old code ──────────────────")
                                print(f"  │ {d['old_code'][:600].replace(chr(10), chr(10)+'  │ ')}")
                                print(f"  └────────────────────────────")
                            if d['new_code']:
                                print(f"\n  ┌─ Suggested ─────────────────")
                                print(f"  │ {d['new_code'][:600].replace(chr(10), chr(10)+'  │ ')}")
                                print(f"  └────────────────────────────")
                            print(f"\n  /review approve {d['id']}")
                            print(f"  /review reject {d['id']}")
                            print()
                        else:
                            print(f"❌ Review `{parts[2]}` not found.")
                    else:
                        print("Usage: /review show <id>")
                    continue
                if text.startswith("/review approve"):
                    parts = text.split(maxsplit=2)
                    if len(parts) >= 3:
                        if _self_review.approve_proposal(parts[2]):
                            print(f"✅ Review {parts[2]} approved and applied!")
                        else:
                            print(f"❌ Could not apply {parts[2]}.")
                    else:
                        print("Usage: /review approve <id>")
                    continue
                if text.startswith("/review reject"):
                    parts = text.split(maxsplit=2)
                    if len(parts) >= 3:
                        _self_review.reject_proposal(parts[2])
                        print(f"❌ Review {parts[2]} rejected.")
                    else:
                        print("Usage: /review reject <id>")
                    continue

                # ── Telemetry / Stats commands ─────────────
                if text == "/stats" or text == "/telemetry":
                    s = _telemetry.stats()
                    print(f"\n📊 Telemetry Stats")
                    print(f"  Total events: {s['total_events']}")
                    print(f"  Events last hour: {s['events_last_hour']}")
                    print(f"  Active modules: {s['active_modules']}")
                    print(f"  Avg duration (24h): {s['avg_duration_24h_ms']}ms")
                    print()
                    summary = _telemetry.summary(hours=24)
                    if summary:
                        print("  Per-module (24h):")
                        for m in summary:
                            bar = "█" * int(m["avg_duration_ms"] / 10) if m["avg_duration_ms"] else ""
                            print(f"  {m['module']:>20s} │ {m['events']:>4d} ev │ {m['avg_duration_ms']:>7.1f}ms {bar}")
                    print()
                    continue
                if text == "/insights":
                    import asyncio
                    ins = loop.run_until_complete(_telemetry.insights(refresh=True))
                    if ins:
                        print(f"\n💡 Architecture Insights ({len(ins)}):\n")
                        for i, item in enumerate(ins, 1):
                            icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(item["severity"], "⚪")
                            print(f"  {icon} {item['title']}")
                            print(f"     {item['description'][:200]}")
                            print()
                    else:
                        print("✅ No insights yet. Collect more telemetry data.")
                    continue
                if text == "/errors":
                    errs = _telemetry.errors(hours=48)
                    if errs:
                        print(f"\n⚠ Errors ({len(errs)}):\n")
                        for e in errs:
                            print(f"  `{e['module_name']}/{e['event_type']}`: {e['error_count']}×")
                            if e.get("last_error"):
                                print(f"    Last: {e['last_error'][:150]}")
                            print()
                    else:
                        print("✅ No errors in last 48 hours.")
                    continue

                # ── History / Search commands ──────────────
                if text == "/history":
                    ctx = _history.context_prompt(max_chars=2000)
                    if ctx:
                        print(f"\n📜 Conversation history ({_history.turn_count} turns):\n")
                        for turn in _history.turns[-10:]:
                            label = "👤" if turn.role == "user" else "🤖"
                            print(f"  {label} {turn.content[:300]}")
                        print()
                    else:
                        print("📭 No conversation history yet.")
                    continue
                if text.startswith("/search "):
                    query = text[8:].strip()
                    if query:
                        print(f"\n🔍 Searching: \"{query}\"...")
                        result = _searcher.smart_search(query)
                        print(_searcher.format_result(result))
                    else:
                        print("Usage: /search <query>")
                    continue
                if text == "/search" or text == "/s":
                    print("Usage: /search <query>")
                    print("  Example: /search що ми вчора робили")
                    print("  Example: /search github обговорення")
                    print("  Example: /search self-review пропозиції")
                    continue

                # ── Profile / Memory commands ──────────────
                if text == "/profile":
                    p = _profile.to_dict()
                    if p:
                        print(f"\n👤 User profile:")
                        for key, value in sorted(p.items()):
                            print(f"  {key}: {value}")
                    else:
                        print("👤 Profile empty. Facts will be auto-collected.")
                    continue
                if text.startswith("/remember "):
                    content = text[10:].strip()
                    if content:
                        _facts.add(content, entity="user", category="user_pref", trust=0.7, source="manual")
                        _profile.update_from_text(content, source="manual")
                        print(f"✅ Remembered: {content[:100]}")
                        # Auto-sync
                        if _config.get("sync.enabled", False) and _sync._configured:
                            _sync.auto_sync(message=f"remember: {content[:40]}")
                    else:
                        print("Usage: /remember <fact to remember>")
                    continue
                if text.startswith("/forget "):
                    query = text[8:].strip()
                    results = _facts.search(query)
                    if results:
                        for r in results[:3]:
                            _facts.add(r["content"], entity=r["entity"],
                                       category=r["category"], trust=0.0, source="correction")
                        print(f"✅ Forgotten: {query}")
                    else:
                        print(f"No facts match '{query}'")
                    continue
                if text == "/facts":
                    facts = _facts.search("", min_trust=0.3, limit=20)
                    if facts:
                        print(f"\n📚 Stored facts ({len(facts)}):\n")
                        for f in facts:
                            icon = {"user_pref": "👤", "project": "📦", "tool": "🔧", "general": "📝"}.get(f["category"], "•")
                            trust_str = f" (trust: {f['trust']:.1f})"
                            print(f"  {icon} {f['content'][:120]}{trust_str}")
                    else:
                        print("📚 No stored facts. Use /remember to add some.")
                    continue

                # ── Skills commands ────────────────────────
                if text == "/skills" or text == "/skill list":
                    skills = _skills.list_skills()
                    if skills:
                        print(f"\n📚 Skills ({len(skills)}):\n")
                        for s in skills:
                            tags = f" [{', '.join(s['tags'][:3])}]" if s['tags'] else ""
                            print(f"  • {s['name']}: {s['description'][:80]}{tags}")
                    else:
                        print("📚 No skills available.")
                    continue
                if text.startswith("/skill show "):
                    name = text[12:].strip()
                    skill = _skills.get(name)
                    if skill:
                        print()
                        print(skill.format())
                    else:
                        print(f"❌ Skill not found: {name}")
                        similar = _skills.find(name)
                        if similar:
                            print(f"   Did you mean: {', '.join(s.name for s in similar)}")
                    continue
                if text.startswith("/skill create "):
                    # Usage: /skill create name: Description
                    rest = text[14:].strip()
                    if ":" not in rest:
                        print("Usage: /skill create <name>: <description>")
                        print("  Example: /skill create tdd: Run tests before writing code")
                        continue
                    name, _, desc = rest.partition(":")
                    name = name.strip().lower().replace(" ", "-")
                    desc = desc.strip()
                    path = _skills.create(name, desc, steps=["See body for details"])
                    print(f"✅ Skill created: {path}")
                    print("   Edit the file to add steps, commands, and pitfalls.")
                    # Auto-sync
                    if _config.get("sync.enabled", False) and _sync._configured:
                        _sync.auto_sync(message=f"new skill: {name}")
                    continue
                if text.startswith("/do "):
                    name = text[4:].strip()
                    skill = _skills.get(name)
                    if skill:
                        print(f"\n📋 Running skill: {skill.name}")
                        print(f"   {skill.description}\n")
                        print(skill.to_prompt())
                    else:
                        print(f"❌ Skill not found: {name}")
                    continue


                # ── Sync commands ─────────────────────────
                if text == "/sync status":
                    status = _sync.status()
                    if status.get("error"):
                        print(f"❌ {status['error']}")
                    else:
                        branch = status.get("branch", "?")
                        clean = "✅" if status.get("clean") else "📝"
                        ahead = status.get("ahead", 0)
                        behind = status.get("behind", 0)
                        modified = status.get("modified", [])
                        print(f"\n{clean} Sync status (branch: {branch})")
                        if ahead or behind:
                            print(f"   📤 {ahead} ahead, 📥 {behind} behind")
                        if modified:
                            print(f"   📄 {len(modified)} file(s) modified:")
                            for f in modified[:5]:
                                print(f"      • {f}")
                            if len(modified) > 5:
                                print(f"      ... and {len(modified) - 5} more")
                        print(f"   🌐 {_sync.url}")
                    continue
                if text == "/sync now":
                    print("\n🔄 Syncing to GitHub...")
                    if not _sync._configured:
                        print("   Setting up sync...")
                        token_env = _config.get("sync.token_env", "GITHUB_TOKEN")
                        token = os.environ.get(token_env, "")
                        if not token:
                            print(f"❌ No token found in ${token_env}")
                            print("   Set it in your environment or zeus.yaml")
                            continue
                        _sync.setup(
                            token=token,
                            repo=_config.get("sync.repo", ""),
                            branch=_config.get("sync.branch", "agent-state"),
                        )
                    result = _sync.auto_sync(message="interactive save")
                    print(f"{'✅ Synced!' if result else '⚠ Nothing to sync or push failed'}")
                    continue
                if text == "/sync on":
                    _config._data.setdefault("sync", {})["enabled"] = True
                    print("✅ Auto-sync enabled")
                    continue
                if text == "/sync off":
                    _config._data.setdefault("sync", {})["enabled"] = False
                    print("⏸ Auto-sync disabled")
                    continue
                if text.startswith("/sync log"):
                    count = 10
                    if text.strip() != "/sync log":
                        try:
                            count = int(text.split()[-1])
                        except ValueError:
                            pass
                    commits = _sync.log(max_count=count)
                    if commits:
                        print(f"\n📜 Recent commits ({len(commits)}):\n")
                        for c in commits:
                            print(f"  {c['hash']} {c['message']} ({c.get('age', '')})")
                    else:
                        print("📜 No commits found.")
                    continue
                if text == "/sync" or text.startswith("/sync "):
                    print("Usage: /sync status | /sync now | /sync on | /sync off | /sync log [n]")
                    print("  /sync status   — show current git status")
                    print("  /sync now      — commit + push to GitHub")
                    print("  /sync on       — enable auto-sync")
                    print("  /sync off      — disable auto-sync")
                    print("  /sync log [n]  — show recent commits")
                    continue

                # ── Actions commands ──────────────────────
                if text == "/actions" or text == "/actions list":
                    print(_actions.status_report())
                    continue
                if text.startswith("/actions enable "):
                    wf_id = text[16:].strip()
                    try:
                        _actions.enable(wf_id)
                        print(f"✅ Enabled workflow: {wf_id}")
                        # Auto-sync the changes
                        if _sync._configured:
                            _sync.commit(message=f"enable action: {wf_id}")
                    except ValueError as e:
                        print(f"❌ {e}")
                    continue
                if text.startswith("/actions disable "):
                    wf_id = text[17:].strip()
                    try:
                        _actions.disable(wf_id)
                        print(f"⏸ Disabled workflow: {wf_id}")
                        if _sync._configured:
                            _sync.commit(message=f"disable action: {wf_id}")
                    except ValueError as e:
                        print(f"❌ {e}")
                    continue
                if text == "/actions generate":
                    generated = _actions.generate_all()
                    if generated:
                        print(f"✅ Generated {len(generated)} workflow(s): {', '.join(generated)}")
                        if _sync._configured:
                            _sync.commit(message="regenerate workflows")
                    else:
                        print("⚠ No workflows were generated (all disabled)")
                    continue
                if text == "/actions validate":
                    missing_tools = _actions.validate_tools_docs()
                    missing_modules = _actions.validate_modules_docs()
                    print("\n📋 Docs validation:\n")
                    if not missing_tools and not missing_modules:
                        print("  ✅ All tools and modules are documented!")
                    if missing_tools:
                        print(f"  ❌ Missing from TOOLS.md: {', '.join(missing_tools)}")
                    if missing_modules:
                        print(f"  ❌ Missing from ARCHITECTURE.md: {', '.join(missing_modules)}")
                    continue
                if text.startswith("/actions "):
                    print("Usage: /actions list | enable <id> | disable <id> | generate | validate")
                    print("  /actions list          — show workflow status")
                    print("  /actions enable test   — enable test workflow")
                    print("  /actions disable lint  — disable lint workflow")
                    print("  /actions generate      — regenerate all workflow files")
                    print("  /actions validate      — check docs match code")
                    continue

                # ── MCP commands ──────────────────────────
                if text == "/mcp" or text == "/mcp status":
                    print(_mcp.format_status())
                    continue
                if text.startswith("/mcp connect "):
                    name = text[13:].strip()
                    if not name:
                        print("Usage: /mcp connect <server_name>")
                        continue
                    try:
                        import asyncio as _mcp_aio
                        _mcp_al = _mcp_aio.new_event_loop()
                        ok = _mcp_al.run_until_complete(_mcp.connect_server(name))
                        _mcp_al.close()
                        if ok:
                            print(f"✅ Connected '{name}'")
                        else:
                            print(f"❌ Failed to connect '{name}'")
                    except Exception as e:
                        print(f"❌ {e}")
                    continue
                if text.startswith("/mcp disconnect "):
                    name = text[16:].strip()
                    if not name:
                        print("Usage: /mcp disconnect <server_name>")
                        continue
                    try:
                        import asyncio as _mcp_aio
                        _mcp_al = _mcp_aio.new_event_loop()
                        ok = _mcp_al.run_until_complete(_mcp.disconnect_server(name))
                        _mcp_al.close()
                        if ok:
                            print(f"⏸ Disconnected '{name}'")
                        else:
                            print(f"⚠ Server '{name}' not found")
                    except Exception as e:
                        print(f"❌ {e}")
                    continue
                if text == "/mcp auto on":
                    _mcp.set_hot_mode(True)
                    print("✅ MCP hot mode ON — connects on demand")
                    continue
                if text == "/mcp auto off":
                    _mcp.set_hot_mode(False)
                    print("⏸ MCP hot mode OFF — manual only")
                    continue
                if text == "/mcp list":
                    print(_mcp.format_status())
                    continue

                # ── MCP hot connect: check if query needs MCP tools ──
                _mcp_needs_disconnect = False
                if not text.startswith("/"):
                    # Non-command: check if MCP tools are relevant
                    try:
                        import asyncio as _mcp_asyncio
                        _mcp_loop = _mcp_asyncio.new_event_loop()
                        connected = _mcp_loop.run_until_complete(
                            _mcp.connect_if_needed(text)
                        )
                        _mcp_loop.close()
                        if connected:
                            _mcp_needs_disconnect = True
                            logger.info("MCP hot: connected %s for query", connected)
                    except Exception as _mcp_e:
                        logger.debug("MCP hot connect: %s", _mcp_e)

                # ── Save to conversation history ──────────
                _history.add("user", text)

                # Process via modular EventBus
                outputs.clear()
                loop.run_until_complete(bus.publish(Event(USER_INPUT, {"text": text}, source="cli")))

                # Wait for response
                import time
                deadline = time.time() + 60
                last_len = 0
                while time.time() < deadline:
                    if len(outputs) > last_len:
                        for i in range(last_len, len(outputs)):
                            out_text = outputs[i].get("text", "")
                            print()
                            print(out_text)
                            print()
                            # Save assistant response to history
                            if out_text and not any(
                                out_text.startswith(prefix)
                                for prefix in ["📋", "📊", "💡", "ℹ", "⚠", "✅", "❌", "📝", "⏳", "🔴", "🟡", "🟢", "⚪"]
                            ):
                                _history.add("assistant", out_text)
                        last_len = len(outputs)
                        break
                    loop.run_until_complete(asyncio.sleep(0.1))

                # MCP hot disconnect: clean up after query completes
                if _mcp_needs_disconnect:
                    try:
                        _mcp_dc_loop = asyncio.new_event_loop()
                        _mcp_dc_loop.run_until_complete(_mcp.disconnect_idle())
                        _mcp_dc_loop.close()
                        _mcp_needs_disconnect = False
                    except Exception:
                        pass

            except KeyboardInterrupt:
                # MCP cleanup
                if _mcp_needs_disconnect:
                    try:
                        _mcp_dc_loop = asyncio.new_event_loop()
                        _mcp_dc_loop.run_until_complete(_mcp.disconnect_idle())
                        _mcp_dc_loop.close()
                    except Exception:
                        pass
                print()
                break
            except EOFError:
                if _mcp_needs_disconnect:
                    try:
                        _mcp_dc_loop = asyncio.new_event_loop()
                        _mcp_dc_loop.run_until_complete(_mcp.disconnect_idle())
                        _mcp_dc_loop.close()
                    except Exception:
                        pass
                break
            except Exception as e:
                print(f"⚠ Error: {e}")
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
        print(f"🔌 Provider system: ✅ available")
    except ImportError:
        print(f"🔌 Provider system: ❌ not available (install hermes-agent)")

    # Telegram gateway status
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if tg_token:
        print(f"📱 Telegram: ✅ token found, chat_id={tg_chat or 'not set'}")
    else:
        print(f"📱 Telegram: ⚠ token not set (TELEGRAM_BOT_TOKEN)")

    print()


if __name__ == "__main__":
    main()