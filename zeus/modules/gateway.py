"""Gateway module — Telegram bridge as an EventBus module.

Subscribes to: user.output
Emits:         user.input (from Telegram messages)
Runs:          parallel with other modules (polling in background task)

Usage:
    from zeus.modules.gateway import GatewayModule
    module = GatewayModule(bus=bus, token="YOUR_TOKEN")
    await manager.register(module)
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any, Callable

from zeus.module import Module, Event, USER_INPUT, USER_OUTPUT
from zeus.gateway import TelegramBot

logger = logging.getLogger(__name__)


class GatewayModule(Module):
    """Telegram gateway — bridges Telegram messages with the EventBus.

    Runs its own polling loop as an asyncio task so it doesn't block
    the event loop. Incoming messages from Telegram are published as
    ``user.input`` events; outgoing responses on ``user.output`` are
    forwarded to the configured chat.
    """

    def __init__(
        self,
        bus=None,
        token: str | None = None,
        chat_id: str | int | None = None,
        allowed_users: list[str] | None = None,
    ):
        super().__init__(
            name="gateway",
            description="Telegram gateway: bidirectional bridge with EventBus",
            bus=bus,
        )
        self._token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        self._allowed_users = allowed_users or []
        self._bot: TelegramBot | None = None
        self._polling_task: asyncio.Task | None = None
        self._pending_responses: dict[str, list[str]] = {}  # chat_id -> pending texts

    async def start(self):
        """Start the module: init bot, subscribe to events, start polling."""
        await super().start()

        if not self._token:
            logger.warning(
                "GatewayModule: TELEGRAM_BOT_TOKEN not set. "
                "Set env TELEGRAM_BOT_TOKEN or pass token= to constructor."
            )
            return

        # Init bot
        try:
            self._bot = TelegramBot(token=self._token)
            logger.info("GatewayModule: Telegram bot initialized")
        except Exception as e:
            logger.error("GatewayModule: Failed to init Telegram bot: %s", e)
            return

        # Subscribe to outgoing events
        self.subscribe(USER_OUTPUT, self._handle_output)

        # Start polling in background
        self._polling_task = asyncio.create_task(self._polling_loop())
        logger.info("GatewayModule: started Telegram polling")

    async def stop(self):
        """Stop the module: cancel polling, cleanup."""
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None
        await super().stop()
        logger.info("GatewayModule: stopped")

    # ── Outgoing: EventBus → Telegram ─────────────────────

    async def _handle_output(self, event: Event):
        """Forward user.output events to Telegram.

        Sends the response text to the configured chat_id.
        Batches multiple outputs into one message if they arrive
        within a short window.
        """
        text = event.data.get("text", "")
        if not text or not self._bot or not self._chat_id:
            return

        # Queue the text for batched delivery
        chat_key = str(self._chat_id)
        if chat_key not in self._pending_responses:
            self._pending_responses[chat_key] = []
            # Schedule flush after 1 second to batch rapid outputs
            asyncio.create_task(self._flush_delayed(chat_key, delay=1.0))

        self._pending_responses[chat_key].append(text)

    async def _flush_delayed(self, chat_key: str, delay: float = 1.0):
        """Wait a bit for more outputs to batch, then send."""
        await asyncio.sleep(delay)
        if chat_key not in self._pending_responses:
            return
        texts = self._pending_responses.pop(chat_key, [])
        if not texts or not self._bot:
            return

        combined = "\n\n".join(texts)
        try:
            self._bot.send_message(chat_id=chat_key, text=combined)
        except Exception as e:
            logger.error("GatewayModule: send error: %s", e)

    # ── Incoming: Telegram → EventBus ─────────────────────

    async def _polling_loop(self):
        """Background polling loop that reads Telegram updates.

        Runs as an asyncio task. Each text message from Telegram is
        published as a ``user.input`` event.
        """
        if not self._bot:
            return

        offset = 0
        while True:
            try:
                await asyncio.sleep(2.0)  # poll interval

                # Use python-telegram-bot's get_updates via raw HTTP
                updates = await self._get_updates(offset=offset)
                for update in updates:
                    offset = update.get("update_id", 0) + 1
                    msg = update.get("message", {})
                    text = msg.get("text", "")
                    chat = msg.get("chat", {})
                    from_user = msg.get("from", {})

                    if not text:
                        continue

                    chat_id = str(chat.get("id", ""))
                    user_id = str(from_user.get("id", ""))

                    # Check allowed users (if configured)
                    if self._allowed_users and user_id not in self._allowed_users:
                        logger.info("GatewayModule: ignored msg from %s", user_id)
                        continue

                    logger.info(
                        "GatewayModule: << %s from %s (%s)",
                        text[:80], from_user.get("username", user_id), chat_id,
                    )

                    # Publish to EventBus
                    await self.emit(USER_INPUT, {
                        "text": text,
                        "source": "telegram",
                        "chat_id": chat_id,
                        "user_id": user_id,
                        "username": from_user.get("username", ""),
                    })

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("GatewayModule: polling error: %s", e)
                await asyncio.sleep(5.0)

    async def _get_updates(self, offset: int = 0, timeout: int = 10) -> list[dict]:
        """Fetch updates from Telegram Bot API directly.

        Returns parsed JSON updates array. Uses raw HTTP to avoid
        blocking the event loop with sync polling.
        """
        import urllib.request
        import json

        url = f"https://api.telegram.org/bot{self._token}/getUpdates"
        params = {
            "offset": offset,
            "timeout": timeout,
            "allowed_updates": json.dumps(["message"]),
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        full_url = f"{url}?{query}"

        try:
            req = urllib.request.Request(full_url)
            with urllib.request.urlopen(req, timeout=timeout + 2) as resp:
                data = json.loads(resp.read().decode())
                if data.get("ok"):
                    return data.get("result", [])
                logger.warning("GatewayModule: getUpdates error: %s", data.get("description"))
                return []
        except Exception as e:
            logger.debug("GatewayModule: getUpdates failed: %s", e)
            return []

    # ── Helpers ────────────────────────────────────────────

    def send_message(self, text: str, chat_id: str | None = None) -> bool:
        """Send a message immediately (not queued).

        Args:
            text: Message text
            chat_id: Target (default: configured chat_id)

        Returns:
            True if sent successfully.
        """
        target = chat_id or self._chat_id
        if not target or not self._bot:
            return False
        try:
            self._bot.send_message(chat_id=target, text=text)
            return True
        except Exception as e:
            logger.error("GatewayModule: send_message error: %s", e)
            return False

    # ── Config ─────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        """Check if the module has an active bot."""
        return self._bot is not None and bool(self._token)

    @property
    def configured_chat(self) -> str | int | None:
        """Return the configured chat ID."""
        return self._chat_id
