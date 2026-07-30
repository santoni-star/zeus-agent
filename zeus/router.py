"""Router — directs classified intents to the right handler."""

from __future__ import annotations
import logging
import sys

from zeus.models.types import ClassificationResult, ExecutionResult
from zeus.planner import plan
from zeus.runtime import execute_dag
from zeus.synthesizer import synthesize

logger = logging.getLogger(__name__)


# Commands that should be executed directly (not via DAG)
_KNOWN_COMMANDS = {
    "cd", "ls", "cat", "rm", "mv", "cp", "mkdir", "touch",
    "pwd", "echo", "grep", "find", "git", "docker", "npm",
    "pip", "cargo", "chmod", "curl", "wget", "ps", "top",
    "kill", "python", "node", "go", "rustc", "make", "cmake",
    "which", "whereis", "head", "tail", "sort", "uniq", "wc",
    "tar", "gzip", "gunzip", "zip", "unzip", "ssh", "scp",
    "df", "du", "free", "uname", "env", "export", "alias",
    "ping", "nslookup", "dig", "traceroute", "netstat",
}


def route(
    classification: ClassificationResult,
    tool_registry=None,
    llm_call=None,
) -> ExecutionResult:
    """Route a classified input to the appropriate handler."""
    intent = classification.intent
    text = intent.raw_input
    first_word = text.strip().split()[0] if text.strip() else ""

    # Command: only if first word is a known binary
    if intent.type == "command" and first_word in _KNOWN_COMMANDS:
        return _handle_command(text)

    # else fall through to handler chain
    if intent.type == "simple_chat":
        return _handle_chat(text)

    elif intent.type == "simple_question" and llm_call:
        # If there are custom tools available, try Planner first
        if tool_registry and len(tool_registry.names()) > 3:
            return _handle_complex_task(text, tool_registry, llm_call)
        return _handle_question(text, llm_call)

    elif intent.type == "task_complex" or intent.type == "task_simple":
        return _handle_complex_task(text, tool_registry, llm_call)

    elif intent.type == "skill_search":
        return _handle_skill_search(text)

    elif intent.type == "system":
        return _handle_system(text)

    else:
        # Fallback: if LLM available, treat as task
        if llm_call:
            return _handle_complex_task(text, tool_registry, llm_call)
        return _handle_question(text, llm_call)


def _handle_command(text: str) -> ExecutionResult:
    """Execute a direct terminal command."""
    from zeus.tools.terminal import execute

    result = execute({"command": text})
    return ExecutionResult(
        success=True,
        output=result,
    )


def _handle_chat(text: str) -> ExecutionResult:
    """Handle casual conversation."""
    import random

    responses = {
        "привіт": ["Привіт! Чим можу допомогти?", "Вітаю! Що робимо?"],
        "хай": ["Хай! Як справи?", "Хей! Чим займемось?"],
        "hello": ["Hello! Ready to work.", "Hey! What's up?"],
        "дякую": ["Будь ласка! Завжди радий допомогти.", "Нема за що!"],
        "thanks": ["You're welcome!", "Happy to help!"],
        "як справи": ["У мене все добре! Працюю, як завжди. А в тебе?", "Все ок! Чим можу бути корисним?"],
    }

    text_lower = text.lower()
    for key, replies in responses.items():
        if key in text_lower:
            return ExecutionResult(success=True, output=random.choice(replies))

    return ExecutionResult(success=True, output="Чим можу допомогти?")


def _handle_question(text: str, llm_call) -> ExecutionResult:
    """Handle a simple factual question — direct LLM, no tools."""
    if llm_call is None:
        return ExecutionResult(success=True, output="Я не налаштований відповідати на питання без LLM (Phase 0).")

    response = llm_call(
        messages=[
            {"role": "system", "content": "Ти — Zeus Agent. Відповідай коротко і точно."},
            {"role": "user", "content": text},
        ],
        tools=None,
    )
    return ExecutionResult(success=True, output=response)


def _handle_skill_search(text: str) -> ExecutionResult:
    """Search for a Hermes skill."""
    return ExecutionResult(
        success=True,
        output="🔍 Пошук скілів — функція в розробці (Phase 0). Скоро буде!"
    )


def _handle_simple_task(text: str, tool_registry) -> ExecutionResult:
    """Handle a single-step task with one tool call."""
    return _handle_complex_task(text, tool_registry, None)


def _handle_complex_task(text: str, tool_registry, llm_call) -> ExecutionResult:
    """Handle a complex multi-step task via Planner + Runtime + Synthesizer."""
    import time

    if llm_call is None:
        return ExecutionResult(
            success=True,
            output="Для складних задач потрібен LLM. Налаштуй провайдера (Phase 0)."
        )

    start = time.time()

    # 1. Planner: generate Task DAG (filter tools by query relevance)
    tools_schemas = tool_registry.schemas(filter_query=text) if tool_registry else []
    dag = plan(text=text, tools=tools_schemas, llm_call=llm_call)

    if not dag:
        return ExecutionResult(
            success=False,
            output="Planner не зміг створити план для цієї задачі.",
        )

    # 2. Validate DAG against available tools
    errors = dag.validate()
    if errors:
        return ExecutionResult(
            success=False,
            output=f"План містить помилки: {'; '.join(errors)}",
        )

    # Check that all tool nodes reference real tools
    tool_names = set(tool_registry.names())
    bad_nodes = [
        n for n in dag.nodes
        if n.type == "tool" and n.tool not in tool_names
    ]
    if bad_nodes:
        bad_tools = [n.tool for n in bad_nodes]
        logger.warning("Planner made up tools: %s — falling back to find_api", bad_tools)
        # Fall back: try find_api(action='call') as a smarter alternative
        if tool_registry.get_schema("find_api"):
            try:
                from zeus.tools.find_api import execute as find_api_execute
                result_text = find_api_execute({
                    "action": "call",
                    "query": text,
                    "no_auth": False,
                    "https_only": True,
                })
                if not result_text.startswith("❌"):
                    return ExecutionResult(
                        success=True,
                        output=result_text,
                    )
            except Exception as fe:
                logger.debug("find_api fallback failed: %s", fe)

        return ExecutionResult(
            success=False,
            output=f"Планувальник створив неіснуючі інструменти: {bad_tools}. "
                   f"Доступні: {', '.join(sorted(tool_names)[:10])}...",
        )

    # 3. Execute DAG
    results = execute_dag(dag, tool_registry, llm_call=llm_call)

    # 4. Synthesize final response
    final = synthesize(
        goal=dag.goal,
        results=results,
        llm_call=llm_call,
    )

    duration = (time.time() - start) * 1000
    return ExecutionResult(
        success=all(r.success for r in results),
        output=final,
        duration_ms=duration,
        dag_result=[{
            "node_id": r.node_id,
            "success": r.success,
            "duration_ms": r.duration_ms,
            "error": r.error,
        } for r in results],
    )


def _handle_system(text: str) -> ExecutionResult:
    """Handle system commands."""
    return ExecutionResult(
        success=True,
        output="Zeus Agent — Phase 0. Працюю. Стан: ✅"
    )