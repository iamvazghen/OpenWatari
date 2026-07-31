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
    return isinstance(out, str) and ("isn't configured" in out or "not configured" in out)


def main() -> None:
    import jarvis.brain.tools.notion as notion
    from jarvis.brain.proactive import confirm_required
    from jarvis.brain.tools import tool_names
    from jarvis.config import settings

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

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
