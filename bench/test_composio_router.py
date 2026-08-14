"""Composio tool router — Phase 7 (hermetic, no network).

Verifies the two-step router that gives Afon 250+ external apps with a TINY tool footprint:
  * composio_find_tools searches the owner's CONNECTED apps (filters out unconnected ones) and lists
    slug + required args;
  * composio_run_tool executes by slug and surfaces the result;
  * WRITES (create/send/post/charge) are confirm-gated, READS (get/list/search) run free;
  * everything degrades to a spoken note without a key, and the 2 tools live in the lazy "apps" group.

    uv run python bench/test_composio_router.py
"""

from __future__ import annotations

import asyncio
import sys
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


def test_confirm_gating() -> None:
    print("[1] writes are confirm-gated, reads run free (slug-verb heuristic)")
    from afon.brain.proactive import _composio_write, confirm_required

    for slug in ("GITHUB_CREATE_AN_ISSUE", "SLACKBOT_CHAT_POST_MESSAGE", "STRIPE_CREATE_CHARGE",
                 "GOOGLESHEETS_ADD_ROW", "GITHUB_DELETE_A_REPOSITORY"):
        check(f"write gated: {slug}", _composio_write(slug) is True, slug)
    for slug in ("GITHUB_GET_THE_AUTHENTICATED_USER", "GITHUB_LIST_REPOSITORIES",
                 "SLACK_SEARCH_MESSAGES", "LINEAR_LIST_ISSUES"):
        check(f"read free: {slug}", _composio_write(slug) is False, slug)
    check("unknown verb -> gated (safe)", _composio_write("FOO_BAR_BAZ") is True)
    check("confirm_required gates a write",
          confirm_required("composio_run_tool", {"tool_slug": "STRIPE_CREATE_CHARGE"}) is True)
    check("confirm_required frees a read",
          confirm_required("composio_run_tool", {"tool_slug": "GITHUB_GET_THE_AUTHENTICATED_USER"})
          is False)


async def test_find_and_run() -> None:
    print("\n[2] find scopes to connected apps; run executes by slug")
    import afon.brain.tools.composio as cx
    from afon.config import settings

    settings.composio_api_key = "test-key"
    # Clear the memo the way a fresh process would. `_context_resolved` is the actual memo key since
    # J3.8 — clearing only the values leaves the cache live and this test silently stops testing.
    cx._user_id = None
    cx._active_toolkits = None
    cx._context_resolved = False

    async def fake_get(path, params=None):
        if path == "/connected_accounts":
            return {"items": [{"status": "ACTIVE", "toolkit": {"slug": "github"}, "user_id": "u1"}]}
        if path == "/tools":
            return {"items": [
                {"slug": "GITHUB_CREATE_AN_ISSUE", "toolkit": {"slug": "github"},
                 "description": "Create an issue", "input_parameters": {"required": ["owner", "repo", "title"]}},
                {"slug": "BITBUCKET_CREATE_ISSUE", "toolkit": {"slug": "bitbucket"},
                 "description": "x", "input_parameters": {}},
            ]}
        return {}

    posted = {}

    async def fake_post(path, body):
        posted["path"] = path
        posted["body"] = body
        return {"successful": True, "data": {"login": "iamvazghen"}}

    cx._get, cx._post = fake_get, fake_post
    out = await cx.composio_find_tools({"query": "create issue"})
    check("connected github tool is offered", "GITHUB_CREATE_AN_ISSUE" in out, out)
    check("unconnected bitbucket tool is filtered out", "BITBUCKET" not in out, out)
    check("required args are surfaced", "owner" in out and "title" in out, out)

    out2 = await cx.composio_run_tool({"tool_slug": "GITHUB_GET_THE_AUTHENTICATED_USER", "arguments": {}})
    check("run executes the right slug with the resolved user_id",
          posted["path"].endswith("GITHUB_GET_THE_AUTHENTICATED_USER") and posted["body"]["user_id"] == "u1")
    check("run surfaces the result", "iamvazghen" in out2, out2)

    settings.composio_api_key = None
    cx._user_id = cx._active_toolkits = None
    cx._context_resolved = False
    out3 = await cx.composio_find_tools({"query": "x"})
    from afon.brain.tools.base import is_not_configured
    check("no key -> graceful not-configured note", is_not_configured(out3), out3)


def test_registry_wiring() -> None:
    print("\n[3] the 2 router tools are registered in the lazy 'apps' group")
    from afon.brain.tools import group_tool_schemas, groups_for_text, tool_handlers

    h = tool_handlers()
    check("composio_find_tools registered", "composio_find_tools" in h)
    check("composio_run_tool registered", "composio_run_tool" in h)
    check("'apps' group activates on an app utterance", "apps" in groups_for_text("create an issue on github"))
    names = [s["function"]["name"] for s in group_tool_schemas("apps")]
    check("both tools surface when 'apps' activates", set(names) == {"composio_find_tools", "composio_run_tool"},
          str(names))


async def main() -> None:
    test_confirm_gating()
    await test_find_and_run()
    test_registry_wiring()
    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
