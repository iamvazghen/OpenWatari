"""MCP client — Phase 4.6 (hermetic: spawns a real local echo MCP server over stdio, no network).

Verifies the acceptance directly: a local MCP server's tools appear in Afon's registry and are
callable; schema conversion is correct; no config -> zero tools; a bad/missing server degrades cleanly
without crashing startup.

    uv run python bench/test_mcp_client.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


# A minimal, spec-correct MCP stdio server (newline-delimited JSON-RPC 2.0) that exposes one tool.
_ECHO_SERVER = r'''
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    mid, method = msg.get("id"), msg.get("method")
    if method == "initialize":
        res = {"protocolVersion": "2024-11-05", "capabilities": {},
               "serverInfo": {"name": "echo", "version": "1"}}
    elif method == "tools/list":
        res = {"tools": [{"name": "echo", "description": "Echo the given text back",
                          "inputSchema": {"type": "object",
                                          "properties": {"text": {"type": "string"}},
                                          "required": ["text"]}}]}
    elif method == "tools/call":
        args = (msg.get("params") or {}).get("arguments") or {}
        res = {"content": [{"type": "text", "text": "echo: " + str(args.get("text", ""))}]}
    elif method and method.startswith("notifications/"):
        continue  # notifications get no response
    else:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid,
                         "error": {"code": -32601, "message": "no method"}}) + "\n")
        sys.stdout.flush()
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": res}) + "\n")
    sys.stdout.flush()
'''


async def run() -> None:
    from afon.brain.mcp_client import MCPRegistry, to_openai_schema
    from afon.config import settings

    print("[1] schema conversion (pure)")
    schema = to_openai_schema("demo", {"name": "echo", "description": "Echo text",
                                       "inputSchema": {"type": "object",
                                                       "properties": {"text": {"type": "string"}}}})
    check("name is namespaced mcp__server__tool", schema["function"]["name"] == "mcp__demo__echo")
    check("parameters carried through", "text" in schema["function"]["parameters"]["properties"])
    check("missing inputSchema still yields a valid object",
          to_openai_schema("d", {"name": "t"})["function"]["parameters"]["type"] == "object")

    print("\n[2] no config -> zero tools, clean")
    settings.mcp_servers = None
    reg0 = MCPRegistry()
    n0 = await reg0.load()
    check("no MCP config -> 0 tools", n0 == 0 and reg0.handlers == {})

    print("\n[3] a real local MCP server exposes a callable tool through the registry")
    tmp = Path(tempfile.mkdtemp(prefix="afon-mcp-")) / "echo_server.py"
    tmp.write_text(_ECHO_SERVER, encoding="utf-8")
    settings.mcp_servers = json.dumps({"demo": {"command": sys.executable, "args": [str(tmp)]}})
    reg = MCPRegistry()
    n = await reg.load()
    check("one tool was loaded from the server", n == 1, str(n))
    check("tool registered under the namespaced name", "mcp__demo__echo" in reg.handlers)
    check("its schema is advertised",
          any(s["function"]["name"] == "mcp__demo__echo" for s in reg.schemas))
    out = await reg.handlers["mcp__demo__echo"]({"text": "hello"})
    check("calling the MCP tool returns its result", out == "echo: hello", out)
    await reg.stop_all()

    print("\n[4] a bad/missing server degrades cleanly")
    settings.mcp_servers = json.dumps({"nope": {"command": "this-binary-does-not-exist-xyz", "args": []}})
    regb = MCPRegistry()
    nb = await regb.load()
    check("missing server -> 0 tools, no crash", nb == 0)
    await regb.stop_all()
    settings.mcp_servers = None


def main() -> None:
    asyncio.run(run())
    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
