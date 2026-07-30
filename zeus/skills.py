"""Skill system — reusable procedural knowledge for Zeus.

Each skill is a Markdown file with YAML frontmatter:
  ---
  name: my-skill
  description: What this skill does
  tags: [python, testing, tdd]
  version: 1.0.0
  ---
  ## Steps
  1. First do this
  2. Then that

  ## Commands
  ```bash
  python -m pytest
  ```

  ## Pitfalls
  - Watch out for X

Skills are auto-discovered from ~/.zeus/skills/ and can be
loaded on demand by matching task description to skill tags.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SKILLS_DIR = "~/.zeus/skills"


class Skill:
    """A single skill — reusable procedural knowledge."""

    def __init__(self, path: Path):
        self.path = path
        self.name: str = ""
        self.description: str = ""
        self.tags: list[str] = []
        self.version: str = "1.0.0"
        self.body: str = ""
        self._parsed = False

    def _parse(self):
        """Parse the SKILL.md file."""
        if self._parsed:
            return

        try:
            content = self.path.read_text()
        except Exception:
            return

        # Parse YAML frontmatter
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
        if fm_match:
            fm_text = fm_match.group(1)
            self.body = fm_match.group(2).strip()
            self._parse_frontmatter(fm_text)
        else:
            self.body = content.strip()

        self._parsed = True

    def _parse_frontmatter(self, fm_text: str):
        """Parse YAML-like frontmatter (simple key: value)."""
        for line in fm_text.splitlines():
            line = line.strip()
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip().strip("\"'")

            if key == "name":
                self.name = value
            elif key == "description":
                self.description = value
            elif key == "tags":
                # Parse ["python", "testing"] or [python, testing] or python, testing
                tags_match = re.findall(r'"([^"]+)"', value)
                if not tags_match:
                    tags_match = re.findall(r"'([^']+)'", value)
                if not tags_match:
                    tags_match = [t.strip() for t in value.strip("[]").split(",")]
                self.tags = [t for t in tags_match if t]
            elif key == "version":
                self.version = value

    def get_steps(self) -> list[str]:
        """Extract steps from the body."""
        self._parse()
        steps = []
        in_steps = False
        for line in self.body.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("## steps"):
                in_steps = True
                continue
            if stripped.startswith("## ") and in_steps:
                break
            if in_steps and re.match(r"^\d+\.\s+", stripped):
                steps.append(re.sub(r"^\d+\.\s+", "", stripped))
        return steps

    def get_commands(self) -> list[str]:
        """Extract bash commands from the body."""
        self._parse()
        commands = []
        in_code = False
        for line in self.body.splitlines():
            if line.strip().startswith("```bash") or line.strip().startswith("```shell"):
                in_code = True
                continue
            if line.strip().startswith("```") and in_code:
                in_code = False
                continue
            if in_code and line.strip():
                commands.append(line.strip())
        return commands

    def get_pitfalls(self) -> list[str]:
        """Extract pitfalls from the body."""
        self._parse()
        pitfalls = []
        in_pitfalls = False
        for line in self.body.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("## pitfalls"):
                in_pitfalls = True
                continue
            if stripped.startswith("## ") and in_pitfalls:
                break
            if in_pitfalls and stripped.startswith("- "):
                pitfalls.append(stripped[2:])
        return pitfalls

    def format(self) -> str:
        """Format skill as a readable instruction block."""
        self._parse()
        lines = [
            f"📦 Skill: {self.name}",
            f"   {self.description}",
            f"   Tags: {', '.join(self.tags)}",
            f"   Version: {self.version}",
            "",
        ]

        steps = self.get_steps()
        if steps:
            lines.append("   Steps:")
            for i, step in enumerate(steps, 1):
                lines.append(f"     {i}. {step}")

        cmds = self.get_commands()
        if cmds:
            lines.append("   Commands:")
            for cmd in cmds:
                lines.append(f"     $ {cmd}")

        pitfalls = self.get_pitfalls()
        if pitfalls:
            lines.append("   Pitfalls:")
            for p in pitfalls:
                lines.append(f"     ⚠ {p}")

        return "\n".join(lines)

    def to_prompt(self) -> str:
        """Format as a context injection block for LLM."""
        self._parse()
        parts = [f"## Skill: {self.name}"]
        parts.append(f"Description: {self.description}")
        if self.tags:
            parts.append(f"Tags: {', '.join(self.tags)}")
        parts.append("")
        parts.append(self.body[:2000])
        return "\n".join(parts)


class SkillManager:
    """Manages skill discovery, loading, and execution."""

    def __init__(self, skills_dir: str = SKILLS_DIR):
        self._dir = Path(skills_dir).expanduser()
        self._skills: dict[str, Skill] = {}
        self._dir.mkdir(parents=True, exist_ok=True)

        # Create default skill if none exist
        self._ensure_default_skill()

        self.discover()

    def _ensure_default_skill(self):
        """Create a default skill if the directory is empty."""
        existing = list(self._dir.glob("*.md"))
        if existing:
            return

        default = self._dir / "getting-started.md"
        default.write_text("""---
name: getting-started
description: How to work with Zeus Agent
tags: [zeus, basics, help]
version: 1.0.0
---

## Steps
1. Start Zeus: `python -m zeus --interactive`
2. Type your request in natural language
3. Use /commands for special actions

## Commands
- `/help` — Show available commands
- `/stats` — Show telemetry
- `/review list` — Show pending code reviews
- `/search <query>` — Search past conversations
- `/remember <fact>` — Save a fact

## Pitfalls
- Each conversation turn is processed independently
- For complex multi-step tasks, be specific
""")

    def discover(self) -> int:
        """Scan skills directory and load all skills."""
        count = 0
        self._skills.clear()

        for f in sorted(self._dir.glob("*.md")):
            try:
                skill = Skill(f)
                skill._parse()
                if skill.name:
                    self._skills[skill.name] = skill
                    count += 1
            except Exception as e:
                logger.warning("Failed to load skill %s: %s", f.name, e)

        return count

    def get(self, name: str) -> Skill | None:
        """Get a skill by name."""
        return self._skills.get(name)

    def find(self, query: str) -> list[Skill]:
        """Find skills matching a query.

        Matches by name, description, or tags.
        """
        query_lower = query.lower()
        results = []
        for skill in self._skills.values():
            if query_lower in skill.name.lower():
                results.append(skill)
            elif query_lower in skill.description.lower():
                results.append(skill)
            elif any(query_lower in tag.lower() for tag in skill.tags):
                results.append(skill)
        return results

    def find_relevant(self, task: str, max_results: int = 3) -> list[Skill]:
        """Find skills relevant to a given task description.

        Uses keyword matching on tags and description.

        Args:
            task: Task description or user query
            max_results: Max skills to return

        Returns:
            List of matching skills.
        """
        # Extract keywords from task
        words = set(
            w.lower().strip(".,!?()[]{}")
            for w in task.split()
            if len(w) > 2
        )

        scored = []
        for skill in self._skills.values():
            score = 0
            # Tag matches
            for tag in skill.tags:
                if tag.lower() in words:
                    score += 3
                elif any(tag.lower() in w for w in words):
                    score += 2

            # Description matches
            desc_lower = skill.description.lower()
            for word in words:
                if word in desc_lower:
                    score += 1

            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[:max_results]]

    def create(self, name: str, description: str,
               steps: list[str], commands: list[str] | None = None,
               pitfalls: list[str] | None = None,
               tags: list[str] | None = None) -> str:
        """Create a new skill from components.

        Args:
            name: Skill name (lowercase, hyphens)
            description: One-line description
            steps: Ordered list of steps
            commands: Optional list of shell commands
            pitfalls: Optional list of warnings
            tags: Optional list of tags

        Returns:
            Path to created skill file.
        """
        safe_name = re.sub(r"[^a-z0-9-]", "", name.lower().replace(" ", "-"))
        path = self._dir / f"{safe_name}.md"

        lines = [
            "---",
            f"name: {safe_name}",
            f"description: {description}",
            f"tags: {json.dumps(tags or [])}",
            "version: 1.0.0",
            "---",
            "",
            "## Steps",
        ]

        for i, step in enumerate(steps, 1):
            lines.append(f"{i}. {step}")

        if commands:
            lines.extend(["", "## Commands", "", "```bash"])
            for cmd in commands:
                lines.append(cmd)
            lines.append("```")

        if pitfalls:
            lines.extend(["", "## Pitfalls"])
            for p in pitfalls:
                lines.append(f"- {p}")

        content = "\n".join(lines) + "\n"
        path.write_text(content)

        # Reload
        skill = Skill(path)
        skill._parse()
        if skill.name:
            self._skills[skill.name] = skill

        logger.info("Skill created: %s (%s)", safe_name, path)
        return str(path)

    def list_skills(self) -> list[dict]:
        """List all skills with metadata."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "tags": s.tags,
                "version": s.version,
                "steps": len(s.get_steps()),
            }
            for s in sorted(self._skills.values(), key=lambda x: x.name)
        ]

    @property
    def count(self) -> int:
        return len(self._skills)


# Convenience
_skill_manager: SkillManager | None = None


def get_skill_manager() -> SkillManager:
    global _skill_manager
    if _skill_manager is None:
        _skill_manager = SkillManager()
    return _skill_manager
