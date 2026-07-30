"""Self-review module — code analysis and improvement proposals.

Periodically scans Zeus's own codebase, runs LLM-based analysis,
and generates structured improvement proposals. User reviews and
approves them via /review commands.

Key design:
  - One file at a time (context budget)
  - Focuses on one analysis type per run
  - User approves before any code change
  - Proposals stored in ReviewStore (SQLite)

Analysis types:
  1. duplication — repeated code patterns
  2. error_handling — missing or bare exception handling
  3. complexity — overly complex functions (McCabe-like heuristic)
  4. sync_async — mixing sync/async incorrectly
  5. performance — inefficient patterns (repeated calls, no caching)
  6. architecture — drift from EventBus pattern
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from zeus.module import Module, Event
from zeus.review import ReviewStore, ReviewProposal

logger = logging.getLogger(__name__)

# How often to auto-scan (in task completions)
AUTO_SCAN_INTERVAL = 10

# File patterns to skip
SKIP_PATTERNS = [
    "__pycache__",
    ".git",
    "__init__.py",
    "venv",
    ".venv",
    "*.pyc",
]


class SelfReviewModule(Module):
    """Code review agent — analyzes Zeus's own code and proposes improvements.

    Can run:
      - On demand: /review scan-pending (next unscanned file)
      - Scheduled: auto-scan every N task completions
      - Manual: /review scan-file <path>

    Does NOT modify code autonomously — only stores proposals.
    """

    def __init__(
        self,
        bus=None,
        llm_call: Callable | None = None,
        scan_dir: str | None = None,
        review_store: ReviewStore | None = None,
    ):
        super().__init__(
            name="self_review",
            description="Self-code review: analyzes Zeus source and proposes improvements",
            bus=bus,
        )
        self._llm_call = llm_call
        self._scan_dir = Path(scan_dir or Path(__file__).parent.parent)
        self._store = review_store or ReviewStore()
        self._task_count = 0
        self._scanned_files: set[str] = set()

    async def start(self):
        """Start the module: subscribe to task events and review commands."""
        await super().start()
        self.subscribe("task.completed", self._on_task_completed)
        self.subscribe("review.scan", self._handle_scan_cmd)
        self.subscribe("review.list", self._handle_list_cmd)
        self.subscribe("review.show", self._handle_show_cmd)
        self.subscribe("review.approve", self._handle_approve_cmd)
        self.subscribe("review.reject", self._handle_reject_cmd)
        logger.info("SelfReviewModule: started (scan_dir=%s)", self._scan_dir)

    async def stop(self):
        await super().stop()
        logger.info("SelfReviewModule: stopped")

    # ── Event handlers ────────────────────────────────────

    async def _on_task_completed(self, event: Event):
        """Auto-trigger scan after N task completions."""
        self._task_count += 1
        if self._task_count >= AUTO_SCAN_INTERVAL and self._llm_call:
            self._task_count = 0
            logger.info("SelfReview: auto-scan triggered (%d tasks)", AUTO_SCAN_INTERVAL)
            # Run in background to not block the pipeline
            asyncio.create_task(self._scan_next_file())

    async def _handle_scan_cmd(self, event: Event):
        """Handle review.scan event — scan a specific file or next unscanned."""
        data = event.data or {}
        target = data.get("file", "")
        results = await self._scan_file(target) if target else await self._scan_next_file()
        await self.emit("review.result", {"count": len(results), "status": "done"})

    async def _handle_list_cmd(self, event: Event):
        """Handle review.list event — list pending proposals."""
        data = event.data or {}
        status = data.get("status", "pending")
        proposals = self._store.list(status=status)
        await self.emit("review.list_result", {
            "proposals": [p.to_dict() for p in proposals],
            "count": len(proposals),
        })

    async def _handle_show_cmd(self, event: Event):
        """Handle review.show event — show a specific proposal."""
        review_id = (event.data or {}).get("id", "")
        proposal = self._store.get(review_id)
        await self.emit("review.show_result", {
            "found": proposal is not None,
            "proposal": proposal.to_dict() if proposal else None,
        })

    async def _handle_approve_cmd(self, event: Event):
        """Handle review.approve event — apply an approved proposal."""
        review_id = (event.data or {}).get("id", "")
        proposal = self._store.get(review_id)
        if not proposal:
            await self.emit("user.output", {
                "text": f"❌ Proposal `{review_id}` not found.",
                "source": "self_review",
            })
            return

        if proposal.status != "pending":
            await self.emit("user.output", {
                "text": f"⏭ Proposal `{review_id}` is already `{proposal.status}`.",
                "source": "self_review",
            })
            return

        success = await self._apply_proposal(proposal)
        if success:
            self._store.update_status(review_id, "applied")
            await self.emit("user.output", {
                "text": f"✅ Applied: `{proposal.title}`",
                "source": "self_review",
            })
        else:
            self._store.update_status(review_id, "failed")
            await self.emit("user.output", {
                "text": f"⚠ Failed to apply: `{proposal.title}`",
                "source": "self_review",
            })

    async def _handle_reject_cmd(self, event: Event):
        """Handle review.reject event — mark a proposal as rejected."""
        review_id = (event.data or {}).get("id", "")
        proposal = self._store.get(review_id)
        if not proposal:
            return
        self._store.update_status(review_id, "rejected")
        await self.emit("user.output", {
            "text": f"❌ Rejected: `{proposal.title}`",
            "source": "self_review",
        })

    # ── Scanning ──────────────────────────────────────────

    async def _scan_next_file(self) -> list[ReviewProposal]:
        """Find the next unscanned .py file and analyze it."""
        py_files = self._collect_py_files()

        # Find files not yet scanned
        for f in py_files:
            rel = f.relative_to(self._scan_dir)
            if str(rel) not in self._scanned_files:
                self._scanned_files.add(str(rel))
                return await self._scan_file(str(f))

        logger.info("SelfReview: all files scanned already")
        return []

    def _collect_py_files(self) -> list[Path]:
        """Collect all Python files in the scan directory."""
        py_files = []
        for root, dirs, files in os.walk(self._scan_dir):
            # Skip __pycache__, .git, venv
            dirs[:] = [d for d in dirs if d not in {"__pycache__", ".git", "venv", ".venv"}]

            for f in files:
                if f.endswith(".py"):
                    path = Path(root) / f
                    py_files.append(path)

        return sorted(py_files)

    async def _scan_file(self, file_path: str) -> list[ReviewProposal]:
        """Analyze a single Python file and generate proposals.

        Reads the file, creates an LLM prompt for code review,
        parses the response into ReviewProposal objects.

        Args:
            file_path: Absolute path to the file

        Returns:
            List of generated proposals (may be empty).
        """
        path = Path(file_path)
        if not path.exists():
            logger.warning("SelfReview: file not found: %s", file_path)
            return []

        # Read file
        try:
            code = path.read_text()
        except Exception as e:
            logger.warning("SelfReview: can't read %s: %s", file_path, e)
            return []

        if not code.strip():
            return []

        if not self._llm_call:
            logger.warning("SelfReview: no LLM configured, using heuristic analysis")
            return self._heuristic_scan(file_path)

        # Build LLM prompt (short for free-tier models)
        max_chars = 2000
        if len(code) > max_chars:
            code = code[:max_chars] + "\n# ... [truncated] ...\n"

        prompt = f"Code:\n```python\n{code}\n```\nReturn issues as JSON array. Max 2.\n[] if clean.\n"

        try:
            messages = [
                {"role": "system", "content": "You are a code reviewer. Return ONLY a JSON array, nothing else."},
                {"role": "user", "content": prompt},
            ]
            response = self._llm_call(messages=messages)
            result = self._parse_llm_response(response, path)
            if not result:
                # Fallback to heuristic
                result = self._heuristic_scan(file_path)
            logger.info("SelfReview: %s → %d proposals", path.name, len(result))
            return result
        except Exception as e:
            logger.error("SelfReview: LLM failed for %s: %s, using heuristics", path.name, e)
            return self._heuristic_scan(file_path)

    def _heuristic_scan(self, file_path: str) -> list[ReviewProposal]:
        """Static code analysis — finds issues without LLM.

        Checks for:
        1. Bare except clauses
        2. Functions over 60 lines
        3. Overly nested blocks (>4 levels)
        4. Missing docstrings on public functions
        5. Sync sleep in async functions

        Args:
            file_path: Path to file

        Returns:
            List of ReviewProposal objects.
        """
        path = Path(file_path)
        if not path.exists():
            return []

        try:
            lines = path.read_text().splitlines()
        except Exception:
            return []

        proposals = []
        rel_path = path.relative_to(self._scan_dir) if self._scan_dir in path.parents else Path(path.name)

        # Track line numbers for functions and classes
        current_func = ""
        current_func_start = 0
        func_lengths: list[tuple[str, int, int]] = []
        nesting_levels: list[tuple[int, int, str]] = []
        bare_excepts: list[int] = []
        sync_sleeps: list[int] = []
        missing_docstrings: list[tuple[int, str]] = []

        in_async = False
        in_function = False
        indent_stack: list[int] = [0]

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            indent = len(line) - len(line.lstrip()) if stripped else 0

            # Skip comments and empty lines
            if not stripped or stripped.startswith("#"):
                continue

            # Detect function/class definitions
            func_match = re.match(r"^(\s*)(?:async\s+)?def\s+(\w+)\s*\(", line)
            class_match = re.match(r"^\s*class\s+(\w+)\s*", line)

            if func_match:
                indent = len(func_match.group(1))
                if current_func:
                    func_lengths.append((current_func, current_func_start, i - 1))
                current_func = func_match.group(2)
                current_func_start = i
                in_function = True
                in_async = "async" in line[:indent + 10] or in_async
                indent_stack = [indent]

            elif class_match:
                if current_func:
                    func_lengths.append((current_func, current_func_start, i - 1))
                    current_func = ""
                # Check docstring on next line
                if i < len(lines):
                    next_line = lines[i].strip()
                    if not (next_line.startswith('"""') or next_line.startswith("'''")):
                        missing_docstrings.append((i, f"class {class_match.group(1)}"))

            # Track nesting levels by indent
            if in_function and stripped:
                indent = len(line) - len(line.lstrip())
                while indent_stack and indent <= indent_stack[-1]:
                    indent_stack.pop()
                indent_stack.append(indent)
                if len(indent_stack) > 4:
                    nesting_levels.append((i, len(indent_stack), current_func))

            # Bare except
            if stripped == "except:" or stripped.startswith("except :"):
                bare_excepts.append(i)

            # time.sleep in async context
            if in_async and "time.sleep(" in stripped and "await" not in line[:indent]:
                sync_sleeps.append(i)

        # Finalize last function
        if current_func:
            func_lengths.append((current_func, current_func_start, len(lines)))

        # ── Generate proposals ──

        # 1. Overly long functions
        for name, start, end in func_lengths:
            length = end - start + 1
            if length > 60:
                proposals.append(ReviewProposal(
                    target_file=str(rel_path),
                    module_name=rel_path.parts[0] if len(rel_path.parts) > 1 else rel_path.stem,
                    issue_type="complexity",
                    severity="medium" if length < 100 else "high",
                    title=f"Function `{name}` is {length} lines long",
                    description=f"Function `{name}` spans {length} lines (lines {start}-{end}). "
                                f"Consider splitting into smaller functions for better readability and testability.",
                    line_range=[start, end],
                ))

        # 2. Bare except clauses
        for line_no in bare_excepts:
            proposals.append(ReviewProposal(
                target_file=str(rel_path),
                module_name=rel_path.parts[0] if len(rel_path.parts) > 1 else rel_path.stem,
                issue_type="error_handling",
                severity="high",
                title="Bare `except:` clause",
                description=f"Line {line_no}: bare `except:` catches ALL exceptions including SystemExit and "
                            f"KeyboardInterrupt. Use `except Exception:` instead.",
                line_range=[line_no, line_no],
            ))

        # 3. Overly nested blocks
        for line_no, depth, func in nesting_levels[:3]:  # Max 3
            proposals.append(ReviewProposal(
                target_file=str(rel_path),
                module_name=rel_path.parts[0] if len(rel_path.parts) > 1 else rel_path.stem,
                issue_type="complexity",
                severity="medium",
                title=f"Deep nesting (level {depth}) in `{func}`",
                description=f"Line {line_no}: {depth} levels of nesting in `{func}`. "
                            f"Deep nesting makes code hard to read. Consider extracting inner blocks to functions.",
                line_range=[line_no, line_no],
            ))

        # 4. Sync sleep in async
        for line_no in sync_sleeps:
            proposals.append(ReviewProposal(
                target_file=str(rel_path),
                module_name=rel_path.parts[0] if len(rel_path.parts) > 1 else rel_path.stem,
                issue_type="sync_async",
                severity="high",
                title="Blocking `time.sleep()` in async function",
                description=f"Line {line_no}: `time.sleep()` blocks the event loop. "
                            f"Use `await asyncio.sleep()` instead.",
                line_range=[line_no, line_no],
            ))

        # 5. Missing docstrings on public functions
        for line_no, name in missing_docstrings[:3]:
            proposals.append(ReviewProposal(
                target_file=str(rel_path),
                module_name=rel_path.parts[0] if len(rel_path.parts) > 1 else rel_path.stem,
                issue_type="other",
                severity="low",
                title=f"Missing docstring for `{name}`",
                description=f"Line {line_no}: `{name}` has no docstring. Consider adding one.",
                line_range=[line_no, line_no],
            ))

        # Save proposals to store
        for p in proposals:
            self._store.save(p)

        return proposals

    def _parse_llm_response(self, response: str, file_path: Path) -> list[ReviewProposal]:
        """Parse LLM response into ReviewProposal objects.

        Handles truncated responses and various JSON formats.
        """
        import json

        if not response or not response.strip():
            return []

        # Remove markdown code fences if present
        text = response.strip()
        if text.startswith("```"):
            # Find the first [ or { after ```
            text = re.sub(r"^```[a-z]*\s*\n?", "", text)
            text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()

        # Find the JSON array
        start = text.find("[")
        end = text.rfind("]")

        # Handle truncated JSON — if no closing ], try to close it
        if start >= 0 and end <= start:
            # Try adding closing bracket
            text = text[start:] + "]"
            start = 0
            end = text.rfind("]")
            if end <= start:
                return []
        elif start >= 0 and end > start:
            text = text[start : end + 1]
        else:
            # Maybe it's a single object?
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                return self._parse_llm_response(f"[{text[start:end+1]}]", file_path)
            return []

        # Remove trailing commas before ] (common JSON error)
        text = re.sub(r",\s*]", "]", text)
        # Remove trailing commas before } (common in truncated)
        text = re.sub(r",\s*}", "}", text)

        try:
            items = json.loads(text)
            if not isinstance(items, list):
                return []
        except json.JSONDecodeError:
            # Try line-by-line recovery for truncated responses
            try:
                items = json.loads(text + "]")
            except json.JSONDecodeError:
                try:
                    items = json.loads(text + '""}]')
                except json.JSONDecodeError:
                    logger.warning("SelfReview: failed to parse LLM response")
                    return []

        if not items:
            return []

        proposals = []
        rel_path = file_path.relative_to(self._scan_dir)

        for item in items[:3]:  # Max 3 per file
            if not isinstance(item, dict):
                continue
            title = item.get("title", "Unnamed issue")
            description = item.get("description", "")
            issue_type = item.get("issue_type", "other")
            severity = item.get("severity", "medium")
            old_code = item.get("old_code", "")
            new_code = item.get("new_code", "")
            line_start = item.get("line_start")
            line_end = item.get("line_end")
            line_range = [line_start, line_end] if line_start and line_end else []

            # Validate issue type
            valid_types = {"duplication", "error_handling", "complexity", "sync_async", "performance", "architecture", "other"}
            if issue_type not in valid_types:
                issue_type = "other"

            # Validate severity
            valid_severity = {"low", "medium", "high", "critical"}
            if severity not in valid_severity:
                severity = "medium"

            proposal = ReviewProposal(
                target_file=str(rel_path),
                module_name=rel_path.parts[0] if len(rel_path.parts) > 1 else rel_path.stem,
                issue_type=issue_type,
                severity=severity,
                title=title[:120],
                description=description[:2000],
                old_code=old_code[:2000],
                new_code=new_code[:2000],
                line_range=line_range,
                llm_analysis=response[:5000],
            )
            self._store.save(proposal)
            proposals.append(proposal)

        return proposals

    async def _apply_proposal(self, proposal: ReviewProposal) -> bool:
        """Apply a code change proposal.

        Uses string replacement (old_code → new_code) on the target file.
        Returns True if applied successfully.

        Args:
            proposal: The proposal to apply

        Returns:
            True if patch was applied.
        """
        if not proposal.old_code or not proposal.new_code:
            logger.warning("Cannot apply proposal %s: no code diff", proposal.id)
            return False

        target = self._scan_dir / proposal.target_file
        if not target.exists():
            logger.warning("Cannot apply proposal %s: file not found: %s", proposal.id, target)
            return False

        try:
            content = target.read_text()
            if proposal.old_code not in content:
                logger.warning(
                    "Cannot apply proposal %s: old_code not found in %s",
                    proposal.id, target,
                )
                return False

            # Simple replacement
            new_content = content.replace(proposal.old_code, proposal.new_code, 1)
            target.write_text(new_content)
            logger.info("SelfReview: applied proposal %s to %s", proposal.id, target)
            return True
        except Exception as e:
            logger.error("SelfReview: apply failed for %s: %s", proposal.id, e)
            return False

    # ── Public API ────────────────────────────────────────

    def scan_file(self, file_path: str) -> list[ReviewProposal]:
        """Synchronously scan a file (for CLI use without event loop)."""
        return asyncio.run(self._scan_file(file_path))

    def list_proposals(self, status: str = "pending") -> list[ReviewProposal]:
        """Get proposals by status."""
        return self._store.list(status=status)

    def get_proposal(self, review_id: str) -> ReviewProposal | None:
        """Get a specific proposal."""
        return self._store.get(review_id)

    def approve_proposal(self, review_id: str) -> bool:
        """Approve and apply a proposal."""
        proposal = self._store.get(review_id)
        if not proposal or proposal.status != "pending":
            return False
        success = asyncio.run(self._apply_proposal(proposal))
        self._store.update_status(review_id, "applied" if success else "failed")
        return success

    def reject_proposal(self, review_id: str) -> bool:
        """Reject a proposal."""
        return self._store.update_status(review_id, "rejected")

    @property
    def pending_count(self) -> int:
        """Number of pending reviews."""
        return self._store.pending_count()
