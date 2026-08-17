"""Background task queue with live status — the "status-keeping butler" capability.

Long work (a fleet delegation that takes 20 minutes, say) shouldn't block a voice turn or leave you
in the dark. This module runs such work in the BACKGROUND, keeps a persisted record of every live
task and its latest progress, lets Afon answer "how's that going?" at any time, and — when a task
finishes — fires a completion callback (a voice note with metadata) and drops it from the queue.

Design:
  * SQLite-backed (``AFON_TASKS_DB_PATH``, default <repo>/afon_tasks.sqlite) so the queue
    survives a 24/7 brain restart; an in-memory mirror keeps reads instant.
  * ``run()`` wraps any coroutine *factory* that accepts an ``on_progress`` callback (e.g.
    ``delegate_to_fleet``); each streamed note is persisted as ``last_progress`` so a status query
    just reads the row — no second round-trip to the fleet.
  * Fail-quiet: a task that raises is recorded as ``failed`` with the error; it never crashes the brain.
  * ``on_complete(task)`` (set by the server) delivers the spoken completion note.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from afon.brain.dbconn import connect
from afon.config import settings
from afon.shared.paths import store_path



def _db_path() -> Path:
    return Path(settings.tasks_db_path) if settings.tasks_db_path else store_path("tasks")


# Priority ordering for the manageable to-do list (higher = more urgent = sorts first).
PRIORITIES = ("low", "normal", "high", "urgent")
_PRIO_RANK = {p: i for i, p in enumerate(PRIORITIES)}


def normalize_priority(value: str | None) -> str:
    """Map free-text priority ('med', 'important', 'p1', 'critical') onto a canonical level."""
    v = (value or "").strip().lower()
    if not v:
        return "normal"
    if v in _PRIO_RANK:
        return v
    aliases = {
        "urgent": ("urgent", "critical", "asap", "p0", "p1", "highest", "top"),
        "high": ("high", "important", "hi", "p2"),
        "normal": ("normal", "medium", "med", "mid", "default", "p3", "moderate"),
        "low": ("low", "minor", "someday", "later", "p4", "p5", "lowest"),
    }
    for level, words in aliases.items():
        if any(w in v for w in words):
            return level
    return "normal"


@dataclass
class Task:
    id: str
    title: str
    kind: str = "fleet"                  # fleet | coding | generic | todo
    status: str = "running"             # running | done | failed  (todo: open | done)
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_progress: str = ""
    result: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    # --- manageable to-do fields (kind == "todo") ---------------------------------------
    description: str = ""               # a longer note about what the task involves
    priority: str = "normal"           # low | normal | high | urgent
    deadline: float | None = None      # Unix epoch of the due date/time, or None
    progress: int = 0                  # 0..100 percent complete

    @property
    def elapsed_s(self) -> float:
        end = self.updated_at if self.status not in ("running", "open") else time.time()
        return max(0.0, end - self.started_at)

    def human_elapsed(self) -> str:
        s = int(self.elapsed_s)
        if s < 60:
            return f"{s}s"
        if s < 3600:
            return f"{s // 60}m {s % 60}s"
        return f"{s // 3600}h {(s % 3600) // 60}m"

    def human_deadline(self) -> str:
        """A human phrase for the deadline ('overdue by 2d', 'due today', 'in 3d'), or '' if none."""
        if self.deadline is None:
            return ""
        delta = self.deadline - time.time()
        days = delta / 86400
        if delta < 0:
            od = -delta
            if od < 3600:
                return "overdue"
            if od < 86400:
                return f"overdue by {int(od // 3600)}h"
            return f"overdue by {int(od // 86400)}d"
        if delta < 3600:
            return f"due in {int(delta // 60)}m"
        if days < 1:
            return f"due in {int(delta // 3600)}h"
        if days < 2:
            return "due tomorrow"
        return f"due in {int(days)}d"

    @property
    def prio_rank(self) -> int:
        return _PRIO_RANK.get(self.priority, 1)


# Completion callback: set by the server so a finished task is announced by voice. Signature: (Task).
OnComplete = Callable[[Task], Awaitable[None] | None]


class TaskQueue:
    """A small persisted registry of background tasks + a runner that keeps them updated."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or _db_path()
        self._tasks: dict[str, Task] = {}
        self._bg: set[asyncio.Task] = set()
        self.on_complete: OnComplete | None = None
        self._init_db()
        self._load()

    # ---- persistence ------------------------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        return connect(self._path)

    # Columns added after the original release; migrated onto pre-existing DBs at startup.
    _EXTRA_COLS = (("description", "TEXT"), ("priority", "TEXT"), ("deadline", "REAL"),
                   ("progress", "INTEGER"))

    def _init_db(self) -> None:
        try:
            with self._conn() as c:
                c.execute(
                    "CREATE TABLE IF NOT EXISTS tasks ("
                    "id TEXT PRIMARY KEY, title TEXT, kind TEXT, status TEXT, "
                    "started_at REAL, updated_at REAL, last_progress TEXT, result TEXT, meta TEXT, "
                    "description TEXT, priority TEXT, deadline REAL, progress INTEGER)"
                )
                # Migrate older DBs that predate the to-do columns (ALTER is a no-op-if-exists guard).
                have = {r["name"] for r in c.execute("PRAGMA table_info(tasks)")}
                for name, sqltype in self._EXTRA_COLS:
                    if name not in have:
                        c.execute(f"ALTER TABLE tasks ADD COLUMN {name} {sqltype}")
        except sqlite3.Error as e:
            logger.warning(f"task queue: db init failed ({e}); running in-memory only")

    def _persist(self, t: Task) -> None:
        try:
            with self._conn() as c:
                c.execute(
                    "INSERT OR REPLACE INTO tasks "
                    "(id, title, kind, status, started_at, updated_at, last_progress, result, meta, "
                    "description, priority, deadline, progress) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (t.id, t.title, t.kind, t.status, t.started_at, t.updated_at,
                     t.last_progress, t.result, json.dumps(t.meta),
                     t.description, t.priority, t.deadline, t.progress),
                )
        except sqlite3.Error as e:
            logger.warning(f"task queue: persist failed ({e})")

    def _delete_row(self, task_id: str) -> None:
        try:
            with self._conn() as c:
                c.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        except sqlite3.Error:
            pass

    def _load(self) -> None:
        """Reload the owner's open to-do items after a restart (so the list survives).

        Background WORK jobs are in-process asyncio tasks — they CANNOT survive a restart, so a
        persisted ``running`` row is always from a dead process. The old code reloaded those as
        ``running``, so every crash left a fake-"running" job forever (the 20-orphan pile-up). We now
        expire them to ``failed`` on load and reload only real persistent to-dos.
        """
        try:
            with self._conn() as c:
                c.execute("UPDATE tasks SET status='failed', last_progress='abandoned on restart' "
                          "WHERE status='running'")
                rows = c.execute("SELECT * FROM tasks WHERE kind='todo' AND status='open'")
                for r in rows:
                    self._tasks[r["id"]] = Task(
                        id=r["id"], title=r["title"], kind=r["kind"], status=r["status"],
                        started_at=r["started_at"], updated_at=r["updated_at"],
                        last_progress=r["last_progress"] or "", result=r["result"] or "",
                        meta=json.loads(r["meta"] or "{}"),
                        description=(r["description"] or "") if "description" in r.keys() else "",
                        priority=(r["priority"] or "normal") if "priority" in r.keys() else "normal",
                        deadline=r["deadline"] if "deadline" in r.keys() else None,
                        progress=int(r["progress"] or 0) if "progress" in r.keys() else 0,
                    )
        except sqlite3.Error:
            pass

    # ---- registry ---------------------------------------------------------------------
    def add(self, title: str, kind: str = "fleet", meta: dict | None = None) -> Task:
        t = Task(id=uuid.uuid4().hex[:8], title=title.strip()[:200], kind=kind, meta=meta or {})
        self._tasks[t.id] = t
        self._persist(t)
        return t

    def update(self, task_id: str, progress: str) -> None:
        t = self._tasks.get(task_id)
        if not t:
            return
        t.last_progress = progress.strip()[:500]
        t.updated_at = time.time()
        self._persist(t)

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def active(self) -> list[Task]:
        return [t for t in self._tasks.values() if t.status == "running"]

    def find(self, topic: str) -> list[Task]:
        """Match active tasks by a word in their title (for 'how's the website going?')."""
        q = (topic or "").strip().lower()
        act = self.active()
        if not q:
            return act
        return [t for t in act if any(w in t.title.lower() for w in q.split())] or act

    def complete(self, task_id: str, status: str, result: str = "", meta: dict | None = None) -> Task | None:
        t = self._tasks.get(task_id)
        if not t:
            return None
        t.status = status
        t.result = (result or "").strip()
        t.updated_at = time.time()
        if meta:
            t.meta.update(meta)
        t.meta.setdefault("duration", t.human_elapsed())
        self._persist(t)
        return t

    def drop(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)
        self._delete_row(task_id)

    # ---- manageable to-do list --------------------------------------------------------
    # These are owner-facing tasks (kind == "todo") the model can add, describe, prioritise,
    # give a deadline, track progress on, complete, and delete — distinct from the background
    # execution jobs above (which the fleet runner drives).
    def add_todo(
        self,
        title: str,
        *,
        description: str = "",
        priority: str = "normal",
        deadline: float | None = None,
        meta: dict | None = None,
    ) -> Task:
        t = Task(
            id=uuid.uuid4().hex[:8],
            title=title.strip()[:200],
            kind="todo",
            status="open",
            description=(description or "").strip()[:1000],
            priority=normalize_priority(priority),
            deadline=deadline,
            meta=meta or {},
        )
        self._tasks[t.id] = t
        self._persist(t)
        return t

    def edit_todo(self, task_id: str, **fields: Any) -> Task | None:
        """Update any of title/description/priority/deadline/progress/status on a to-do.

        Only keys explicitly present in ``fields`` are touched (so a caller can clear the
        deadline by passing ``deadline=None`` and leave everything else alone)."""
        t = self._tasks.get(task_id)
        if t is None:
            return None
        if "title" in fields and fields["title"]:
            t.title = str(fields["title"]).strip()[:200]
        if "description" in fields:
            t.description = str(fields["description"] or "").strip()[:1000]
        if "priority" in fields and fields["priority"]:
            t.priority = normalize_priority(fields["priority"])
        if "deadline" in fields:
            t.deadline = fields["deadline"]
        if "progress" in fields and fields["progress"] is not None:
            t.progress = max(0, min(100, int(fields["progress"])))
        if "note" in fields and fields["note"]:
            t.last_progress = str(fields["note"]).strip()[:500]
        if "status" in fields and fields["status"]:
            t.status = str(fields["status"])
        t.updated_at = time.time()
        self._persist(t)
        return t

    def complete_todo(self, task_id: str) -> Task | None:
        t = self._tasks.get(task_id)
        if t is None:
            return None
        t.status = "done"
        t.progress = 100
        t.updated_at = time.time()
        self._persist(t)  # keep it as a record; todos() filters it out of the OPEN list
        return t

    def todos(self, include_done: bool = False) -> list[Task]:
        """Open to-do items, most-urgent first: higher priority, then nearest deadline."""
        items = [t for t in self._tasks.values()
                 if t.kind == "todo" and (include_done or t.status == "open")]
        items.sort(key=lambda t: (-t.prio_rank, t.deadline if t.deadline is not None else 9e18,
                                  t.started_at))
        return items

    def find_todo(self, topic: str) -> list[Task]:
        """Match open to-dos by id or a word in the title/description."""
        q = (topic or "").strip().lower()
        if not q:
            return self.todos()
        exact = self._tasks.get(q)
        if exact is not None and exact.kind == "todo":
            return [exact]
        return [t for t in self.todos()
                if q in t.id or any(w in (t.title + " " + t.description).lower() for w in q.split())]

    def todo_summary_line(self, t: Task) -> str:
        """A spoken one-liner for a to-do: title, priority, deadline, progress."""
        bits = [f"'{t.title}'"]
        if t.priority != "normal":
            bits.append(f"[{t.priority}]")
        dl = t.human_deadline()
        if dl:
            bits.append(dl)
        if t.status == "open" and t.progress:
            bits.append(f"{t.progress}% done")
        if t.status == "done":
            bits.append("✓ done")
        tail = f" — {t.last_progress}" if (t.last_progress and t.status == "open") else ""
        return " ".join(bits) + f" (id {t.id[:8]})" + tail

    # ---- the runner -------------------------------------------------------------------
    def run(
        self,
        title: str,
        coro_factory: Callable[[Callable[[str], None]], Awaitable[str]],
        kind: str = "fleet",
        meta: dict | None = None,
    ) -> Task:
        """Enqueue background work and return its Task immediately.

        ``coro_factory(on_progress)`` must return the awaitable doing the work; each ``on_progress``
        note is persisted as the task's latest status. On finish we mark done/failed, attach
        metadata, fire ``on_complete``, and drop it from the active queue.
        """
        t = self.add(title, kind=kind, meta=meta)

        def _progress(note: str) -> None:
            self.update(t.id, note)

        async def _runner() -> None:
            try:
                result = await coro_factory(_progress)
                self.complete(t.id, "done", result=str(result),
                              meta={"complexity": _grade(t.elapsed_s)})
            except Exception as e:  # noqa: BLE001 — a failed task must never crash the brain
                logger.warning(f"background task {t.id} failed: {type(e).__name__}: {e}")
                # Autonomous work the owner never watched happen. Record the TITLE, not just the id
                # — six rows saying "task 0c7d735c failed" tell you nothing about what was lost.
                from afon.shared import errors as _err

                _err.record_op("agentic", "background_task", ok=False,
                               detail=f"{type(e).__name__}: {e}",
                               context={"task": t.title[:120], "id": t.id})
                self.complete(t.id, "failed", result=f"{type(e).__name__}: {e}")
            await self._announce(t.id)
            self.drop(t.id)

        bg = asyncio.ensure_future(_runner())
        self._bg.add(bg)
        bg.add_done_callback(self._bg.discard)
        logger.info(f"task queue: started [{t.id}] {t.title!r}")
        return t

    async def _announce(self, task_id: str) -> None:
        t = self._tasks.get(task_id)
        if not t or self.on_complete is None:
            return
        try:
            res = self.on_complete(t)
            if asyncio.iscoroutine(res):
                await res
        except Exception as e:  # noqa: BLE001
            logger.warning(f"task completion announce failed: {e}")

    def summary_line(self, t: Task) -> str:
        """A spoken status line for one task."""
        if t.status == "running":
            tail = f" — {t.last_progress}" if t.last_progress else ""
            return f"'{t.title}' is still in progress ({t.human_elapsed()} so far){tail}, sir."
        if t.status == "failed":
            return f"'{t.title}' failed after {t.human_elapsed()}, sir: {t.result[:160]}"
        return f"'{t.title}' is done ({t.meta.get('duration', t.human_elapsed())}), sir."


def _grade(elapsed_s: float) -> str:
    if elapsed_s < 30:
        return "quick"
    if elapsed_s < 300:
        return "moderate"
    return "heavy"


# Process-wide singleton (mirrors STORE / SCHEDULER).
TASKS = TaskQueue()
