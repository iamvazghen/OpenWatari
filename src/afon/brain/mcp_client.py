"""Minimal MCP client — expose external MCP-server tools through Afon's normal registry (Phase 4.6).

The Model Context Protocol (MCP) stdio transport is just newline-delimited JSON-RPC 2.0 over a child
process's stdin/stdout, so a tiny client covers it with NO new dependency: spawn the configured server,
`initialize`, `tools/list`, and `tools/call`. Each MCP tool is converted into the same OpenAI function
schema + async handler everything else in the registry uses, namespaced `mcp__<server>__<tool>` so
names never collide.

Security boundary: ONLY servers explicitly listed in ``settings.mcp_servers`` are ever launched —
there is no default server, no arbitrary filesystem/network access. Missing config -> zero tools.
A server that fails to start (bad command, missing binary) is logged and skipped; it never blocks
startup or crashes a turn.

Config (``AFON_MCP_SERVERS``) is a JSON object, inline or a path to a .json file::

    {"filesystem": {"command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/safe/dir"]}}
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from afon.config import settings

_PROTOCOL_VERSION = "2024-11-05"


def to_openai_schema(server: str, tool: dict[str, Any]) -> dict[str, Any]:
    """Convert one MCP tool descriptor into an OpenAI function schema (namespaced by server)."""
    params = tool.get("inputSchema") or {"type": "object", "properties": {}}
    # OpenAI expects a JSON-schema object; MCP inputSchema already is one. Ensure the basics exist.
    if "type" not in params:
        params = {**params, "type": "object"}
    params.setdefault("properties", {})
    return {
        "type": "function",
        "function": {
            "name": f"mcp__{server}__{tool['name']}",
            "description": (tool.get("description") or f"{tool['name']} (via MCP server '{server}')")[:1024],
            "parameters": params,
        },
    }


def _load_config() -> dict[str, dict]:
    """Parse ``settings.mcp_servers`` (inline JSON or a path to a .json file). {} if unset/invalid."""
    raw = (settings.mcp_servers or "").strip()
    if not raw:
        return {}
    try:
        if not raw.startswith("{"):
            p = Path(raw).expanduser()
            raw = p.read_text(encoding="utf-8") if p.is_file() else "{}"
        cfg = json.loads(raw)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"MCP config could not be read ({type(e).__name__}); no MCP tools loaded")
        return {}


class StdioMCPServer:
    """One MCP server spoken to over stdio (newline-delimited JSON-RPC 2.0)."""

    def __init__(self, name: str, command: str, args: list[str], env: dict | None = None) -> None:
        self.name = name
        self._command = command
        self._args = args or []
        self._env = env
        self._proc: asyncio.subprocess.Process | None = None
        self._id = 0
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        import os

        env = {**os.environ, **(self._env or {})}
        self._proc = await asyncio.create_subprocess_exec(
            self._command, *self._args,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=env,
        )
        await self._request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "afon", "version": "1"},
        })
        await self._notify("notifications/initialized")

    async def _send(self, msg: dict) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()

    async def _notify(self, method: str, params: dict | None = None) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def _request(self, method: str, params: dict | None = None, timeout: float = 20.0) -> Any:
        assert self._proc and self._proc.stdout
        async with self._lock:
            self._id += 1
            req_id = self._id
            await self._send({"jsonrpc": "2.0", "id": req_id, "method": method,
                              "params": params or {}})
            # Read lines until the response with our id (skip notifications / other messages).
            while True:
                line = await asyncio.wait_for(self._proc.stdout.readline(), timeout=timeout)
                if not line:
                    raise RuntimeError(f"MCP server '{self.name}' closed the connection")
                try:
                    msg = json.loads(line.decode("utf-8").strip())
                except json.JSONDecodeError:
                    continue  # server may emit non-JSON log noise; ignore
                if msg.get("id") == req_id:
                    if "error" in msg:
                        raise RuntimeError(f"MCP error: {msg['error']}")
                    return msg.get("result")

    async def list_tools(self) -> list[dict]:
        result = await self._request("tools/list")
        return (result or {}).get("tools") or []

    async def call(self, tool: str, arguments: dict) -> str:
        result = await self._request("tools/call", {"name": tool, "arguments": arguments or {}})
        # MCP returns content blocks; flatten the text ones into a single string.
        blocks = (result or {}).get("content") or []
        texts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
        out = "\n".join(t for t in texts if t).strip()
        if (result or {}).get("isError"):
            return f"MCP tool '{tool}' reported an error: {out or 'unknown error'}"
        return out or "(the MCP tool returned no text)"

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except (ProcessLookupError, asyncio.TimeoutError):
                pass


class MCPRegistry:
    """Starts the configured MCP servers and exposes their tools as schemas + handlers."""

    def __init__(self) -> None:
        self.servers: list[StdioMCPServer] = []
        self.schemas: list[dict[str, Any]] = []
        self.handlers: dict[str, Callable[[dict], Awaitable[str]]] = {}

    async def load(self) -> int:
        """Start every configured server and collect its tools. Returns the number of tools loaded.
        Best-effort: a server that fails to start is skipped with a warning, never raised."""
        cfg = _load_config()
        for name, spec in cfg.items():
            if not isinstance(spec, dict) or not spec.get("command"):
                logger.warning(f"MCP server '{name}' has no command; skipping")
                continue
            server = StdioMCPServer(name, spec["command"], spec.get("args") or [], spec.get("env"))
            try:
                await server.start()
                tools = await server.list_tools()
            except Exception as e:  # noqa: BLE001 — a bad/missing server must not break startup
                logger.warning(f"MCP server '{name}' unavailable ({type(e).__name__}); skipping")
                await server.stop()
                continue
            self.servers.append(server)
            for tool in tools:
                self.schemas.append(to_openai_schema(name, tool))
                self.handlers[f"mcp__{name}__{tool['name']}"] = self._make_handler(server, tool["name"])
            logger.info(f"MCP server '{name}': exposed {len(tools)} tool(s)")
        return len(self.handlers)

    def _make_handler(self, server: StdioMCPServer, tool: str) -> Callable[[dict], Awaitable[str]]:
        async def handler(args: dict) -> str:
            try:
                return await server.call(tool, args)
            except Exception as e:  # noqa: BLE001 — degrade like every other tool
                return f"The MCP tool ({server.name}/{tool}) hit an error: {type(e).__name__}."
        return handler

    async def stop_all(self) -> None:
        for s in self.servers:
            await s.stop()


# Process-wide registry (empty until load() runs at brain warmup).
MCP = MCPRegistry()
