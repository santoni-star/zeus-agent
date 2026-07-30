"""Task DAG — the core data structure of Zeus."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class NodeResult:
    """Result of executing a single DAG node."""
    node_id: str
    success: bool
    output: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0


@dataclass
class DAGNode:
    """A single node in the Task DAG."""
    id: str
    type: str  # 'tool' | 'llm' | 'wait' | 'merge'
    tool: Optional[str] = None      # tool name (for 'tool' nodes)
    params: dict = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    success_criteria: Optional[str] = None
    retry: int = 1
    timeout: Optional[int] = None
    llm_prompt: Optional[str] = None  # for 'llm' nodes
    merge_strategy: str = "concat"    # for 'merge' nodes: concat | pick_first | combine_json


@dataclass
class TaskDAG:
    """The complete task plan: a DAG of nodes + metadata."""
    goal: str
    nodes: list[DAGNode] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def node_by_id(self, node_id: str) -> Optional[DAGNode]:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def validate(self) -> list[str]:
        """Validate DAG structure. Returns list of errors (empty = valid)."""
        errors = []
        node_ids = {n.id for n in self.nodes}

        for n in self.nodes:
            for dep in n.depends_on:
                if dep not in node_ids:
                    errors.append(f"Node '{n.id}': depends on missing node '{dep}'")
            if n.type not in ('tool', 'llm', 'wait', 'merge'):
                errors.append(f"Node '{n.id}': unknown type '{n.type}'")
            if n.type == 'tool' and not n.tool:
                errors.append(f"Node '{n.id}': tool type but no tool specified")

        # Check for cycles via DFS
        visited = set()
        in_stack = set()

        def _has_cycle(node_id: str) -> bool:
            visited.add(node_id)
            in_stack.add(node_id)
            node = self.node_by_id(node_id)
            if node:
                for dep in node.depends_on:
                    if dep not in visited:
                        if _has_cycle(dep):
                            return True
                    elif dep in in_stack:
                        errors.append(f"Cycle detected involving '{node_id}' -> '{dep}'")
                        return True
            in_stack.discard(node_id)
            return False

        for n in self.nodes:
            if n.id not in visited:
                _has_cycle(n.id)

        return errors

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "nodes": [
                {
                    "id": n.id,
                    "type": n.type,
                    "tool": n.tool,
                    "params": n.params,
                    "depends_on": n.depends_on,
                    "success_criteria": n.success_criteria,
                    "retry": n.retry,
                    "timeout": n.timeout,
                    "llm_prompt": n.llm_prompt,
                    "merge_strategy": n.merge_strategy,
                }
                for n in self.nodes
            ],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TaskDAG:
        nodes = [
            DAGNode(
                id=n["id"],
                type=n["type"],
                tool=n.get("tool"),
                params=n.get("params", {}),
                depends_on=n.get("depends_on", []),
                success_criteria=n.get("success_criteria"),
                retry=n.get("retry", 1),
                timeout=n.get("timeout"),
                llm_prompt=n.get("llm_prompt"),
                merge_strategy=n.get("merge_strategy", "concat"),
            )
            for n in d.get("nodes", [])
        ]
        return cls(goal=d.get("goal", ""), nodes=nodes, metadata=d.get("metadata", {}))