"""MCP Client — lazy/hot MCP server connections.

Connects to MCP servers only when needed (hot mode):
  1. User sends a query
  2. Query is analyzed against MCP tool descriptions
  3. If relevant: connect → register tools → process → disconnect
  4. If not relevant: skip (no context bloat)

Also supports manual /mcp connect, /mcp disconnect, /mcp status.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Try to import MCP SDK
try:
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from mcp import ClientSession
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False


class MCPServerConnection:
    """Persistent connection to a single MCP server."""

    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config
        self._session: ClientSession | None = None
        self._read = None
        self._write = None
        self._stdio_cm = None
        self._session_cm = None
        self._http_cm = None
        self._http_client = None
        self._tools: list[dict] = []
        self._connected = False
        self._last_error = ""
        self._reconnect_count = 0
        self._max_reconnects = 5
        self._last_used = 0.0

    async def connect(self) -> bool:
        """Connect to server and discover tools."""
        if not MCP_AVAILABLE:
            self._last_error = "MCP SDK not installed"
            return False

        try:
            if "command" in self.config:
                await self._connect_stdio()
            elif "url" in self.config:
                await self._connect_http()
            else:
                self._last_error = "Server needs 'command' (stdio) or 'url' (http)"
                return False

            # Discover tools
            tools_result = await self._session.list_tools()
            self._tools = []
            for tool in tools_result.tools:
                self._tools.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "inputSchema": tool.inputSchema if hasattr(tool, 'inputSchema') else {},
                })

            self._connected = True
            self._reconnect_count = 0
            self._last_used = time.time()
            logger.info("MCP '%s': connected, %d tools", self.name, len(self._tools))
            return True

        except Exception as e:
            self._last_error = str(e)[:200]
            self._connected = False
            logger.debug("MCP '%s': failed — %s", self.name, str(e)[:100])
            return False

    async def _connect_stdio(self):
        """Connect via stdio — keep context managers open manually."""
        command = self.config["command"]
        args = self.config.get("args", [])
        env_config = self.config.get("env", {})

        server_env = dict(os.environ)
        safe_vars = {"PATH", "HOME", "USER", "LANG", "TMPDIR", "SHELL", "TERM"}
        for key in list(server_env.keys()):
            if key not in safe_vars and key not in env_config:
                del server_env[key]
        server_env.update(env_config)

        params = StdioServerParameters(command=command, args=args, env=server_env)

        self._stdio_cm = stdio_client(params)
        self._read, self._write = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(self._read, self._write)
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()

    async def _connect_http(self):
        """Connect via HTTP transport — persistent pattern."""
        from mcp.client.streamable_http import streamable_http_client
        import httpx

        url = self.config["url"]

        # Resolve ${VAR} references in config
        import re
        def _resolve_env(s):
            if isinstance(s, str):
                return re.sub(r'\$\{(\w+)\}', lambda m: os.environ.get(m.group(1), m.group(0)), s)
            return s

        headers = {}
        for k, v in self.config.get("headers", {}).items():
            headers[k] = _resolve_env(v)

        http_client = httpx.AsyncClient(
            headers=headers,
            timeout=self.config.get("timeout", 120),
        )
        self._http_client = http_client
        self._http_cm = streamable_http_client(url, http_client=http_client)
        self._read, self._write, _ = await self._http_cm.__aenter__()

        self._session_cm = ClientSession(self._read, self._write)
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()

    async def call_tool(self, tool_name: str, arguments: dict,
                        timeout: int = 120) -> dict:
        if not self._connected or not self._session:
            return {"error": f"MCP '{self.name}' not connected"}
        try:
            self._last_used = time.time()
            result = await asyncio.wait_for(
                self._session.call_tool(tool_name, arguments),
                timeout=timeout,
            )
            if result.isError:
                return {"error": str(result.content) if result.content else "Unknown error"}
            texts = []
            for item in (result.content or []):
                if hasattr(item, 'text') and item.text:
                    texts.append(item.text)
                elif hasattr(item, 'data') and item.data:
                    texts.append(str(item.data))
            return {"result": "\n".join(texts) if texts else str(result.content)}
        except asyncio.TimeoutError:
            return {"error": f"Tool timed out after {timeout}s"}
        except Exception as e:
            return {"error": str(e)}

    async def disconnect(self):
        """Disconnect from server. Catches streamable_http cleanup errors."""
        self._connected = False
        self._tools = []
        self._last_used = 0.0
        if self._session_cm:
            try:
                await self._session_cm.__aexit__(None, None, None)
            except (Exception, BaseExceptionGroup, GeneratorExit):
                pass
            self._session_cm = None
        if self._stdio_cm:
            try:
                await self._stdio_cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._stdio_cm = None
        if self._http_cm:
            try:
                await self._http_cm.__aexit__(None, None, None)
            except (Exception, BaseExceptionGroup):
                pass
            self._http_cm = None
        if self._http_client:
            try:
                await self._http_client.aclose()
            except Exception:
                pass
            self._http_client = None
        self._session = None
        logger.info("MCP '%s': disconnected", self.name)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def tools(self) -> list[dict]:
        return list(self._tools)

    @property
    def last_error(self) -> str:
        return self._last_error

    def status(self) -> dict:
        return {
            "name": self.name,
            "connected": self._connected,
            "tools": len(self._tools),
            "error": self._last_error,
            "transport": "stdio" if "command" in self.config else "http",
            "idle_seconds": int(time.time() - self._last_used) if self._last_used else 0,
        }


class MCPClientManager:
    """Manages MCP server connections — lazy connect on demand.

    Two modes:
      - hot (default): auto-connect when query matches tool descriptions
      - manual: connect only via /mcp connect <server>
    """

    def __init__(self, servers_config: dict | None = None):
        self._servers_config = servers_config or {}
        self._connections: dict[str, MCPServerConnection] = {}
        self._hot_enabled = True
        self._active_in_query = False  # currently connected for a query

    # ── Config ────────────────────────────────────────────

    def set_servers_config(self, config: dict):
        """Set/update MCP server configurations."""
        self._servers_config = config

    def set_hot_mode(self, enabled: bool):
        """Enable/disable hot connect mode."""
        self._hot_enabled = enabled
        logger.info("MCP hot mode: %s", "ON" if enabled else "OFF")

    # ── Query-driven connect (hot mode) ───────────────────

    def find_relevant_servers(self, query: str) -> list[str]:
        """Find servers whose tool descriptions match the query.

        Simple keyword overlap: splits query into words and checks
        which server's tool descriptions have the most overlap.

        Returns:
            List of server names ordered by relevance.
        """
        if not self._hot_enabled or not MCP_AVAILABLE:
            return []

        query_lower = query.lower()
        # Extract meaningful keywords (3+ chars, not stopwords)
        stopwords = {"what", "how", "why", "when", "where", "who", "which",
                     "this", "that", "the", "and", "for", "are", "can",
                     "you", "tell", "show", "get", "find", "make", "do",
                     "with", "from", "has", "its", "not", "but", "all",
                     "any", "was", "were", "been", "have", "will", "would",
                     "could", "should", "about", "into", "over", "after",
                     "also", "very", "just"}
        keywords = {w for w in query_lower.split() if len(w) > 3 and w not in stopwords}

        if not keywords:
            return []

        scored: list[tuple[int, str]] = []
        for name in self._servers_config:
            config = self._servers_config.get(name, {})
            if not config.get("enabled", True):
                continue
            # Skip servers already connected
            conn = self._connections.get(name)
            if conn and conn.connected:
                continue

            # Score by keyword overlap with config keywords + server name
            keywords_list = config.get("keywords", [])
            if isinstance(keywords_list, str):
                keywords_list = [keywords_list]
            keywords_list = list(keywords_list) + [name]

            score = 0
            for kw in keywords_list:
                if kw.lower() in query_lower:
                    score += 2  # keyword match is strong signal
                elif any(word in kw.lower() for word in keywords):
                    score += 1  # partial word overlap

            if score > 0:
                scored.append((score, name))

        scored.sort(reverse=True)
        return [name for _, name in scored]

    async def connect_if_needed(self, query: str) -> list[str]:
        """Analyze query and connect relevant MCP servers.

        Called BEFORE processing a user message.
        If hot mode is on and query matches tool descriptions,
        connects the relevant servers and registers their tools.

        Args:
            query: User's message

        Returns:
            List of newly connected server names.
        """
        if not self._hot_enabled or not MCP_AVAILABLE:
            return []

        relevant = self.find_relevant_servers(query)
        connected = []

        for name in relevant:
            config = self._servers_config.get(name, {})
            if not config.get("enabled", True):
                continue
            if name in self._connections and self._connections[name].connected:
                continue

            conn = MCPServerConnection(name, config)
            self._connections[name] = conn
            success = await conn.connect()
            if success:
                connected.append(name)
                logger.info("MCP hot: connected '%s' for query", name)
            else:
                del self._connections[name]

        if connected:
            # Register tools in the global registry
            try:
                from zeus.tools.registry import get_registry
                reg = get_registry()
                self._register_tools_in_registry(reg)
            except ImportError:
                pass
            self._active_in_query = True

        return connected

    async def disconnect_idle(self):
        """Disconnect all MCP servers after query completes."""
        names = list(self._connections.keys())
        for name in names:
            conn = self._connections.get(name)
            if conn and conn.connected:
                try:
                    # Suppress stderr during cleanup (MCP SDK anyio noise on Android)
                    import sys, io, contextlib
                    with contextlib.redirect_stderr(io.StringIO()):
                        await conn.disconnect()
                except (Exception, BaseExceptionGroup):
                    pass
                if name in self._connections:
                    del self._connections[name]

        # Unregister MCP tools from registry
        if self._active_in_query:
            try:
                from zeus.tools.registry import get_registry
                reg = get_registry()
                for tname in list(reg._tools.keys()):
                    if tname.startswith("mcp_"):
                        del reg._tools[tname]
            except (ImportError, AttributeError):
                pass
            self._active_in_query = False

    # ── Manual connect/disconnect ─────────────────────────

    async def connect_server(self, name: str) -> bool:
        """Manually connect a specific server.

        Args:
            name: Server name from config

        Returns:
            True if connected.
        """
        config = self._servers_config.get(name)
        if not config:
            logger.warning("MCP: unknown server '%s'", name)
            return False
        if not config.get("enabled", True):
            logger.warning("MCP: server '%s' is disabled in config", name)
            return False

        # If already connected, just return
        if name in self._connections and self._connections[name].connected:
            return True

        conn = MCPServerConnection(name, config)
        self._connections[name] = conn
        success = await conn.connect()

        if success:
            try:
                from zeus.tools.registry import get_registry
                self._register_tools_in_registry(get_registry())
            except ImportError:
                pass

        return success

    async def disconnect_server(self, name: str) -> bool:
        """Manually disconnect a specific server and unregister its tools."""
        conn = self._connections.get(name)
        if not conn:
            return False
        await conn.disconnect()
        del self._connections[name]

        # Unregister this server's tools
        try:
            from zeus.tools.registry import get_registry
            reg = get_registry()
            prefix = f"mcp_{name.replace('-', '_').replace('.', '_')}_"
            for tname in list(reg._tools.keys()):
                if tname.startswith(prefix):
                    del reg._tools[tname]
        except (ImportError, AttributeError):
            pass

        return True

    # ── Tool registration ─────────────────────────────────

    def _register_tools_in_registry(self, registry) -> int:
        """Register all connected MCP tools in a registry."""
        count = 0
        from zeus.tools.registry import get_registry

        for name, conn in self._connections.items():
            if not conn.connected:
                continue
            safe_server = name.replace("-", "_").replace(".", "_")

            for tool in conn.tools:
                mcp_name = tool["name"]
                safe_name = mcp_name.replace("-", "_").replace(".", "_")
                registered_name = f"mcp_{safe_server}_{safe_name}"

                def make_handler(conn=conn, tn=mcp_name):
                    def handler(params):
                        import asyncio
                        try:
                            loop = asyncio.new_event_loop()
                            result = loop.run_until_complete(conn.call_tool(tn, params))
                            loop.close()
                            if "error" in result:
                                return f"❌ MCP error: {result['error']}"
                            return result.get("result", "")
                        except Exception as e:
                            return f"❌ {e}"
                    return handler

                input_schema = tool.get("inputSchema", {})
                schema = {
                    "name": registered_name,
                    "description": f"[MCP {name}] {tool.get('description', mcp_name)[:200]}",
                    "parameters": {
                        "type": "object",
                        "properties": input_schema.get("properties", {}),
                        "required": input_schema.get("required", []),
                    },
                }

                reg = get_registry()
                reg.register(registered_name, schema, make_handler(conn, mcp_name))
                count += 1

        return count

    # ── Status ────────────────────────────────────────────

    def get_status(self) -> list[dict]:
        return [conn.status() for conn in self._connections.values()]

    def format_status(self) -> str:
        statuses = self.get_status()
        hot = "✅" if self._hot_enabled else "⏸"

        lines = [f"\n📡 MCP ({hot} hot mode):\n"]
        if not self._servers_config:
            lines.append("   No servers configured.\n")
            return "".join(lines)

        for s_name, s_config in self._servers_config.items():
            if not s_config.get("enabled", True):
                lines.append(f"   ⏸ {s_name} (disabled)")
                continue
            conn = self._connections.get(s_name)
            if conn and conn.connected:
                lines.append(f"   ✅ {s_name}")
                for t in conn.tools:
                    lines.append(f"       • {t['name']}: {t.get('description', '')[:60]}")
            else:
                lines.append(f"   ⚡ {s_name} (lazy — connects on need)")

        total_tools = sum(len(conn.tools) for conn in self._connections.values() if conn.connected)
        total_connected = sum(1 for conn in self._connections.values() if conn.connected)
        lines.append(f"\n   Active: {total_connected} server(s), {total_tools} tool(s)")
        return "\n".join(lines)

    @property
    def connected_count(self) -> int:
        return sum(1 for c in self._connections.values() if c.connected)


# Singleton
_manager: MCPClientManager | None = None


def get_mcp_manager() -> MCPClientManager:
    global _manager
    if _manager is None:
        _manager = MCPClientManager()
    return _manager
