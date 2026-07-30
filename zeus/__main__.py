"""Zeus Agent — CLI entry point.

Usage:
    python -m zeus "your request here"
    python -m zeus --interactive
"""

from __future__ import annotations
import argparse
import sys
import json

from zeus.classifier import classify
from zeus.router import route
from zeus.models.types import ToolRegistry, ExecutionResult
from zeus.tools.terminal import execute as terminal_execute, SCHEMA as terminal_schema
from zeus.tools.file import execute as file_execute, SCHEMA as file_schema


# Default LLM provider (needs to be configured)
_llm_call = None


def setup_tools() -> ToolRegistry:
    """Initialize the tool registry with all available tools."""
    registry = ToolRegistry()
    registry.register("terminal", terminal_schema, terminal_execute)
    registry.register("file", file_schema, file_execute)
    return registry


def default_llm(messages: list, tools: list | None = None) -> str:
    """Default LLM call — placeholder that needs a real provider.
    
    In Phase 1, this will connect to OpenAI/Anthropic/Local endpoint.
    Phase 0 returns a placeholder message.
    """
    return "⚠ LLM not configured. Set ZEUS_LLM_PROVIDER env var.\n" \
           "Phase 0 placeholder input:\n" + json.dumps(messages[-1:], ensure_ascii=False, indent=2)


def process(text: str, registry: ToolRegistry) -> ExecutionResult:
    """Process a user request through the full Zeus pipeline."""
    # 1. Classify
    classification = classify(text)
    
    # 2. Route + execute
    result = route(
        classification=classification,
        tool_registry=registry,
        llm_call=_llm_call or default_llm,
    )
    
    return result


def main():
    parser = argparse.ArgumentParser(description="Zeus Agent — next-gen AI agent framework")
    parser.add_argument("query", nargs="?", help="Single query (non-interactive)")
    parser.add_argument("-i", "--interactive", action="store_true", 
                        help="Interactive mode")
    parser.add_argument("--llm-provider", default=None,
                        help="LLM provider (openai, anthropic, openrouter)")
    parser.add_argument("--llm-model", default=None,
                        help="LLM model name")
    parser.add_argument("--llm-api-key", default=None,
                        help="LLM API key")
    
    args = parser.parse_args()
    
    registry = setup_tools()
    
    if args.query:
        result = process(args.query, registry)
        if result.dag_result:
            print(f"\n[DAG: {len(result.dag_result)} nodes, "
                  f"{sum(n['duration_ms'] for n in result.dag_result):.0f}ms]")
            for n in result.dag_result:
                icon = "✅" if n["success"] else "❌"
                print(f"  {icon} {n['node_id']} ({n['duration_ms']:.0f}ms)")
        print(f"\n{result.output}")
        
    elif args.interactive:
        print("╔══════════════════════════════════════════════╗")
        print("║           Zeus Agent — Phase 0              ║")
        print("║       'exit', '/quit' to quit               ║")
        print("╚══════════════════════════════════════════════╝")
        print()
        
        while True:
            try:
                text = input("⚡ ").strip()
                if text in ("exit", "quit", "/quit", "/exit"):
                    break
                if not text:
                    continue
                
                result = process(text, registry)
                print()
                print(result.output)
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


if __name__ == "__main__":
    main()