"""T7 — undo last reversible action + tiered confirm guidance.

Afon tracks the last 8 reversible actions (file creates, calendar creates, Notion task
creates, reminder sets, email DRAFTS) in ``~/.afon/recent_actions.json``. ``undo_last`` rolls
back the most recent one. For non-reversible actions (sends, deletes of files that don't have
a backup, payments) the tool returns a graceful "can't undo — that one is destructive".

Confirm-tier is handled centrally in ``proactive.confirm_required`` (already wired into
every confirmable tool). This module just exposes the user-facing ``undo_last`` verb + a tiny
``action_log`` recorder that the agent calls after each confirmable action.

Lazy group: 'trust' (loaded by trigger keywords: "undo", "rollback", "cancel that").
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger


_LOG = Path.home() / ".afon" / "recent_actions.json"
_MAX = 8


def _load() -> list[dict]:
    if not _LOG.exists():
        return []
    try:
        return json.loads(_LOG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save(entries: list[dict]) -> None:
    _LOG.parent.mkdir(parents=True, exist_ok=True)
    _LOG.write_text(json.dumps(entries[-_MAX:], indent=2, ensure_ascii=False), encoding="utf-8")


def record(kind: str, *, what: str, undo_hint: str = "", undone: bool = False) -> None:
    """Append one reversible action to the recent-actions log. Called by the agent."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,            # "file_create" | "calendar_event" | "reminder" | "draft_email" | ...
        "what": what,            # human-readable summary
        "undo_hint": undo_hint,  # machine instruction to roll back (e.g. tool name + args)
        "undone": undone,
    }
    entries = [e for e in _load() if not e.get("undone")]
    entries.append(entry)
    _save(entries)


async def undo_last(_args: dict) -> str:
    """Roll back the most recent reversible action. Returns what it did (or didn't do)."""
    entries = _load()
    if not entries:
        return "Nothing to undo, sir."
    next_un = next((e for e in reversed(entries) if not e.get("undone")), None)
    if not next_un:
        return "Everything recent has already been undone, sir."
    kind = next_un.get("kind", "")
    what = next_un.get("what", "(unknown)")
    hint = next_un.get("undo_hint", "")
    # Best-effort rollback for known reversible kinds.
    if kind == "reminder":
        try:
            from afon.brain.tools.reminders import cancel_reminder
            await cancel_reminder({"id": hint or what})
        except Exception as e:  # noqa: BLE001
            return tool_error("undo reminder", e)
    elif kind == "calendar_event":
        try:
            from afon.brain.tools.calendar import delete_event
            await delete_event({"id": hint or what})
        except Exception as e:  # noqa: BLE001
            return tool_error("undo calendar", e)
    elif kind == "file_create":
        try:
            from afon.brain.tools.system import file_op
            await file_op({"action": "delete_file", "path": hint})
        except Exception as e:  # noqa: BLE001
            return tool_error("undo file", e)
    elif kind == "draft_email":
        # Drafts aren't sent — no rollback needed beyond dropping from local draft cache.
        pass
    elif kind in ("send_email", "send_telegram", "kill_process", "delete_file"):
        return f"That one isn't reversible, sir ({what}). I can't un-send it."
    else:
        return f"I don't know how to undo that kind ({kind}), sir. Try forgetting it instead."
    next_un["undone"] = True
    _save(entries)
    return f"Undone, sir — {what}."


def list_recent(args: dict) -> str:
    """Show the recent reversible action log."""
    entries = [e for e in _load() if not e.get("undone")]
    if not entries:
        return "No recent reversible actions, sir."
    lines = [f"  • [{e.get('ts', '')[-8:-3]}] {e.get('kind', '')}: {e.get('what', '')[:100]}"
             for e in entries[-_MAX:]]
    return f"{len(entries)} recent action(s):\n" + "\n".join(lines)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "undo_last",
            "description": (
                "Roll back the most recent reversible action (file create, reminder, calendar "
                "event, etc.). Use when the owner says 'undo that', 'cancel what you just did', "
                "or 'revert'. For genuinely irreversible actions (sending an email, deleting a "
                "system file) it returns a graceful 'can't undo' note."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_recent_actions",
            "description": "Show the log of recent reversible actions — the last 8 things undo_last "
                           "could roll back. Use for 'what have you done', 'what can you undo'.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

HANDLERS = {"undo_last": undo_last, "list_recent_actions": list_recent}


def tool_error(what: str, err: Exception) -> str:
    logger.warning(f"undo '{what}' failed: {type(err).__name__}: {err}")
    return f"I couldn't undo that, sir ({type(err).__name__})."