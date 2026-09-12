"""Notion integration — graceful degradation + registration + confirm-gating. Hermetic, no network.

With no token (the test env), every Notion tool must return a spoken "not configured" note rather
than crash, all five must be registered, the writes must be confirm-gated, and the rich-text/title
flatteners must parse Notion's shapes correctly.
"""

from __future__ import annotations

import asyncio
import sys
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
    print(f"  {mark} {name}" + (f" — {detail}" if detail and not ok else ""))


def degrades(out: str) -> bool:
    from afon.brain.tools.base import is_not_configured
    return isinstance(out, str) and is_not_configured(out)


def main() -> None:
    import afon.brain.tools.notion as notion
    from afon.brain.proactive import confirm_required
    from afon.brain.tools import tool_names
    from afon.config import settings

    # Force the UNCONFIGURED state so this hermetic degradation test holds regardless of a real .env
    # (live Notion is checked by bench/test_live_integrations.py).
    settings.notion_token = None

    print("[1] all Notion tools degrade gracefully with no token")
    check("notion_search degrades", degrades(asyncio.run(notion.notion_search({"query": "x"}))))
    check("notion_read_page degrades", degrades(asyncio.run(notion.notion_read_page({"page_id": "abc"}))))
    check("notion_append degrades", degrades(asyncio.run(notion.notion_append({"page_id": "a", "text": "t"}))))
    check("notion_comment degrades", degrades(asyncio.run(notion.notion_comment({"page_id": "a", "text": "t"}))))
    check("notion_create_page degrades",
          degrades(asyncio.run(notion.notion_create_page({"parent_id": "a", "title": "t"}))))

    print("\n[2] rich-text + title flatteners parse Notion shapes")
    rt = [{"plain_text": "Hello "}, {"plain_text": "world"}]
    check("rich_text flattens", notion._rich_text(rt) == "Hello world", notion._rich_text(rt))
    page = {"properties": {"Name": {"type": "title", "title": [{"plain_text": "My Page"}]}}}
    check("title_of reads the title prop", notion._title_of(page) == "My Page", notion._title_of(page))
    check("title_of falls back to untitled", notion._title_of({"properties": {}}) == "(untitled)")

    print("\n[2b] long text chunks at word boundaries — never a mid-sentence slice")
    long = ("The quick brown fox jumps over the lazy dog. " * 100).strip()  # ~4.5k chars
    cs = notion._rt_chunks(long)
    check("every chunk within Notion's 2000-char element cap", all(len(c) <= 1900 for c in cs))
    check("no content lost and no mid-word cuts", " ".join(cs) == long)
    check("short text passes through as one element", notion._rt_chunks("short") == ["short"])
    check("runaway text bounded to 10 chunks", len(notion._rt_chunks("x" * 100000)) == 10)

    print("\n[3] all five tools registered")
    names = set(tool_names())
    expected = {"notion_search", "notion_read_page", "notion_append", "notion_comment",
                "notion_create_page"}
    check("every Notion tool is registered", expected <= names, str(sorted(expected - names)))

    print("\n[4] writes/comments confirm-gated; reads are not")
    check("notion_append confirm-gated", confirm_required("notion_append"))
    check("notion_comment confirm-gated", confirm_required("notion_comment"))
    check("notion_create_page confirm-gated", confirm_required("notion_create_page"))
    check("notion_read_page NOT gated", not confirm_required("notion_read_page"))
    check("notion_search NOT gated", not confirm_required("notion_search"))

    # The bulk sweep clears every open task at once with no undo, so it confirms — while completing
    # one task by name stays frictionless, which is the whole point of capture-by-voice.
    check("notion_complete_task ALL is gated", confirm_required("notion_complete_task", {"all": True}))
    check("...also by whole-list phrase",
          confirm_required("notion_complete_task", {"query": "everything"}))
    check("...but a single task by name is NOT gated",
          not confirm_required("notion_complete_task", {"query": "call the dentist"}))
    check("...and a bare call is NOT gated", not confirm_required("notion_complete_task", {}))
    check("notion_delete_task still gated", confirm_required("notion_delete_task"))

    print("\n[5] task briefing buckets: overdue / today / this week / recurring / undated inbox (3.6)")
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Europe/Berlin")).date()

    def _title(s: str) -> dict:
        return {"type": "title", "title": [{"plain_text": s}]}

    def _date(d) -> dict:
        return {"type": "date", "date": {"start": d.isoformat()}}

    def _page(title: str, d=None, status="To do") -> dict:
        props = {"Name": _title(title), "Status": {"type": "status", "status": {"name": status}}}
        props["Deadline"] = _date(d) if d else {"type": "date", "date": None}
        return {"properties": props}

    fake_schema = {"properties": {"Name": {"type": "title"}, "Deadline": {"type": "date"},
                                  "Status": {"type": "status"}}}
    fake_query = {"results": [
        _page("File tax return", today - timedelta(days=2)),          # overdue
        _page("Call the vet", today),                                  # due today
        _page("Dentist", today + timedelta(days=3)),                   # this week
        _page("Water the plants daily"),                               # recurring (undated)
        _page("Refactor the parser"),                                  # undated inbox
        _page("Old finished thing", today - timedelta(days=9), status="Done"),  # done -> skipped
    ]}

    settings.notion_token = "fake-token"          # pass _configured()
    settings.notion_tasks_db_id = "fakedb"
    orig_get, orig_post = notion._get, notion._post

    async def fake_get(_path):
        return fake_schema

    async def fake_post(_path, _json):
        return fake_query

    notion._get, notion._post = fake_get, fake_post
    try:
        out = asyncio.run(notion.notion_tasks({"scope": "open"}))
    finally:
        notion._get, notion._post = orig_get, orig_post
        settings.notion_token = None
    check("overdue surfaced", "overdue" in out and "File tax return" in out, out)
    check("due today surfaced", "due today" in out and "Call the vet" in out, out)
    check("this week surfaced", "this week" in out and "Dentist" in out, out)
    check("recurring surfaced", "recurring" in out and "Water the plants daily" in out, out)
    check("undated inbox surfaced", "inbox" in out and "Refactor the parser" in out, out)
    check("completed task excluded", "Old finished thing" not in out, out)

    print("\n[queue pointer] a stale tasks database is LOUD, not an empty task list  [16.F3]")
    import afon.brain.health as _h

    _saved = (notion._get, settings.notion_token, settings.notion_tasks_db_id, _h.check)
    try:
        settings.notion_token, settings.notion_tasks_db_id = "tok", "deadbeef"

        # Notion answers 200 with an error OBJECT for a bad id, so a status code is not the answer.
        # That is precisely how the stale pointer survived for weeks: something came back, and it
        # looked fine.
        async def _error_object(path):
            return {"object": "error", "code": "object_not_found", "status": 404}

        notion._get = _error_object
        ok, detail = asyncio.run(notion.queue_pointer_ok())
        check("a 404-as-200 error object is a FAULT", ok is False, detail)
        check("and the detail names the reason", "object_not_found" in detail, detail)

        async def _no_id(path):
            return {"object": "database"}

        notion._get = _no_id
        check("a response with no id is a fault too",
              asyncio.run(notion.queue_pointer_ok())[0] is False)

        async def _fine(path):
            return {"object": "database", "id": "deadbeef"}

        notion._get = _fine
        check("a real database resolves", asyncio.run(notion.queue_pointer_ok())[0] is True)

        # Not configured is NOT a fault: an owner who never wired Notion has nothing broken.
        settings.notion_token = None
        check("unconfigured is not a fault", asyncio.run(notion.queue_pointer_ok())[0] is True)
        settings.notion_token, settings.notion_tasks_db_id = "tok", ""
        check("a token with no database is not a fault either",
              asyncio.run(notion.queue_pointer_ok())[0] is True)

        # The whole point of 16.F3: it must reach the OWNER as a signal, not only a log line.
        settings.notion_tasks_db_id = "deadbeef"
        notion._get = _error_object
        queue = asyncio.run(_h._check_task_queue())
        check("the health snapshot carries the queue as a component", queue[0] is False, queue[1])

        async def _degraded_snapshot():
            return {"vault": {"ok": True, "detail": ""}, "ticker": {"ok": True, "detail": ""},
                    "cache": {"ok": True, "detail": ""}, "pc_link": {"ok": True, "detail": ""},
                    "task_queue": {"ok": queue[0], "detail": queue[1]}}

        _h.check = _degraded_snapshot
        sigs = asyncio.run(_h.health_signals())
        keys = [s.key for s in sigs]
        check("an unreachable queue raises a health signal", "health-task-queue" in keys, str(keys))
        if "health-task-queue" in keys:
            msg = next(s.message for s in sigs if s.key == "health-task-queue")
            check("and the signal says what it means for his answers", "incomplete" in msg, msg)
    finally:
        notion._get, settings.notion_token, settings.notion_tasks_db_id, _h.check = _saved

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
