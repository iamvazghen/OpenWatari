"""Presence / activity layer (Phase 0 companion) — Afon knows what you're doing.

A background poller asks the laptop (via the pc_agent perception op ``activity_snapshot``) for the
foreground window + idle time every ``presence_poll_seconds`` and records it to a local SQLite DB.
From that we derive two things the rest of the companion needs:

  * **context** — "you're coding in VS Code", "watching YouTube in Chrome", "idle 12 min" — so
    proactive interjections can be gated on whether NOW is a sensible moment (Phase 1 uses this);
  * **screen-time** — per-app / per-category time today, so Afon can report and coach on it.

Privacy by design: everything is local (no data leaves the brain host), samples are pruned after
``presence_retention_days``, and there's a hard off-switch (``activity_tracking_enabled`` +
``PRESENCE.set_enabled(False)`` at runtime, e.g. "pause activity tracking"). Phone usage is NOT
tracked — there's no clean, sanctioned API for it, so we don't pretend to.

Fail-quiet throughout: a missing laptop, a bad sample, or a DB hiccup never raises into the loop.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

from afon.brain.dbconn import connect
from afon.brain import loops
from afon.config import settings
from afon.shared.paths import store_path



def _db_path() -> Path:
    if settings.presence_db_path:
        return Path(settings.presence_db_path)
    return store_path("presence")


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.user_tz)


# Coarse app -> category map for a friendlier screen-time report. Substring match on the lowercased
# process name; unknown apps fall into "other". Deliberately small — extend as needed.
_CATEGORIES: dict[str, tuple[str, ...]] = {
    "browsing": ("chrome", "firefox", "msedge", "edge", "brave", "opera", "safari", "arc"),
    "coding": ("code", "devenv", "pycharm", "idea", "sublime", "windowsterminal", "cmd",
               "powershell", "wt", "cursor", "rider", "goland", "webstorm"),
    "comms": ("telegram", "discord", "slack", "teams", "zoom", "whatsapp", "outlook", "thunderbird"),
    "media": ("vlc", "spotify", "mpc", "wmplayer", "netflix", "potplayer", "musicbee"),
    "docs": ("winword", "excel", "powerpnt", "onenote", "acrobat", "notion", "obsidian"),
    "gaming": ("steam", "epicgames", "battle.net", "riotclient", "leagueclient"),
}


def _category(app: str) -> str:
    a = (app or "").lower()
    for cat, keys in _CATEGORIES.items():
        if any(k in a for k in keys):
            return cat
    return "other"


def _fmt_dur(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h {(s % 3600) // 60}m"


@dataclass
class Snapshot:
    app: str
    title: str
    idle: float
    ts: float


class Presence:
    """Persisted activity log + a poller that samples the laptop's foreground window."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or _db_path()
        self._enabled = settings.activity_tracking_enabled
        self._latest: Snapshot | None = None
        self._task: asyncio.Task | None = None
        self._was_away = False   # arrival() state: True once the owner has gone away, so a return fires once
        self._init_db()

    # ---- persistence ------------------------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        return connect(self._path)

    def _init_db(self) -> None:
        try:
            with self._conn() as c:
                c.execute("CREATE TABLE IF NOT EXISTS activity ("
                          "ts REAL, app TEXT, title TEXT, idle REAL)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity(ts)")
        except sqlite3.Error as e:
            logger.warning(f"presence: db init failed ({e})")

    def record(self, app: str, title: str, idle: float, ts: float | None = None) -> None:
        ts = ts if ts is not None else time.time()
        self._latest = Snapshot(app=app, title=title, idle=idle, ts=ts)
        try:
            with self._conn() as c:
                c.execute("INSERT INTO activity VALUES (?,?,?,?)", (ts, app, title[:300], idle))
        except sqlite3.Error as e:
            logger.warning(f"presence: record failed ({e})")

    def prune(self) -> None:
        try:
            cutoff = time.time() - settings.presence_retention_days * 86400
            with self._conn() as c:
                c.execute("DELETE FROM activity WHERE ts < ?", (cutoff,))
        except sqlite3.Error:
            pass

    # ---- privacy off-switch -----------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        logger.info(f"presence: activity tracking {'ON' if on else 'PAUSED'}")

    # ---- reads ------------------------------------------------------------------------
    def current(self) -> Snapshot | None:
        """Most recent sample (in-memory if the poller is live, else the last DB row)."""
        if self._latest is not None:
            return self._latest
        try:
            with self._conn() as c:
                r = c.execute("SELECT * FROM activity ORDER BY ts DESC LIMIT 1").fetchone()
                if r:
                    return Snapshot(app=r["app"], title=r["title"] or "", idle=r["idle"], ts=r["ts"])
        except sqlite3.Error:
            pass
        return None

    def continuous_active_minutes(self, now: float | None = None) -> float:
        """Minutes of the CURRENT unbroken active stretch — how long he's been heads-down right now.

        Walks recent samples newest-first while each is active (idle < threshold) and contiguous (no gap
        bigger than a few poll intervals — a real break ends the run). Returns 0 when the latest sample is
        idle (he's not at the screen now). Used by the Phase 6.3 wellbeing pushback. Fail-quiet -> 0.
        """
        now = now if now is not None else time.time()
        poll = max(10, settings.presence_poll_seconds)
        idle_max = settings.presence_idle_threshold_seconds
        try:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT ts, idle FROM activity WHERE ts >= ? ORDER BY ts DESC",
                    (now - 12 * 3600,),
                ).fetchall()
        except sqlite3.Error:
            return 0.0
        run_start: float | None = None
        prev_ts: float | None = None
        for r in rows:
            ts, idle = r["ts"], (r["idle"] or 0)
            if idle >= idle_max:
                break                                   # hit an idle sample -> the active run ends here
            if prev_ts is not None and (prev_ts - ts) > 3 * poll:
                break                                   # a gap in the record -> a session boundary
            run_start = ts
            prev_ts = ts
        if run_start is None:
            return 0.0
        return max(0.0, (now - run_start) / 60.0)

    def _day_bounds(self, day_offset: int) -> tuple[float, float]:
        now = datetime.now(_tz())
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_ts = start.timestamp() + day_offset * 86400
        return start_ts, start_ts + 86400

    def screen_time(self, day_offset: int = 0) -> dict:
        """Aggregate one local day's samples into per-app / per-category active seconds.

        Each ACTIVE sample (idle < threshold) credits ``presence_poll_seconds`` to its app — an
        approximation, but a faithful one at a steady poll cadence. Returns
        ``{"total": s, "apps": {app: s}, "categories": {cat: s}, "samples": n}``."""
        lo, hi = self._day_bounds(day_offset)
        per_poll = max(1, settings.presence_poll_seconds)
        idle_max = settings.presence_idle_threshold_seconds
        apps: dict[str, float] = {}
        cats: dict[str, float] = {}
        total = 0.0
        n = 0
        try:
            with self._conn() as c:
                rows = c.execute("SELECT app, idle FROM activity WHERE ts >= ? AND ts < ?", (lo, hi))
                for r in rows:
                    n += 1
                    if (r["idle"] or 0) >= idle_max:
                        continue  # user was away — don't count it as screen time
                    app = (r["app"] or "unknown") or "unknown"
                    apps[app] = apps.get(app, 0.0) + per_poll
                    cats[_category(app)] = cats.get(_category(app), 0.0) + per_poll
                    total += per_poll
        except sqlite3.Error:
            pass
        return {"total": total, "apps": apps, "categories": cats, "samples": n}

    def report(self, day_offset: int = 0) -> str:
        """A spoken screen-time line for today (offset 0) or a past day."""
        data = self.screen_time(day_offset)
        if not data["samples"]:
            when = "today" if day_offset == 0 else f"{-day_offset} day(s) ago"
            return (f"I've no activity recorded for {when}, sir"
                    + ("" if self._enabled else " — tracking is paused") + ".")
        top = sorted(data["apps"].items(), key=lambda kv: kv[1], reverse=True)[:5]
        cats = sorted(data["categories"].items(), key=lambda kv: kv[1], reverse=True)[:4]
        when = "Today" if day_offset == 0 else f"{-day_offset} day(s) ago"
        head = f"{when} you've been active about {_fmt_dur(data['total'])}, sir."
        cat_part = " By category: " + ", ".join(f"{c} {_fmt_dur(s)}" for c, s in cats) + "."
        app_part = " Top apps: " + ", ".join(f"{a} {_fmt_dur(s)}" for a, s in top) + "."
        return head + cat_part + app_part

    # ---- context: "is now a bad moment to interrupt?" (Phase 1) ------------------------
    def _fresh(self, snap: Snapshot | None, now: float) -> bool:
        """A sample recent enough to describe what's happening RIGHT NOW."""
        return snap is not None and (now - snap.ts) < 3 * max(1, settings.presence_poll_seconds)

    def in_meeting(self, now: float | None = None) -> bool:
        """Foreground looks like a live video meeting (Zoom/Teams/Meet/Webex)."""
        now = now if now is not None else time.time()
        snap = self.current()
        if not self._fresh(snap, now):
            return False
        blob = f"{snap.app} {snap.title}".lower()
        return any(k in blob for k in ("zoom", "microsoft teams", "teams meeting", "webex",
                                       "google meet", "meet.google", "whereby", "skype"))

    def engaged(self, now: float | None = None) -> bool:
        """Deep in focused work — actively typing in an editor/doc (low idle), OR watching media."""
        now = now if now is not None else time.time()
        snap = self.current()
        if not self._fresh(snap, now):
            return False
        cat = _category(snap.app)
        engaged_idle = settings.presence_engaged_idle_seconds
        if cat in ("coding", "docs") and snap.idle < engaged_idle:
            return True
        if cat == "media":  # watching a video = low input, high idle; still "occupied"
            return True
        if cat == "gaming" and snap.idle < engaged_idle:
            return True
        return False

    def busy(self, now: float | None = None) -> bool:
        """True when a ROUTINE interjection should be held for a better moment."""
        return self.in_meeting(now) or self.engaged(now)

    # ---- arrival: the owner sat back down (Phase 3 — presence-aware proactivity) -------
    def arrival(self, now: float | None = None) -> bool:
        """True EXACTLY ONCE when the owner returns to the desk after being genuinely away.

        Presence here is idle-based (the camera lives on the laptop, the brain on the VPS — the one
        signal that crosses that split is ``activity_snapshot``'s idle time). "Away" = a fresh sample
        with idle >= ``presence_away_seconds`` (a real departure, not a 90s pause); "back" = the next
        fresh sample showing active input. Returns True on that away->active edge, then re-arms only
        after the owner leaves again — so a return greeting fires once per genuine return, not per poll.
        Stale/absent samples don't change state (a disconnected edge won't fake a departure).
        """
        now = now if now is not None else time.time()
        snap = self.current()
        if not self._fresh(snap, now):
            return False
        if snap.idle >= settings.presence_away_seconds:
            self._was_away = True
            return False
        # active now
        if self._was_away:
            self._was_away = False
            return True
        return False

    def busy_reason(self, now: float | None = None) -> str:
        if self.in_meeting(now):
            return "in a meeting"
        if self.engaged(now):
            snap = self.current()
            cat = _category(snap.app) if snap else "?"
            return {"coding": "deep in code", "docs": "writing", "media": "watching something",
                    "gaming": "gaming"}.get(cat, "focused")
        return ""

    def current_line(self) -> str:
        snap = self.current()
        if snap is None:
            return "I don't have a read on your screen right now, sir."
        age = time.time() - snap.ts
        if snap.idle >= settings.presence_idle_threshold_seconds:
            return f"You've been idle about {_fmt_dur(snap.idle)}, sir (last app: {snap.app or 'unknown'})."
        stale = " (a little stale)" if age > 3 * max(1, settings.presence_poll_seconds) else ""
        title = (snap.title or "").strip()
        detail = f" — “{title[:80]}”" if title else ""
        return f"Right now you're in {snap.app or 'an app'}{detail}{stale}, sir."

    # ---- the poller -------------------------------------------------------------------
    async def run_poller(self) -> None:
        """Sample the laptop's foreground window on an interval, forever. Cancelled on shutdown.
        No laptop connected / tracking paused -> the tick is a harmless no-op."""
        interval = max(10, settings.presence_poll_seconds)
        loops.set_period("presence-poller", interval)
        logger.info(f"presence poller on: every {interval}s (tracking "
                    f"{'ON' if self._enabled else 'PAUSED'})")
        ticks = 0
        try:
            while True:
                await asyncio.sleep(interval)
                ticks += 1
                if ticks % 200 == 0:
                    self.prune()
                if not self._enabled:
                    continue
                try:
                    # Inside the try: a poll that raises is still a tick that happened, and the
                    # registry records the error rather than reporting a loop that went quiet.
                    with loops.tick("presence-poller"):
                        await self._poll_once()
                except Exception as e:  # noqa: BLE001 — never break the loop
                    logger.debug(f"presence poll skipped: {type(e).__name__}: {e}")
        except asyncio.CancelledError:
            logger.info("presence poller stopped")
            raise

    async def _poll_once(self) -> None:
        from afon.brain.pc_link import PC_LINK
        from afon.brain.tools.system import activity_snapshot

        if not PC_LINK.active:
            return  # no laptop attached to this brain (VPS with edge offline) — nothing to sample
        raw = await activity_snapshot({})
        self.ingest(raw)

    def ingest(self, raw: str) -> bool:
        """Parse one raw ``activity_snapshot`` JSON string and record it. True if a sample landed."""
        try:
            data = json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            return False
        if not isinstance(data, dict) or not (data.get("app") or data.get("title")):
            return False
        self.record(str(data.get("app") or "unknown"),
                    str(data.get("title") or ""), float(data.get("idle") or 0.0))
        return True

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self.run_poller())
        return self._task


# Process-wide singleton (mirrors STORE / SCHEDULER / TASKS).
PRESENCE = Presence()


def _greeting(now: datetime | None = None) -> str:
    """A short, time-aware welcome-back line in Afon's voice."""
    h = (now or datetime.now(_tz())).hour
    if h < 5:
        return "Welcome back, sir — burning the midnight oil?"
    if h < 12:
        return "Welcome back, sir."
    if h < 18:
        return "Back at it, sir. Welcome back."
    return "Welcome back, sir."


def presence_signals() -> list:
    """Proactive source: greet the owner ONCE when they return to the desk after being away.

    Idle-transition based (works across the VPS-brain / laptop-camera split), gated by the privacy
    off-switch, modest urgency (so quiet hours still hold it at night), and a per-return key so the
    engine's repeat-suppression never eats a genuine second return later in the day.
    """
    from afon.brain.proactive import Signal  # local import: avoid a cycle at module load

    if not PRESENCE.enabled or not PRESENCE.arrival():
        return []
    key = f"arrival-{int(time.time() // 60)}"
    # 0.62 clears the 0.60 relevance threshold so the welcome-back actually fires (it was 0.50 = dormant),
    # but stays below the 0.85 context-override and 0.95 quiet-override so night/quiet-hours still hold it.
    return [Signal(key=key, message=_greeting(), urgency=0.62, kind="presence")]
