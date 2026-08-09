"""Proactive scheduler — the engine behind reminders and timed/recurring nudges (Phase 4).

A single process-wide ``SCHEDULER`` (APScheduler ``AsyncIOScheduler``) persisted to SQLite, so
reminders survive a restart. When a job fires it calls the module-level coroutine ``_fire`` (a
top-level function so APScheduler can serialize the reference), which delivers the message two
ways: it **speaks** it through the live edge callback if Afon is running, and **pushes** it
to the phone via ntfy. Either path is best-effort.

Triggers supported by ``add_reminder``: a one-shot delay (``in_minutes``), an absolute time
(``at`` ISO-8601), or a daily time (``daily`` 'HH:MM').
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from loguru import logger

from afon.config import settings

USER_TZ = ZoneInfo(settings.user_tz)

# Set by SCHEDULER.start(): how to SPEAK a fired reminder on the live edge (push a TTS frame).
_LIVE_SPEAK: Callable[[str], None] | None = None

# Set by the brain server: richest proactive delivery (voice -> Telegram voice note -> ntfy push).
# Signature mirrors BrainServer.proactive_emit(message, urgency, speak) -> awaitable[str].
_BRIEFING_EMIT: Callable | None = None

# Set by the brain server to the agent's autonomous backlog pass (agent.run_backlog) — Phase 3.1.
_BACKLOG_RUNNER: Callable | None = None
_OBJECTIVES_RUNNER: Callable | None = None
# Set by the brain server to agent.backlog_report: a proactive action report (what+why+reasoning) so
# the autonomous backlog pass is never silent. None = report disabled (the pass still runs + comments).
_BACKLOG_REPORTER: Callable | None = None


async def _emit_proactive(msg: str, urgency: float, title: str) -> None:
    """Deliver an unprompted line the proactive way: listening edge → Telegram voice note → ntfy push,
    with a speak+push fallback if no emitter is wired. Shared by the autonomous report jobs."""
    if not msg:
        return
    if _BRIEFING_EMIT is not None:
        try:
            r = _BRIEFING_EMIT(msg, urgency, True)
            if hasattr(r, "__await__"):
                await r
            return
        except Exception as e:  # noqa: BLE001
            logger.warning(f"proactive-emit failed ({e}); falling back to speak+push")
    if _LIVE_SPEAK is not None:
        try:
            _LIVE_SPEAK(msg)
        except Exception:  # noqa: BLE001
            pass
    try:
        from afon.brain.tools.notify import push

        await push(msg, title=title)
    except Exception:  # noqa: BLE001
        pass


async def _fire_briefing() -> None:
    """Top-level job target (importable for the SQLite jobstore): the daily consolidated catch-up.

    Builds ONE digest at fire time (past-due tasks + important unread email — see daily_digest),
    delivers it the proactive way (spoken to a listening device, else Telegram voice note, else
    ntfy push), and marks the 'push' channel delivered for today so it can't repeat. The same digest
    is separately appended to the owner's first live-edge turn (server._daily_digest_addendum).
    """
    from afon.brain import daily_digest

    try:
        body = await daily_digest.build_body()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"daily digest build failed: {e}")
        return
    if not body:
        msg = "Good morning, sir. Nothing past due and no important mail — you're all clear."
    else:
        msg = "Good morning, sir. Here's your catch-up. " + body
    daily_digest.mark_delivered("push", datetime.now(USER_TZ))
    logger.info("firing daily digest briefing")
    if _BRIEFING_EMIT is not None:
        try:
            res = _BRIEFING_EMIT(msg, 0.6, True)
            if hasattr(res, "__await__"):
                await res
            return
        except Exception as e:  # noqa: BLE001
            logger.warning(f"briefing proactive-emit failed ({e}); falling back to speak+push")
    # Fallback (no proactive emitter wired): speak on the live edge + push to phone.
    if _LIVE_SPEAK is not None:
        try:
            _LIVE_SPEAK(msg)
        except Exception:  # noqa: BLE001
            pass
    try:
        from afon.brain.tools.notify import push

        await push(msg, title="Afon — today")
    except Exception:  # noqa: BLE001
        pass


async def _fire_backlog() -> None:
    """Top-level job target: the daily autonomous backlog pass (Phase 3.1).

    Calls the runner the brain wired to ``agent.run_backlog`` — it pulls overdue/inbox Notion tasks,
    has the bounded worker attempt the safe work, and comments the results. Safe by construction (the
    worker defers every outward step). No runner wired -> no-op; any error is logged, never raised."""
    if _BACKLOG_RUNNER is None:
        return
    logger.info("firing daily backlog pass")
    try:
        res = _BACKLOG_RUNNER()
        done = await res if hasattr(res, "__await__") else res
        logger.info(f"daily backlog pass attempted {len(done) if done else 0} task(s)")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"daily backlog pass failed: {e}")
        return
    # Report it UNPROMPTED: what he did, why, and his reasoning — so an autonomous action is never
    # silent (the owner's ask). Skipped when nothing was done or no reporter is wired.
    if not done or _BACKLOG_REPORTER is None:
        return
    try:
        rr = _BACKLOG_REPORTER(done)
        msg = await rr if hasattr(rr, "__await__") else rr
    except Exception as e:  # noqa: BLE001 — a report must never break the pass
        logger.warning(f"daily backlog report failed: {e}")
        return
    logger.info(f"backlog pass done: {len(done)} task(s) — reporting proactively")
    await _emit_proactive(msg, 0.55, "Afon — backlog")


async def _fire_objectives() -> None:
    """Top-level job target: the daily multi-day OBJECTIVES advance (Phase 4.1).

    Calls the runner the brain wired to ``agent.advance_objectives`` — it advances the top active
    objectives one SAFE step each (bounded worker; outward steps deferred), logs dated progress, and
    returns what moved. This job then reports it UNPROMPTED via the proactive briefing path. No runner
    wired -> no-op; any error is logged, never raised."""
    if _OBJECTIVES_RUNNER is None:
        return
    logger.info("firing daily objectives advance")
    try:
        res = _OBJECTIVES_RUNNER()
        advanced = await res if hasattr(res, "__await__") else res
    except Exception as e:  # noqa: BLE001
        logger.warning(f"daily objectives advance failed: {e}")
        return
    from afon.brain.objectives import spoken_objectives_report

    msg = spoken_objectives_report(advanced or [])
    if not msg:
        return
    logger.info(f"objectives advanced: {len(advanced)} — reporting")
    if _BRIEFING_EMIT is not None:
        try:
            r = _BRIEFING_EMIT(msg, 0.55, True)
            if hasattr(r, "__await__"):
                await r
            return
        except Exception as e:  # noqa: BLE001
            logger.warning(f"objectives proactive-emit failed ({e}); falling back to speak+push")
    if _LIVE_SPEAK is not None:
        try:
            _LIVE_SPEAK(msg)
        except Exception:  # noqa: BLE001
            pass
    try:
        from afon.brain.tools.notify import push

        await push(msg, title="Afon — objectives")
    except Exception:  # noqa: BLE001
        pass


async def _fire_pattern_scan() -> None:
    """T3b: scan the rolling command log for repeating patterns; persist as L1 facts."""
    try:
        from afon.brain.patterns import persist_as_l1
        from afon.brain.memory import STORE
        added = persist_as_l1(STORE)
        if added:
            logger.info(f"daily pattern scan: {len(added)} new pattern fact(s)")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"daily pattern scan failed: {e}")


async def _fire_weekly_review() -> None:
    """T3c: weekly memory review prompt — drop the last 20 learned facts into the owner's chat
    and ask them to confirm / correct / forget any. Routes through the proactive engine if
    available; otherwise logs the proposed review for the next session."""
    try:
        from afon.brain.memory import STORE
        recent = STORE.recent_digest(limit=20)
        if not recent:
            return
        body = "Afon learned these facts this week, sir — anything to correct or forget?\n\n"
        body += "\n".join(f"  • {f}" for f in recent)
        # Surface via the proactive engine (it'll DM Telegram / nudge) if the brain has one.
        try:
            from afon.brain.proactive import proactive  # noqa: F401  may not exist as instance
            sched = get_scheduler()
            if hasattr(sched, "_proactive") and sched._proactive:  # type: ignore[attr-defined]
                sched._proactive.request_speak(body)  # type: ignore[attr-defined]
                return
        except Exception:
            pass
        # Fallback: log so the next session surfaces it.
        logger.info(f"weekly memory review prompt:\n{body}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"weekly memory review failed: {e}")


async def _fire_backup() -> None:
    """Top-level job target: zip Afon's learned facts + journal (L1/L2) into ``backups/`` and keep
    the most recent 14. The memory dir is the one durable store with no other automated backup (the
    OpenClaw vault sync only covers the Obsidian vault). Fail-quiet — never raises into the loop."""
    import shutil
    from pathlib import Path

    try:
        from afon.brain.memory import STORE

        src = STORE.base
        if not src.is_dir():
            return
        backups = Path(__file__).resolve().parents[3] / "backups"
        backups.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.make_archive(str(backups / f"afon-memory-{stamp}"), "zip", root_dir=str(src))
        keep = sorted(backups.glob("afon-memory-*.zip"))[:-14]
        for old in keep:
            old.unlink(missing_ok=True)
        logger.info(f"memory backup written ({stamp}); pruned {len(keep)} old archive(s)")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"memory backup failed: {e}")


async def _fire(message: str, push_phone: bool = True) -> None:
    """Top-level job target (must be importable for the SQLite jobstore). Delivers a reminder.

    ``push_phone`` is False when the phone delivery was already handed to ntfy's server-side
    scheduler at set-time (Phase 4b): in that case this in-process firing only *speaks* it (when
    the edge is live), and ntfy independently delivers the push — avoiding a double-push. If the
    edge is off, ntfy still delivers; if ntfy couldn't take it, push_phone stays True as a fallback.
    """
    logger.info(f"reminder fired: {message!r} (push_phone={push_phone})")
    spoke = False
    if _LIVE_SPEAK is not None:
        try:
            _LIVE_SPEAK(message)
            spoke = True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"reminder speak failed: {e}")
    if push_phone:
        try:
            from afon.brain.tools.notify import push

            await push(message, title="Reminder" if spoke else "Afon reminder")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"reminder push failed: {e}")


def _db_url() -> str:
    from pathlib import Path

    path = settings.scheduler_db_path or str(
        Path(__file__).resolve().parents[3] / "afon_jobs.sqlite"
    )
    return f"sqlite:///{path}"


class Scheduler:
    def __init__(self) -> None:
        self._sched = None

    def _ensure(self):
        if self._sched is not None:
            return self._sched
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        self._sched = AsyncIOScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=_db_url())},
            timezone=USER_TZ,
        )
        # One listener tracks EVERY scheduled job — reminders, the daily briefing, objectives,
        # backlog, backups, pattern scans, the weekly review, and anything added later. These run
        # unattended, so a job that starts erroring (or silently stops firing) is precisely the kind
        # of failure that otherwise goes unnoticed for weeks. A MISSED job matters as much as a
        # failed one: a reminder that never fired is indistinguishable, to the owner, from a
        # reminder that was never set.
        try:
            from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED

            from afon.shared import errors as _err

            def _on_job_event(event) -> None:
                name = getattr(event, "job_id", "") or "job"
                exc = getattr(event, "exception", None)
                if exc is not None:
                    _err.record_op("job", name, ok=False, detail=f"{type(exc).__name__}: {exc}")
                else:
                    _err.record_op("job", name, ok=False, detail="job missed its scheduled run")

            self._sched.add_listener(_on_job_event, EVENT_JOB_ERROR | EVENT_JOB_MISSED)
        except Exception as e:  # noqa: BLE001 — tracking must never stop the scheduler existing
            logger.warning(f"scheduler error tracking unavailable: {type(e).__name__}: {e}")
        return self._sched

    def start(self, on_speak: Callable[[str], None] | None = None) -> None:
        """Start the scheduler (idempotent) and register how to speak fired reminders."""
        global _LIVE_SPEAK
        if on_speak is not None:
            _LIVE_SPEAK = on_speak
        sched = self._ensure()
        if not sched.running:
            sched.start()
            logger.info("scheduler started")

    def set_briefing_emit(self, emit: Callable | None) -> None:
        """Register the proactive delivery used by the daily briefing (server.proactive_emit)."""
        global _BRIEFING_EMIT
        _BRIEFING_EMIT = emit

    def schedule_daily_briefing(self, hhmm: str) -> str | None:
        """(Re)register the daily task briefing at HH:MM. Fixed id so restarts refresh, not duplicate.
        Returns the job id, or None if disabled/invalid."""
        if not (hhmm or "").strip():
            return None
        from apscheduler.triggers.cron import CronTrigger

        hh, mm = _parse_hhmm(hhmm)
        sched = self._ensure()
        sched.add_job(_fire_briefing, trigger=CronTrigger(hour=hh, minute=mm, timezone=USER_TZ),
                      id="daily-task-briefing", name="daily task briefing",
                      misfire_grace_time=3600, coalesce=True, replace_existing=True)
        logger.info(f"daily task briefing scheduled for {hh:02d}:{mm:02d}")
        return "daily-task-briefing"

    def set_backlog_runner(self, runner: Callable | None) -> None:
        """Register the async callable the daily backlog job runs (server wires agent.run_backlog)."""
        global _BACKLOG_RUNNER
        _BACKLOG_RUNNER = runner

    def set_backlog_reporter(self, reporter: Callable | None) -> None:
        """Register the async callable that turns the pass's attempts into a proactive spoken report
        (server wires agent.backlog_report). None = the pass runs + comments but stays silent."""
        global _BACKLOG_REPORTER
        _BACKLOG_REPORTER = reporter

    def set_objectives_runner(self, runner: Callable | None) -> None:
        """Register the async callable the daily objectives job runs (server wires
        agent.advance_objectives)."""
        global _OBJECTIVES_RUNNER
        _OBJECTIVES_RUNNER = runner

    def schedule_daily_objectives(self, hhmm: str) -> str | None:
        """(Re)register the daily multi-day objectives advance at HH:MM. Fixed id so restarts refresh,
        not duplicate. Returns the job id, or None if disabled/invalid."""
        if not (hhmm or "").strip():
            return None
        from apscheduler.triggers.cron import CronTrigger

        hh, mm = _parse_hhmm(hhmm)
        sched = self._ensure()
        sched.add_job(_fire_objectives, trigger=CronTrigger(hour=hh, minute=mm, timezone=USER_TZ),
                      id="daily-objectives", name="daily objectives advance",
                      misfire_grace_time=3600, coalesce=True, replace_existing=True)
        logger.info(f"daily objectives advance scheduled for {hh:02d}:{mm:02d}")
        return "daily-objectives"

    def schedule_daily_backlog(self, hhmm: str) -> str | None:
        """(Re)register the daily autonomous backlog pass at HH:MM. Fixed id so restarts refresh,
        not duplicate. Returns the job id, or None if disabled/invalid."""
        if not (hhmm or "").strip():
            return None
        from apscheduler.triggers.cron import CronTrigger

        hh, mm = _parse_hhmm(hhmm)
        sched = self._ensure()
        sched.add_job(_fire_backlog, trigger=CronTrigger(hour=hh, minute=mm, timezone=USER_TZ),
                      id="daily-backlog", name="daily backlog pass",
                      misfire_grace_time=3600, coalesce=True, replace_existing=True)
        logger.info(f"daily backlog pass scheduled for {hh:02d}:{mm:02d}")
        return "daily-backlog"

    def schedule_daily_backup(self, hhmm: str = "03:30") -> str | None:
        """(Re)register the daily memory backup at HH:MM. Fixed id so restarts refresh, not
        duplicate. Returns the job id."""
        from apscheduler.triggers.cron import CronTrigger

        hh, mm = _parse_hhmm(hhmm)
        sched = self._ensure()
        sched.add_job(_fire_backup, trigger=CronTrigger(hour=hh, minute=mm, timezone=USER_TZ),
                      id="daily-memory-backup", name="daily memory backup",
                      misfire_grace_time=3600, coalesce=True, replace_existing=True)
        logger.info(f"daily memory backup scheduled for {hh:02d}:{mm:02d}")
        return "daily-memory-backup"

    def schedule_pattern_scan(self, hhmm: str = "04:30") -> str | None:
        """T3b: daily pattern-detection pass — scans the rolling command log and writes new patterns
        as L1 facts (with the 'pattern' tag). Runs after maintenance so fresh digests are available."""
        from apscheduler.triggers.cron import CronTrigger

        hh, mm = _parse_hhmm(hhmm)
        sched = self._ensure()
        sched.add_job(_fire_pattern_scan, trigger=CronTrigger(hour=hh, minute=mm, timezone=USER_TZ),
                      id="daily-pattern-scan", name="daily pattern scan",
                      misfire_grace_time=3600, coalesce=True, replace_existing=True)
        logger.info(f"daily pattern scan scheduled for {hh:02d}:{mm:02d}")
        return "daily-pattern-scan"

    def schedule_weekly_review(self, hhmm: str = "SUN 20:00") -> str | None:
        """T3c: weekly review — surface the last 20 learned facts to the owner as a Telegram voice-note
        or a proactive nudge. ``SUN 20:00`` = Sunday 8pm in the owner's TZ."""
        from apscheduler.triggers.cron import CronTrigger

        parts = (hhmm or "").strip().split()
        if len(parts) != 2:
            return None
        dow, hm = parts[0].upper(), parts[1]
        dow_map = {"SUN": "sun", "MON": "mon", "TUE": "tue", "WED": "wed", "THU": "thu", "FRI": "fri", "SAT": "sat"}
        dow_lit = dow_map.get(dow)
        if dow_lit is None:
            return None
        try:
            hh, mm = _parse_hhmm(hm)
        except Exception:
            return None
        sched = self._ensure()
        sched.add_job(_fire_weekly_review, trigger=CronTrigger(day_of_week=dow_lit, hour=hh, minute=mm,
                      timezone=USER_TZ),
                      id="weekly-memory-review", name="weekly memory review",
                      misfire_grace_time=86400, coalesce=True, replace_existing=True)
        logger.info(f"weekly memory review scheduled for {dow} {hh:02d}:{mm:02d}")
        return "weekly-memory-review"

    def run_briefing_now(self) -> None:
        """Fire the briefing immediately (for a 'brief me now' voice command or a test)."""
        import asyncio as _a

        try:
            _a.get_running_loop().create_task(_fire_briefing())
        except RuntimeError:
            _a.run(_fire_briefing())

    def add_reminder(
        self,
        message: str,
        in_minutes: float | None = None,
        at: str | None = None,
        daily: str | None = None,
    ) -> tuple[str, str, float | None]:
        """Schedule a reminder. Returns (job_id, human-readable when, ntfy_epoch_or_None).

        ``ntfy_epoch_or_None`` is the absolute fire time (Unix seconds) when this is a one-shot
        reminder that ntfy can deliver server-side (true PC-off 24/7 — Phase 4b); the caller
        should then hand that push to ntfy. It's None for recurring (daily) reminders or one-shots
        outside ntfy's window — those rely on the live edge / an always-on host. Raises ValueError
        on bad input.
        """
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.date import DateTrigger

        from afon.brain.tools.notify import ntfy_can_schedule

        sched = self._ensure()
        fire_dt: datetime | None = None  # absolute fire time for one-shots (for ntfy scheduling)
        if daily:
            hh, mm = _parse_hhmm(daily)
            trigger = CronTrigger(hour=hh, minute=mm, timezone=USER_TZ)
            when = f"every day at {hh:02d}:{mm:02d}"
        elif at:
            dt = datetime.fromisoformat(at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=USER_TZ)
            trigger = DateTrigger(run_date=dt)
            when = dt.strftime("%A %d %B at %H:%M")
            fire_dt = dt
        elif in_minutes is not None:
            dt = datetime.now(USER_TZ) + timedelta(minutes=float(in_minutes))
            trigger = DateTrigger(run_date=dt)
            when = f"in {_fmt_minutes(float(in_minutes))} (at {dt.strftime('%H:%M')})"
            fire_dt = dt
        else:
            raise ValueError("need one of in_minutes / at / daily")

        # Phase 4b: if ntfy can hold this one-shot server-side, let IT own the phone push (fires
        # even with the PC off). The in-process job then only SPEAKS (push_phone=False) to avoid a
        # double-push. Recurring/out-of-window jobs keep push_phone=True (live edge / VPS ticker).
        ntfy_epoch = fire_dt.timestamp() if (fire_dt and ntfy_can_schedule(fire_dt.timestamp())) else None
        push_phone = ntfy_epoch is None

        # misfire_grace_time large + coalesce: if the PC was off/asleep when a reminder was due,
        # still fire it (once) the moment the edge comes back, instead of silently dropping it.
        job = sched.add_job(_fire, trigger=trigger, args=[message, push_phone], name=message,
                            misfire_grace_time=86400, coalesce=True, replace_existing=False)
        return job.id, when, ntfy_epoch

    def set_push_phone(self, job_id: str, value: bool) -> None:
        """Flip a job's push_phone flag — used to re-enable the in-process push if ntfy refused."""
        sched = self._ensure()
        try:
            job = sched.get_job(job_id)
            if job is not None:
                sched.modify_job(job_id, args=[job.args[0], value])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"set_push_phone failed: {e}")

    def list_reminders(self) -> list[tuple[str, str, str]]:
        sched = self._ensure()
        out = []
        for job in sched.get_jobs():
            nxt = job.next_run_time.strftime("%a %d %b %H:%M") if job.next_run_time else "—"
            out.append((job.id, job.name or "", nxt))
        return out

    def cancel(self, job_id: str) -> bool:
        sched = self._ensure()
        try:
            sched.remove_job(job_id)
            return True
        except Exception:  # noqa: BLE001
            return False


def _parse_hhmm(s: str) -> tuple[int, int]:
    parts = s.strip().split(":")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def _fmt_minutes(m: float) -> str:
    if m < 60:
        return f"{int(m)} minute(s)"
    h, mm = divmod(int(m), 60)
    return f"{h}h{mm:02d}m"


SCHEDULER = Scheduler()
