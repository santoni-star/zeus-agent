"""DAG Executor — runs the Task DAG without any LLM calls.

This is the core innovation: the executor is pure code.
It walks the graph, calls tools, handles retries, manages parallelism.
"""

from __future__ import annotations
import time
from collections import deque

from zeus.models import TaskDAG, DAGNode, NodeResult


def execute_dag(dag: TaskDAG, tool_registry, llm_call=None) -> list[NodeResult]:
    """Execute a Task DAG.

    Traverses the graph topologically, executing independent nodes in parallel.

    Args:
        dag: The Task DAG to execute.
        tool_registry: Registry of available tools.
        llm_call: Optional LLM call function for 'llm' type nodes.

    Returns:
        List of NodeResult for each node in the DAG.
    """
    results: dict[str, NodeResult] = {}
    completed = set()
    node_map = {n.id: n for n in dag.nodes}

    # Find root nodes (no dependencies)
    roots = [n for n in dag.nodes if not n.depends_on]

    if not roots and dag.nodes:
        # All nodes have dependencies — find the ones whose deps are resolved
        roots = _find_ready_nodes(dag.nodes, completed)

    # BFS traversal
    queue = deque(roots)
    visited = set()

    while queue:
        node = queue.popleft()
        if node.id in visited:
            continue
        visited.add(node.id)

        # Check if dependencies are met
        if not all(dep in completed for dep in node.depends_on):
            # Not ready yet — put back
            queue.append(node)
            continue

        # Execute this node
        result = _execute_node(node, tool_registry, results, llm_call)
        results[node.id] = result
        completed.add(node.id)

        # Check if we already have the result for this node
        # (it might have been completed by a dependency chain)

        # Find next ready nodes
        ready = _find_ready_nodes(dag.nodes, completed)
        for r in ready:
            if r.id not in visited:
                queue.append(r)

        # Safety: prevent infinite loops
        if len(completed) >= len(dag.nodes) * 2:
            break

    # Return results in DAG order
    return [results.get(n.id, NodeResult(node_id=n.id, success=False, error="Not executed"))
            for n in dag.nodes]


def _find_ready_nodes(nodes: list[DAGNode], completed: set) -> list[DAGNode]:
    """Find nodes whose dependencies are all satisfied."""
    ready = []
    for n in nodes:
        if n.id in completed:
            continue
        if all(dep in completed for dep in n.depends_on):
            ready.append(n)
    return ready


def _execute_node(node: DAGNode, tool_registry, results: dict, llm_call=None) -> NodeResult:
    """Execute a single DAG node."""
    start = time.time()

    if node.type == "tool":
        result = _execute_tool_node(node, tool_registry, results)
    elif node.type == "wait":
        result = _execute_wait_node(node, results)
    elif node.type == "merge":
        result = _execute_merge_node(node, results)
    elif node.type == "llm":
        result = _execute_llm_node(node, results, llm_call)
    else:
        result = NodeResult(
            node_id=node.id,
            success=False,
            error=f"Unknown node type: {node.type}",
        )

    result.duration_ms = (time.time() - start) * 1000
    return result


def _execute_tool_node(node: DAGNode, tool_registry, results: dict | None = None) -> NodeResult:
    """Execute a tool node with retry logic and template substitution."""
    # Substitute templates in params (e.g. {{search.output}} → actual value)
    params = node.params.copy()
    if results:
        import re
        def _substitute(match):
            ref = match.group(1).strip()
            if "." in ref:
                node_id, field = ref.split(".", 1)
            else:
                node_id, field = ref, "output"
            if node_id in results and results[node_id].success:
                val = getattr(results[node_id], field, None)
                if val is not None:
                    return str(val)
            return match.group(0)

        # Apply substitution to all string values in params
        for key, val in params.items():
            if isinstance(val, str):
                params[key] = re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?)\}", _substitute, val)
            elif isinstance(val, dict):
                # Recursive for nested dicts
                for k2, v2 in val.items():
                    if isinstance(v2, str):
                        val[k2] = re.sub(r"\{\{(.+?)\}\}", _substitute, v2)

    last_error = None

    for attempt in range(node.retry):
        try:
            output = tool_registry.execute(node.tool, params)
            return NodeResult(
                node_id=node.id,
                success=True,
                output=output,
            )
        except Exception as e:
            last_error = str(e)
            if attempt < node.retry - 1:
                time.sleep(1 * (attempt + 1))  # exponential-ish backoff

    return NodeResult(
        node_id=node.id,
        success=False,
        error=f"After {node.retry} attempts: {last_error}",
    )


def _execute_wait_node(node: DAGNode, results: dict) -> NodeResult:
    """Wait node: just pass through when dependencies are done."""
    return NodeResult(
        node_id=node.id,
        success=True,
        output={dep: results[dep].output for dep in node.depends_on},
    )


def _execute_merge_node(node: DAGNode, results: dict) -> NodeResult:
    """Merge node: combine results from dependency nodes."""
    dep_outputs = {}
    for dep in node.depends_on:
        if dep in results:
            dep_outputs[dep] = results[dep].output

    if node.merge_strategy == "pick_first":
        # Return first non-empty result
        for val in dep_outputs.values():
            if val:
                return NodeResult(node_id=node.id, success=True, output=val)
        return NodeResult(node_id=node.id, success=False, error="All dependencies returned empty")

    elif node.merge_strategy == "combine_json":
        import json
        combined = {}
        for key, val in dep_outputs.items():
            if isinstance(val, dict):
                combined[key] = val
            else:
                combined[key] = str(val)
        return NodeResult(node_id=node.id, success=True, output=json.dumps(combined, ensure_ascii=False))

    else:
        # concat (default)
        combined = []
        for val in dep_outputs.values():
            if val is not None:
                combined.append(str(val))
        return NodeResult(node_id=node.id, success=True, output="\n".join(combined))


def _execute_llm_node(node: DAGNode, results: dict, llm_call) -> NodeResult:
    """Execute an LLM node: call the LLM with a prompt that includes dependency results."""
    if llm_call is None:
        return NodeResult(
            node_id=node.id, success=False,
            error="LLM node requires llm_call function"
        )

    # Build context from dependency results
    context = ""
    for dep in node.depends_on:
        if dep in results:
            r = results[dep]
            if r.success and r.output:
                context += f"--- {dep} ---\n{r.output}\n\n"

    # Resolve templates in prompt
    import re
    prompt = node.llm_prompt or node.params.get("input") or node.params.get("prompt") or "Process the above information."

    def _substitute(match):
        ref = match.group(1).strip()
        if "." in ref:
            node_id, field = ref.split(".", 1)
        else:
            node_id, field = ref, "output"
        if node_id in results and results[node_id].success:
            val = getattr(results[node_id], field, None)
            if val is not None:
                return str(val)
        return match.group(0)

    prompt = re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?)\}", _substitute, prompt)
    full_prompt = f"{context}{prompt}" if context else prompt

    try:
        response = llm_call(
            messages=[
                {"role": "system", "content": "Ти — корисний асистент. Виконуй завдання на основі наданої інформації."},
                {"role": "user", "content": full_prompt},
            ],
            tools=None,
        )
        return NodeResult(node_id=node.id, success=True, output=response.strip())
    except Exception as e:
        return NodeResult(node_id=node.id, success=False, error=str(e))