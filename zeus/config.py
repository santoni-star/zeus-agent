"""Zeus configuration system.

Loads and merges config from multiple sources (priority: high → low):
  1. CLI flags (--provider, --model, --gateway, etc.)
  2. zeus.yaml at ~/.zeus/ or $ZEUS_HOME/
  3. Environment variables (ZEUS_LLM_API_KEY, etc.)
  4. Hermes auto-config (if available)

Usage:
    from zeus.config import ZeusConfig
    cfg = ZeusConfig.load()
    cfg.get("model.provider")  # "opencode-zen"
    cfg.module_enabled("gateway")  # True/False
"""

from __future__ import annotations

import os
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Default configuration
DEFAULT_CONFIG = {
    "zeus": {
        "version": "0.1.0",
        "home": str(Path.home() / ".zeus"),
    },
    "model": {
        "provider": "openrouter",
        "model": "gpt-4o-mini",
        "max_tokens": 4096,
        "temperature": 0.7,
    },
    "gateway": {
        "enabled": False,
        "provider": "telegram",
        "token_env": "TELEGRAM_BOT_TOKEN",
        "chat_id_env": "TELEGRAM_CHAT_ID",
        "allowed_users": [],
    },
    "modules": {
        "enabled": [
            "classifier",
            "memory",
            "router",
            "pipeline",
            "reflection",
            "self_review",
            "telemetry",
            "sub_agent",
            "mcp",
        ],
        "disabled": [
            "gateway",
        ],
    },
    "scheduler": {
        "enabled": True,
        "db_path": "~/.zeus/jobs.db",
    },
    "memory": {
        "enabled": True,
        "db_path": "~/.zeus/memory.db",
        "fts_enabled": True,
        "auto_save": True,
    },
    "tools": {
        "custom_dir": "~/.zeus/custom_tools/",
        "auto_discover": True,
    },
    "logging": {
        "level": "INFO",
        "file": "~/.zeus/logs/zeus.log",
        "max_size_mb": 10,
        "backup_count": 3,
    },
    "sync": {
        "enabled": False,
        "repo": "github.com/santoni-star/zeus-agent",
        "token_env": "GITHUB_TOKEN",
        "auto_commit": True,
        "auto_push": True,
        "include_data": True,
        "include_config": True,
        "branch": "agent-state",
        "interval_minutes": 60,
        "actions": {
            "enabled": True,
            "auto_generate": True,
            "validate_docs": True,
        },
    },
    "mcp_servers": {
        # Time server (example)
        # "time": {
        #     "command": "uvx",
        #     "args": ["mcp-server-time"],
        #     "enabled": True,
        # },
        # Context7 — knowledge graph / context store
        "ctx7": {
            "url": "https://mcp.context7.com/mcp",
            "headers": {
                "Authorization": "Bearer ${CONTEXT7_API_KEY}",
            },
            "enabled": True,
            "timeout": 120,
            "keywords": ["documentation", "docs", "library", "libraryId",
                         "how to", "example", "code example", "api docs",
                         "python requests", "npm package", "function reference",
                         "resolve library", "query docs", "get request",
                         "http request", "sdk", "framework"],
        },
    },
}


class ZeusConfig:
    """Configuration manager for Zeus Agent.

    Provides dict-style access with dot-notation support.
    Config is loaded once and cached.
    """

    _instance: ZeusConfig | None = None

    def __init__(self):
        self._data: dict = {}
        self._loaded = False

    @classmethod
    def load(cls, path: str | Path | None = None) -> "ZeusConfig":
        """Load config from file, with defaults and env overrides.

        Args:
            path: Path to zeus.yaml (default: ~/.zeus/config.yaml)

        Returns:
            ZeusConfig instance (singleton).
        """
        if cls._instance is not None and cls._instance._loaded:
            return cls._instance

        instance = cls()
        instance._load(path)
        cls._instance = instance
        return instance

    def _load(self, path: str | Path | None = None):
        """Internal load: defaults → file → env overrides."""
        # 1. Start with defaults
        self._data = deep_merge({}, DEFAULT_CONFIG)

        # 2. Load from file
        config_path = self._find_config(path)
        if config_path and config_path.exists():
            try:
                import yaml
                with open(config_path) as f:
                    file_config = yaml.safe_load(f) or {}
                self._data = deep_merge(self._data, file_config)
                logger.info("Config loaded from %s", config_path)
            except Exception as e:
                logger.warning("Failed to load config from %s: %s", config_path, e)

        # 3. Environment overrides
        self._apply_env_overrides()

        self._loaded = True

    def _find_config(self, hint: str | Path | None = None) -> Path:
        """Find config file in priority order."""
        if hint:
            p = Path(hint).expanduser()
            if p.exists():
                return p

        candidates = [
            Path(os.environ.get("ZEUS_HOME", "~/.zeus")).expanduser() / "config.yaml",
            Path.home() / ".zeus" / "config.yaml",
            Path.cwd() / "zeus.yaml",
            Path.cwd() / ".zeus.yaml",
        ]
        for p in candidates:
            if p.exists():
                return p
        # Return default even if it doesn't exist yet
        return candidates[0]

    def _apply_env_overrides(self):
        """Apply environment variable overrides."""
        env_map = {
            "ZEUS_LLM_API_KEY": ["model", "api_key"],
            "ZEUS_LLM_PROVIDER": ["model", "provider"],
            "ZEUS_LLM_MODEL": ["model", "model"],
            "TELEGRAM_BOT_TOKEN": ["gateway", "token"],
            "TELEGRAM_CHAT_ID": ["gateway", "chat_id"],
            "ZEUS_HOME": ["zeus", "home"],
            "ZEUS_LOG_LEVEL": ["logging", "level"],
        }
        for env_var, path_parts in env_map.items():
            value = os.environ.get(env_var)
            if value:
                self._set_nested(path_parts, value.strip().strip("'\""))

    # ── Access ────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """Get config value by dot-notation key.

        Examples:
            cfg.get("model.provider") → "opencode-zen"
            cfg.get("modules.enabled") → ["classifier", ...]
        """
        parts = key.split(".")
        current = self._data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
                if current is None:
                    return default
            else:
                return default
        return current

    def set(self, key: str, value: Any):
        """Set config value by dot-notation key."""
        parts = key.split(".")
        self._set_nested(parts, value)

    def _set_nested(self, parts: list[str], value: Any):
        """Set nested dict value by path parts."""
        current = self._data
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value

    def all(self) -> dict:
        """Return the full config dict."""
        return deep_merge({}, self._data)

    def to_yaml(self) -> str:
        """Serialize config to YAML string."""
        import yaml
        return yaml.dump(self._data, default_flow_style=False, sort_keys=False)

    # ── Module queries ────────────────────────────────────

    def module_enabled(self, name: str) -> bool:
        """Check if a module is enabled in config."""
        enabled = self.get("modules.enabled", [])
        disabled = self.get("modules.disabled", [])
        # Explicitly enabled overrides disabled
        if name in enabled:
            return True
        if name in disabled:
            return False
        # Default: enabled (except gateway)
        if name == "gateway":
            return False
        return True

    def list_enabled_modules(self) -> list[str]:
        """Get list of enabled module names."""
        return self.get("modules.enabled", [])

    def list_all_modules(self) -> dict[str, bool]:
        """Get dict of all known modules with enabled/disabled status."""
        known = [
            "classifier", "memory", "router", "pipeline",
            "reflection", "sub_agent", "mcp", "gateway",
        ]
        return {name: self.module_enabled(name) for name in known}

    # ── Persistence ──────────────────────────────────────

    def save(self, path: str | Path | None = None):
        """Save current config to YAML file."""
        target = Path(path or self._find_config()).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            import yaml
            with open(target, "w") as f:
                yaml.dump(self._data, f, default_flow_style=False, sort_keys=False)
            logger.info("Config saved to %s", target)
        except Exception as e:
            logger.error("Failed to save config: %s", e)

    def ensure_dirs(self):
        """Create all required directories."""
        paths = [
            self.get("zeus.home", "~/.zeus"),
            self.get("tools.custom_dir", "~/.zeus/custom_tools"),
            str(Path(self.get("logging.file", "~/.zeus/logs/zeus.log")).parent),
        ]
        for p in paths:
            Path(p).expanduser().mkdir(parents=True, exist_ok=True)


def deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dicts. override values win."""
    result = {}
    for key in set(base) | set(override):
        if key in base and key in override:
            if isinstance(base[key], dict) and isinstance(override[key], dict):
                result[key] = deep_merge(base[key], override[key])
            else:
                result[key] = override[key]
        elif key in base:
            result[key] = deep_merge({}, base[key]) if isinstance(base[key], dict) else base[key]
        else:
            result[key] = deep_merge({}, override[key]) if isinstance(override[key], dict) else override[key]
    return result


# Convenience
def load_config(path: str | None = None) -> ZeusConfig:
    """Quick-load config."""
    return ZeusConfig.load(path)


def show_config():
    """Print current config to stdout."""
    cfg = ZeusConfig.load()
    print("╔══════════════════════════════════════════╗")
    print("║         Zeus Agent — Configuration       ║")
    print("╚══════════════════════════════════════════╝")
    print()
    print(cfg.to_yaml())
    print()
    print("--- Modules ---")
    for name, enabled in cfg.list_all_modules().items():
        icon = "✅" if enabled else "⭕"
        print(f"  {icon} {name}")
