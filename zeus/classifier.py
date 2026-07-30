"""Intent classifier for Zeus.

Phase 0: keyword-based classifier (no LLM needed for basic routing).
Phase 1+: will use a tiny LLM (1B-3B) for better accuracy.
"""

from __future__ import annotations
import re
from zeus.models.types import INTENT_TYPES, Intent, ClassificationResult


# Keywords that map to specific intents
_PATTERNS: list[tuple[str, str, float]] = [
    # Commands (terminal-like)
    (r"^(cd |ls |cat |rm |mv |cp |mkdir |touch |pwd|echo|grep|find)", "command", 0.9),
    (r"^(git |docker |npm |pip |cargo |brew |apt |pacman)", "command", 0.9),
    (r"^(chmod|chown|wget|curl|kill|ps |top|htop)", "command", 0.9),

    # Simple chat
    (r"^(привіт|хай|hello|hi|доброго|здоров|hey|yo)", "simple_chat", 0.8),
    (r"(дякую|thanks|thx|спасибі|gracias|merci)", "simple_chat", 0.8),
    (r"^(як справи|як ти|how are you|what's up)", "simple_chat", 0.8),

    # Skill search
    (r"(знайди скіл|find skill|шукаю скіл|skill for|потрібен скіл)", "skill_search", 0.9),
    (r"(встанови скіл|install skill|завантаж скіл)", "skill_search", 0.9),

    # System
    (r"^(status|config|setup|doctor|update|version)", "system", 0.8),

    # Complex tasks (multi-step)
    (r"(створи проект|create project|зроби|build|develop|implement|refactor)", "task_complex", 0.7),
    (r"(знайди|search|find|look up|досліди|research|аналізуй)", "task_complex", 0.6),
    (r"(налаштуй|configure|deploy|встанови|install|setup)", "task_complex", 0.7),
    (r"(напиши|write|create|generate|згенеруй)", "task_complex", 0.6),
]


def classify(text: str) -> ClassificationResult:
    """Classify user input into an intent.

    Uses regex patterns first, falls back to heuristics.
    Phase 0: no LLM needed. Phase 1+: tiny LLM classifier.
    """
    text_stripped = text.strip()
    text_lower = text_stripped.lower()

    # 1. Try keyword patterns
    for pattern, intent_type, confidence in _PATTERNS:
        if re.search(pattern, text_lower):
            return ClassificationResult(
                intent=Intent(
                    type=intent_type,
                    confidence=confidence,
                    raw_input=text_stripped,
                ),
                entities=_extract_entities(text_stripped),
            )

    # 2. Heuristics
    word_count = len(text_stripped.split())

    if word_count <= 3 and not text_stripped.endswith("?"):
        # Short, no question → likely command
        return ClassificationResult(
            intent=Intent(type="command", confidence=0.5, raw_input=text_stripped),
            entities={},
        )

    if text_stripped.endswith("?") and word_count <= 10:
        # Short question → simple question
        return ClassificationResult(
            intent=Intent(type="simple_question", confidence=0.6, raw_input=text_stripped),
            entities={},
        )

    if word_count > 8:
        # Long input → likely complex task
        return ClassificationResult(
            intent=Intent(type="task_complex", confidence=0.5, raw_input=text_stripped),
            entities={},
        )

    # 3. Default: simple question
    return ClassificationResult(
        intent=Intent(type="simple_question", confidence=0.3, raw_input=text_stripped),
        entities={},
    )


def _extract_entities(text: str) -> dict:
    """Basic entity extraction. Will be replaced by LLM in Phase 1+."""
    entities = {}

    # Extract URLs
    urls = re.findall(r'https?://[^\s\'"]+', text)
    if urls:
        entities["urls"] = urls

    # Extract paths (Unix-like)
    paths = re.findall(r'(?:~|/)[^\s\'"]+', text)
    if paths:
        entities["paths"] = paths

    return entities