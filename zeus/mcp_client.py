"""MCP Client — connect to MCP servers and use their tools.

Integrates with Zeus ToolRegistry: tools from MCP servers appear
as first-class tools with mcp_{server}_{tool} naming.

Supports:
  - Stdio transport (command + args)
  - HTTP transport (url + headers)
  - Tool discovery via list_tools()
  - Persistent connections with auto-reconnect
  - Tool call forwarding with timeout
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Try to import MCP SDK
try:
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from mcp import ClientSession
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    logger.warning("MCP SDK not available — install with: pip install mcp")


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
        self._tools: list[dict] = []
        self._connected = False
        self._last_error = ""
        self._reconnect_count = 0
        self._max_reconnects = 5

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
            logger.info("MCP '%s': connected, %d tools", self.name, len(self._tools))
            return True

        except Exception as e:
            self._last_error = str(e)[:200]
            self._connected = False
            logger.warning("MCP '%s': failed — %s", self.name, str(e)[:100])
            return False

    async def _connect_stdio(self):
        """Connect via stdio — keep context managers open manually."""
        command = self.config["command"]
        args = self.config.get("args", [])
        env_config = self.config.get("env", {})

        # Filtered environment
        server_env = dict(os.environ)
        safe_vars = {"PATH", "HOME", "USER", "LANG", "TMPDIR", "SHELL", "TERM"}
        for key in list(server_env.keys()):
            if key not in safe_vars and key not in env_config:
                del server_env[key]
        server_env.update(env_config)

        params = StdioServerParameters(command=command, args=args, env=server_env)

        # Open stdio_client context manager (persistent)
        self._stdio_cm = stdio_client(params)
        self._read, self._write = await self._stdio_cm.__aenter__()

        # Open ClientSession context manager (persistent)
        self._session_cm = ClientSession(self._read, self._write)
        self._session = await self._session_cm.__aenter__()

        await self._session.initialize()

    async def _connect_http(self):
        """Connect via HTTP transport."""
        from mcp.client.streamable_http import StreamableHTTPClientSession
        import httpx

        url = self.config["url"]
        headers = self.config.get("headers", {})

        client = httpx.AsyncClient(headers=headers)
        self._http_client = client
        self._session = StreamableHTTPClientSession(client, url)
        await self._session.initialize()

    async def call_tool(self, tool_name: str, arguments: dict,
                        timeout: int = 120) -> dict:
        """Call a tool on the MCP server."""
        if not self._connected or not self._session:
            return {"error": f"MCP '{self.name}' not connected"}

        try:
            result = await asyncio.wait_for(
                self._session.call_tool(tool_name, arguments),
                timeout=timeout,
            )

            if result.isError:
                return {"error": str(result.content) if result.content else "Unknown error"}

            # Extract text content
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
        """Disconnect from server."""
        self._connected = False
        # Close session
        if self._session_cm:
            try:
                await self._session_cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._session_cm = None
        # Close stdio
        if self._stdio_cm:
            try:
                await self._stdio_cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._stdio_cm = None
        # Close HTTP client
        if hasattr(self, '_http_client'):
            try:
                await self._http_client.aclose()
            except Exception:
                pass
        self._session = None
        logger.info("MCP '%s': disconnected", self.name)

    async def reconnect(self) -> bool:
        """Reconnect with exponential backoff."""
        await self.disconnect()
        self._reconnect_count += 1

        if self._reconnect_count > self._max_reconnects:
            logger.warning("MCP '%s': max reconnects reached", self.name)
            return False

        delay = min(2 ** self._reconnect_count, 30)
        logger.info("MCP '%s': reconnecting in %ds (attempt %d/%d)",
                    self.name, delay, self._reconnect_count, self._max_reconnects)
        await asyncio.sleep(delay)
        return await self.connect()

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
            "reconnects": self._reconnect_count,
            "transport": "stdio" if "command" in self.config else "http",
        }


class MCPClientManager:
    """Manages MCP server connections and tool registration."""

    def __init__(self, servers_config: dict | None = None):
        self._servers_config = servers_config or {}
        self._connections: dict[str, MCPServerConnection] = {}
        self._tool_registry = None

    async def start(self):
        """Connect to all configured MCP servers."""
        if not MCP_AVAILABLE:
            logger.warning("MCP SDK not available — skipping")
            return

        for name, config in self._servers_config.items():
            if not config.get("enabled", True):
                continue

            conn = MCPServerConnection(name, config)
            self._connections[name] = conn

            success = await conn.connect()
            if not success:
                logger.warning("MCP '%s': connect failed — %s", name, conn.last_error)

        connected = sum(1 for c in self._connections.values() if c.connected)
        total = len(self._connections)
        if total > 0:
            logger.info("MCP: %d/%d connected, %d tools",
                       connected, total,
                       sum(len(c.tools) for c in self._connections.values() if c.connected))

    def register_tools(self, registry) -> int:
        """Register MCP tools in a ToolRegistry.

        Each tool becomes mcp_{server}_{tool_name}.
        """
        self._tool_registry = registry
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

                # Create sync handler for this tool
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

                # Build schema
                input_schema = tool.get("inputSchema", {})
                parameters = input_schema.get("properties", {})
                required = input_schema.get("required", [])

                schema = {
                    "name": registered_name,
                    "description": f"[MCP {name}] {tool.get('description', mcp_name)[:200]}",
                    "parameters": {
                        "type": "object",
                        "properties": parameters,
                        "required": required,
                    },
                }

                reg = get_registry()
                reg.register(registered_name, schema, make_handler(conn, mcp_name))
                count += 1

        logger.info("MCP: registered %d tools", count)
        return count

    async def stop(self):
        """Disconnect all servers."""
        for conn in self._connections.values():
            await conn.disconnect()
        self._connections.clear()

    def get_status(self) -> list[dict]:
        return [conn.status() for conn in self._connections.values()]

    def format_status(self) -> str:
        statuses = self.get_status()
        if not statuses:
            return "📡 No MCP servers configured.\n   Add mcp_servers to config."

        lines = [f"\n📡 MCP Servers ({len(statuses)}):\n"]
        total_tools = 0
        for s in statuses:
            icon = "✅" if s["connected"] else "❌"
            transport = s.get("transport", "?")
            tools_str = f"{s['tools']} tools" if s['tools'] else "no tools"
            error = f" — {s['error']}" if s.get("error") else ""
            lines.append(f"  {icon} {s['name']} [{transport}] — {tools_str}{error}")
            total_tools += s["tools"]
        lines.append(f"\n  Total: {len(statuses)} servers, {total_tools} tools")
        return "\n".join(lines)

    async def reconnect_server(self, name: str) -> bool:
        conn = self._connections.get(name)
        if not conn:
            return False
        success = await conn.reconnect()
        if success and self._tool_registry:
            self.register_tools(self._tool_registry)
        return success

    @property
    def connected_count(self) -> int:
        return sum(1 for c in self._connections.values() if c.connected)

    @property
    def total_servers(self) -> int:
        return len(self._connections)

    @property
    def total_tools(self) -> int:
        return sum(len(c.tools) for c in self._connections.values() if c.connected)


# Singleton
_manager: MCPClientManager | None = None


def get_mcp_manager() -> MCPClientManager:
    global _manager
    if _manager is None:
        _manager = MCPClientManager()
    return _manager
