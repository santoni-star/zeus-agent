"""ContextManager — smart context building for LLM calls.

Integrates:
  - ContextBudget (model window, token estimation)
  - ConversationBuffer (dialog history)
  - UserProfile + FactStore (persistent memory)
  - SkillManager (relevant skills)
  - Smart pruning (prioritize: system > user > assistant > tool)

Usage:
    from zeus.context import ContextManager

    ctx = ContextManager(
        provider="opencode-zen",
        model="deepseek-v4-flash-free",
        profile=user_profile,
        facts=fact_store,
        skills=skill_manager,
    )
    messages = ctx.build(user_input="hello")
    # → [system, profile, facts, skill, history..., user]
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from zeus.context_budget import (
    ContextBudget, get_budget, estimate_tokens, format_budget,
)
from zeus.memory.history import ConversationBuffer

logger = logging.getLogger(__name__)


class ContextManager:
    """Manages LLM context: budget, injection, pruning.

    Builds the optimal prompt within the model's context window:
      1. System prompt (fixed)
      2. User profile (if available)
      3. Relevant facts (if available)
      4. Relevant skill (if available)
      5. Conversation history (pruned to fit)
      6. Current user input
    """

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        budget: ContextBudget | None = None,
        profile: Any = None,
        facts: Any = None,
        skills: Any = None,
        system_prompt: str = "You are Zeus Agent, an intelligent AI assistant.",
    ):
        """Initialize ContextManager.

        Args:
            provider: LLM provider name (for budget lookup)
            model: Model name (for budget lookup)
            budget: Explicit ContextBudget (overrides provider/model)
            profile: UserProfile instance (optional)
            facts: FactStore instance (optional)
            skills: SkillManager instance (optional)
            system_prompt: Base system prompt
        """
        self._budget = budget or get_budget(provider=provider, model=model)
        self._profile = profile
        self._facts = facts
        self._skills = skills
        self._base_system = system_prompt
        self._history: ConversationBuffer | None = None

        # Stats tracking
        self._total_prompt_tokens = 0
        self._total_calls = 0

    def set_history(self, history: ConversationBuffer):
        """Attach conversation buffer."""
        self._history = history

    def build(
        self,
        user_input: str,
        task_context: str | None = None,
        tools: list | None = None,
        lang: str = "uk",
    ) -> list[dict]:
        """Build the optimal message list within context budget.

        Args:
            user_input: Current user message
            task_context: Optional task context (for pipeline)
            tools: Optional tool schemas
            lang: Language for token estimation

        Returns:
            List of message dicts: [system, ...context..., user]
        """
        self._total_calls += 1
        budget = self._budget
        max_input = budget.max_input

        messages: list[dict] = []
        used_tokens = 0

        # 1. System prompt
        system = self._build_system_prompt()
        sys_tokens = estimate_tokens(system, lang)
        messages.append({"role": "system", "content": system})
        used_tokens += sys_tokens

        # 2. User profile
        if self._profile:
            profile_text = self._profile.to_prompt()
            if profile_text:
                p_tokens = estimate_tokens(profile_text, lang)
                if used_tokens + p_tokens < max_input:
                    messages.append({"role": "system", "content": profile_text})
                    used_tokens += p_tokens

        # 3. Relevant facts (search by user input keywords)
        if self._facts:
            fact_text = self._facts.get_relevant_context(user_input, limit=3)
            if fact_text:
                f_tokens = estimate_tokens(fact_text, "en")
                if used_tokens + f_tokens < max_input:
                    messages.append({"role": "system", "content": fact_text})
                    used_tokens += f_tokens

        # 4. Relevant skill (auto-detect)
        if self._skills:
            relevant = self._skills.find_relevant(user_input, max_results=1)
            if relevant:
                skill_text = relevant[0].to_prompt()
                sk_tokens = estimate_tokens(skill_text, lang)
                if used_tokens + sk_tokens < max_input:
                    messages.append({"role": "system", "content": "Relevant skill:\n" + skill_text[:1500]})
                    used_tokens += sk_tokens

        # 5. Task context (for pipeline/planner)
        if task_context:
            tc_tokens = estimate_tokens(task_context, lang)
            if used_tokens + tc_tokens < max_input:
                messages.append({"role": "system", "content": f"Task context:\n{task_context}"})
                used_tokens += tc_tokens

        # 6. Conversation history (pruned to fit)
        user_tokens = estimate_tokens(user_input, lang)
        if self._history and self._history.turn_count > 0:
            history_msgs = self._history.to_api_messages()
            history_tokens = sum(
                estimate_tokens(m.get("content", ""), lang)
                for m in history_msgs
            )
            total_needed = history_tokens + user_tokens
            max_history = max_input - used_tokens - user_tokens - 256  # buffer

            if total_needed > max_history:
                # Need to prune — keep the most recent messages
                pruned = self._prune_history(history_msgs, max_history, lang)
                messages.extend(pruned)
                used_tokens += sum(
                    estimate_tokens(m.get("content", ""), lang)
                    for m in pruned
                )
            else:
                messages.extend(history_msgs)
                used_tokens += history_tokens

        # 7. Current user input
        messages.append({"role": "user", "content": user_input})

        self._total_prompt_tokens = used_tokens + user_tokens
        return messages

    def _build_system_prompt(self) -> str:
        """Build the base system prompt."""
        parts = [self._base_system]

        # Add budget hint if window is moderate
        if self._budget.window <= 65536:
            pass  # No need to mention — just be concise

        return "\n".join(parts)

    def _prune_history(
        self,
        history: list[dict],
        max_tokens: int,
        lang: str,
    ) -> list[dict]:
        """Prune conversation history to fit within budget.

        Strategy:
          1. Keep user message (high priority)
          2. Keep recent messages (last is most relevant)
          3. Drop assistant tool calls first, then old assistant, then old user

        Args:
            history: Full history message list
            max_tokens: Max tokens for history
            lang: Language for estimation

        Returns:
            Pruned message list (chronological).
        """
        if not history:
            return []

        # Score each message: newer = more important
        scored = []
        for i, msg in enumerate(history):
            role = msg.get("role", "")
            content = msg.get("content", "")
            tokens = estimate_tokens(content, lang)
            # Score: recency + role bonus
            recency = i / max(1, len(history))
            role_bonus = 0.3 if role == "user" else 0.1
            scored.append((recency + role_bonus, tokens, msg))

        # Sort by score descending, take what fits
        scored.sort(key=lambda x: -x[0])

        selected = []
        used = 0
        for score, tokens, msg in scored:
            if used + tokens <= max_tokens:
                selected.append(msg)
                used += tokens

        # Restore chronological order
        # (we need to reconstruct from original indices)
        selected_indices = {id(msg) for msg in selected}
        return [msg for msg in history if id(msg) in selected_indices]

    @property
    def budget(self) -> ContextBudget:
        return self._budget

    @property
    def last_prompt_tokens(self) -> int:
        return self._total_prompt_tokens

    @property
    def total_calls(self) -> int:
        return self._total_calls

    def stats(self) -> dict:
        return {
            "total_calls": self._total_calls,
            "last_prompt_tokens": self._total_prompt_tokens,
            "budget": self._budget.to_dict(),
        }


# ── Context-aware LLM wrapper ────────────────────────────

class ContextAwareLLM:
    """Wraps an LLM callable with automatic context management.

    Builds optimal prompts within model budget, injecting
    profile, facts, skills, and conversation history.
    """

    def __init__(
        self,
        llm: Callable,
        context_mgr: ContextManager,
    ):
        self._llm = llm
        self._ctx = context_mgr

    def __call__(self, messages: list, tools: list | None = None, **kwargs) -> str:
        """Call LLM with context-managed messages.

        If messages are passed directly (len=2, system+user), builds
        the full context using ContextManager.

        Args:
            messages: Message list (or [system, user] to expand)
            tools: Optional tool schemas
            **kwargs: Additional LLM kwargs

        Returns:
            LLM response.
        """
        # Detect if this is a raw call needing context
        if len(messages) <= 2 and messages[0].get("role") == "system":
            user_content = messages[-1].get("content", "")
            context_messages = self._ctx.build(user_content)
            return self._llm(context_messages, tools, **kwargs)

        # Already manually constructed messages
        return self._llm(messages, tools, **kwargs)

    @property
    def context(self) -> ContextManager:
        return self._ctx
