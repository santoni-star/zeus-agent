"""Provider module registry for Zeus.

Provider profiles live in ``zeus/providers/plugins/<name>/``.

Each plugin directory contains:
  - ``__init__.py`` — calls ``register_provider(profile)`` at import
  - ``plugin.yaml`` — manifest (name, kind: model-provider, version, description)

Discovery is lazy: the first call to ``get_provider_profile()`` or
``list_providers()`` scans and imports every plugin.

Usage::

    from zeus.providers import get_provider_profile
    profile = get_provider_profile("nvidia")   # ProviderProfile or None
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
from pathlib import Path

from zeus.providers.base import OMIT_TEMPERATURE, ProviderProfile  # noqa: F401

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, ProviderProfile] = {}
_ALIASES: dict[str, str] = {}
_discovered = False

_BUNDLED_PLUGINS_DIR = Path(__file__).resolve().parent / "plugins"


def register_provider(profile: ProviderProfile) -> None:
    """Register a provider profile by name and aliases."""
    _REGISTRY[profile.name] = profile
    for alias in profile.aliases:
        _ALIASES[alias] = profile.name


def get_provider_profile(name: str) -> ProviderProfile | None:
    """Look up a provider profile by name or alias."""
    if not _discovered:
        _discover_providers()
    canonical = _ALIASES.get(name, name)
    return _REGISTRY.get(canonical)


def list_providers() -> list[ProviderProfile]:
    """Return all registered provider profiles."""
    if not _discovered:
        _discover_providers()
    seen: set[int] = set()
    result: list[ProviderProfile] = []
    for profile in _REGISTRY.values():
        pid = id(profile)
        if pid not in seen:
            seen.add(pid)
            result.append(profile)
    return result


def _user_plugins_dir() -> Path | None:
    """Return ``$HERMES_HOME/plugins/model-providers/`` if it exists."""
    try:
        hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
        d = hermes_home / "plugins" / "model-providers"
        return d if d.is_dir() else None
    except Exception:
        return None


def _install_shim():
    """Install a shim so provider plugins can ``from providers import ...``.

    Copied Hermes provider plugins import ``from providers import register_provider``
    and ``from providers.base import ProviderProfile``. We alias ``providers`` to
    the ``zeus.providers`` package so both resolve correctly.
    """
    if "providers" not in sys.modules:
        import zeus.providers as _zeus_providers
        sys.modules["providers"] = _zeus_providers


def _import_plugin_dir(plugin_dir: Path, source: str) -> None:
    """Import a single plugin directory so it self-registers."""
    init_file = plugin_dir / "__init__.py"
    if not init_file.exists():
        return

    safe_name = plugin_dir.name.replace("-", "_")
    if source == "bundled":
        module_name = f"zeus.providers.plugins.{safe_name}"
    else:
        module_name = f"_hermes_user_provider_{safe_name}"

    if module_name in sys.modules:
        return

    try:
        spec = importlib.util.spec_from_file_location(
            module_name, init_file, submodule_search_locations=[str(plugin_dir)]
        )
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception as exc:
        logger.warning(
            "Failed to load %s provider plugin %s: %s", source, plugin_dir.name, exc
        )
        sys.modules.pop(module_name, None)


def _discover_providers() -> None:
    """Populate the registry by importing every provider plugin."""
    global _discovered
    if _discovered:
        return
    _discovered = True

    # Install shim so plugins can ``from providers import ...``
    _install_shim()

    # 1. Bundled plugins
    if _BUNDLED_PLUGINS_DIR.is_dir():
        for child in sorted(_BUNDLED_PLUGINS_DIR.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            _import_plugin_dir(child, "bundled")

    # 2. User plugins from Hermes (if available)
    user_dir = _user_plugins_dir()
    if user_dir is not None:
        for child in sorted(user_dir.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            _import_plugin_dir(child, "user")