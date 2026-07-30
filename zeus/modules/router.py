"""Router module — routes classified intents to pipeline or direct handlers.

Subscribes to: classification.result
Emits:         route.result, user.output

Independent module — can be replaced with different routing logic
without affecting other modules.
"""

from __future__ import annotations
from zeus.module import Module, Event, CLASSIFICATION_RESULT, USER_OUTPUT
from zeus.memory.history import ConversationBuffer


# Commands that should be executed directly
_KNOWN_COMMANDS = {
    "cd", "ls", "cat", "rm", "mv", "cp", "mkdir", "touch",
    "pwd", "echo", "grep", "find", "git", "docker", "npm",
    "pip", "cargo", "chmod", "curl", "wget", "ps", "top",
    "kill", "python", "node", "go", "rustc", "make", "cmake",
    "which", "whereis", "head", "tail", "sort", "uniq", "wc",
    "tar", "gzip", "gunzip", "zip", "unzip", "ssh", "scp",
    "df", "du", "free", "uname", "env", "export", "alias",
    "ping", "nslookup", "dig", "traceroute", "netstat",
}

_CHAT_RESPONSES = {
    "привіт": "Привіт! Чим можу допомогти?",
    "хай": "Хай! Як справи?",
    "hello": "Hello! Ready to work.",
    "дякую": "Будь ласка!",
    "thanks": "You're welcome!",
    "як справи": "Все добре! А в тебе?",
    "хто ти": "Я — Zeus Agent. Інтелектуальний агент, створений для допомоги.",
    "що вмієш": "Можу: виконувати команди, шукати в інтернеті, працювати з файлами, створювати інструменти, конвертувати валюти, і багато іншого.",
}


class RouterModule(Module):
    """Routes classified intents to appropriate handlers.

    Routes to:
      - Direct terminal commands (if intent=command + known binary)
      - Direct chat responses (if intent=simple_chat)
      - Direct LLM answer (if intent=simple_question, no custom tools)
      - Pipeline (Planner→Runtime→Synthesizer) for complex tasks
    """

    def __init__(self, bus=None, tool_registry=None, llm_call=None, history=None):
        super().__init__(
            name="router",
            description="Routes intents to pipeline or direct handlers",
            bus=bus,
        )
        self._tool_registry = tool_registry
        self._llm_call = llm_call
        self._history: ConversationBuffer = history or ConversationBuffer()

    async def start(self):
        await super().start()
        self.subscribe(CLASSIFICATION_RESULT, self._handle_classification)

    async def _handle_classification(self, event: Event):
        """Handle a classification result — route to appropriate handler."""
        text = event.data.get("text", "")
        intent = event.data.get("intent", "")
        confidence = event.data.get("confidence", 0)
        first_word = text.strip().split()[0] if text.strip() else ""

        # Command: only known binaries
        if intent == "command" and first_word in _KNOWN_COMMANDS:
            output = await self._exec_command(text)
            await self.emit(USER_OUTPUT, {"text": output, "source": "command", "event_id": event.id})
            return

        # Simple chat
        if intent == "simple_chat":
            output = self._exec_chat(text)
            await self.emit(USER_OUTPUT, {"text": output, "source": "chat", "event_id": event.id})
            return

        # Simple question — direct LLM (no tools) or Pipeline (if custom tools exist)
        if intent == "simple_question":
            if self._tool_registry and hasattr(self._tool_registry, 'names'):
                if len(self._tool_registry.names()) > 3:
                    # Has custom tools → Pipeline
                    await self.emit("pipeline.request", {
                        "text": text, "event_id": event.id
                    })
                    return

            # Fallback: direct LLM
            if self._llm_call:
                output = self._exec_llm(text)
                await self.emit(USER_OUTPUT, {"text": output, "source": "llm", "event_id": event.id})
                return
            else:
                await self.emit(USER_OUTPUT, {"text": "LLM не налаштований.", "source": "error", "event_id": event.id})
                return

        # Task complex / fallback → Pipeline
        await self.emit("pipeline.request", {
            "text": text, "event_id": event.id
        })

    # ── Handlers ───────────────────────────────────────────

    async def _exec_command(self, text: str) -> str:
        """Execute a terminal command."""
        try:
            from zeus.tools.terminal import execute
            return execute({"command": text})
        except Exception as e:
            return f"Помилка: {e}"

    def _exec_chat(self, text: str) -> str:
        """Return a chat response with history context."""
        text_lower = text.lower().strip("?.,!")
        for key, response in _CHAT_RESPONSES.items():
            if text_lower == key or text_lower.startswith(key):
                return response

        # LLM chat with full history
        if not self._llm_call:
            return "Чим можу допомогти?"

        try:
            history_msgs = self._history.to_api_messages(
                system_prompt="Ти — Zeus Agent. Відповідай коротко і дружньо."
            )
            # Ensure last message is the current user input
            if not history_msgs or history_msgs[-1].get("role") != "user" or history_msgs[-1].get("content") != text:
                history_msgs.append({"role": "user", "content": text})

            response = self._llm_call(messages=history_msgs, tools=None)
            return response
        except Exception as e:
            return f"LLM помилка: {e}"

    def _exec_llm(self, text: str) -> str:
        """Call LLM directly with history context."""
        if not self._llm_call:
            return "LLM не налаштований."
        try:
            history_msgs = self._history.to_api_messages(
                system_prompt="Ти — Zeus Agent. Відповідай коротко і точно."
            )
            if not history_msgs or history_msgs[-1].get("role") != "user" or history_msgs[-1].get("content") != text:
                history_msgs.append({"role": "user", "content": text})

            return self._llm_call(messages=history_msgs, tools=None)
        except Exception as e:
            return f"LLM помилка: {e}"