"""Telegram gateway adapter for Zeus.

Minimal, self-contained Telegram bot using python-telegram-bot.

Usage:
    from zeus.gateway import TelegramBot
    bot = TelegramBot(token="YOUR_TOKEN")
    bot.send_message(chat_id="@username", text="Hello!")
    bot.run_polling(handler=my_handler)  # blocks
"""

from __future__ import annotations
import asyncio
import logging
import os
from typing import Callable, Any

logger = logging.getLogger(__name__)

# Try to find Telegram token from various sources
def _find_token() -> str | None:
    """Find Telegram bot token from env or Hermes config."""
    # Direct env
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        return token

    # Hermes .env file
    hermes_env = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(hermes_env):
        with open(hermes_env) as f:
            for line in f:
                line = line.strip()
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    return None


class TelegramBot:
    """Minimal Telegram bot using python-telegram-bot."""

    def __init__(self, token: str | None = None):
        self.token = token or _find_token()
        if not self.token:
            raise ValueError(
                "Telegram bot token required. Set TELEGRAM_BOT_TOKEN env var "
                "or pass token= to TelegramBot()."
            )
        self._app = None

    def _get_app(self):
        """Lazy-init the Application (python-telegram-bot)."""
        if self._app is None:
            from telegram.ext import Application
            self._app = Application.builder().token(self.token).build()
        return self._app

    def send_message(
        self,
        chat_id: str | int,
        text: str,
        parse_mode: str = "Markdown",
        disable_web_page_preview: bool = True,
    ) -> dict:
        """Send a message to a Telegram chat.

        Args:
            chat_id: Chat ID or @username
            text: Message text
            parse_mode: 'Markdown' or 'HTML'
            disable_web_page_preview: Don't show link previews

        Returns:
            Sent message data as dict.
        """
        import asyncio

        async def _send():
            bot = self._get_app().bot
            msg = await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )
            return msg.to_dict()

        return asyncio.run(_send())

    def send_document(
        self,
        chat_id: str | int,
        document_path: str,
        caption: str | None = None,
    ):
        """Send a file/document to a Telegram chat.

        Args:
            chat_id: Chat ID or @username
            document_path: Path to file on disk
            caption: Optional caption
        """
        import asyncio

        async def _send():
            bot = self._get_app().bot
            with open(document_path, "rb") as f:
                msg = await bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    caption=caption,
                )
            return msg.to_dict()

        return asyncio.run(_send())

    def run_polling(
        self,
        handler: Callable | None = None,
        stop_on_error: bool = False,
    ):
        """Run the bot in polling mode (blocking).

        Args:
            handler: Optional message handler function.
                      Signature: handler(update, context) -> None
            stop_on_error: If True, stop on unhandled exceptions.
        """
        app = self._get_app()

        if handler:
            from telegram.ext import MessageHandler, filters
            app.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, handler)
            )

        logger.info("Starting Telegram bot polling...")
        app.run_polling(stop_on_error=stop_on_error)

    def set_webhook(self, url: str):
        """Set a webhook URL for the bot."""
        import asyncio

        async def _set():
            bot = self._get_app().bot
            await bot.set_webhook(url=url)
            info = await bot.get_webhook_info()
            return info.to_dict()

        return asyncio.run(_set())

    def delete_webhook(self):
        """Remove the webhook."""
        import asyncio

        async def _del():
            bot = self._get_app().bot
            await bot.delete_webhook()
            return True

        return asyncio.run(_del())


def send_notification(text: str, chat_id: str | None = None) -> str:
    """Quick helper to send a one-off notification via Telegram.

    Uses the default token and chat_id from env or Hermes config.

    Args:
        text: Message text
        chat_id: Override chat ID (default: from TELEGRAM_CHAT_ID env)

    Returns:
        Status message.
    """
    target = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not target:
        # Try to find from Hermes config
        try:
            import yaml
            config_path = os.path.expanduser("~/.hermes/config.yaml")
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config = yaml.safe_load(f) or {}
                channels = config.get("gateway", {}).get("channels", {})
                tg = channels.get("telegram", {})
                target = tg.get("chat_id")
        except Exception:
            pass

    if not target:
        return "⚠ TELEGRAM_CHAT_ID not set. Set env or configure in Hermes."

    try:
        bot = TelegramBot()
        bot.send_message(chat_id=target, text=text)
        return f"✅ Sent to {target}"
    except Exception as e:
        return f"⚠ Telegram error: {e}"


# Convenience: one-shot send
def send(chat_id: str | int, text: str, token: str | None = None) -> dict:
    """One-shot send a message. Creates bot, sends, discards.

    Args:
        chat_id: Target chat
        text: Message text
        token: Bot token (default: from env)

    Returns:
        Sent message data.
    """
    bot = TelegramBot(token=token)
    return bot.send_message(chat_id=chat_id, text=text)