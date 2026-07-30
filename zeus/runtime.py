"""DAG Executor — runs the Task DAG without any LLM calls.

This is the core innovation: the executor is pure code.
It walks the graph, calls tools, handles retries, manages parallelism.
"""

from __future__ import annotations
import asyncio
import time
from collections import deque

from zeus.models import TaskDAG, DAGNode, NodeResult


def execute_dag(dag: TaskDAG, tool_registry, llm_call=None) -> list[NodeResult]:
    """Execute a Task DAG with per-sub-tree failure handling.

    Traverses the graph topologically, executing independent nodes in parallel.
    If a node fails, its dependents are cancelled but other branches continue.

    Args:
        dag: The Task DAG to execute.
        tool_registry: Registry of available tools.
        llm_call: Optional LLM call function for 'llm' type nodes.

    Returns:
        List of NodeResult for each node in the DAG.
    """
    results: dict[str, NodeResult] = {}
    completed = set()
    failed = set()
    cancelled = set()
    node_map = {n.id: n for n in dag.nodes}

    # Find root nodes (no dependencies)
    roots = [n for n in dag.nodes if not n.depends_on]

    if not roots and dag.nodes:
        roots = _find_ready_nodes(dag.nodes, completed, failed)

    # BFS traversal
    queue = deque(roots)
    visited = set()

    while queue:
        node = queue.popleft()
        if node.id in visited:
            continue
        visited.add(node.id)

        # Check if any dependency failed — cancel this node
        failed_deps = [dep for dep in node.depends_on if dep in failed]
        if failed_deps:
            results[node.id] = NodeResult(
                node_id=node.id,
                success=False,
                error=f"Cancelled: dependency {failed_deps[0]} failed",
            )
            cancelled.add(node.id)
            # Propagate cancellation to dependents
            _propagate_cancellation(node.id, dag.nodes, results, cancelled)
            continue

        # Check if dependencies are met
        if not all(dep in completed for dep in node.depends_on):
            # Not ready yet — put back
            queue.append(node)
            continue

        # Execute this node
        result = _execute_node(node, tool_registry, results, llm_call)
        results[node.id] = result

        if result.success:
            completed.add(node.id)
        else:
            failed.add(node.id)
            # Cancel dependents
            _propagate_cancellation(node.id, dag.nodes, results, cancelled)

        # Find next ready nodes
        ready = _find_ready_nodes(dag.nodes, completed, failed)
        for r in ready:
            if r.id not in visited:
                queue.append(r)

        # Safety: prevent infinite loops
        if len(completed) + len(failed) + len(cancelled) >= len(dag.nodes):
            break

    # Return results in DAG order, filling missing nodes as cancelled
    final_results = []
    for n in dag.nodes:
        if n.id in results:
            final_results.append(results[n.id])
        elif n.id in cancelled or any(dep in failed for dep in n.depends_on):
            final_results.append(NodeResult(
                node_id=n.id, success=False,
                error="Cancelled due to dependency failure"
            ))
        else:
            final_results.append(NodeResult(
                node_id=n.id, success=False,
                error="Not executed before termination"
            ))

    return final_results


def execute_dag_async(dag: TaskDAG, tool_registry, llm_call=None) -> list[NodeResult]:
    """Async version of execute_dag — runs independent nodes in parallel.

    Same semantics as execute_dag, but uses asyncio to execute
    ready nodes concurrently, improving throughput on I/O-bound tasks.

    Args:
        dag: The Task DAG to execute.
        tool_registry: Registry of available tools.
        llm_call: Optional LLM call function for 'llm' type nodes.

    Returns:
        List of NodeResult for each node in the DAG.
    """
    results: dict[str, NodeResult] = {}
    completed = set()
    failed = set()
    cancelled = set()

    # Find root nodes
    roots = [n for n in dag.nodes if not n.depends_on]
    if not roots and dag.nodes:
        roots = _find_ready_nodes(dag.nodes, completed, failed)

    queue = deque(roots)
    visited = set()

    while queue:
        node = queue.popleft()

        # Check if any dependency failed
        failed_deps = [dep for dep in node.depends_on if dep in failed]
        if failed_deps:
            results[node.id] = NodeResult(
                node_id=node.id, success=False,
                error=f"Cancelled: dependency {failed_deps[0]} failed",
            )
            cancelled.add(node.id)
            _propagate_cancellation(node.id, dag.nodes, results, cancelled)
            continue

        # Check if dependencies met
        if not all(dep in completed for dep in node.depends_on):
            queue.append(node)
            continue

        # Don't add popped node to visited yet — it goes into the parallel batch
        if node.id in results or node.id in visited:
            continue

        # Find ALL ready nodes and run them in parallel
        ready = _find_ready_nodes(dag.nodes, completed, failed)
        # Filter: only nodes not already processed
        ready = [r for r in ready if r.id not in visited and r.id not in completed and r.id not in results]

        if ready:
            # Run all ready nodes concurrently
            async def run_ready():
                tasks = []
                for r in ready:
                    visited.add(r.id)
                    tasks.append(asyncio.to_thread(_execute_node, r, tool_registry, dict(results), llm_call))
                node_results = await asyncio.gather(*tasks, return_exceptions=True)

                for r, nr in zip(ready, node_results):
                    if isinstance(nr, NodeResult):
                        results[r.id] = nr
                        if nr.success:
                            completed.add(r.id)
                        else:
                            failed.add(r.id)
                            _propagate_cancellation(r.id, dag.nodes, results, cancelled)
                    else:
                        # Exception was raised
                        results[r.id] = NodeResult(
                            node_id=r.id, success=False,
                            error=f"Thread exception: {nr}",
                        )
                        failed.add(r.id)
                        _propagate_cancellation(r.id, dag.nodes, results, cancelled)

                # Find next level
                next_ready = _find_ready_nodes(dag.nodes, completed, failed)
                for nr in next_ready:
                    if nr.id not in visited and nr.id not in completed:
                        queue.append(nr)

            asyncio.run(run_ready())

        # Safety check
        if len(completed) + len(failed) + len(cancelled) >= len(dag.nodes):
            break

    # Return results in DAG order
    final_results = []
    for n in dag.nodes:
        if n.id in results:
            final_results.append(results[n.id])
        elif n.id in cancelled:
            final_results.append(NodeResult(
                node_id=n.id, success=False,
                error="Cancelled due to dependency failure"
            ))
        else:
            final_results.append(NodeResult(
                node_id=n.id, success=False,
                error="Not executed before termination"
            ))

    return final_results


def _propagate_cancellation(failed_node_id: str, nodes: list, results: dict, cancelled: set):
    """Mark all dependents of a failed node as cancelled."""
    for n in nodes:
        if failed_node_id in n.depends_on and n.id not in results:
            cancelled.add(n.id)
            results[n.id] = NodeResult(
                node_id=n.id,
                success=False,
                error=f"Cancelled: dependency '{failed_node_id}' failed",
            )
            # Recurse for deeper dependents
            _propagate_cancellation(n.id, nodes, results, cancelled)


def _find_ready_nodes(nodes: list[DAGNode], completed: set, failed: set | None = None) -> list[DAGNode]:
    """Find nodes whose dependencies are all satisfied."""
    failed = failed or set()
    ready = []
    for n in nodes:
        if n.id in completed or n.id in failed:
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