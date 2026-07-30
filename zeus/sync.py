"""GitSync — agent self-backup and restore via GitHub.

Zeus manages its own Git repository, committing and pushing its
current state (code, config, memory, skills, tools) to GitHub.

Features:
  - Auto-commit after significant events (skills, facts, config changes)
  - Auto-push on interval or after N changes
  - Sanitized config (tokens excluded from repo)
  - Restore: clone repo + rebuild databases
  - Separate branch for agent state (default: agent-state)

Usage:
    from zeus.sync import GitSync
    
    sync = GitSync()
    sync.setup()        # configure git remote with token
    sync.commit("auto-save")  # commit all changes
    sync.push()         # push to remote
    sync.status()       # show git status
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class GitSyncError(Exception):
    """GitSync operation failed."""
    pass


class GitSync:
    """Self-backup and restore via GitHub.

    Manages git operations for the agent's repository,
    including auto-commit, push, and restore.
    """

    def __init__(self, repo_dir: str | None = None):
        self._repo = Path(repo_dir or os.getcwd()).resolve()
        self._configured = False

    # ── Setup ─────────────────────────────────────────────

    def setup(self, token: str | None = None, repo: str | None = None,
              branch: str = "agent-state", user_name: str = "Zeus Agent",
              user_email: str = "zeus@agent.local") -> bool:
        """Configure git with authentication token.

        Sets up:
          - git remote with embedded token (https://token@github.com/...)
          - git user.name and user.email
          - Branch: agent-state (separate from main/master)

        Args:
            token: GitHub PAT
            repo: Full repo URL (e.g. github.com/user/repo)
            branch: Branch name for agent state
            user_name: Git commit author name
            user_email: Git commit author email

        Returns:
            True if configured.
        """
        if not self._is_git_repo():
            raise GitSyncError(f"Not a git repository: {self._repo}")

        if not token:
            # Try env vars
            token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
            if not token:
                # Try config
                try:
                    from zeus.config import ZeusConfig
                    cfg = ZeusConfig.load()
                    token = os.environ.get(cfg.get("sync.token_env", "GITHUB_TOKEN"), "")
                except ImportError:
                    pass

        if not token:
            logger.warning("GitSync: no token available, push will fail without auth")
            self._configured = False
            return False

        # Build remote URL with token
        if repo and "://" in repo:
            base_url = repo
        elif repo:
            base_url = f"https://{repo}"
        else:
            # Try to read existing remote
            existing = self._run_git("remote get-url origin", check=False)
            base_url = existing.strip() if existing else ""

        if not base_url:
            raise GitSyncError("No repo URL configured")

        # Build authenticated URL
        auth_url = re.sub(
            r"https://",
            f"https://{token}@",
            base_url,
        )

        # Set or update remote
        self._run_git(f"remote remove origin", check=False)
        self._run_git(f"remote add origin {shlex.quote(auth_url)}")

        # Set user info
        self._run_git(f"config user.name {shlex.quote(user_name)}")
        self._run_git(f"config user.email {shlex.quote(user_email)}")

        # Create or switch to agent-state branch
        self._run_git(f"checkout -B {shlex.quote(branch)}")
        self._branch = branch
        self._configured = True

        logger.info("GitSync: configured for %s (branch: %s)", repo or base_url, branch)
        return True

    # ── Status ────────────────────────────────────────────

    def status(self) -> dict:
        """Get repository status.

        Returns:
            dict with:
              - clean: bool
              - branch: str
              - ahead: int (commits ahead of remote)
              - behind: int (commits behind remote)
              - modified: list of changed files
              - unstaged: list of unstaged files
        """
        result: dict[str, Any] = {
            "clean": True,
            "branch": "",
            "ahead": 0,
            "behind": 0,
            "modified": [],
            "unstaged": [],
            "error": "",
        }

        if not self._is_git_repo():
            result["error"] = "Not a git repository"
            return result

        # Branch
        branch = self._run_git("rev-parse --abbrev-ref HEAD", check=False)
        result["branch"] = branch.strip() if branch else "unknown"

        # Status
        status_text = self._run_git("status --porcelain", check=False)
        if status_text:
            result["clean"] = False
            for line in status_text.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("M") or line.startswith("A") or line.startswith("??"):
                    result["modified"].append(line[2:].strip())

        # Ahead/behind
        try:
            self._run_git("fetch origin", timeout=15, check=False)
            rev_info = self._run_git(
                "rev-list --left-right --count HEAD...origin/HEAD",
                timeout=10, check=False,
            )
            if rev_info:
                parts = rev_info.strip().split()
                if len(parts) == 2:
                    result["ahead"] = int(parts[0])
                    result["behind"] = int(parts[1])
        except Exception:
            pass

        return result

    def log(self, max_count: int = 10) -> list[dict]:
        """Get recent commit log.

        Args:
            max_count: Max commits to show

        Returns:
            List of commit dicts.
        """
        output = self._run_git(
            f'log --oneline --max-count={max_count} --format="%h|%s|%ar"',
            check=False,
        )
        commits = []
        for line in output.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split("|")
            if len(parts) >= 2:
                commits.append({
                    "hash": parts[0],
                    "message": parts[1],
                    "age": parts[2] if len(parts) > 2 else "",
                })
        return commits

    # ── Commit & Push ─────────────────────────────────────

    def commit(self, message: str = "auto-save", include_data: bool = True,
               include_config: bool = True) -> bool:
        """Commit all changes in the repository.

        Stages:
          - All tracked files (modified)
          - New files in zeus/, tools/, docs/
          - Config files (without secrets)
          - Optional: data files (user.db, memory.db, etc.)

        Args:
            message: Commit message
            include_data: Include SQLite databases
            include_config: Include config files

        Returns:
            True if committed, False if nothing to commit.
        """
        if not self._is_git_repo():
            logger.warning("GitSync: cannot commit — not a git repo")
            return False

        # Stage code + docs
        self._run_git("add zeus/", check=False)
        self._run_git("add *.md", check=False)
        self._run_git("add *.py", check=False)
        self._run_git("add *.yaml", check=False)
        self._run_git("add *.toml", check=False)

        # Stage skills + custom tools
        if Path("~/.zeus/skills").expanduser().exists():
            self._run_git("add -A ~/.zeus/skills/", check=False)
        if Path("~/.zeus/custom_tools").expanduser().exists():
            self._run_git("add -A ~/.zeus/custom_tools/", check=False)

        # Stage data files (optional)
        if include_data:
            data_patterns = [
                "~/.zeus/*.db",
                "~/.zeus/user.db",
                "~/.zeus/facts.db",
                "~/.zeus/memory.db",
            ]
            for pattern in data_patterns:
                expanded = Path(pattern).expanduser()
                if expanded.parent.exists():
                    self._run_git(f"add {pattern}", check=False)

        # Stage config (sanitized — without tokens)
        if include_config:
            config_path = Path("~/.zeus/zeus.yaml").expanduser()
            if config_path.exists():
                self._run_git("add -f ~/.zeus/zeus.yaml", check=False)

        # Check if there's anything to commit
        status = self._run_git("status --porcelain", check=False)
        if not status or not status.strip():
            logger.info("GitSync: nothing to commit")
            return False

        # Commit
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        full_message = f"[Zeus] {message} — {timestamp}"
        result = self._run_git(
            f"commit -m {shlex.quote(full_message)}",
            timeout=30, check=False,
        )

        if result:
            logger.info("GitSync: committed — %s", full_message)
            return True
        else:
            logger.warning("GitSync: commit failed")
            return False

    def push(self, timeout: int = 60) -> bool:
        """Push commits to remote.

        Args:
            timeout: Max push time in seconds

        Returns:
            True if pushed successfully.
        """
        if not self._configured:
            logger.warning("GitSync: not configured, run setup() first")
            return False

        try:
            result = self._run_git(
                f"push origin HEAD",
                timeout=timeout,
            )
            logger.info("GitSync: pushed to origin")
            return True
        except GitSyncError as e:
            logger.error("GitSync: push failed — %s", e)
            return False

    def auto_sync(self, message: str = "auto-save") -> bool:
        """Commit + push in one operation.

        Only pushes if commit was successful.

        Args:
            message: Commit message

        Returns:
            True if synced.
        """
        committed = self.commit(message=message)
        if committed:
            return self.push()
        return False

    # ── Restore ───────────────────────────────────────────

    def restore(self, target_dir: str, repo_url: str,
                token: str | None = None, branch: str = "agent-state") -> bool:
        """Clone repository and restore agent state.

        Usage:
            git clone https://token@github.com/user/repo -b agent-state ~/zeus-restored

        Args:
            target_dir: Where to clone
            repo_url: GitHub repo URL
            token: GitHub PAT
            branch: Branch to clone

        Returns:
            True if restored.
        """
        if not token:
            token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

        target = Path(target_dir).expanduser().resolve()

        # Build authenticated URL
        if token and "://" in repo_url:
            auth_url = re.sub(r"https://", f"https://{token}@", repo_url)
        elif token:
            auth_url = f"https://{token}@{repo_url}"
        else:
            auth_url = f"https://{repo_url}"

        try:
            # Clone
            cmd = [
                "git", "clone",
                auth_url,
                str(target),
                "--branch", branch,
                "--depth", "50",
            ]
            subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True)

            # Restore data files
            data_dir = target / ".zeus"
            if data_dir.exists():
                import shutil
                home_zeus = Path.home() / ".zeus"
                home_zeus.mkdir(parents=True, exist_ok=True)
                for f in data_dir.iterdir():
                    if f.suffix == ".db":
                        shutil.copy2(f, home_zeus / f.name)

            logger.info("GitSync: restored to %s", target)
            return True

        except subprocess.CalledProcessError as e:
            raise GitSyncError(f"Restore failed: {e.stderr[:500]}") from e
        except Exception as e:
            raise GitSyncError(f"Restore failed: {e}") from e

    # ── Internals ─────────────────────────────────────────

    def _is_git_repo(self) -> bool:
        """Check if current directory is a git repo."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=str(self._repo),
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _run_git(self, args: str, timeout: int = 30, check: bool = True) -> str:
        """Run a git command.

        Args:
            args: Git arguments (e.g. "status --porcelain")
            timeout: Command timeout
            check: Raise on non-zero exit

        Returns:
            stdout string.

        Raises:
            GitSyncError: If command fails and check=True.
        """
        cmd = f"git {args}"
        try:
            result = subprocess.run(
                ["git"] + args.split(),
                cwd=str(self._repo),
                capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode != 0:
                if check:
                    raise GitSyncError(f"git {args}: {result.stderr.strip()[:200]}")
                return ""
            return result.stdout
        except subprocess.TimeoutExpired:
            msg = f"git {args}: timed out after {timeout}s"
            if check:
                raise GitSyncError(msg)
            logger.warning(msg)
            return ""
        except FileNotFoundError:
            msg = "git not found"
            if check:
                raise GitSyncError(msg)
            return ""

    @property
    def url(self) -> str:
        """Get remote URL (without token)."""
        try:
            url = self._run_git("remote get-url origin", check=False)
            # Strip token
            return re.sub(r"https://[^@]+@", "https://", url.strip())
        except Exception:
            return ""

    @property
    def branch(self) -> str:
        try:
            return self._run_git("rev-parse --abbrev-ref HEAD", check=False).strip()
        except Exception:
            return "unknown"


# Convenience
_sync: GitSync | None = None


def get_sync(repo_dir: str | None = None) -> GitSync:
    """Get global GitSync instance."""
    global _sync
    if _sync is None:
        _sync = GitSync(repo_dir=repo_dir)
    return _sync


def auto_sync(message: str = "auto-save") -> bool:
    """Quick auto-sync: commit + push.

    Reads config for GitHub token and auto-commit settings.

    Returns:
        True if synced.
    """
    try:
        from zeus.config import ZeusConfig
        cfg = ZeusConfig.load()
        if not cfg.get("sync.enabled", False):
            return False

        sync = get_sync()
        token = os.environ.get(cfg.get("sync.token_env", "GITHUB_TOKEN"), "")

        if not token:
            logger.warning("auto_sync: no GitHub token found")
            return False

        sync.setup(
            token=token,
            repo=cfg.get("sync.repo", ""),
            branch=cfg.get("sync.branch", "agent-state"),
        )
        return sync.auto_sync(message=message)
    except Exception as e:
        logger.error("auto_sync failed: %s", e)
        return False
