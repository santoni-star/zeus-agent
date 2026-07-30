"""Router — directs classified intents to the right handler."""

from __future__ import annotations
import sys

from zeus.models.types import ClassificationResult, ExecutionResult
from zeus.planner import plan
from zeus.runtime import execute_dag
from zeus.synthesizer import synthesize


def route(
    classification: ClassificationResult,
    tool_registry=None,
    llm_call=None,
) -> ExecutionResult:
    """Route a classified input to the appropriate handler.

    Args:
        classification: Result from classifier.
        tool_registry: Available tools (for task execution).
        llm_call: Function to call LLM. Signature: llm_call(messages, tools) -> str

    Returns:
        ExecutionResult with final output.
    """
    intent = classification.intent
    text = intent.raw_input

    if intent.type == "command":
        return _handle_command(text)

    elif intent.type == "simple_chat":
        return _handle_chat(text)

    elif intent.type == "simple_question":
        return _handle_question(text, llm_call)

    elif intent.type == "skill_search":
        return _handle_skill_search(text)

    elif intent.type == "task_simple":
        return _handle_simple_task(text, tool_registry)

    elif intent.type == "task_complex":
        return _handle_complex_task(text, tool_registry, llm_call)

    elif intent.type == "system":
        return _handle_system(text)

    else:
        # Fallback: treat as question
        return _handle_question(text, llm_call)


def _handle_command(text: str) -> ExecutionResult:
    """Execute a direct terminal command."""
    from zeus.tools.terminal import execute

    # Strip leading 'cd ' etc. if it's just the command
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
        tools=None,  # no tools needed for simple questions
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
    # For Phase 0, escalate to complex task handler
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

    # 1. Planner: generate Task DAG
    tools_schemas = tool_registry.schemas() if tool_registry else []
    dag = plan(text=text, tools=tools_schemas, llm_call=llm_call)

    if not dag:
        return ExecutionResult(
            success=False,
            output="Planner не зміг створити план для цієї задачі.",
        )

    # 2. Validate DAG
    errors = dag.validate()
    if errors:
        return ExecutionResult(
            success=False,
            output=f"План містить помилки: {'; '.join(errors)}",
        )

    # 3. Execute DAG
    results = execute_dag(dag, tool_registry)

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