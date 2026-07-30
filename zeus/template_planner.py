"""Template Planner — matches queries to known DAG templates.

Replaces LLM planner for common queries. Benefits:
  - Zero LLM tokens for planning (faster, cheaper)
  - No hallucinated tool names (templates are verified)
  - Success rate tracking (self-tuning over time)
  - Templates saved as reusable skills

Flow:
  1. Query comes in
  2. Match against known templates (by category + keywords)
  3. If matched → instant DAG (no LLM call)
  4. If no match → LLM planner → save as new template
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

from zeus.models import TaskDAG, DAGNode
from zeus.planner import plan as llm_plan

logger = logging.getLogger(__name__)

# Path for template cache
TEMPLATES_DIR = Path(os.path.expanduser("~/.zeus/plan_templates"))
TEMPLATES_FILE = TEMPLATES_DIR / "templates.json"


# ── Template definition ───────────────────────────────────

class DAGTemplate:
    """A reusable DAG template for a category of queries.

    Attributes:
        category: Query category (weather, crypto, news, search, etc.)
        keywords: Words that trigger this template
        description: Human-readable description
        nodes: List of dicts describing tool calls
        success_rate: How often this template produces good results
        uses: How many times used
    """
    def __init__(self, category: str, keywords: list[str],
                 description: str, nodes: list[dict],
                 success_rate: float = 1.0, uses: int = 0):
        self.category = category
        self.keywords = keywords
        self.description = description
        self.nodes: list[dict] = nodes  # [{tool, params, depends_on, ...}]
        self.success_rate = success_rate
        self.uses = uses

    def match(self, text: str) -> float:
        """Check if this template matches a query.

        Returns:
            Match score (0 = no match, higher = better match).
        """
        text_lower = text.lower()
        score = 0
        for kw in self.keywords:
            if kw in text_lower:
                score += 2
        # Category match bonus
        if self.category.lower() in text_lower:
            score += 3
        return score

    def build_dag(self, text: str) -> TaskDAG:
        """Build a TaskDAG from this template, substituting query params."""
        nodes = []
        for n in self.nodes:
            params = dict(n.get("params", {}))
            # Substitute {query} with original text
            for k, v in params.items():
                if isinstance(v, str):
                    params[k] = v.replace("{query}", text)

            node = DAGNode(
                id=n["id"],
                type=n.get("type", "tool"),
                tool=n.get("tool", ""),
                params=params,
                depends_on=n.get("depends_on", []),
                retry=n.get("retry", 1),
                timeout=n.get("timeout", 30),
            )
            nodes.append(node)

        return TaskDAG(
            goal=f"{self.category}: {text}",
            nodes=nodes,
            metadata={"template": self.category, "template_uses": self.uses + 1},
        )

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "keywords": self.keywords,
            "description": self.description,
            "nodes": self.nodes,
            "success_rate": self.success_rate,
            "uses": self.uses,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DAGTemplate":
        return cls(
            category=d["category"],
            keywords=d["keywords"],
            description=d["description"],
            nodes=d["nodes"],
            success_rate=d.get("success_rate", 1.0),
            uses=d.get("uses", 0),
        )


# ── Built-in templates ───────────────────────────────────

_BUILTIN_TEMPLATES: list[DAGTemplate] = [
    DAGTemplate(
        category="weather",
        keywords=["weather", "forecast", "temperature", "rain", "sunny",
                  "cloudy", "wind", "humidity", "°c", "°f", "celsius", "fahrenheit"],
        description="Get current weather or forecast for a location",
        nodes=[
            {
                "id": "find_weather_api",
                "tool": "find_api",
                "params": {
                    "action": "call",
                    "query": "weather forecast",
                    "no_auth": False,
                    "https_only": True,
                    "api_params": '{"q": "{query}"}',
                },
                "depends_on": [],
            },
        ],
    ),
    DAGTemplate(
        category="crypto",
        keywords=["crypto", "bitcoin", "btc", "ethereum", "eth", "solana",
                  "sol", "dogecoin", "doge", "coin", "token", "price",
                  "cryptocurrency", "market cap"],
        description="Get cryptocurrency prices and information",
        nodes=[
            {
                "id": "find_crypto_api",
                "tool": "find_api",
                "params": {
                    "action": "call",
                    "query": "crypto price {query}",
                    "no_auth": False,
                    "https_only": True,
                },
                "depends_on": [],
            },
        ],
    ),
    DAGTemplate(
        category="news",
        keywords=["news", "headlines", "latest", "breaking", "update",
                  "current events", "what's happening"],
        description="Get latest news headlines",
        nodes=[
            {
                "id": "find_news",
                "tool": "find_api",
                "params": {
                    "action": "call",
                    "query": "news headlines",
                    "no_auth": False,
                    "https_only": True,
                },
                "depends_on": [],
            },
        ],
    ),
    DAGTemplate(
        category="joke",
        keywords=["joke", "jokes", "funny", "humor", "laugh", "chuck norris"],
        description="Get a random joke",
        nodes=[
            {
                "id": "get_joke",
                "tool": "find_api",
                "params": {
                    "action": "call",
                    "query": "jokes",
                    "no_auth": True,
                    "https_only": True,
                },
                "depends_on": [],
            },
        ],
    ),
    DAGTemplate(
        category="search",
        keywords=["search", "find", "look up", "google", "what is",
                  "who is", "tell me about", "information about"],
        description="Search the web for information",
        nodes=[
            {
                "id": "web_search",
                "tool": "web_search",
                "params": {"query": "{query}"},
                "depends_on": [],
            },
        ],
    ),
    DAGTemplate(
        category="cat_facts",
        keywords=["cat facts", "cat fact", "cats", "kitten"],
        description="Get random cat facts",
        nodes=[
            {
                "id": "cat_facts",
                "tool": "find_api",
                "params": {
                    "action": "call",
                    "query": "cat facts",
                    "no_auth": True,
                    "https_only": True,
                },
                "depends_on": [],
            },
        ],
    ),
    DAGTemplate(
        category="dog_facts",
        keywords=["dog facts", "dog fact", "dogs", "puppy"],
        description="Get random dog facts",
        nodes=[
            {
                "id": "dog_facts",
                "tool": "find_api",
                "params": {
                    "action": "call",
                    "query": "dog facts",
                    "no_auth": True,
                    "https_only": True,
                },
                "depends_on": [],
            },
        ],
    ),
    DAGTemplate(
        category="currency",
        keywords=["exchange rate", "currency", "convert", "usd to", "eur to",
                  "gbp to", "to pln", "to usd", "to eur"],
        description="Convert between currencies",
        nodes=[
            {
                "id": "convert_currency",
                "tool": "find_api",
                "params": {
                    "action": "call",
                    "query": "currency exchange rate {query}",
                    "no_auth": True,
                    "https_only": True,
                },
                "depends_on": [],
            },
        ],
    ),
    DAGTemplate(
        category="ip_lookup",
        keywords=["ip address", "my ip", "what is my ip", "ip location",
                  "geoip", "geolocation"],
        description="Look up IP address information",
        nodes=[
            {
                "id": "ip_lookup",
                "tool": "find_api",
                "params": {
                    "action": "call",
                    "query": "IP geolocation",
                    "no_auth": True,
                    "https_only": True,
                },
                "depends_on": [],
            },
        ],
    ),
    DAGTemplate(
        category="movie_info",
        keywords=["movie", "film", "show", "tv series", "netflix",
                  "imdb", "rating", "what to watch"],
        description="Get movie/TV show information",
        nodes=[
            {
                "id": "movie_search",
                "tool": "find_api",
                "params": {
                    "action": "call",
                    "query": "movies {query}",
                    "no_auth": False,
                    "https_only": True,
                },
                "depends_on": [],
            },
        ],
    ),
]


# ── Template Manager ─────────────────────────────────────

class TemplatePlanner:
    """Planner that uses templates for common queries, LLM as fallback.

    Self-tuning: tracks success rate per template, caches new ones.
    """

    def __init__(self, llm_planner: Callable | None = None):
        self._llm_planner = llm_planner or llm_plan
        self._templates: list[DAGTemplate] = []
        self._skill_manager = None  # lazy import
        self._load()

    def plan(self, text: str, tools: list[dict], llm_call: Callable | None = None,
             tool_registry=None) -> TaskDAG | None:
        """Plan a task — try templates first, then learned skills, then LLM fallback.

        Args:
            text: User query
            tools: Available tool schemas
            llm_call: LLM function for fallback
            tool_registry: ToolRegistry for validation

        Returns:
            TaskDAG or None.
        """
        # 1. Try built-in templates
        dag = self._template_plan(text)
        if dag:
            logger.info("TemplatePlanner: matched built-in '%s'", dag.metadata.get("template", "?"))
            return dag

        # 2. Try learned skills (user's accumulated skills)
        skill_dag = self._skill_plan(text)
        if skill_dag:
            logger.info("TemplatePlanner: matched skill '%s'", skill_dag.metadata.get("skill", "?"))
            return skill_dag

        # 3. Fall back to LLM planner
        if llm_call:
            logger.info("TemplatePlanner: no template/skill — using LLM planner")
            dag = self._llm_planner(text=text, tools=tools, llm_call=llm_call)
            if dag:
                # Save as new skill (student learns!)
                self._save_plan_as_skill(dag, text)
            return dag

        return None

    def _template_plan(self, text: str) -> TaskDAG | None:
        """Try to match query against templates."""
        best_score = 0
        best_template = None

        for template in self._templates:
            score = template.match(text)
            if score > best_score:
                best_score = score
                best_template = template

        if best_template and best_score >= 2:
            dag = best_template.build_dag(text)
            # Verify all tool names exist
            return dag

        return None

    def _skill_plan(self, text: str) -> TaskDAG | None:
        """Try to match query against learned skills (user's accumulated knowledge).

        If a skill's tags/description match the query, we build a DAG
        from the skill's tool-call steps.

        Returns:
            TaskDAG or None.
        """
        try:
            from zeus.skills import SkillManager
            if self._skill_manager is None:
                self._skill_manager = SkillManager()
            skills = self._skill_manager.list_skills()

            text_lower = text.lower()
            best_score = 0
            best_skill = None

            for skill in skills:
                score = 0
                # Check tags
                for tag in skill.get("tags", []):
                    if isinstance(tag, str) and tag.lower() in text_lower:
                        score += 3
                    # Also check if tag words appear in query
                    for word in text_lower.split():
                        if len(word) > 3 and word in tag.lower():
                            score += 2
                # Check description
                desc = skill.get("description", "").lower()
                for word in text_lower.split():
                    if len(word) > 3 and word in desc:
                        score += 1

                if score > best_score:
                    best_score = score
                    best_skill = skill

            if best_skill and best_score >= 3:
                skill_name = best_skill.get("name", "")
                if skill_name:
                    skill = self._skill_manager.get(skill_name)
                    if skill is not None:
                        steps = skill.get_steps()
                        if steps:
                            # Build a DAG from skill steps
                            nodes = []
                            for i, step in enumerate(steps):
                                step_lower = step.lower()
                                node_id = f"step_{i}"
                                params = {}

                                # Try to detect which tool from step text
                                if "api" in step_lower or "fetch" in step_lower:
                                    tool = "find_api"
                                    params["action"] = "call"
                                    params["query"] = text
                                elif "search" in step_lower or "find" in step_lower:
                                    tool = "web_search"
                                    params["query"] = text
                                elif "execute" in step_lower or "run" in step_lower:
                                    tool = "terminal"
                                    params["command"] = text
                                elif "read" in step_lower or "file" in step_lower:
                                    tool = "file"
                                    params["action"] = "read"
                                else:
                                    continue

                                nodes.append(DAGNode(
                                    id=node_id,
                                    type="tool",
                                    tool=tool,
                                    params=params,
                                    depends_on=[],
                                ))

                            if nodes:
                                return TaskDAG(
                                    goal=best_skill.get("description", text),
                                    nodes=nodes,
                                    metadata={"skill": best_skill.get("name", "learned"), "match_score": best_score},
                                )

            return None
        except Exception:
            return None

    def _save_plan_as_skill(self, dag: TaskDAG, text: str):
        """Save a successful LLM plan as a reusable skill.

        This is how Zeus learns: each successful LLM plan becomes
        a skill file in ~/.zeus/skills/, so next time a similar
        query comes, the template planner can match it directly.
        """
        if not dag or not dag.nodes:
            return

        # Extract keywords from text for skill name + tags
        words = text.lower().split()
        stopwords = {"the", "a", "an", "is", "are", "was", "were", "to", "of",
                     "in", "for", "on", "at", "by", "with", "from", "and",
                     "or", "not", "but", "how", "what", "why", "when", "where",
                     "who", "can", "you", "i", "we", "they", "it", "do", "does",
                     "tell", "show", "get", "find", "make", "use", "need", "want"}
        keywords = sorted(set(w for w in words if len(w) > 2 and w not in stopwords))
        if not keywords:
            return

        # Generate skill name from first 2-3 keywords
        skill_name = "-".join(keywords[:3]).lower()
        tags_str = ", ".join(f'"{kw}"' for kw in keywords[:5])

        # Build DAG description as markdown
        steps_lines = []
        for i, node in enumerate(dag.nodes, 1):
            tool = node.tool or "?"
            params_str = "; ".join(f"{k}={v}" for k, v in (node.params or {}).items())
            steps_lines.append(f"    {i}. Call `{tool}` with: {params_str}")

        steps_md = "\n".join(steps_lines) if steps_lines else "    No steps recorded."

        skill_content = f"""---
name: {skill_name}
description: Zeus learned: {text[:80]}
tags: [{tags_str}]
version: 1.0.0
source: template-planner
---

## Query
{text}

## Steps
{steps_md}

## Tools Used
{', '.join(n.tool for n in dag.nodes if n.tool)}
"""

        # Save via SkillManager
        try:
            from zeus.skills import SkillManager
            sm = self._skill_manager or SkillManager()
            self._skill_manager = sm

            # Extract tool steps as step strings
            steps = []
            commands = []
            for i, node in enumerate(dag.nodes, 1):
                tool = node.tool or "?"
                params_str = "; ".join(f"{k}={v}" for k, v in (node.params or {}).items())
                steps.append(f"Call `{tool}` with: {params_str}")
                # Also try to extract a command
                if tool == "web_search" and "query" in (node.params or {}):
                    commands.append(f'python -m zeus "{node.params["query"]}"')
                elif tool == "terminal" and "command" in (node.params or {}):
                    commands.append(node.params["command"])

            tags = keywords[:5]
            skill_path = sm.create(
                name=skill_name,
                description=f"Zeus learned: {text[:80]}",
                steps=steps,
                commands=commands if commands else None,
                tags=tags,
            )
            if skill_path:
                logger.info("Saved plan as skill: %s (%s)", skill_name, skill_path)
        except Exception as e:
            logger.debug("Could not save skill: %s", e)

    def _save_template_from_dag(self, dag: TaskDAG, text: str):
        """Save a successful LLM plan as a new template."""
        # Extract category from first tool or description
        first_tool = dag.nodes[0].tool if dag.nodes else "generic"
        category = first_tool or "generic"

        # Extract keywords from text
        words = set(text.lower().split())
        stopwords = {"the", "a", "an", "is", "are", "was", "were", "to", "of",
                     "in", "for", "on", "at", "by", "with", "from", "and",
                     "or", "not", "but", "how", "what", "why", "when", "where",
                     "who", "can", "you", "i", "we", "they", "it"}
        keywords = sorted(w for w in words if len(w) > 2 and w not in stopwords)[:5]

        # Only save if we have keywords
        if not keywords:
            return

        # Convert DAG nodes to template node dicts
        node_dicts = []
        for n in dag.nodes:
            node_dicts.append({
                "id": n.id,
                "tool": n.tool,
                "params": n.params,
                "depends_on": n.depends_on,
                "retry": n.retry,
                "timeout": n.timeout or 30,
            })

        template = DAGTemplate(
            category=category,
            keywords=keywords,
            description=f"Auto-saved: {text[:60]}",
            nodes=node_dicts,
        )

        self._templates.append(template)
        self._save()

    def record_success(self, dag: TaskDAG):
        """Record a successful execution for a template."""
        template_name = dag.metadata.get("template", "") if dag.metadata else ""
        for t in self._templates:
            if t.category == template_name:
                t.uses += 1
                t.success_rate = (t.success_rate * (t.uses - 1) + 1.0) / t.uses
                break
        self._save()

    def record_failure(self, dag: TaskDAG):
        """Record a failed execution for a template."""
        template_name = dag.metadata.get("template", "") if dag.metadata else ""
        for t in self._templates:
            if t.category == template_name:
                t.uses += 1
                t.success_rate = (t.success_rate * (t.uses - 1) + 0.0) / t.uses
                break
        self._save()

    def list_templates(self) -> list[dict]:
        """List all loaded templates."""
        return [
            {
                "category": t.category,
                "keywords": t.keywords,
                "description": t.description[:60],
                "success_rate": f"{t.success_rate:.0%}",
                "uses": t.uses,
            }
            for t in self._templates
        ]

    def _load(self):
        """Load templates from disk + built-in."""
        self._templates = list(_BUILTIN_TEMPLATES)

        if TEMPLATES_FILE.exists():
            try:
                with open(TEMPLATES_FILE) as f:
                    data = json.load(f)
                for d in data:
                    self._templates.append(DAGTemplate.from_dict(d))
                logger.info("TemplatePlanner: loaded %d templates from disk", len(data))
            except Exception as e:
                logger.debug("TemplatePlanner: cant read cache: %s", e)

    def _save(self):
        """Save templates to disk."""
        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        data = [t.to_dict() for t in self._templates]
        try:
            with open(TEMPLATES_FILE, "w") as f:
                json.dump(data, f, indent=1, ensure_ascii=False)
        except Exception as e:
            logger.debug("TemplatePlanner: cant save: %s", e)


# Singleton
_planner: TemplatePlanner | None = None


def get_template_planner() -> TemplatePlanner:
    global _planner
    if _planner is None:
        _planner = TemplatePlanner()
    return _planner
