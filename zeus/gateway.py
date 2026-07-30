"""Gateway adapter — wraps Hermes Agent's multi-platform messaging gateway.

Zeus reuses Hermes's gateway infrastructure (Telegram, Discord, Slack, etc.)
instead of reimplementing 20+ platform adapters.

Phase 1+: full gateway integration.
Phase 0: skeleton that imports and configures the Hermes gateway.
"""

from __future__ import annotations
import os
import sys
from pathlib import Path

_HERMES_PATH = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "hermes-agent"
if str(_HERMES_PATH) not in sys.path:
    sys.path.insert(0, str(_HERMES_PATH))


def get_gateway_config() -> dict | None:
    """Read Hermes gateway configuration.

    Returns the parsed GatewayConfig dict, or None if not configured.
    """
    try:
        from gateway.config import load_gateway_config
        config = load_gateway_config()
        return config
    except ImportError:
        return None
    except Exception:
        return None


def list_platforms() -> list[dict]:
    """List configured gateway platforms."""
    try:
        from gateway.platform_registry import list_platforms
        return list_platforms()
    except ImportError:
        return []


def start_gateway(detach: bool = False):
    """Start the Hermes gateway.

    Args:
        detach: If True, run in background (service mode).
    """
    try:
        from gateway.run import run_gateway
        run_gateway(detach=detach)
    except ImportError as e:
        print(f"⚠ Cannot start gateway: {e}")
        print("  Hermes gateway is not available. Install hermes-agent.")
    except Exception as e:
        print(f"⚠ Gateway error: {e}")


def send_message(platform: str, chat_id: str, text: str):
    """Send a message via the gateway.

    Args:
        platform: Platform name (telegram, discord, etc.)
        chat_id: Target chat/channel ID
        text: Message text
    """
    try:
        from gateway.delivery import DeliveryRouter
        router = DeliveryRouter()
        router.send(platform=platform, chat_id=chat_id, text=text)
    except ImportError as e:
        print(f"⚠ Cannot send message: {e}")
    except Exception as e:
        print(f"⚠ Send error: {e}")