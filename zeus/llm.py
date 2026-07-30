"""LLM client factory — creates callable LLM functions from provider profiles.

Uses Zeus's native provider system (zeus/providers/) to discover endpoints
and auth config, with fallback to direct HTTP calls.

Usage:
    from zeus.llm import make_llm_call
    llm = make_llm_call("openai", model="gpt-4")
    response = llm(messages=[...], tools=[...])
"""

from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

# Try to read config from Hermes (for auto-config)
_HERMES_CONFIG = None
_HERMES_ENV = None
try:
    import yaml
    _config_path = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "config.yaml"
    if _config_path.exists():
        with open(_config_path) as f:
            _HERMES_CONFIG = yaml.safe_load(f) or {}
except Exception:
    _HERMES_CONFIG = {}

try:
    _env_path = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / ".env"
    if _env_path.exists():
        _HERMES_ENV = {}
        with open(_env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    _HERMES_ENV[k.strip()] = v.strip().strip("'\"")
except Exception:
    _HERMES_ENV = {}

# Default model config — read from Hermes if available
_DEFAULT_PROVIDER = "openrouter"
_DEFAULT_MODEL = "gpt-4o-mini"
_DEFAULT_API_KEY = ""

if _HERMES_CONFIG and "model" in _HERMES_CONFIG:
    m = _HERMES_CONFIG["model"]
    if "provider" in m:
        _DEFAULT_PROVIDER = m["provider"]
    if "default" in m:
        _DEFAULT_MODEL = m["default"]

# Find the right API key from env or Hermes .env
_provider_key_map = {
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "opencode-zen": "OPENCODE_ZEN_API_KEY",
    "opencode-go": "OPENCODE_GO_API_KEY",
    "google": "GOOGLE_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "xai": "XAI_API_KEY",
    "github": "GITHUB_API_KEY",
    "nous": None,  # OAuth
    "openai-codex": None,  # OAuth
}
env_var = _provider_key_map.get(_DEFAULT_PROVIDER)
if env_var:
    _DEFAULT_API_KEY = os.environ.get(env_var) or ""
    if not _DEFAULT_API_KEY and _HERMES_ENV:
        _DEFAULT_API_KEY = _HERMES_ENV.get(env_var, "")

# Cache
_llm_call_cache: dict[str, Callable] = {}


def list_providers() -> list[dict]:
    """List all available providers from Zeus's native provider system."""
    try:
        from zeus.providers import list_providers as zeus_list
        profiles = zeus_list()
        result = []
        for p in profiles:
            result.append({
                "name": getattr(p, 'name', '?'),
                "display_name": getattr(p, 'display_name', ''),
                "description": getattr(p, 'description', ''),
                "api_mode": getattr(p, 'api_mode', 'chat_completions'),
            })
        return result
    except ImportError:
        return _fallback_providers()


def make_llm_call(
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> Callable:
    """Create an LLM call function for the given provider/model.

    Uses Zeus's native provider system to discover endpoints and auth.
    Falls back to direct OpenAI-compatible HTTP call.

    Args:
        provider: Provider name (openai, anthropic, openrouter, etc.)
        model: Model name
        api_key: API key
        base_url: Override base URL

    Returns:
        Function with signature: (messages, tools) -> str
    """
    provider = provider or _DEFAULT_PROVIDER
    model = model or _DEFAULT_MODEL
    api_key = api_key or _DEFAULT_API_KEY
    cache_key = f"{provider}:{model}"

    if cache_key in _llm_call_cache:
        return _llm_call_cache[cache_key]

    # Try to get provider profile from Zeus's native system
    profile = None
    try:
        from zeus.providers import get_provider_profile
        profile = get_provider_profile(provider)
    except ImportError:
        pass

    if profile:
        llm = _create_from_profile(profile, model, api_key, base_url)
    else:
        llm = _create_direct_call(provider, model, api_key, base_url)

    _llm_call_cache[cache_key] = llm
    return llm


def _create_from_profile(profile, model: str, api_key: str, base_url: str | None) -> Callable:
    """Create LLM call from a ProviderProfile."""
    url = base_url or profile.base_url
    headers = {}

    if profile.auth_type == "api_key":
        env_key = None
        for env_var in profile.env_vars:
            val = os.environ.get(env_var) or (api_key if api_key else None)
            if val:
                headers["Authorization"] = f"Bearer {val}"
                env_key = env_var
                break
        if not env_key and api_key:
            headers["Authorization"] = f"Bearer {api_key}"

    def _call(messages: list, tools: list | None = None) -> str:
        import requests

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": 4096,
        }
        if tools:
            payload["tools"] = tools

        try:
            resp = requests.post(
                f"{url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"] or ""
        except Exception as e:
            return f"⚠ LLM error: {e}"

    return _call


def _create_direct_call(provider: str, model: str, api_key: str, base_url: str | None) -> Callable:
    """Create a direct OpenAI-compatible LLM call."""
    urls = {
        "openai": "https://api.openai.com/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "deepseek": "https://api.deepseek.com/v1",
        "github": "https://models.inference.ai.azure.com",
    }

    url = base_url or urls.get(provider, "https://openrouter.ai/api/v1")

    def _call(messages: list, tools: list | None = None) -> str:
        import requests

        if provider == "anthropic":
            return _call_anthropic(messages, tools, api_key, model)

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": 4096,
        }
        if tools:
            payload["tools"] = tools

        try:
            resp = requests.post(
                f"{url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"] or ""
        except Exception as e:
            return f"⚠ LLM error: {e}"

    return _call


def _call_anthropic(messages: list, tools: list | None, api_key: str, model: str) -> str:
    """Call Anthropic API (different format than OpenAI)."""
    import requests

    system = ""
    anthropic_messages = []
    for m in messages:
        if m["role"] == "system":
            system = m["content"]
        else:
            role = "assistant" if m["role"] == "assistant" else "user"
            anthropic_messages.append({"role": role, "content": m["content"]})

    payload = {
        "model": model,
        "max_tokens": 4096,
        "messages": anthropic_messages,
    }
    if system:
        payload["system"] = system

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["content"][0]["text"]


def _fallback_providers() -> list[dict]:
    """Fallback list when no provider system is available."""
    return [
        {"name": "openai", "display_name": "OpenAI"},
        {"name": "openrouter", "display_name": "OpenRouter"},
        {"name": "anthropic", "display_name": "Anthropic"},
        {"name": "deepseek", "display_name": "DeepSeek"},
    ]


def configure_from_env() -> Callable:
    """Configure LLM from environment variables and Hermes config."""
    return make_llm_call(
        provider=os.environ.get("ZEUS_LLM_PROVIDER") or _DEFAULT_PROVIDER,
        model=os.environ.get("ZEUS_LLM_MODEL") or _DEFAULT_MODEL,
        api_key=os.environ.get("ZEUS_LLM_API_KEY") or _DEFAULT_API_KEY,
    )