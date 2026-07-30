"""Zeus Agent — CLI entry point.

Usage:
    python -m zeus "your request here"
    python -m zeus --interactive
    python -m zeus --provider openrouter --model deepseek/deepseek-v4-flash-free "search for latest python"
"""

from __future__ import annotations
import argparse
import os
import sys

from zeus.classifier import classify
from zeus.router import route
from zeus.models.types import ToolRegistry
from zeus.tools.terminal import execute as terminal_execute, SCHEMA as terminal_schema
from zeus.tools.file import execute as file_execute, SCHEMA as file_schema
from zeus.tools.web import execute as web_execute, SCHEMA as web_schema
from zeus.providers import (
    make_llm_call,
    configure_from_env,
    list_providers,
    list_models,
)
import zeus.providers as _providers  # for constants

_llm_call = None
_tool_registry = None


def setup_tools() -> ToolRegistry:
    """Initialize the tool registry with all available tools."""
    registry = ToolRegistry()
    registry.register("terminal", terminal_schema, terminal_execute)
    registry.register("file", file_schema, file_execute)
    registry.register("web_search", web_schema, web_execute)
    return registry


def process(text: str, registry: ToolRegistry) -> str:
    """Process a user request through the full Zeus pipeline."""
    global _llm_call

    # 1. Classify
    classification = classify(text)

    # 2. Route + execute
    result = route(
        classification=classification,
        tool_registry=registry,
        llm_call=_llm_call,
    )

    # Format output
    output = []
    if result.dag_result:
        total_ms = sum(n["duration_ms"] for n in result.dag_result)
        output.append(f"⚡ DAG: {len(result.dag_result)} nodes, {total_ms:.0f}ms")
        for n in result.dag_result:
            icon = "✅" if n["success"] else "❌"
            output.append(f"   {icon} {n['node_id']} ({n['duration_ms']:.0f}ms)")
        output.append("")

    output.append(result.output)
    return "\n".join(output)


def show_providers():
    """Display available LLM providers."""
    providers = list_providers()
    if providers:
        print("\nAvailable providers:")
        for p in providers[:30]:  # limit display
            name = p.get("name", "?")
            display = p.get("display_name") or p.get("description", "")
            print(f"  • {name}{' — ' + display if display else ''}")
    else:
        print("\n⚠ No providers found. Install hermes-agent or configure manually.")
    print()


def main():
    global _llm_call, _tool_registry

    parser = argparse.ArgumentParser(
        description="Zeus Agent — next-gen AI agent framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m zeus "hello"
  python -m zeus "list files in current dir"
  python -m zeus --interactive
  python -m zeus --provider openrouter "search python 3.13"
  python -m zeus --providers          # list available providers
        """,
    )
    parser.add_argument("query", nargs="?", help="Single query (non-interactive)")
    parser.add_argument("-i", "--interactive", action="store_true",
                        help="Interactive mode")
    parser.add_argument("--provider", default=None,
                        help="LLM provider (e.g. openai, openrouter, anthropic)")
    parser.add_argument("--model", default=None,
                        help="LLM model (e.g. gpt-4o, claude-sonnet-4)")
    parser.add_argument("--api-key", default=None,
                        help="API key (default: env ZEUS_LLM_API_KEY)")
    parser.add_argument("--base-url", default=None,
                        help="Base URL override")
    parser.add_argument("--providers", action="store_true",
                        help="List available LLM providers and exit")
    parser.add_argument("--doctor", action="store_true",
                        help="Check Zeus configuration")

    args = parser.parse_args()

    # Special commands
    if args.providers:
        show_providers()
        return

    if args.doctor:
        show_doctor()
        return

    # Initialize tool registry
    _tool_registry = setup_tools()

    # Initialize LLM — auto-configure from Hermes if possible
    if args.provider or args.model or args.api_key:
        _llm_call = make_llm_call(
            provider=args.provider,
            model=args.model,
            api_key=args.api_key,
            base_url=args.base_url,
        )
    elif _providers._DEFAULT_API_KEY:
        _llm_call = make_llm_call(
            provider=_providers._DEFAULT_PROVIDER,
            model=_providers._DEFAULT_MODEL,
            api_key=_providers._DEFAULT_API_KEY,
        )
        if args.query or args.interactive:
            print(f"⚡ Auto-configured: {_providers._DEFAULT_PROVIDER}/{_providers._DEFAULT_MODEL}")
    elif os.environ.get("ZEUS_LLM_API_KEY"):
        _llm_call = configure_from_env()

    if args.query:
        result = process(args.query, _tool_registry)
        print(result)

    elif args.interactive:
        print("╔══════════════════════════════════════════════╗")
        print("║           Zeus Agent — Phase 0              ║")
        print("║       'exit', '/quit' to quit               ║")
        if _llm_call:
            print("║       LLM: ✅ configured                    ║")
        else:
            print("║       LLM: ⚠ not set (env ZEUS_LLM_API_KEY) ║")
        print("╚══════════════════════════════════════════════╝")
        print()

        while True:
            try:
                text = input("⚡ ").strip()
                if text in ("exit", "quit", "/quit", "/exit"):
                    break
                if not text:
                    continue
                if text == "/providers":
                    show_providers()
                    continue

                result = process(text, _tool_registry)
                print()
                print(result)
                print()
            except KeyboardInterrupt:
                print()
                break
            except EOFError:
                break
            except Exception as e:
                print(f"⚠ Error: {e}")
    else:
        parser.print_help()


def show_doctor():
    """Check system configuration."""
    print("\n╔══════════════════════════════════════════════╗")
    print("║          Zeus Agent — Doctor                ║")
    print("╚══════════════════════════════════════════════╝")

    # Python version
    print(f"\n🐍 Python: {sys.version}")

    # LLM
    api_key_set = bool(_providers._DEFAULT_API_KEY)
    print(f"🔑 LLM API Key: {'✅ set' if api_key_set else '❌ not set'}")
    print(f"   Provider: {_providers._DEFAULT_PROVIDER}")
    print(f"   Model: {_providers._DEFAULT_MODEL}")
    if _llm_call:
        print(f"   Status: ✅ configured and ready")
    elif api_key_set:
        print(f"   Status: ⚠ key found — use --interactive or pass a query")
    else:
        print(f"   Status: ❌ no API key found")

    # Tools
    if _tool_registry:
        print(f"🔧 Tools: {', '.join(_tool_registry.names())}")

    # Hermes
    hermes_path = os.path.expanduser("~/.hermes")
    if os.path.exists(hermes_path):
        print(f"📦 Hermes: ✅ found at {hermes_path}")
    else:
        print(f"📦 Hermes: ❌ not found")

    # Provider system
    try:
        import providers
        print(f"🔌 Provider system: ✅ available")
    except ImportError:
        print(f"🔌 Provider system: ❌ not available (install hermes-agent)")

    print()


if __name__ == "__main__":
    main()