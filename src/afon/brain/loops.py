"""Every background loop, declared in one place — "no silent work" (SYSTEMS.md 31.F4).

The standing efficiency clause says a background loop must declare three things: its **period**, its
**budget**, and its **kill switch**. Nothing enforced that, and the cost is on the record twice —
the focus sprint that was dead for three days while every surface stayed green, and the seven crons
that reported ``status=ok`` with a failed tool inside them. Both were loops nobody could *see*.

Two halves, and the first is the one that matters:

* ``DECLARED`` is **static**. It lists every recurring loop in the brain whether or not it ever
  starts. A registry populated at start-up cannot report the failure it exists to catch: a loop that
  never started leaves no entry, so the surface reads clean precisely when it should read alarming.
* the tick records are runtime. ``last_tick is None`` on a declared loop means *never ran in this
  process* — which is a statement, not a gap.

Recording is deliberately not a decorator-only affair. A cron job's tick is the whole call, so
``@ticks`` wraps it; a poller's tick is one iteration of a ``while True`` that never returns, so it
uses ``with tick(...)`` inside the loop. Wrapping a poller's ``run()`` would measure the process
lifetime and call it a duration.

ponytail: in-process dict, no store, mirroring METRICS. Ticks reset on restart and that is correct —
"last tick" is a question about *this* process. Durable history belongs in the audit trail.
"""

from __future__ import annotations

import functools
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

DAY = 86_400
HOUR = 3_600


@dataclass(frozen=True)
class Loop:
    """One declared background loop. ``period_s`` is the nominal gap between runs — for a cron job
    that is its schedule expressed as seconds, which is what makes staleness comparable across
    interval pollers and cron jobs without a second rule."""

    name: str
    what: str
    kind: str            # "cron" (scheduled) | "poller" (fixed interval) | "listener" (event-driven)
    period_s: float | None
    schedule: str
    budget_ms: float
    kill: str            # how the owner stops it, in his own terms
    module: str


#: The declarations. Adding a recurring loop to the brain without adding a row here fails
#: ``bench/test_loop_registry.py`` — that is the whole point of the table being static.
DECLARED: tuple[Loop, ...] = (
    Loop("daily-task-briefing", "the morning catch-up: past-due tasks + important unread mail",
         "cron", DAY, "daily at settings.briefing_time", 30_000,
         "set briefing_time empty", "afon.brain.scheduler"),
    Loop("daily-objectives", "advance each active multi-day objective by one step",
         "cron", DAY, "daily at settings.objectives_time", 120_000,
         "set objectives_time empty", "afon.brain.scheduler"),
    Loop("daily-backlog", "the autonomous backlog pass over overdue/inbox Notion tasks",
         "cron", DAY, "daily at settings.backlog_time", 300_000,
         "set backlog_time empty", "afon.brain.scheduler"),
    Loop("daily-memory-backup", "snapshot the memory stores",
         "cron", DAY, "daily at 03:30", 60_000,
         "unschedule the job", "afon.brain.scheduler"),
    Loop("daily-restore-drill", "restore the newest backup into a throwaway dir and prove it works",
         "cron", DAY, "daily at 05:00", 60_000,
         "unschedule the job", "afon.brain.scheduler"),
    Loop("daily-pattern-scan", "scan the command log and write new patterns as L1 facts",
         "cron", DAY, "daily at 04:30", 30_000,
         "unschedule the job", "afon.brain.scheduler"),
    Loop("weekly-memory-review", "surface the last 20 learned facts to the owner",
         "cron", 7 * DAY, "weekly, SUN 20:00", 60_000,
         "unschedule the job", "afon.brain.scheduler"),
    Loop("memory-maintenance", "dedupe facts, cap learned memory, rotate journals",
         "cron", DAY, "daily at 04:00", 30_000,
         "unschedule the job", "afon.brain.maintenance"),
    Loop("reliability-health-probe", "probe the critical organs, self-repair, escalate a real outage",
         "cron", 4 * HOUR, "every 4 hours", 15_000,
         "unschedule the job", "afon.brain._scheduled_jobs"),
    Loop("composio-catalog-refresh", "re-cache the Composio tool catalogue overnight",
         "cron", DAY, "daily at 03:15", 60_000,
         "unschedule the job", "afon.brain._scheduled_jobs"),
    Loop("presence-poller", "sample the laptop's foreground window so context knows the situation",
         "poller", None, "every settings.presence_poll_seconds", 2_000,
         "PRESENCE.pause() / stop tracking", "afon.brain.presence"),
    Loop("proactive-tick", "decide whether anything is worth saying unprompted",
         "poller", None, "every settings.proactive_tick_seconds", 5_000,
         "AFON_PROACTIVE_ENABLED=false", "afon.brain.proactive"),
    Loop("telegram-bridge", "inbound Telegram messages — 24/7 reachability with the laptop off",
         "listener", None, "long-poll, per inbound message", 120_000,
         "clear the bot token", "afon.brain.telegram_bridge"),
)

BY_NAME: dict[str, Loop] = {lp.name: lp for lp in DECLARED}


@dataclass
class _Ticks:
    runs: int = 0
    errors: int = 0
    overruns: int = 0
    last_tick: float | None = None
    last_ms: float | None = None
    last_error: str = ""
    period_s: float | None = None   # observed at runtime, for loops whose interval is configured
    extra: dict[str, Any] = field(default_factory=dict)


_lock = threading.Lock()
_ticks: dict[str, _Ticks] = {}


def _rec(name: str) -> _Ticks:
    t = _ticks.get(name)
    if t is None:
        t = _ticks[name] = _Ticks()
    return t


def set_period(name: str, period_s: float | None) -> None:
    """Record the interval a poller actually started with. The declaration names the *setting*;
    this names the number it resolved to, which is the one that explains a stale tick."""
    with _lock:
        _rec(name).period_s = float(period_s) if period_s else None


class tick:
    """Context manager recording one iteration. Never swallows: an exception is counted and re-raised,
    because a loop that hides its own failures is the thing this module exists to prevent."""

    __slots__ = ("name", "_t0")

    def __init__(self, name: str) -> None:
        self.name = name
        self._t0 = 0.0

    def __enter__(self) -> "tick":
        self._t0 = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        ms = (time.monotonic() - self._t0) * 1000.0
        budget = BY_NAME[self.name].budget_ms if self.name in BY_NAME else None
        with _lock:
            r = _rec(self.name)
            r.runs += 1
            r.last_tick = time.time()
            r.last_ms = round(ms, 1)
            if budget is not None and ms > budget:
                r.overruns += 1
            if exc is not None:
                r.errors += 1
                r.last_error = f"{type(exc).__name__}: {exc}"[:200]
        return False   # never suppress


def ticks(name: str) -> Callable:
    """Decorator form, for a cron job target whose whole call IS the tick.

    ``functools.wraps`` matters beyond tidiness here: APScheduler's SQLite jobstore serialises the
    target by its module path and qualname, so a wrapper that renamed the function would make every
    persisted job unresolvable after a restart.
    """

    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def inner(*a, **kw):
            with tick(name):
                return await fn(*a, **kw)
        return inner

    return deco


def snapshot(now: float | None = None) -> list[dict]:
    """One row per DECLARED loop, in declaration order. Loops that have never run are included and
    say so; that is the row worth reading."""
    now = now if now is not None else time.time()
    out: list[dict] = []
    with _lock:
        for lp in DECLARED:
            r = _ticks.get(lp.name) or _Ticks()
            period = r.period_s or lp.period_s
            age = (now - r.last_tick) if r.last_tick else None
            # Two periods of silence. One is a race with the schedule; two is a loop that stopped.
            # No period, no staleness — and that is exactly why a listener declares none: it is
            # event-driven, so a quiet week means nobody wrote, not that it died. Guarding on `kind`
            # as well was unfalsifiable: it could never change an outcome the period check hadn't
            # already decided, so it read as protection while protecting nothing.
            stale = bool(period and age is not None and age > 2 * period)
            out.append({
                "name": lp.name,
                "what": lp.what,
                "kind": lp.kind,
                "schedule": lp.schedule,
                "period_s": period,
                "budget_ms": lp.budget_ms,
                "kill": lp.kill,
                "module": lp.module,
                "runs": r.runs,
                "errors": r.errors,
                "overruns": r.overruns,
                "last_tick": round(r.last_tick, 1) if r.last_tick else None,
                "last_age_s": round(age, 1) if age is not None else None,
                "last_ms": r.last_ms,
                "last_error": r.last_error,
                "never_ran": r.last_tick is None,
                "stale": stale,
            })
    return out


def problems(now: float | None = None) -> list[str]:
    """One line per loop that is not behaving, for a status sentence. Empty when all is well.
    Never-ran is NOT a problem on its own — a fresh process has run nothing yet, and crying about
    that every restart is how a surface teaches its owner to ignore it."""
    out = []
    for row in snapshot(now):
        if row["stale"]:
            out.append(f"{row['name']}: no tick in {int(row['last_age_s'])}s "
                       f"(period {int(row['period_s'])}s)")
        elif row["errors"] and row["last_error"]:
            out.append(f"{row['name']}: {row['last_error']}")
        elif row["overruns"]:
            out.append(f"{row['name']}: {row['overruns']} run(s) over its "
                       f"{int(row['budget_ms'])}ms budget")
    return out


def reset() -> None:
    """Drop all tick records (tests only — the declarations are untouched)."""
    with _lock:
        _ticks.clear()


def _iter_declared() -> Iterator[Loop]:
    return iter(DECLARED)
