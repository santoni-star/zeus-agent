"""Memory extractor — extracts and saves key facts from conversation turns."""

from __future__ import annotations
from zeus.memory.session import SessionStore


def extract_facts(store: SessionStore, user_input: str, assistant_output: str):
    """Extract and save key facts from a turn.

    Uses heuristic patterns rather than an LLM to keep Phase 0 simple.

    Saved facts:
    - User's stated goals/intents (if explicit)
    - Tools used (from DAG results)
    - Key results (numbers, file paths, URLs)
    """
    text = user_input + " " + assistant_output

    # Extract file paths (absolute paths with /data/ or /home/ or ~/)
    import re
    paths = re.findall(r'(?:^|\s)(/~?[/\w.-]+(?:\.\w+)?)', text)
    for p in paths:
        p = p.strip()
        if '/' in p and len(p) > 5:
            store.save_fact(p, "file_path", entities=["filesystem"])

    # Save full turn as a general fact
    store.save_fact(
        f"User: {user_input[:200]}",
        "interaction",
        entities=["user_query"],
    )


def save_task_result(store: SessionStore, goal: str, dag_results: list[dict]):
    """Save task execution results as facts.

    Args:
        store: Session store
        goal: Task goal from Planner
        dag_results: List of DAG node results
    """
    # Save the goal
    store.save_fact(goal, "task_goal", entities=["task"])

    # Save each successful tool result
    for r in dag_results:
        if r.get("success") and not r.get("error"):
            node_id = r.get("node_id", "?")
            store.save_fact(
                f"{node_id}: completed in {r.get('duration_ms', 0):.0f}ms",
                "task_step",
                entities=["task", node_id],
            )
        elif r.get("error"):
            node_id = r.get("node_id", "?")
            err = r["error"][:100]
            store.save_fact(f"{node_id} failed: {err}", "error", entities=["error"])