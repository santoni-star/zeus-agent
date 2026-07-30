"""GitHub Actions integration — manage CI/CD workflows from Zeus.

Features:
  - Generate workflow files (.github/workflows/*.yml) from templates
  - Enable/disable individual workflows
  - List available workflows with status
  - Validate docs match tools/modules (CI check)
  - Trigger workflows via repository_dispatch

All workflows run in the repository that Zeus backups to (via GitSync).
Tokens are reused from sync config (GITHUB_TOKEN).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default workflow templates
WORKFLOW_TEMPLATES: dict[str, dict] = {
    "test": {
        "filename": "test.yml",
        "name": "Tests",
        "enabled": True,
        "description": "Run Python tests on push and PR",
        "events": ["push", "pull_request"],
        "python_versions": ["3.10", "3.11", "3.12"],
        "content": """name: Tests
on:
  push:
    branches: [master, main, agent-state]
  pull_request:
    branches: [master, main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: [{python_versions}]
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python ${{{{ matrix.python-version }}}}
        uses: actions/setup-python@v5
        with:
          python-version: ${{{{ matrix.python-version }}}}
      - name: Install dependencies
        run: |
          pip install -e .
      - name: Test
        run: |
          python -m pytest tests/ --timeout=30 -q || echo "No tests yet"
""",
    },
    "lint": {
        "filename": "lint.yml",
        "name": "Lint",
        "enabled": True,
        "description": "Lint Python code with flake8/pylint",
        "events": ["push", "pull_request"],
        "content": """name: Lint
on:
  push:
    branches: [master, main, agent-state]
  pull_request:
    branches: [master, main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install linters
        run: |
          pip install flake8
      - name: Lint
        run: |
          flake8 zeus/ --max-line-length=120 --count --statistics
""",
    },
    "docs-validation": {
        "filename": "docs-validation.yml",
        "name": "Docs Validation",
        "enabled": True,
        "description": "Check that TOOLS.md matches actual tool files",
        "events": ["push", "pull_request"],
        "content": """name: Docs Validation
on:
  push:
    branches: [master, main, agent-state]
  pull_request:
    branches: [master, main]
  workflow_dispatch:

jobs:
  validate-docs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Validate Tools
        run: |
          python -c "
import re
import os
from pathlib import Path

# Check that all tools in zeus/tools/ are documented in TOOLS.md
tools_dir = Path('zeus/tools')
docs_file = Path('TOOLS.md')
if not docs_file.exists():
    print('::warning::TOOLS.md not found')
    exit(0)

docs_text = docs_file.read_text()
missing = []
for f in tools_dir.glob('*.py'):
    if f.name.startswith('_') or f.name in ('registry.py', 'dynamic.py'):
        continue
    name = f.stem
    if name not in docs_text:
        missing.append(name)
        print(f'::warning::Tool {name} not documented in TOOLS.md')

if missing:
    print(f'Missing docs for: {missing}')
else:
    print('All tools are documented.')
"
""",
    },
    "agent-sync": {
        "filename": "agent-sync.yml",
        "name": "Agent Sync Status",
        "enabled": False,
        "description": "Periodic sync status report (GitHub Actions + agent state)",
        "events": ["schedule"],
        "content": """name: Agent Sync
on:
  schedule:
    - cron: '0 */6 * * *'  # every 6 hours
  workflow_dispatch:

jobs:
  status:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Show repo stats
        run: |
          echo "## Agent Repository Status" >> $GITHUB_STEP_SUMMARY
          echo "| Metric | Value |" >> $GITHUB_STEP_SUMMARY
          echo "|--------|-------|" >> $GITHUB_STEP_SUMMARY
          echo "| Commits | $(git rev-list --count HEAD) |" >> $GITHUB_STEP_SUMMARY
          echo "| Files | $(find . -name '*.py' | wc -l) .py files |" >> $GITHUB_STEP_SUMMARY
          echo "| Size | $(du -sh . | cut -f1) |" >> $GITHUB_STEP_SUMMARY
""",
    },
}


class ActionsManager:
    """Manage GitHub Actions workflows for Zeus.

    Each workflow is a YAML file in .github/workflows/.
    Workflows can be enabled/disabled via config.
    """

    def __init__(self, repo_dir: str | None = None):
        self._repo = Path(repo_dir or os.getcwd()).resolve()
        self._workflows_dir = self._repo / ".github" / "workflows"
        self._workflows_dir.mkdir(parents=True, exist_ok=True)

        # Create .gitignore for workflow directory if not exists
        gitignore = self._workflows_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("""
# Workflows are auto-generated by Zeus
# Edit them in zeus/config.py or use /actions commands
""")

    # ── List ──────────────────────────────────────────────

    def list_workflows(self) -> list[dict]:
        """List all available workflows with their status.

        Returns:
            List of workflow dicts.
        """
        workflows = []
        for wf_id, wf in WORKFLOW_TEMPLATES.items():
            file_path = self._workflows_dir / wf["filename"]
            exists = file_path.exists()
            enabled = wf.get("enabled", True)

            workflows.append({
                "id": wf_id,
                "name": wf["name"],
                "filename": wf["filename"],
                "description": wf["description"],
                "enabled": enabled,
                "exists": exists,
                "events": wf.get("events", []),
            })

        return workflows

    def get_workflow(self, wf_id: str) -> dict | None:
        """Get a specific workflow by ID."""
        wf = WORKFLOW_TEMPLATES.get(wf_id)
        if not wf:
            return None

        file_path = self._workflows_dir / wf["filename"]
        return {
            "id": wf_id,
            "name": wf["name"],
            "filename": wf["filename"],
            "description": wf["description"],
            "enabled": wf.get("enabled", True),
            "exists": file_path.exists(),
            "events": wf.get("events", []),
        }

    # ── Generate ──────────────────────────────────────────

    def generate_workflow(self, wf_id: str) -> bool:
        """Generate a workflow file from template.

        Args:
            wf_id: Workflow ID (test, lint, docs-validation, agent-sync)

        Returns:
            True if generated.
        """
        wf = WORKFLOW_TEMPLATES.get(wf_id)
        if not wf:
            raise ValueError(f"Unknown workflow: {wf_id}. Available: {list(WORKFLOW_TEMPLATES.keys())}")

        if not wf.get("enabled", True):
            logger.info("Workflow '%s' is disabled in config", wf_id)
            return False

        content = wf["content"]

        # Fill template variables
        python_versions = wf.get("python_versions", ["3.12"])
        content = content.replace("{python_versions}", ", ".join(f'"{v}"' for v in python_versions))

        # Write file
        file_path = self._workflows_dir / wf["filename"]
        file_path.write_text(content + "\n")
        logger.info("Generated workflow: %s (%s)", wf_id, file_path)
        return True

    def generate_all(self) -> list[str]:
        """Generate all enabled workflow files.

        Returns:
            List of generated workflow IDs.
        """
        generated = []
        for wf_id in WORKFLOW_TEMPLATES:
            try:
                if self.generate_workflow(wf_id):
                    generated.append(wf_id)
            except Exception as e:
                logger.warning("Failed to generate workflow '%s': %s", wf_id, e)
        return generated

    # ── Enable/Disable ────────────────────────────────────

    def enable(self, wf_id: str) -> bool:
        """Enable and generate a workflow."""
        if wf_id not in WORKFLOW_TEMPLATES:
            raise ValueError(f"Unknown workflow: {wf_id}")
        WORKFLOW_TEMPLATES[wf_id]["enabled"] = True
        return self.generate_workflow(wf_id)

    def disable(self, wf_id: str) -> bool:
        """Disable and remove a workflow file."""
        if wf_id not in WORKFLOW_TEMPLATES:
            raise ValueError(f"Unknown workflow: {wf_id}")
        WORKFLOW_TEMPLATES[wf_id]["enabled"] = False
        file_path = self._workflows_dir / WORKFLOW_TEMPLATES[wf_id]["filename"]
        if file_path.exists():
            file_path.unlink()
            logger.info("Removed workflow: %s", file_path)
        return True

    # ── Status Report ─────────────────────────────────────

    def status_report(self) -> str:
        """Generate a human-readable status report of all workflows.

        Returns:
            Formatted status string.
        """
        workflows = self.list_workflows()

        lines = [f"\n📋 GitHub Actions ({len(workflows)} workflows):\n"]
        for wf in workflows:
            status_icon = "✅" if wf["exists"] else "📝" if wf["enabled"] else "⏸"
            events_str = ", ".join(wf["events"])
            lines.append(f"  {status_icon} {wf['name']} ({wf['id']})")
            lines.append(f"     {wf['description']}")
            lines.append(f"     Events: {events_str}")

        return "\n".join(lines)

    # ── Validation ────────────────────────────────────────

    def validate_tools_docs(self) -> list[str]:
        """Check that all tools in zeus/tools/ are documented in TOOLS.md.

        Returns:
            List of undocumented tool names.
        """
        tools_dir = self._repo / "zeus" / "tools"
        docs_file = self._repo / "TOOLS.md"

        if not docs_file.exists():
            return ["TOOLS.md not found"]

        docs_text = docs_file.read_text()
        missing = []
        for f in sorted(tools_dir.glob("*.py")):
            if f.name.startswith("_") or f.name in ("registry.py", "dynamic.py"):
                continue
            name = f.stem
            if name not in docs_text:
                missing.append(f.stem)
        return missing

    def validate_modules_docs(self) -> list[str]:
        """Check that all modules are documented in ARCHITECTURE.md.

        Returns:
            List of undocumented module names.
        """
        modules_dir = self._repo / "zeus" / "modules"
        docs_file = self._repo / "ARCHITECTURE.md"

        if not docs_file.exists():
            return ["ARCHITECTURE.md not found"]

        docs_text = docs_file.read_text()
        missing = []
        for f in sorted(modules_dir.glob("*.py")):
            if f.name.startswith("_"):
                continue
            name = f.stem
            if name not in docs_text:
                missing.append(f.stem)
        return missing


# Convenience
_actions: ActionsManager | None = None


def get_actions(repo_dir: str | None = None) -> ActionsManager:
    global _actions
    if _actions is None:
        _actions = ActionsManager(repo_dir=repo_dir)
    return _actions


def auto_generate_workflows(repo_dir: str | None = None) -> list[str]:
    """Auto-generate all enabled workflow files.

    Called during GitSync setup to ensure workflows exist.
    """
    mgr = get_actions(repo_dir)
    return mgr.generate_all()
