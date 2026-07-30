"""Planner — converts a user request into a Task DAG via one LLM call."""

from __future__ import annotations
import json

from zeus.models import TaskDAG, DAGNode


PLANNER_SYSTEM_PROMPT = """Ти — Planner агента Zeus. Твоя задача: перетворити запит користувача в граф задач (Task DAG).

Ти отримуєш:
1. Запит користувача
2. Список доступних інструментів з їхніми схемами

Ти повертаєш:
JSON з Task DAG. Формат:
{
  "goal": "string — мета, перефразована коротко",
  "nodes": [
    {
      "id": "string — унікальний ідентифікатор ноди",
      "type": "tool | llm | wait | merge",
      "tool": "ім'я інструмента (тільки для type=tool)",
      "params": {ключ: значення} — параметри для інструмента",
      "depends_on": ["id_іншої_ноди"] — від чого залежить (порожній для перших нод),
      "success_criteria": "як визначити що успіх (опціонально)",
      "retry": 2 — скільки разів повторити при помилці,
      "timeout": 60 — таймаут в секундах (опціонально)
    }
  ]
}

ПРАВИЛА:
1. Не створюй циклів (A залежить від B, B залежить від A).
2. Незалежні задачі можуть мати однаковий depends_on або порожній.
3. Використовуй тільки інструменти зі списку. Не вигадуй свої.
4. Для підзадач які потребують додаткового LLM — став type="llm".
5. Для об'єднання результатів — став type="merge".
6. Відповідь — ТІЛЬКИ JSON, без додаткового тексту.
7. Якщо задача проста (один крок) — створи одну ноду.
8. Якщо задача потребує пошуку → читання → дії — створи DAG."""


def plan(text: str, tools: list[dict], llm_call) -> TaskDAG | None:
    """Plan a task by calling the LLM once to generate a Task DAG.

    Args:
        text: User request.
        tools: List of tool schemas available.
        llm_call: Function to call LLM. Signature: llm_call(messages, tools) -> str

    Returns:
        TaskDAG or None if planning failed.
    """
    tools_json = json.dumps(tools, indent=2, ensure_ascii=False)
    user_prompt = f"""Запит користувача: {text}

Доступні інструменти:
{tools_json}"""

    response = llm_call(
        messages=[
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        tools=None,
    )

    # Parse JSON from response
    try:
        # Try direct JSON parse first
        parsed = json.loads(response)
    except json.JSONDecodeError:
        # Try to extract JSON block from markdown
        import re
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1))
            except json.JSONDecodeError:
                return None
        else:
            # Last resort: find first { and last }
            start = response.find('{')
            end = response.rfind('}')
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(response[start:end+1])
                except json.JSONDecodeError:
                    return None
            else:
                return None

    try:
        dag = TaskDAG.from_dict(parsed)
        errors = dag.validate()
        if errors:
            print(f"⚠ DAG validation errors: {errors}")
        return dag
    except Exception as e:
        print(f"⚠ Failed to parse DAG: {e}")
        return None