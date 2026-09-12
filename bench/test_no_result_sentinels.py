"""No-result sentinels (MASTER 3.6) — every read/lookup tool speaks a COMPLETE sentence when empty.

The failure this guards against: a read tool that returns "" or a bare empty list lets a weak LLM
dress the emptiness up as a guess ("Yes sir, you have a meeting at 3"). So every lookup that can come
back empty must return a fully-formed spoken sentence the agent can relay verbatim — non-empty, and
clearly negative. This drives each tool into its empty/unconfigured path and asserts exactly that.

Offline: tools are driven with no credentials (the unconfigured path) or an empty store, so no
network and no real accounts are touched.

    uv run python bench/test_no_result_sentinels.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f"  {detail}" if detail and not ok else ""))


def _is_complete_sentence(s: str) -> bool:
    """A relayable spoken reply: non-trivial length, ends with sentence punctuation."""
    return isinstance(s, str) and len(s.strip()) >= 8 and s.strip()[-1] in ".!?"


def main() -> None:
    from afon.config import settings

    # Unconfigure the credentialed integrations so each read tool takes its empty/degraded path.
    for attr in ("notion_token", "google_refresh_token", "google_client_id", "telegram_api_id",
                 "telegram_api_hash", "telegram_bot_token"):
        if hasattr(settings, attr):
            setattr(settings, attr, None)
    # Keep this OFFLINE: semantic recall would otherwise hit the Jina embeddings API over the network.
    settings.memory_semantic_enabled = False
    settings.jina_api_key = None

    print("[1] recall (memory) — all layers empty returns a complete negative")
    from afon.brain.memory import MemoryStore
    from afon.brain.tools import memory as mem_tools
    # recall is CROSS-LAYER (L1 store + L3 vault + …). Empty the L1 store AND point the vault at an
    # empty dir, else the real vault's fuzzy matches make "empty" impossible to test.
    _saved_vault = settings.vault_path
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as vd:
        mem_tools.STORE = MemoryStore(base_dir=Path(d))  # empty L1/L2
        settings.vault_path = vd                          # empty L3
        try:
            r = asyncio.run(mem_tools.recall({"query": "submarines in antarctica"}))
            check("recall: complete sentence", _is_complete_sentence(r), r)
            check("recall: clearly negative", any(w in r.lower() for w in
                  ("don't have", "nothing", "no ")), r)
        finally:
            settings.vault_path = _saved_vault

    print("\n[2] reminders — no reminders set returns a complete negative")
    from afon.brain.tools import reminders
    r = asyncio.run(reminders.list_reminders({}))
    check("list_reminders: complete sentence", _is_complete_sentence(r), r)
    check("list_reminders: clearly negative", "no reminder" in r.lower(), r)
    r = asyncio.run(reminders.cancel_reminder({"id": "nonexistent-xyz"}))
    check("cancel_reminder(unknown): complete sentence", _is_complete_sentence(r), r)
    check("cancel_reminder(unknown): says couldn't find", "couldn't find" in r.lower(), r)

    print("\n[3] calendar — unconfigured returns a complete note (not a blank)")
    from afon.brain.tools import calendar
    r = asyncio.run(calendar.list_events({"days": 1}))
    check("list_events: complete sentence", _is_complete_sentence(r), r)

    print("\n[4] gmail — unconfigured read returns a complete note")
    from afon.brain.tools import gmail
    r = asyncio.run(gmail.read_email({"query": "invoice"}))
    check("read_email: complete sentence", _is_complete_sentence(r), r)

    print("\n[5] tasks — no matching background task returns a complete negative")
    from afon.brain.tools import tasks
    r = asyncio.run(tasks.task_status({"query": "zzz-nonexistent"}))
    check("task_status(none): complete sentence", _is_complete_sentence(r), r)

    print("\n[6] telegram — unconfigured check returns a complete note")
    from afon.brain.tools import telegram
    r = asyncio.run(telegram.check_telegram({}))
    check("check_telegram: complete sentence", _is_complete_sentence(r), r)

    print("\n[7] vault — no match returns a complete negative")
    from afon.brain.tools import vault
    saved_vault = settings.vault_path
    try:
        with tempfile.TemporaryDirectory() as d:
            settings.vault_path = d   # a real, empty vault dir
            r = asyncio.run(vault.search_vault({"query": "nonexistent topic zzz"}))
            check("search_vault(empty): complete sentence", _is_complete_sentence(r), r)
            check("search_vault(empty): clearly negative",
                  any(w in r.lower() for w in ("nothing", "no ", "couldn't")), r)
    finally:
        settings.vault_path = saved_vault

    print("\n[19.F3] an unlinked app is a missing credential, not a failed action")
    import asyncio as _aio

    import afon.brain.tools.composio as cx
    from afon.brain.tools.base import is_not_configured

    real_configured, real_context, real_post = cx._configured, cx._context, cx._post
    cx._configured = lambda: True

    async def _ctx_slack_missing():
        return "u1", {"github", "linear"}

    async def _ctx_unknown():
        return "u1", set()

    async def _post_unlinked(path, body):
        return {"successful": False, "error": "No connected account found for toolkit SLACK"}

    async def _post_real_failure(path, body):
        return {"successful": False, "error": "channel_not_found"}

    try:
        cx._context = _ctx_slack_missing
        cx._post = _post_unlinked
        r = _aio.run(cx.composio_run_tool({"tool_slug": "SLACK_SEND_MESSAGE", "arguments": {}}))
        check("an app Afon knows is unlinked types as not-configured", is_not_configured(r), r)
        check("...and it never claims the send was attempted",
              "didn't go through" not in r, r)

        # The lookup itself failing gives an EMPTY set, which is not evidence of anything. Refusing
        # on it would turn a network blip into "you never connected Slack".
        cx._context = _ctx_unknown
        r2 = _aio.run(cx.composio_run_tool({"tool_slug": "SLACK_SEND_MESSAGE", "arguments": {}}))
        check("an unknown connection list still types as not-configured via the API's own answer",
              is_not_configured(r2), r2)

        cx._context = _ctx_slack_missing
        cx._post = _post_real_failure
        r3 = _aio.run(cx.composio_run_tool({"tool_slug": "GITHUB_CREATE_ISSUE", "arguments": {}}))
        check("a genuine action failure on a LINKED app is still reported as a failure",
              not is_not_configured(r3) and "didn't go through" in r3, r3)
    finally:
        cx._configured, cx._context, cx._post = real_configured, real_context, real_post

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
