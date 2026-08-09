"""Task-queue tools — Afon's manageable to-do list + background-job status.

Two things live behind these tools, both on the process-wide ``TASKS`` queue:

  * a **to-do list** the owner drives by voice — add a task with a description, priority and
    deadline, track progress on it, complete it, or delete it. When a task has a deadline, Afon
    schedules a spoken reminder that fires through the **edge process** (the live TTS path) and is
    also pushed to the phone, so the deadline actually reaches him.
  * the **background execution queue** (fleet delegations that run for minutes) — the read tools
    still answer "what are you working on?" / "how's the website going?" for those.

Adds/edits/deletes go straight to the persisted SQLite queue, so the list survives a brain restart.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from loguru import logger

from afon.brain.tasks import TASKS
from afon.brain.tools.base import tool_error
from afon.config import settings


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.user_tz)


def _parse_deadline(text: str) -> tuple[float | None, str | None]:
    """Parse a deadline into (epoch, error). Accepts an ISO date ('2026-07-20') or datetime
    ('2026-07-20T18:00'); a bare date defaults to 09:00 in the owner's timezone."""
    s = (text or "").strip()
    if not s:
        return None, None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None, (f"I couldn't read '{s}' as a date, sir — give me a date like 2026-07-20 "
                      "or 2026-07-20T18:00.")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz())
    # A bare date (midnight) is really "that day" — pin the reminder to a civilised morning hour.
    if dt.hour == 0 and dt.minute == 0 and "T" not in s and " " not in s:
        dt = dt.replace(hour=9)
    return dt.timestamp(), None


async def _schedule_deadline_reminder(title: str, epoch: float) -> str | None:
    """Schedule a spoken deadline reminder via the scheduler (the same path daily reminders use).

    The scheduler SPEAKS a fired reminder on the live edge (``_LIVE_SPEAK``) and — mirroring
    ``set_reminder`` — hands the phone push to ntfy when it can, so the deadline reaches him even
    with the PC off. Returns the reminder job id (to cancel later), or None if it couldn't be set."""
    if epoch <= datetime.now(_tz()).timestamp():
        return None  # already past — nothing to remind about
    try:
        from afon.brain.scheduler import SCHEDULER

        at_iso = datetime.fromtimestamp(epoch, _tz()).isoformat()
        job_id, _when, ntfy_epoch = SCHEDULER.add_reminder(f"Task due, sir: {title}", at=at_iso)
        # Phone delivery (best-effort), same handshake as reminders.set_reminder.
        if ntfy_epoch is not None:
            from afon.brain.tools.notify import push

            if not await push(f"Task due: {title}", title="Task deadline", at=ntfy_epoch):
                SCHEDULER.set_push_phone(job_id, True)
        return job_id
    except Exception as e:  # noqa: BLE001 — a scheduling hiccup must never fail the task write
        logger.warning(f"deadline reminder schedule failed: {type(e).__name__}: {e}")
        return None


def _cancel_reminder(t) -> None:
    job_id = (t.meta or {}).get("reminder_job")
    if not job_id:
        return
    try:
        from afon.brain.scheduler import SCHEDULER

        SCHEDULER.cancel(job_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"deadline reminder cancel failed: {e}")


def _resolve(topic: str):
    """Find a single to-do by id/word, or return (None, message) describing the ambiguity."""
    matches = TASKS.find_todo(topic)
    if not matches:
        return None, f"I don't have a task matching '{topic}', sir."
    if len(matches) > 1:
        listed = "; ".join(m.title for m in matches[:6])
        return None, f"A few tasks match, sir: {listed}. Which one?"
    return matches[0], None


# --- to-do management ---------------------------------------------------------------------

async def add_task(args: dict) -> str:
    title = (args.get("title") or args.get("task") or "").strip()
    if not title:
        return "What's the task, sir?"
    deadline_epoch, err = _parse_deadline(args.get("deadline") or "")
    if err:
        return err
    try:
        t = TASKS.add_todo(
            title,
            description=args.get("description") or "",
            priority=args.get("priority") or "normal",
            deadline=deadline_epoch,
        )
        if deadline_epoch is not None:
            job_id = await _schedule_deadline_reminder(t.title, deadline_epoch)
            if job_id:
                t.meta["reminder_job"] = job_id
                TASKS.edit_todo(t.id)  # persist the reminder id
        extras = []
        if t.priority != "normal":
            extras.append(f"{t.priority} priority")
        dl = t.human_deadline()
        if dl:
            extras.append(dl + (", I'll remind you" if deadline_epoch else ""))
        suffix = f" ({'; '.join(extras)})" if extras else ""
        return f"Added to your list, sir: '{t.title}'{suffix}. (id {t.id[:8]})"
    except Exception as e:  # noqa: BLE001
        return tool_error("add task", e)


async def update_task(args: dict) -> str:
    topic = (args.get("topic") or args.get("id") or args.get("query") or "").strip()
    if not topic:
        return "Which task should I update, sir?"
    t, msg = _resolve(topic)
    if t is None:
        return msg
    fields: dict = {}
    for key in ("title", "description", "priority", "progress", "note"):
        if args.get(key) is not None:
            fields[key] = args[key]
    reschedule = False
    if "deadline" in args:
        deadline_epoch, err = _parse_deadline(args.get("deadline") or "")
        if err:
            return err
        fields["deadline"] = deadline_epoch
        reschedule = True
    if not fields:
        return "What should I change, sir — description, priority, deadline, or progress?"
    try:
        if reschedule:  # cancel the old spoken reminder before setting a new one
            _cancel_reminder(t)
            t.meta.pop("reminder_job", None)
        TASKS.edit_todo(t.id, **fields)
        if reschedule and fields["deadline"] is not None:
            job_id = await _schedule_deadline_reminder(t.title, fields["deadline"])
            if job_id:
                t.meta["reminder_job"] = job_id
        TASKS.edit_todo(t.id)  # persist meta changes
        return f"Updated, sir: {TASKS.todo_summary_line(t)}"
    except Exception as e:  # noqa: BLE001
        return tool_error("update task", e)


async def complete_task(args: dict) -> str:
    topic = (args.get("topic") or args.get("id") or args.get("query") or "").strip()
    if not topic:
        return "Which task did you finish, sir?"
    t, msg = _resolve(topic)
    if t is None:
        return msg
    try:
        _cancel_reminder(t)
        TASKS.complete_todo(t.id)
        return f"Marked '{t.title}' done, sir. Nice work."
    except Exception as e:  # noqa: BLE001
        return tool_error("complete task", e)


async def delete_task(args: dict) -> str:
    topic = (args.get("topic") or args.get("id") or args.get("query") or "").strip()
    if not topic:
        return "Which task should I delete, sir?"
    t, msg = _resolve(topic)
    if t is None:
        return msg
    try:
        _cancel_reminder(t)
        TASKS.drop(t.id)
        return f"Deleted '{t.title}' from your list, sir."
    except Exception as e:  # noqa: BLE001
        return tool_error("delete task", e)


# --- read / status ------------------------------------------------------------------------

async def list_tasks(_args: dict) -> str:
    todos = TASKS.todos()
    running = TASKS.active()
    if not todos and not running:
        return "Your list is clear and nothing's running in the background, sir."
    lines: list[str] = []
    if todos:
        lines.append(f"{len(todos)} task(s) on your list, sir (most urgent first):")
        lines += [f"- {TASKS.todo_summary_line(t)}" for t in todos]
    if running:
        lines.append(f"{len(running)} running in the background:")
        lines += [f"- {TASKS.summary_line(t)}" for t in running]
    return "\n".join(lines)


async def task_status(args: dict) -> str:
    topic = (args.get("topic") or args.get("id") or "").strip()
    # Direct id hit first (either a to-do or a background job).
    t = TASKS.get(topic) if topic else None
    if t is not None:
        return TASKS.todo_summary_line(t) if t.kind == "todo" else TASKS.summary_line(t)
    todo_hits = TASKS.find_todo(topic) if topic else TASKS.todos()
    bg_hits = [b for b in TASKS.active()
               if any(w in b.title.lower() for w in topic.lower().split())] if topic else []
    hits = todo_hits + bg_hits
    if not hits:
        # Fall back to the background matcher's fuzzy behaviour ('anything running?').
        bg = TASKS.find(topic)
        if bg:
            return (TASKS.summary_line(bg[0]) if len(bg) == 1
                    else "A few match, sir:\n" + "\n".join(f"- {TASKS.summary_line(b)}" for b in bg))
        return "I don't have any task matching that, sir."
    if len(hits) == 1:
        h = hits[0]
        return TASKS.todo_summary_line(h) if h.kind == "todo" else TASKS.summary_line(h)
    return "A few match, sir:\n" + "\n".join(
        f"- {(TASKS.todo_summary_line(h) if h.kind == 'todo' else TASKS.summary_line(h))}"
        for h in hits)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": (
                "Add a task to the owner's to-do list, with an optional description, priority and "
                "deadline. If a deadline is given, Afon will SPEAK a reminder when it's due (and "
                "push it to his phone). Use for 'add X to my list', 'remind me to X by Friday', "
                "'I need to do X'. Deadline must be an ISO date (YYYY-MM-DD) or datetime "
                "(YYYY-MM-DDThh:mm) — compute it from today yourself if he says 'tomorrow'/'Friday'."),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The task, short (the title)."},
                    "description": {"type": "string", "description": "Optional longer detail/notes."},
                    "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"],
                                 "description": "Optional priority (default normal)."},
                    "deadline": {"type": "string",
                                 "description": "Optional due date/time, ISO (YYYY-MM-DD or YYYY-MM-DDThh:mm)."},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_task",
            "description": (
                "Update a task on the to-do list — change its description, priority or deadline, or "
                "record progress (a percent and/or a short note). Identify it by a word from its "
                "title or its id. Use for 'set groceries to high', 'push the report to Monday', "
                "'the website's about 60% done', 'mark X in progress'."),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "A word from the task title, or its id."},
                    "title": {"type": "string", "description": "New title (rename)."},
                    "description": {"type": "string", "description": "New/updated description."},
                    "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                    "deadline": {"type": "string",
                                 "description": "New due date/time, ISO. Empty string clears it."},
                    "progress": {"type": "integer",
                                 "description": "Percent complete, 0-100."},
                    "note": {"type": "string", "description": "A short progress note."},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": ("Mark a to-do task as done (identify it by a word from its title or its "
                            "id). Use for 'I finished X', 'mark X done', 'check off X'."),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "A word from the task title, or its id."},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_task",
            "description": ("Delete a task from the to-do list (identify it by a word from its title "
                            "or its id). Use for 'drop X', 'remove X from my list', 'cancel X'."),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "A word from the task title, or its id."},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": ("List the owner's to-do tasks (most urgent first, with priority, deadline "
                            "and progress) plus any background jobs Afon is currently running. Use "
                            "for 'what's on my list?', 'what do I have to do?', 'anything running?'."),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_status",
            "description": ("Report the status/progress of one task by a word from its title (or its "
                            "id) — a to-do item or a running background job. Use for 'how's the "
                            "website going?' / 'is that done yet?' / 'what's left on X?'."),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string",
                              "description": "A word from the task (e.g. 'website') or its id."}
                },
                "required": ["topic"],
            },
        },
    },
]

HANDLERS = {
    "add_task": add_task,
    "update_task": update_task,
    "complete_task": complete_task,
    "delete_task": delete_task,
    "list_tasks": list_tasks,
    "task_status": task_status,
}
