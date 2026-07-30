"""Context budget — model context windows and token estimation.

Retrieves model context windows from:
  1. Provider profile (default_max_tokens)
  2. Known model map (hardcoded for common models)
  3. User config override

Token estimation: ~4 chars per token for UA, ~3 for EN.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Known context windows for common models (provider:model -> context tokens)
KNOWN_CONTEXT_WINDOWS: dict[str, int] = {
    # OpenAI
    "gpt-4": 8192,
    "gpt-4-32k": 32768,
    "gpt-4-turbo": 128000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-3.5-turbo": 16384,
    "o1": 200000,
    "o3": 200000,
    # Anthropic
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    "claude-3.5-sonnet": 200000,
    "claude-sonnet-4": 200000,
    # DeepSeek
    "deepseek-v2": 128000,
    "deepseek-v3": 128000,
    "deepseek-v4": 128000,
    "deepseek-r1": 128000,
    "deepseek-chat": 128000,
    "deepseek-coder": 128000,
    # OpenRouter / opencode-zen
    "deepseek-v4-flash-free": 32000,
    "deepseek/deepseek-v4-flash-free": 32000,
    "opencode-zen/deepseek-v4-flash-free": 32000,
    "opencode-zen/mimo-v2.5-pro": 262144,
    # Qwen
    "qwen2.5": 32768,
    "qwen2.5-coder": 32768,
    "qwen2.5-72b": 128000,
    "qwen-max": 32768,
    # Llama
    "llama-3": 8192,
    "llama-3.1": 128000,
    "llama-3.2": 128000,
    "llama-3.3": 128000,
    # Mixtral
    "mixtral-8x7b": 32768,
    # Gemini
    "gemini-1.5": 1048576,
    "gemini-2.0": 1048576,
    "gemini-2.5": 1048576,
    # Default
    "default": 32768,
}

# Character-to-token ratios (empirical, chars per token)
_CHARS_PER_TOKEN = {
    "uk": 3.5,    # Ukrainian (mixed Latin + Cyrillic)
    "ru": 3.5,    # Russian
    "en": 4.0,    # English
    "code": 3.0,  # Python, JSON, etc.
    "default": 3.8,
}


def estimate_tokens(text: str, lang: str = "default") -> int:
    """Estimate token count from character length.

    Args:
        text: Input text
        lang: Language code (uk, en, code, default)

    Returns:
        Estimated token count.
    """
    ratio = _CHARS_PER_TOKEN.get(lang, _CHARS_PER_TOKEN["default"])
    return max(1, int(len(text) / ratio))


def get_context_window(provider: str | None, model: str | None) -> int:
    """Get context window size for a provider/model combination.

    Resolution order:
      1. Hardcoded model map (exact match)
      2. Hardcoded model map (prefix match)
      3. Provider profile default_max_tokens * 2
      4. Default: 32768

    Args:
        provider: Provider name
        model: Model name

    Returns:
        Context window in tokens.
    """
    model_key = model or ""
    full_key = f"{provider}:{model}" if provider and model else ""

    # 1. Exact model match
    if model_key in KNOWN_CONTEXT_WINDOWS:
        return KNOWN_CONTEXT_WINDOWS[model_key]

    # 2. Full key match
    if full_key in KNOWN_CONTEXT_WINDOWS:
        return KNOWN_CONTEXT_WINDOWS[full_key]

    # 3. Prefix match (try longest first)
    sorted_keys = sorted(KNOWN_CONTEXT_WINDOWS.keys(), key=len, reverse=True)
    for known_key in sorted_keys:
        if known_key == "default":
            continue
        if model_key.startswith(known_key) or known_key.startswith(model_key):
            return KNOWN_CONTEXT_WINDOWS[known_key]

    # 4. Provider profile
    if provider:
        try:
            from zeus.providers import get_provider_profile
            profile = get_provider_profile(provider)
            if profile and profile.default_max_tokens:
                return profile.default_max_tokens * 2
        except ImportError:
            pass

    # 5. Default
    logger.info("Unknown model %s/%s, defaulting to 32K context", provider, model)
    return 32768


class ContextBudget:
    """Context budget for a single LLM call.

    Manages:
      - Total context window (model limit)
      - Reserve for response (output tokens)
      - Available for input (system + history + tools)
    """

    def __init__(
        self,
        context_window: int,
        reserve_output: int = 4096,
        reserve_tools: int = 2048,
    ):
        self._window = context_window
        self._reserve_output = reserve_output
        self._reserve_tools = reserve_tools

    @property
    def max_input(self) -> int:
        return self._window - self._reserve_output - self._reserve_tools

    @property
    def window(self) -> int:
        return self._window

    @property
    def output_limit(self) -> int:
        return self._reserve_output

    def remaining(self, used: int) -> int:
        return self.max_input - used

    def fits(self, tokens: int) -> int:
        return tokens <= self.max_input

    def to_dict(self) -> dict:
        return {
            "window": self._window,
            "max_input": self.max_input,
            "reserve_output": self._reserve_output,
            "reserve_tools": self._reserve_tools,
        }

    def __repr__(self) -> str:
        return (
            f"ContextBudget(window={self._window}, "
            f"input={self.max_input}, "
            f"output={self._reserve_output})"
        )


def get_budget(provider: str | None = None,
               model: str | None = None,
               reserve_output: int = 4096) -> ContextBudget:
    """Get context budget for a provider/model.

    Args:
        provider: Provider name
        model: Model name
        reserve_output: Tokens to reserve for response

    Returns:
        ContextBudget instance.
    """
    window = get_context_window(provider, model)
    return ContextBudget(
        context_window=window,
        reserve_output=min(reserve_output, window // 4),
    )


def format_budget(budget: ContextBudget) -> str:
    """Format budget as human-readable string."""
    d = budget.to_dict()
    return (
        f"  Window: {d['window']:,} tokens\n"
        f"  Input:  {d['max_input']:,} tokens\n"
        f"  Output: {d['reserve_output']:,} tokens"
    )
