"""Notion tools — read, write, and comment on the owner's Notion pages (Phase 11+).

Uses a Notion **internal integration** token: create one at https://www.notion.so/my-integrations,
then **share** the specific pages/databases you want Afon to touch with that integration (Notion is
deny-by-default — the integration only ever sees pages explicitly shared with it, which is the right
safety boundary). Set ``AFON_NOTION_TOKEN``.

Tools: ``notion_search`` (find a page), ``notion_read_page`` (read its text), ``notion_append``
(add content — write), ``notion_comment`` (leave a comment), ``notion_create_page`` (new sub-page).
Writes/comments are confirm-gated. Everything degrades to a spoken note until the token is set.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from afon.brain.tools.base import clip, not_configured, tool_error
from afon.config import settings

_API = "https://api.notion.com/v1"
_NEEDS = "a Notion integration token (AFON_NOTION_TOKEN) and the page shared with the integration"


def _configured() -> bool:
    return bool(settings.notion_token)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.notion_token}",
        "Notion-Version": settings.notion_version,
        "Content-Type": "application/json",
    }


async def _post(path: str, json: dict) -> dict:
    from afon.brain.tools.base import http_post

    r = await http_post(f"{_API}{path}", headers=_headers(), json=json)
    return r.json()


async def _get(path: str) -> dict:
    from afon.brain.tools.base import http_get

    r = await http_get(f"{_API}{path}", headers=_headers())
    return r.json()


async def _patch(path: str, json: dict) -> dict:
    from afon.brain.tools.base import http_patch

    r = await http_patch(f"{_API}{path}", headers=_headers(), json=json)
    return r.json()


def _rich_text(blocks_or_rich) -> str:
    """Flatten a Notion rich_text array to plain text."""
    out = []
    for rt in blocks_or_rich or []:
        txt = (rt.get("plain_text") or rt.get("text", {}).get("content") or "")
        if txt:
            out.append(txt)
    return "".join(out)


def _title_of(page: dict) -> str:
    props = page.get("properties") or {}
    for prop in props.values():
        if prop.get("type") == "title":
            t = _rich_text(prop.get("title"))
            if t:
                return t
    # database results expose the title differently
    return _rich_text(page.get("title")) or "(untitled)"


async def notion_search(args: dict) -> str:
    if not _configured():
        return not_configured("Notion", _NEEDS)
    query = (args.get("query") or "").strip()
    try:
        data = await _post("/search", {"query": query, "page_size": 8})
        results = data.get("results") or []
        if not results:
            return f"No Notion pages matching '{query}', sir." if query else "No shared Notion pages, sir."
        lines = []
        for r in results[:8]:
            kind = r.get("object", "page")
            lines.append(f"{_title_of(r)} [{kind} {r.get('id', '')[:8]}]")
        return f"Found {len(lines)} in Notion, sir: " + "; ".join(lines)
    except Exception as e:  # noqa: BLE001
        return tool_error("Notion search", e)


async def notion_read_page(args: dict) -> str:
    if not _configured():
        return not_configured("Notion", _NEEDS)
    page_id = (args.get("page_id") or "").strip()
    if not page_id:
        return "Which Notion page, sir? Give me its id (search for it first if needed)."
    try:
        data = await _get(f"/blocks/{page_id}/children?page_size=100")
        texts: list[str] = []
        for b in data.get("results") or []:
            bt = b.get("type", "")
            payload = b.get(bt, {})
            txt = _rich_text(payload.get("rich_text"))
            if txt:
                prefix = "• " if "list_item" in bt else ("# " if bt.startswith("heading") else "")
                texts.append(prefix + txt)
        body = "\n".join(texts)
        return clip(body, 4000) if body else "That page has no readable text blocks, sir."
    except Exception as e:  # noqa: BLE001
        return tool_error("Notion read", e)


def _rt_chunks(text: str, limit: int = 1900, max_chunks: int = 10) -> list[str]:
    """Split text at whitespace into <=limit-char pieces (Notion caps one rich_text element at 2000).
    The old ``text[:1900]`` slice ended long comments MID-SENTENCE and silently threw the rest away —
    the owner's #1 Notion complaint. max_chunks bounds a runaway worker at ~19k chars."""
    text = text.strip()
    out: list[str] = []
    while text and len(out) < max_chunks:
        if len(text) <= limit:
            out.append(text)
            break
        cut = text.rfind(" ", 0, limit)
        cut = cut if cut > limit // 2 else limit   # no usable space -> hard cut, still no data loss
        out.append(text[:cut])
        text = text[cut:].lstrip()
    return out or [""]


def _rt(content: str) -> dict:
    return {"type": "text", "text": {"content": content}}


async def notion_append(args: dict) -> str:
    if not _configured():
        return not_configured("Notion", _NEEDS)
    page_id = (args.get("page_id") or "").strip()
    text = (args.get("text") or "").strip()
    if not (page_id and text):
        return "I need a page id and the text to add, sir."
    try:
        await _post(f"/blocks/{page_id}/children", {
            "children": [{
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [_rt(c)]},
            } for c in _rt_chunks(text)],
        })
        return "Added that to the Notion page, sir."
    except Exception as e:  # noqa: BLE001
        return tool_error("Notion write", e)


async def notion_comment(args: dict) -> str:
    if not _configured():
        return not_configured("Notion", _NEEDS)
    page_id = (args.get("page_id") or "").strip()
    text = (args.get("text") or "").strip()
    if not (page_id and text):
        return "I need a page id and the comment text, sir."
    try:
        await _post("/comments", {
            "parent": {"page_id": page_id},
            "rich_text": [_rt(c) for c in _rt_chunks(text)],
        })
        return "Comment posted on the Notion page, sir."
    except Exception as e:  # noqa: BLE001
        return tool_error("Notion comment", e)


async def notion_create_page(args: dict) -> str:
    if not _configured():
        return not_configured("Notion", _NEEDS)
    parent_id = (args.get("parent_id") or "").strip()
    title = (args.get("title") or "").strip()
    content = (args.get("content") or "").strip()
    if not (parent_id and title):
        return "I need a parent page id and a title, sir."
    try:
        body: dict = {
            "parent": {"page_id": parent_id},
            "properties": {"title": {"title": [{"type": "text", "text": {"content": title}}]}},
        }
        if content:
            body["children"] = [{
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [_rt(c)]},
            } for c in _rt_chunks(content)]
        await _post("/pages", body)
        return f"Created the Notion page '{title}', sir."
    except Exception as e:  # noqa: BLE001
        return tool_error("Notion create", e)


def _prop_value(prop: dict):
    """Extract a plain value from a Notion property cell (date->iso str, status/select->name, etc.)."""
    t = prop.get("type")
    v = prop.get(t)
    if t == "date":
        return (v or {}).get("start")
    if t in ("status", "select"):
        return (v or {}).get("name")
    if t == "checkbox":
        return bool(v)
    if t == "title":
        return _rich_text(v)
    if t == "formula":
        f = v or {}
        return f.get("string") or f.get("date", {}).get("start") if isinstance(f.get("date"), dict) else f.get("string")
    return None


def _detect_props(schema: dict) -> dict:
    """From a DB schema, guess which properties are the title, the deadline date, and the done/status."""
    props = schema.get("properties") or {}
    title = date_prop = status_prop = None
    for name, meta in props.items():
        tp = meta.get("type")
        low = name.lower()
        if tp == "title":
            title = name
        elif tp == "date" and (date_prop is None or any(k in low for k in ("deadline", "due", "date"))):
            date_prop = name
        elif tp in ("status", "checkbox") and (status_prop is None or any(
                k in low for k in ("status", "done", "complete", "state"))):
            status_prop = name
    return {"title": title, "date": date_prop, "status": status_prop}


def _is_done(val) -> bool:
    if isinstance(val, bool):
        return val
    return str(val or "").strip().lower() in {"done", "complete", "completed", "closed", "archived"}


# The Tasks DB has no native recurrence field, so a task is treated as RECURRING when its title says
# so ("water plants daily", "weekly review", "every morning"). Heuristic + additive: a miss just
# means it isn't called out as recurring, never a wrong action.
_RECURRING_HINTS = ("recurring", "daily", "weekly", "monthly", "every day", "every week",
                    "every morning", "every evening", "each day", "each week", "every month")


def _is_recurring(title: str) -> bool:
    t = (title or "").lower()
    return any(h in t for h in _RECURRING_HINTS)


async def notion_tasks(args: dict) -> str:
    """Read the tasks dashboard DB and report what's due (today / overdue / upcoming), by voice."""
    if not _configured():
        return not_configured("Notion", _NEEDS)
    db_id = (args.get("database_id") or settings.notion_tasks_db_id or "").strip()
    if not db_id:
        return ("No tasks database is configured yet, sir. Share your tasks dashboard with the "
                "'Personal Assistant' integration in Notion, then set AFON_NOTION_TASKS_DB_ID.")
    scope = (args.get("scope") or "today").strip().lower()
    from datetime import date, datetime, timedelta
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo(settings.user_tz)).date()
    try:
        schema = await _get(f"/databases/{db_id}")
        p = _detect_props(schema)
        data = await _post(f"/databases/{db_id}/query", {"page_size": 100})
        overdue: list[str] = []
        due_today: list[str] = []
        upcoming: list[tuple[date, str]] = []
        recurring: list[str] = []
        inbox: list[str] = []   # undated, non-recurring, not-done = the backlog to triage
        for page in data.get("results") or []:
            props = page.get("properties") or {}
            title = _prop_value(props.get(p["title"], {})) or "(untitled)" if p["title"] else _title_of(page)
            if p["status"] and _is_done(_prop_value(props.get(p["status"], {}))):
                continue  # skip completed
            if _is_recurring(title):
                recurring.append(title)
            dstr = _prop_value(props.get(p["date"], {})) if p["date"] else None
            if not dstr:
                if not _is_recurring(title):
                    inbox.append(title)  # no deadline and not a standing routine -> inbox backlog
                continue
            try:
                d = datetime.fromisoformat(dstr.replace("Z", "+00:00")).date()
            except ValueError:
                continue
            if d < today:
                overdue.append(f"{title} ({(today - d).days}d overdue)")
            elif d == today:
                due_today.append(title)
            elif d <= today + timedelta(days=7):
                upcoming.append((d, title))
        upcoming.sort()
        # De-dupe (keep order): the Tasks DB can hold duplicate rows (e.g. leftover test artifacts),
        # which otherwise made the morning briefing read the same task 3x ("X overdue; X overdue; …").
        overdue = list(dict.fromkeys(overdue))
        due_today = list(dict.fromkeys(due_today))
        parts: list[str] = []
        if scope in ("today", "open", "all") and overdue:
            parts.append(f"{len(overdue)} overdue, sir: " + "; ".join(overdue[:6]))
        if scope in ("today", "open", "all"):
            parts.append(f"{len(due_today)} due today" + (": " + "; ".join(due_today[:8]) if due_today else ""))
        if scope in ("week", "upcoming", "open", "all") and upcoming:
            parts.append("this week: " + "; ".join(f"{t} ({d.strftime('%a')})" for d, t in upcoming[:8]))
        # Recurring tasks are surfaced for the broader scopes (and on the morning briefing, which uses
        # 'open') so standing routines get a voice reminder even without a per-day deadline.
        if scope in ("week", "upcoming", "open", "all") and recurring:
            uniq = list(dict.fromkeys(recurring))  # de-dupe, keep order
            parts.append("recurring: " + "; ".join(uniq[:6]))
        # Undated backlog — only on the broad scopes (and the morning briefing's 'open'), so the
        # day view stays focused on what actually has a deadline.
        if scope in ("open", "all") and inbox:
            uniq_inbox = list(dict.fromkeys(inbox))
            parts.append(f"{len(uniq_inbox)} undated in your inbox: " + "; ".join(uniq_inbox[:5]))
        if not parts or (not overdue and not due_today and not upcoming and not recurring and not inbox):
            return "Nothing due, sir — you're clear for today."
        return ". ".join(parts) + "."
    except Exception as e:  # noqa: BLE001
        return tool_error("Notion tasks", e)


async def notion_tasks_structured() -> list[dict]:
    """Structured task fetch for the world-model (Phase 2.2): ``[{id, title, due, project, done}]``.

    Same DB + prop detection as ``notion_tasks``, but returns data (not a spoken string) so the
    world-model can turn open tasks into goals and completed ones into done goals. Fail-quiet → [].
    """
    from datetime import datetime

    db_id = (settings.notion_tasks_db_id or "").strip()
    if not _configured() or not db_id:
        return []
    try:
        p = _detect_props(await _get(f"/databases/{db_id}"))
        data = await _post(f"/databases/{db_id}/query", {"page_size": 100})
        out: list[dict] = []
        for page in data.get("results") or []:
            props = page.get("properties") or {}
            title = (_prop_value(props.get(p["title"], {})) if p["title"] else _title_of(page)) or ""
            title = title.strip()
            if not title:
                continue
            done = bool(p["status"] and _is_done(_prop_value(props.get(p["status"], {}))))
            dstr = _prop_value(props.get(p["date"], {})) if p["date"] else None
            due = None
            if dstr:
                try:
                    due = datetime.fromisoformat(dstr.replace("Z", "+00:00")).isoformat()
                except ValueError:
                    due = None
            out.append({"id": page.get("id") or title, "title": title,
                        "due": due, "project": "", "done": done})
        return out
    except Exception as e:  # noqa: BLE001
        logger.debug(f"notion_tasks_structured: {type(e).__name__}")
        return []


async def task_signals():
    """Proactive source: one daily nudge if tasks are overdue or due today (the deadline-tracking
    a AFON does). Repeat-suppressed by date so it nudges once, not every tick. Fail-quiet."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from afon.brain.proactive import Signal

    db_id = (settings.notion_tasks_db_id or "").strip()
    if not _configured() or not db_id:
        return []
    try:
        today = datetime.now(ZoneInfo(settings.user_tz)).date()
        p = _detect_props(await _get(f"/databases/{db_id}"))
        if not p["date"]:
            return []
        data = await _post(f"/databases/{db_id}/query", {"page_size": 100})
        overdue, due_today = [], []
        for page in data.get("results") or []:
            props = page.get("properties") or {}
            if p["status"] and _is_done(_prop_value(props.get(p["status"], {}))):
                continue
            dstr = _prop_value(props.get(p["date"], {}))
            if not dstr:
                continue
            try:
                d = datetime.fromisoformat(dstr.replace("Z", "+00:00")).date()
            except ValueError:
                continue
            title = _prop_value(props.get(p["title"], {})) or _title_of(page)
            if d < today:
                overdue.append(title)
            elif d == today:
                due_today.append(title)
    except Exception:  # noqa: BLE001 — a broken source must never throw into the tick loop
        return []
    overdue = list(dict.fromkeys(overdue))
    due_today = list(dict.fromkeys(due_today))
    if not overdue and not due_today:
        return []
    bits = []
    if overdue:
        bits.append(f"{len(overdue)} overdue ({', '.join(overdue[:3])})")
    if due_today:
        bits.append(f"{len(due_today)} due today")
    msg = "Sir, a heads-up on your tasks: " + " and ".join(bits) + "."
    # Key by date so it's one nudge per day; urgency higher when something is actually overdue.
    return [Signal(key=f"tasks-{today}", kind="reminder",
                   urgency=0.7 if overdue else 0.62, message=msg)]


async def overdue_and_today() -> tuple[list[str], list[str]]:
    """Structured (overdue_titles, due_today_titles) from the Notion tasks DB, for the daily digest.

    Overdue titles carry an '(Nd overdue)' suffix. Completed tasks are skipped. Returns ([], [])
    when Notion or the tasks DB isn't configured, or on any error — always safe to call."""
    if not _configured():
        return [], []
    db_id = (settings.notion_tasks_db_id or "").strip()
    if not db_id:
        return [], []
    from datetime import datetime
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo(settings.user_tz)).date()
    try:
        p = _detect_props(await _get(f"/databases/{db_id}"))
        if not p["date"]:
            return [], []
        data = await _post(f"/databases/{db_id}/query", {"page_size": 100})
    except Exception as e:  # noqa: BLE001 — never throw into the digest/tick
        # ...but never let it look like SILENCE either. A retired database id 404s here, and
        # returning ([], []) makes that indistinguishable from "nothing due" — which is exactly
        # how the 06:00 digest reported an empty day for weeks while 7 tasks sat overdue
        # (2026-08-11: notion_tasks_db_id still pointed at the dead 9c5a572c… queue).
        try:
            from afon.shared import errors as _err

            _err.swallowed("notion.overdue_and_today", e,
                           note=f"tasks DB {db_id[:8]}… unreadable: {type(e).__name__}: {e}")
        except Exception:  # noqa: BLE001
            pass
        logger.warning(f"notion digest source failed for db {db_id[:8]}…: {type(e).__name__}: {e}")
        return [], []
    overdue: list[str] = []
    due_today: list[str] = []
    for page in data.get("results") or []:
        props = page.get("properties") or {}
        if p["status"] and _is_done(_prop_value(props.get(p["status"], {}))):
            continue
        title = _prop_value(props.get(p["title"], {})) or _title_of(page)
        dstr = _prop_value(props.get(p["date"], {}))
        if not dstr:
            continue
        try:
            d = datetime.fromisoformat(dstr.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if d < today:
            overdue.append(f"{title} ({(today - d).days}d overdue)")
        elif d == today:
            due_today.append(title)
    return list(dict.fromkeys(overdue)), list(dict.fromkeys(due_today))


async def fetch_backlog_tasks(limit: int = 5) -> list[dict]:
    """Structured overdue + undated-inbox tasks (``{"id", "title"}``) for the autonomous backlog
    worker (Phase 3.1). Overdue first (most pressing), then the undated inbox; completed and recurring
    tasks are skipped (recurring = a standing routine, not one-off work). Returns ``[]`` when Notion or
    the tasks DB isn't configured, or on any error — so the routine is always safe to call."""
    if not _configured():
        return []
    db_id = (settings.notion_tasks_db_id or "").strip()
    if not db_id:
        return []
    from datetime import datetime
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo(settings.user_tz)).date()
    try:
        schema = await _get(f"/databases/{db_id}")
        p = _detect_props(schema)
        data = await _post(f"/databases/{db_id}/query", {"page_size": 100})
    except Exception:  # noqa: BLE001 — never raise into the scheduler; a failed pass is a no-op
        return []
    overdue: list[dict] = []
    inbox: list[dict] = []
    for page in data.get("results") or []:
        props = page.get("properties") or {}
        title = _prop_value(props.get(p["title"], {})) or "(untitled)" if p["title"] else _title_of(page)
        if p["status"] and _is_done(_prop_value(props.get(p["status"], {}))):
            continue
        if _is_recurring(title):
            continue
        item = {"id": page.get("id", ""), "title": title}
        dstr = _prop_value(props.get(p["date"], {})) if p["date"] else None
        if not dstr:
            inbox.append(item)
            continue
        try:
            d = datetime.fromisoformat(dstr.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if d < today:
            overdue.append(item)
    return (overdue + inbox)[: max(0, limit)]


def _schema_map(schema: dict) -> dict:
    """Map a tasks-DB schema to roles by type + name heuristics (title/date/status/priority/etc.)."""
    props = schema.get("properties") or {}
    m: dict = {"title": None, "date": None, "status": None, "status_options": [],
               "status_done": None, "select": None, "select_options": [],
               "multi_select": None, "multi_options": [], "rich_text": None}
    for name, meta in props.items():
        tp = meta.get("type")
        low = name.lower()
        if tp == "title":
            m["title"] = name
        elif tp == "date" and (m["date"] is None or any(k in low for k in ("deadline", "due", "date"))):
            m["date"] = name
        elif tp == "status" and (m["status"] is None or "status" in low):
            m["status"] = name
            m["status_options"] = [o["name"] for o in meta.get("status", {}).get("options", [])]
            for o in m["status_options"]:
                if o.lower() in {"done", "complete", "completed", "closed"}:
                    m["status_done"] = o
        elif tp == "select" and (m["select"] is None or any(
                k in low for k in ("priority", "prio", "importance", "urgency"))):
            # Name heuristic, matching the date/status branches above. Without it this took the
            # FIRST select in dict order, and the owner's live Task Queue lists four
            # (Recurrence, Priority, Project, Owner agent) — so "priority" bound to *Recurrence*,
            # "Low" was not a valid option, no property was built, and `notion_update_task`
            # answered "What should I change, sir?" to a request that named exactly what to change.
            m["select"] = name
            m["select_options"] = [o["name"] for o in meta.get("select", {}).get("options", [])]
        elif tp == "multi_select" and m["multi_select"] is None:
            m["multi_select"] = name
            m["multi_options"] = [o["name"] for o in meta.get("multi_select", {}).get("options", [])]
        elif tp == "rich_text" and m["rich_text"] is None:
            m["rich_text"] = name
    return m


def _match_option(value, options: list[str]):
    """Map a user value to one of the DB's option names, tolerating typos (e.g. 'high' -> 'Hight')."""
    if not value or not options:
        return None
    v = str(value).strip().lower()
    for o in options:
        if o.lower() == v:
            return o
    for o in options:
        if v in o.lower() or o.lower() in v:
            return o
    for o in options:
        if v[:3] and o.lower().startswith(v[:3]):
            return o
    return None


def _build_task_props(m: dict, args: dict, *, for_create: bool) -> dict:
    """Construct a Notion properties payload for create/update from natural fields."""
    props: dict = {}
    title = (args.get("title") or args.get("task") or "").strip()
    if title and m["title"]:
        props[m["title"]] = {"title": [{"text": {"content": title[:1900]}}]}
    if "deadline" in args and m["date"]:
        dl = (args.get("deadline") or "").strip()
        props[m["date"]] = {"date": {"start": dl} if dl else None}  # blank clears it
    if args.get("status") and m["status"]:
        st = _match_option(args["status"], m["status_options"])
        if st:
            props[m["status"]] = {"status": {"name": st}}
    elif for_create and m["status"] and m["status_options"]:
        props[m["status"]] = {"status": {"name": m["status_options"][0]}}  # default e.g. "To Do"
    if args.get("priority") and m["select"]:
        pr = _match_option(args["priority"], m["select_options"])
        if pr:
            props[m["select"]] = {"select": {"name": pr}}
    if args.get("category") and m["multi_select"]:
        cats = [c.strip() for c in str(args["category"]).split(",") if c.strip()]
        sel = [{"name": o} for c in cats if (o := _match_option(c, m["multi_options"]))]
        if sel:
            props[m["multi_select"]] = {"multi_select": sel}
    if args.get("notes") and m["rich_text"]:
        props[m["rich_text"]] = {"rich_text": [{"text": {"content": str(args["notes"])[:1900]}}]}
    return props


async def _resolve_task(db_id: str, args: dict):
    """Return (page_id, title, error). Explicit page_id wins; else match a task by title text."""
    pid = (args.get("page_id") or "").strip()
    if pid:
        return pid, args.get("query") or args.get("title"), None
    query = (args.get("query") or args.get("title") or "").strip()
    if not query:
        return None, None, "Which task, sir? Tell me a word from its name."
    data = await _post(f"/databases/{db_id}/query", {"page_size": 100})
    ql = query.lower()
    matches = [(p["id"], _title_of(p)) for p in (data.get("results") or []) if ql in _title_of(p).lower()]
    if not matches:
        return None, None, f"No task matching '{query}', sir."
    if len(matches) > 1:
        return None, None, ("A few tasks match, sir: " + "; ".join(t for _, t in matches[:6]) +
                            ". Which one?")
    return matches[0][0], matches[0][1], None


def _tasks_db(args: dict) -> str | None:
    return (args.get("database_id") or settings.notion_tasks_db_id or "").strip() or None


# --- spoken deadline reminders for Notion tasks -------------------------------------------
# A Notion task with a deadline (or an explicit `reminder` time) also gets a spoken reminder that
# fires through the edge process at that time (reusing the local-queue scheduler path). We remember
# page_id -> reminder job id in a small JSON file so completing/deleting/rescheduling a task cancels
# its reminder instead of letting a stale "task due" fire.
def _reminders_path() -> Path:
    base = Path(settings.tasks_db_path).parent if settings.tasks_db_path else Path(__file__).resolve().parents[3]
    return base / "notion_task_reminders.json"


def _load_task_reminders() -> dict:
    try:
        return json.loads(_reminders_path().read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_task_reminders(m: dict) -> None:
    try:
        _reminders_path().write_text(json.dumps(m), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"notion reminder-map save failed: {e}")


async def _set_task_reminder(page_id: str, title: str, when_str: str) -> bool:
    """Schedule a spoken edge reminder for a task deadline/reminder time. Fail-quiet; True if set."""
    from afon.brain.tools.tasks import _parse_deadline, _schedule_deadline_reminder

    epoch, _err = _parse_deadline(when_str)
    if epoch is None:
        return False
    job_id = await _schedule_deadline_reminder(title, epoch)
    if job_id and page_id:
        m = _load_task_reminders()
        m[page_id] = job_id
        _save_task_reminders(m)
        return True
    return False


async def _cancel_task_reminder(page_id: str) -> None:
    if not page_id:
        return
    m = _load_task_reminders()
    job_id = m.pop(page_id, None)
    if not job_id:
        return
    try:
        from afon.brain.scheduler import cancel_everywhere

        await cancel_everywhere(job_id)   # NOT SCHEDULER.cancel: that left the ticker nagging
    except Exception as e:  # noqa: BLE001
        logger.warning(f"notion reminder cancel failed: {e}")
    _save_task_reminders(m)


async def notion_create_task(args: dict) -> str:
    """Create a new row in the tasks dashboard (title + optional deadline/status/priority/category)."""
    if not _configured():
        return not_configured("Notion", _NEEDS)
    db_id = _tasks_db(args)
    if not db_id:
        return "No tasks database is configured yet, sir (set AFON_NOTION_TASKS_DB_ID)."
    title = (args.get("title") or args.get("task") or "").strip()
    if not title:
        return "What should the task be, sir?"
    try:
        m = _schema_map(await _get(f"/databases/{db_id}"))
        props = _build_task_props(m, args, for_create=True)
        resp = await _post("/pages", {"parent": {"database_id": db_id}, "properties": props})
        # Schedule a spoken reminder (announced through the edge at fire time) from an explicit
        # `reminder` datetime, else the deadline.
        when = (args.get("reminder") or args.get("deadline") or "").strip()
        reminded = await _set_task_reminder(resp.get("id", ""), title, when) if when else False
        extra = f" (due {args['deadline']})" if args.get("deadline") else ""
        extra += ", and I'll remind you out loud when it's due" if reminded else ""
        return f"Added '{title}' to your tasks, sir{extra}."
    except Exception as e:  # noqa: BLE001
        return tool_error("Notion create task", e)


async def notion_update_task(args: dict) -> str:
    """Update a task's status / deadline / priority (find it by a word in its name, or page_id)."""
    if not _configured():
        return not_configured("Notion", _NEEDS)
    db_id = _tasks_db(args)
    if not db_id:
        return "No tasks database is configured yet, sir."
    try:
        page_id, title, err = await _resolve_task(db_id, args)
        if err:
            return err
        m = _schema_map(await _get(f"/databases/{db_id}"))
        props = _build_task_props(m, args, for_create=False)
        if not props and "reminder" not in args:
            # Distinguish "you didn't say what to change" from "you did, but that value isn't one
            # this database offers". They used to give the SAME reply, so asking to set priority
            # Low on a DB whose options are P0/P1/P2 was answered "What should I change?" — a
            # question about something the owner had just been explicit about.
            asked = [k for k in ("status", "priority", "category", "deadline", "notes") if args.get(k)]
            if asked:
                offers = {"priority": m["select_options"], "status": m["status_options"],
                          "category": m["multi_options"]}
                for field in asked:
                    opts = offers.get(field)
                    if opts:
                        return (f"'{args[field]}' isn't one of the {field} options in that database, "
                                f"sir — it offers {', '.join(opts)}.")
                return (f"I couldn't apply {', '.join(asked)} to that task, sir — the database has no "
                        "matching property.")
            return "What should I change, sir — status, deadline, or priority?"
        if props:
            await _patch(f"/pages/{page_id}", {"properties": props})
        # Deadline (or explicit reminder) changed -> reschedule the spoken reminder for this task.
        if "deadline" in args or "reminder" in args:
            await _cancel_task_reminder(page_id)
            when = (args.get("reminder") or args.get("deadline") or "").strip()
            if when:
                await _set_task_reminder(page_id, title or "your task", when)
        return f"Updated '{title or 'that task'}', sir."
    except Exception as e:  # noqa: BLE001
        return tool_error("Notion update task", e)


_BULK_WORDS = {"all", "everything", "them all", "all tasks", "all of them", "all my tasks",
               "all of my tasks", "the rest", "the lot", "these", "all these", "all of these"}


def _is_bulk(args: dict) -> bool:
    """Did the owner mean 'mark ALL tasks done' rather than a single one? An explicit ``all`` flag, or
    a whole-list phrase in the title/query. Kept strict so a real task named 'all-hands' isn't swept."""
    if str(args.get("all", "")).strip().lower() in ("true", "1", "yes"):
        return True
    q = (args.get("query") or args.get("title") or "").strip().lower().rstrip(".!?")
    return q in _BULK_WORDS


async def notion_complete_task(args: dict) -> str:
    """Mark a task done — one (by a word in its name / page_id) or ALL open tasks (``all=true``)."""
    if not _configured():
        return not_configured("Notion", _NEEDS)
    db_id = _tasks_db(args)
    if not db_id:
        return "No tasks database is configured yet, sir."
    try:
        m = _schema_map(await _get(f"/databases/{db_id}"))
        done = m["status_done"] or (m["status_options"][-1] if m["status_options"] else None)
        if not (m["status"] and done):
            return "I couldn't find a 'Done' status on that database, sir."

        if _is_bulk(args):
            data = await _post(f"/databases/{db_id}/query", {"page_size": 100})
            open_pages = [p for p in (data.get("results") or [])
                          if not _is_done(_prop_value((p.get("properties") or {}).get(m["status"], {})))]
            if not open_pages:
                return "Your task list is already clear, sir — nothing open to mark done."
            n = 0
            for p in open_pages:
                try:
                    await _patch(f"/pages/{p['id']}",
                                 {"properties": {m["status"]: {"status": {"name": done}}}})
                    await _cancel_task_reminder(p["id"])
                    n += 1
                except Exception:  # noqa: BLE001 — one failure must not abort the sweep
                    continue
            return f"Marked all {n} open task{'s' if n != 1 else ''} as {done}, sir. Clean slate."

        page_id, title, err = await _resolve_task(db_id, args)
        if err:
            return err
        await _patch(f"/pages/{page_id}", {"properties": {m["status"]: {"status": {"name": done}}}})
        await _cancel_task_reminder(page_id)  # done -> no need to nag about the deadline
        return f"Marked '{title or 'that task'}' as {done}, sir."
    except Exception as e:  # noqa: BLE001
        return tool_error("Notion complete task", e)


async def notion_delete_task(args: dict) -> str:
    """Delete (archive) a task from the dashboard (find it by a word in its name, or page_id)."""
    if not _configured():
        return not_configured("Notion", _NEEDS)
    db_id = _tasks_db(args)
    if not db_id:
        return "No tasks database is configured yet, sir."
    try:
        page_id, title, err = await _resolve_task(db_id, args)
        if err:
            return err
        await _patch(f"/pages/{page_id}", {"archived": True})
        await _cancel_task_reminder(page_id)  # gone -> cancel any pending deadline reminder
        return f"Deleted '{title or 'that task'}' from your tasks, sir."
    except Exception as e:  # noqa: BLE001
        return tool_error("Notion delete task", e)


SCHEMAS = [
    {"type": "function", "function": {
        "name": "notion_create_task",
        "description": "Create a new task in the owner's Notion tasks dashboard. Use for 'add a task to "
                       "…', 'remind me to …', 'put X on my list'. Deadline must be an ISO date "
                       "(YYYY-MM-DD) — compute it yourself from today if he says 'tomorrow'/'Friday'.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "The task text (the title)."},
            "deadline": {"type": "string", "description": "Optional due date, YYYY-MM-DD."},
            "reminder": {"type": "string", "description": "Optional time to speak a reminder out loud "
                         "(announced on the live edge + phone), ISO YYYY-MM-DDThh:mm. Defaults to the "
                         "deadline (at 09:00) if omitted. Compute it from 'now'/'in 5 minutes' yourself."},
            "status": {"type": "string", "description": "Optional: To Do / In progress / Done."},
            "priority": {"type": "string", "description": "Optional: High / Medium / Low."},
            "category": {"type": "string", "description": "Optional, comma-separated: Work, Home, Business, Personal, Family, Personal development."},
            "notes": {"type": "string", "description": "Optional extra notes."}},
            "required": ["title"]}}},
    {"type": "function", "function": {
        "name": "notion_update_task",
        "description": "Change a task's status, deadline, or priority. Identify it by a word from its "
                       "name (query) or a page_id. Use for 'move the rent task to Friday', "
                       "'set groceries to high priority', 'mark X in progress'.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "A word from the task name to find it."},
            "page_id": {"type": "string", "description": "Optional exact page id instead of query."},
            "status": {"type": "string", "description": "To Do / In progress / Done."},
            "deadline": {"type": "string", "description": "New due date YYYY-MM-DD (blank clears it)."},
            "reminder": {"type": "string", "description": "Reschedule the spoken reminder to this ISO "
                         "datetime YYYY-MM-DDThh:mm (blank clears it)."},
            "priority": {"type": "string", "description": "High / Medium / Low."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "notion_complete_task",
        "description": "Mark task(s) as Done. For ONE task, identify it by a word from its name (query) "
                       "or page_id ('I finished X', 'mark X done', 'check off X'). To complete EVERY "
                       "open task at once ('mark all my tasks done', 'clear my list'), set all=true.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "A word from the task name (single task)."},
            "page_id": {"type": "string", "description": "Optional exact page id (single task)."},
            "all": {"type": "boolean", "description": "True = mark ALL open tasks done (bulk)."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "notion_delete_task",
        "description": "Delete (archive) a task from the dashboard. Identify it by a word from its name "
                       "(query) or page_id. Confirm with the owner first.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "A word from the task name."},
            "page_id": {"type": "string", "description": "Optional exact page id."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "notion_tasks",
        "description": "Read the owner's Notion TASKS dashboard and report what needs doing: overdue, "
                       "due today, upcoming this week (with deadlines), and recurring/standing tasks. "
                       "Use for 'what's on my plate today?', 'any deadlines?', 'what's due this "
                       "week?', 'what are my recurring tasks?'.",
        "parameters": {"type": "object", "properties": {
            "scope": {"type": "string", "enum": ["today", "week", "open", "all"],
                      "description": "today = overdue+today (default); week = upcoming 7 days; open/all = everything."},
            "database_id": {"type": "string", "description": "Optional DB id; defaults to the configured tasks DB."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "notion_search",
        "description": "Search the owner's Notion (only pages shared with the integration) for a page "
                       "or database. Returns titles + ids. Use first to find a page id.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Search text; blank lists shared pages."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "notion_read_page",
        "description": "Read the text content of one Notion page by its id. Use for 'read me the "
                       "project page', 'what's on that page'. Needs a page id — call notion_search "
                       "first to find it by name.",
        "parameters": {"type": "object", "properties": {
            "page_id": {"type": "string", "description": "Notion page id (from notion_search)."}},
            "required": ["page_id"]}}},
    {"type": "function", "function": {
        "name": "notion_append",
        "description": "Append a paragraph of text to the end of an existing Notion page (a WRITE). "
                       "Confirm with the owner first. Use for 'add that to the page', 'note it on "
                       "my Notion page'.",
        "parameters": {"type": "object", "properties": {
            "page_id": {"type": "string", "description": "Notion page id."},
            "text": {"type": "string", "description": "The text to add."}},
            "required": ["page_id", "text"]}}},
    {"type": "function", "function": {
        "name": "notion_comment",
        "description": "Post a comment on a Notion page (a WRITE, visible to anyone sharing it). "
                       "Confirm with the owner first. Use for 'comment on that page', 'leave a note "
                       "on it'. notion_append edits the page body instead.",
        "parameters": {"type": "object", "properties": {
            "page_id": {"type": "string", "description": "Notion page id."},
            "text": {"type": "string", "description": "The comment text."}},
            "required": ["page_id", "text"]}}},
    {"type": "function", "function": {
        "name": "notion_create_page",
        "description": "Create a NEW sub-page under an existing Notion parent page, optionally with "
                       "body text. Confirm with the owner first. Use for 'make a Notion page for "
                       "X'. To add to a page that already exists, use notion_append.",
        "parameters": {"type": "object", "properties": {
            "parent_id": {"type": "string", "description": "Parent page id (must be shared with the integration)."},
            "title": {"type": "string", "description": "New page title."},
            "content": {"type": "string", "description": "Optional first paragraph."}},
            "required": ["parent_id", "title"]}}},
]

HANDLERS = {
    "notion_create_task": notion_create_task,
    "notion_update_task": notion_update_task,
    "notion_complete_task": notion_complete_task,
    "notion_delete_task": notion_delete_task,
    "notion_tasks": notion_tasks,
    "notion_search": notion_search,
    "notion_read_page": notion_read_page,
    "notion_append": notion_append,
    "notion_comment": notion_comment,
    "notion_create_page": notion_create_page,
}
