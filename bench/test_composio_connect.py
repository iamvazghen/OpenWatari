"""Composio (and any MCP server) connectivity check — end-to-end over the real MCP transport.

This drives the SAME path the live brain uses (`brain/mcp_client.py`): it reads `AFON_MCP_SERVERS`,
spawns each configured server, runs `initialize` + `tools/list`, and reports the tools each exposes.
For Composio that proves your MCP URL + connected app accounts are reachable and which tools Afon
will gain (namespaced `mcp__<server>__<tool>`). Optionally calls ONE read-only tool by name to prove a
round-trip.

    uv run python bench/test_composio_connect.py
    uv run python bench/test_composio_connect.py --call mcp__composio__GITHUB_LIST_REPOSITORIES

If `AFON_MCP_SERVERS` is empty it prints setup instructions and exits 0 (nothing to test yet — not a
failure). A server that can't be reached is reported clearly. No app accounts are modified (list-only,
unless you pass --call with a tool you know is read-only).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_SETUP = """\
No MCP servers configured yet (AFON_MCP_SERVERS is empty) — nothing to test.

To wire Composio (the recommended breadth layer):
  1. Create a Composio account and an API key at https://app.composio.dev
  2. In the dashboard create a Tool Router / MCP server, then CONNECT the app accounts you want
     (GitHub, Slack, Google Drive, …) — each via its OAuth button.
  3. Copy the generated MCP server URL.
  4. Put this single line in .env (Node/npx must be installed — it is, the website uses npm):
       AFON_MCP_SERVERS={"composio":{"command":"npx","args":["-y","mcp-remote","<YOUR_MCP_URL>"]}}
  5. Re-run this script. It should list the tools your connected apps expose.
"""


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--call", help="after listing, call this exact tool name with no args (read-only!)")
    args = ap.parse_args()

    from afon.brain.mcp_client import MCPRegistry, _load_config

    cfg = _load_config()
    if not cfg:
        print(_SETUP)
        return 0

    print("=" * 70)
    print(" MCP / Composio connectivity")
    print("=" * 70)
    print(f"configured servers: {', '.join(cfg.keys())}\n")

    reg = MCPRegistry()
    try:
        n = await asyncio.wait_for(reg.load(), timeout=90)
    except asyncio.TimeoutError:
        print("  ✗ timed out starting/handshaking a server (mcp-remote may be waiting on a browser "
              "auth — complete it once, then re-run).")
        await reg.stop_all()
        return 1

    if n == 0:
        print("  ✗ no tools exposed. Either no app accounts are connected in Composio yet, or the "
              "server URL/command is wrong. Check the dashboard connections and the URL.")
        await reg.stop_all()
        return 1

    # Group the loaded schemas by server prefix for a readable report.
    by_server: dict[str, list[str]] = {}
    for s in reg.schemas:
        name = s["function"]["name"]                # mcp__<server>__<tool>
        server = name.split("__")[1] if name.startswith("mcp__") else "?"
        by_server.setdefault(server, []).append(name)
    for server, names in by_server.items():
        print(f"  ✓ {server}: {len(names)} tool(s)")
        for nm in names[:12]:
            print(f"      - {nm}")
        if len(names) > 12:
            print(f"      … and {len(names) - 12} more")

    print(f"\n  TOTAL: {n} tool(s) now available to Afon (namespaced mcp__<server>__<tool>).")

    if args.call:
        print(f"\n  calling {args.call} (read-only round-trip)…")
        handler = reg.handlers.get(args.call)
        if handler is None:
            print(f"  ✗ no such tool loaded: {args.call}")
        else:
            try:
                out = await asyncio.wait_for(handler({}), timeout=30)
                print(f"  ✓ response: {str(out)[:400]}")
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ call failed: {type(e).__name__}: {e}")

    await reg.stop_all()
    print("\n=== connectivity OK ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
