"""Synthesizer — takes DAG execution results and produces the final response.

One LLM call to combine all tool outputs into a coherent answer.
"""

from __future__ import annotations
import json

from zeus.models import NodeResult


SYNTHESIZER_PROMPT = """Ти — Synthesizer агента Zeus. Твоя задача: взяти результати виконання плану 
і перетворити їх у відповідь користувачу.

Ти отримуєш:
1. Початкову мету (goal)
2. Список виконаних кроків з результатами

Ти повертаєш:
Лаконічну, зрозумілу відповідь користувачу природною мовою.
Якщо якийсь крок не вдався — скажи про це чесно.
Якщо все добре — підсумуй результат."""


def synthesize(goal: str, results: list[NodeResult], llm_call) -> str:
    """Synthesize final response from DAG execution results.

    Args:
        goal: The original task goal.
        results: Results from DAG execution.
        llm_call: Function to call LLM.

    Returns:
        Final response string.
    """
    steps = []
    all_success = True

    for r in results:
        status = "✅" if r.success else "❌"
        steps.append(f"{status} {r.node_id} ({r.duration_ms:.0f}ms)")
        if not r.success and r.error:
            steps.append(f"   Error: {r.error}")

    summary = f"""Goal: {goal}

Execution Results:
{chr(10).join(steps)}

All successful: {all_success}"""

    if llm_call is None:
        # Fallback: simple concatenation without LLM
        output_parts = []
        for r in results:
            if r.success and r.output:
                output_parts.append(str(r.output))
        if output_parts:
            return "\n\n".join(output_parts)
        return summary

    # Use LLM for synthesis
    try:
        response = llm_call(
            messages=[
                {"role": "system", "content": SYNTHESIZER_PROMPT},
                {"role": "user", "content": summary + "\n\nНапиши відповідь користувачу."},
            ],
            tools=None,
        )
        return response.strip()
    except Exception as e:
        # Fallback
        return f"Виконано (без синтезу): {summary}"