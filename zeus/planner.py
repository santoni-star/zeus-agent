"""Planner — converts a user request into a Task DAG via LLM or fast path.

Self-tuning: tracks success rates per strategy and adapts.
Fast path: simple queries skip LLM Planner → direct 1-node DAG.
"""

from __future__ import annotations
import json
import re
import time
from collections import defaultdict
from typing import Any

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


# ── Self-tuning data ───────────────────────────────────────

_STRATEGY_STATS: dict[str, dict] = defaultdict(lambda: {"attempts": 0, "successes": 0, "total_dur_ms": 0})

# Fast-path patterns: directly mapped to tool + params
_FAST_PATH_PATTERNS: list[tuple[re.Pattern, str, dict | None]] = [
    # Currency conversion: "257 USD to PLN" → currency_converter
    (re.compile(r'^(\d+\.?\d*)\s+([A-Za-z]{3})\s+(?:to|in|->|в)\s+([A-Za-z]{3})$'), "currency_converter", None),
    # Simple search: "search X" or "find X"
    (re.compile(r'^(?:search|find|look up)\s+(.+)$', re.IGNORECASE), "web_search", None),
    # File read: "read /path" or "cat /path"
    (re.compile(r'^(?:read|cat)\s+(.+)$', re.IGNORECASE), "file", {"action": "read"}),
    # File list: "list files" or "ls /path"
    (re.compile(r'^(?:list|ls)\s+(.+)$', re.IGNORECASE), "file", {"action": "list"}),
    # Terminal command: "run command"
    (re.compile(r'^run\s+(.+)$', re.IGNORECASE), "terminal", None),
]


def record_strategy(strategy: str, success: bool, duration_ms: float):
    """Record planner strategy performance for self-tuning."""
    stats = _STRATEGY_STATS[strategy]
    stats["attempts"] += 1
    if success:
        stats["successes"] += 1
    stats["total_dur_ms"] += duration_ms


def get_strategy_stats() -> dict:
    """Get self-tuning statistics."""
    return dict(_STRATEGY_STATS)


def get_best_strategy(task_text: str) -> str | None:
    """Return the best strategy for a task based on historical data."""
    # If we have data and fast_path has >80% success, prefer it for matching tasks
    for strat, stats in _STRATEGY_STATS.items():
        if strat == "fast_path" and stats["attempts"] >= 3:
            success_rate = stats["successes"] / stats["attempts"]
            if success_rate >= 0.8:
                # Check if current task matches fast path
                for pattern, *_ in _FAST_PATH_PATTERNS:
                    if pattern.match(task_text.strip()):
                        return "fast_path"
    return None


def _build_fast_path_dag(text: str, tools: list[dict]) -> TaskDAG | None:
    """Try to build a 1-node DAG without calling the LLM.

    Returns TaskDAG if the query matches a fast-path pattern, None otherwise.
    """
    text_stripped = text.strip()

    for pattern, tool_name, fixed_params in _FAST_PATH_PATTERNS:
        m = pattern.match(text_stripped)
        if not m:
            continue

        # Check if the tool is available
        tool_available = any(s.get("name") == tool_name for s in tools)
        if not tool_available:
            continue

        # Build parameters
        params = {}

        # Extract the matched groups
        groups = m.groups()

        if tool_name == "currency_converter":
            if len(groups) >= 3:
                params["amount"] = float(groups[0])
                params["from_currency"] = groups[1].upper()
                params["to_currency"] = groups[2].upper()
            else:
                continue
            goal = f"Convert {params['amount']} {params['from_currency']} to {params['to_currency']}"

        elif tool_name == "web_search":
            params["query"] = groups[0] if groups else text
            goal = f"Search for: {params['query']}"

        elif tool_name == "file":
            if fixed_params:
                params.update(fixed_params)
            if groups:
                params["path"] = groups[0]
            goal = f"{fixed_params.get('action', 'read')} file: {params.get('path', '')}"

        elif tool_name == "terminal":
            params["command"] = groups[0] if groups else text
            goal = f"Run: {params['command']}"

        else:
            goal = text

        node_id = tool_name.replace("_", "_")
        node = DAGNode(
            id=node_id,
            type="tool",
            tool=tool_name,
            params=params,
            depends_on=[],
            retry=1,
            timeout=30,
        )

        dag = TaskDAG(goal=goal, nodes=[node])
        errors = dag.validate()
        if not errors:
            return dag

    return None


# ── Main planner ────────────────────────────────────────────

def plan(text: str, tools: list[dict], llm_call) -> TaskDAG | None:
    """Plan a task.

    Strategy:
      1. Try fast path (no LLM) for simple queries
      2. If fast path fails or query is complex → LLM Planner

    Returns:
        TaskDAG or None if planning failed.
    """
    start = time.time()
    strategy = "fast_path"

    # Try fast path first
    dag = _build_fast_path_dag(text, tools)
    if dag:
        duration = (time.time() - start) * 1000
        record_strategy("fast_path", True, duration)
        return dag

    strategy = "llm_planner"

    # Fall back to LLM Planner
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
        parsed = json.loads(response)
    except json.JSONDecodeError:
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1))
            except json.JSONDecodeError:
                duration = (time.time() - start) * 1000
                record_strategy(strategy, False, duration)
                return None
        else:
            start_idx = response.find('{')
            end_idx = response.rfind('}')
            if start_idx >= 0 and end_idx > start_idx:
                try:
                    parsed = json.loads(response[start_idx:end_idx + 1])
                except json.JSONDecodeError:
                    duration = (time.time() - start) * 1000
                    record_strategy(strategy, False, duration)
                    return None
            else:
                duration = (time.time() - start) * 1000
                record_strategy(strategy, False, duration)
                return None

    try:
        dag = TaskDAG.from_dict(parsed)
        errors = dag.validate()
        if errors:
            print(f"⚠ DAG validation errors: {errors}")
        duration = (time.time() - start) * 1000
        record_strategy(strategy, True, duration)
        return dag
    except Exception as e:
        print(f"⚠ Failed to parse DAG: {e}")
        duration = (time.time() - start) * 1000
        record_strategy(strategy, False, duration)
        return None