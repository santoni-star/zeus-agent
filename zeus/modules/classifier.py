"""Classifier module — intent classification as an independent module.

Subscribes to: user.input
Emits:         classification.result

Runs in parallel with other modules. No dependencies.
"""

from __future__ import annotations
import re

from zeus.module import Module, Event, USER_INPUT, CLASSIFICATION_RESULT


# Keywords that map to specific intents
_PATTERNS: list[tuple[str, str, float]] = [
    # Commands (terminal-like)
    (r"^(cd |ls |cat |rm |mv |cp |mkdir |touch |pwd|echo|grep|find)", "command", 0.9),
    (r"^(git |docker |npm |pip |cargo |brew |apt |pacman)", "command", 0.9),
    (r"^(chmod|chown|wget|curl|kill|ps |top|htop)", "command", 0.9),

    # Simple chat
    (r"^(привіт|хай|hello|hi|доброго|здоров|hey|yo)", "simple_chat", 0.8),
    (r"^(дякую|thanks|thx|спасибі|gracias|merci)", "simple_chat", 0.8),
    (r"^(як справи|як ти|how are you|what's up)", "simple_chat", 0.8),
    (r"^(хто ти|що вмієш|what can you)", "simple_chat", 0.8),

    # Complex tasks (multi-step)
    (r"(створи проект|create project|зроби|build|develop|implement|refactor)", "task_complex", 0.7),
    (r"(знайди|search|find|look up|досліди|research|аналізуй)", "task_complex", 0.6),
    (r"(налаштуй|configure|deploy|встанови|install|setup)", "task_complex", 0.7),
    (r"(напиши|write|create|generate|згенеруй)", "task_complex", 0.6),
]


class ClassifierModule(Module):
    """Classifies user input into intents (command, question, task, etc.)."""

    def __init__(self, bus=None):
        super().__init__(
            name="classifier",
            description="Classifies user input into intents",
            bus=bus,
        )

    async def start(self):
        await super().start()
        self.subscribe(USER_INPUT, self._handle_user_input)

    async def _handle_user_input(self, event: Event):
        """Classify user input and emit classification result."""
        text = event.data.get("text", "")
        if not text:
            return

        result = self._classify(text)
        await self.emit(CLASSIFICATION_RESULT, {
            "text": text,
            "intent": result["intent"],
            "confidence": result["confidence"],
            "entities": result.get("entities", {}),
            "event_id": event.id,  # Link back to original input
        })

    def _classify(self, text: str) -> dict:
        """Classify text into an intent."""
        text_lower = text.strip().lower()

        # 1. Try keyword patterns
        for pattern, intent_type, confidence in _PATTERNS:
            if re.search(pattern, text_lower):
                return {"intent": intent_type, "confidence": confidence, "entities": {}}

        # 2. Heuristics
        word_count = len(text.strip().split())

        if word_count <= 3 and not text.strip().endswith("?"):
            return {"intent": "command", "confidence": 0.5, "entities": {}}

        if text.strip().endswith("?") and word_count <= 10:
            return {"intent": "simple_question", "confidence": 0.6, "entities": {}}

        if word_count > 8:
            return {"intent": "task_complex", "confidence": 0.5, "entities": {}}

        # 3. Default
        return {"intent": "simple_question", "confidence": 0.3, "entities": {}}