"""Field coaching store (Phase 2 companion) — Afon tracks your progress in the fields you focus on.

The owner has standing focus areas (the vault CHARTERs: language-learning, training-diet-health,
coding-projects, …). This is the persistence + logic behind Afon acting as a coach for the
*skill* ones: he remembers your current LEVEL (e.g. German "B1"), logs each review with a 1-10
score + note, tracks a daily STREAK and a trend, and knows when a field is due for a check-in. The
actual quiz/review is conducted conversationally by the agent (LLMs are good at that) — this module
just holds the state and the "is it time?" logic, so it's deterministic and testable.

SQLite-backed (survives restarts), fail-quiet, local-only.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

from afon.brain.dbconn import connect
from afon.config import settings
from afon.shared.paths import store_path



def _db_path() -> Path:
    if settings.coaching_db_path:
        return Path(settings.coaching_db_path)
    return store_path("coaching")


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.user_tz)


def _today(now: datetime | None = None) -> str:
    return (now or datetime.now(_tz())).strftime("%Y-%m-%d")


def configured_fields() -> list[str]:
    return [f.strip().lower() for f in (settings.coaching_fields or "").split(",") if f.strip()]


@dataclass
class SkillProgress:
    field: str
    level: str
    streak: int
    reviews: int
    last_review_day: str | None
    recent_avg: float | None
    trend: str          # improving | steady | slipping | new


class Coaching:
    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or _db_path()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        return connect(self._path)

    def _init_db(self) -> None:
        try:
            with self._conn() as c:
                c.execute("CREATE TABLE IF NOT EXISTS skills ("
                          "field TEXT PRIMARY KEY, level TEXT, streak INTEGER, "
                          "last_review_day TEXT, updated REAL)")
                c.execute("CREATE TABLE IF NOT EXISTS reviews ("
                          "field TEXT, ts REAL, score INTEGER, note TEXT)")
        except sqlite3.Error as e:
            logger.warning(f"coaching: db init failed ({e})")

    # ---- level ------------------------------------------------------------------------
    def get_level(self, field: str) -> str | None:
        try:
            with self._conn() as c:
                r = c.execute("SELECT level FROM skills WHERE field=?", (field.lower(),)).fetchone()
                return (r["level"] if r and r["level"] else None)
        except sqlite3.Error:
            return None

    def set_level(self, field: str, level: str) -> None:
        field = field.lower()
        try:
            with self._conn() as c:
                c.execute(
                    "INSERT INTO skills(field, level, streak, last_review_day, updated) "
                    "VALUES(?,?,COALESCE((SELECT streak FROM skills WHERE field=?),0),"
                    "(SELECT last_review_day FROM skills WHERE field=?),?) "
                    "ON CONFLICT(field) DO UPDATE SET level=excluded.level, updated=excluded.updated",
                    (field, level.strip(), field, field, time.time()))
        except sqlite3.Error as e:
            logger.warning(f"coaching: set_level failed ({e})")

    # ---- reviews ----------------------------------------------------------------------
    def record_review(self, field: str, score: int, note: str = "", now: datetime | None = None) -> SkillProgress:
        field = field.lower()
        score = max(1, min(10, int(score)))
        now = now or datetime.now(_tz())
        today = _today(now)
        try:
            with self._conn() as c:
                c.execute("INSERT INTO reviews VALUES (?,?,?,?)",
                          (field, now.timestamp(), score, (note or "")[:400]))
                r = c.execute("SELECT streak, last_review_day FROM skills WHERE field=?", (field,)).fetchone()
                prev_day = r["last_review_day"] if r else None
                prev_streak = (r["streak"] if r else 0) or 0
                if prev_day == today:
                    streak = prev_streak or 1           # already practised today; don't double-count
                elif prev_day and _is_yesterday(prev_day, today):
                    streak = prev_streak + 1
                else:
                    streak = 1
                c.execute(
                    "INSERT INTO skills(field, level, streak, last_review_day, updated) "
                    "VALUES(?,(SELECT level FROM skills WHERE field=?),?,?,?) "
                    "ON CONFLICT(field) DO UPDATE SET streak=excluded.streak, "
                    "last_review_day=excluded.last_review_day, updated=excluded.updated",
                    (field, field, streak, today, now.timestamp()))
        except sqlite3.Error as e:
            logger.warning(f"coaching: record_review failed ({e})")
        return self.progress(field)

    def progress(self, field: str) -> SkillProgress:
        field = field.lower()
        level = self.get_level(field) or "not set"
        streak = 0
        last_day = None
        scores: list[int] = []
        try:
            with self._conn() as c:
                r = c.execute("SELECT streak, last_review_day FROM skills WHERE field=?", (field,)).fetchone()
                if r:
                    streak = r["streak"] or 0
                    last_day = r["last_review_day"]
                scores = [row["score"] for row in c.execute(
                    "SELECT score FROM reviews WHERE field=? ORDER BY ts DESC LIMIT 10", (field,))]
        except sqlite3.Error:
            pass
        recent = scores[:5]
        recent_avg = round(sum(recent) / len(recent), 1) if recent else None
        trend = "new"
        if len(scores) >= 2:
            last = scores[0]
            prior = scores[1:5]
            prior_avg = sum(prior) / len(prior) if prior else last
            trend = "improving" if last > prior_avg + 0.5 else "slipping" if last < prior_avg - 0.5 else "steady"
        return SkillProgress(field=field, level=level, streak=streak, reviews=len(scores),
                             last_review_day=last_day, recent_avg=recent_avg, trend=trend)

    def fields(self) -> list[str]:
        """Configured coaching fields plus any that already have history."""
        known = set(configured_fields())
        try:
            with self._conn() as c:
                known.update(r["field"] for r in c.execute("SELECT field FROM skills"))
        except sqlite3.Error:
            pass
        return sorted(known)

    def reviewed_today(self, field: str, now: datetime | None = None) -> bool:
        try:
            with self._conn() as c:
                r = c.execute("SELECT last_review_day FROM skills WHERE field=?", (field.lower(),)).fetchone()
                return bool(r and r["last_review_day"] == _today(now))
        except sqlite3.Error:
            return False

    def due_field(self, now: datetime | None = None) -> str | None:
        """A configured field not yet reviewed today (oldest-practised first), or None."""
        now = now or datetime.now(_tz())
        candidates = [f for f in configured_fields() if not self.reviewed_today(f, now)]
        if not candidates:
            return None

        def last_ts(field: str) -> float:
            try:
                with self._conn() as c:
                    r = c.execute("SELECT updated FROM skills WHERE field=?", (field,)).fetchone()
                    return r["updated"] if r and r["updated"] else 0.0
            except sqlite3.Error:
                return 0.0

        candidates.sort(key=last_ts)   # never-practised (0.0) first, else oldest
        return candidates[0]


def _is_yesterday(prev_day: str, today: str) -> bool:
    from datetime import date, timedelta
    try:
        p = date.fromisoformat(prev_day)
        t = date.fromisoformat(today)
        return (t - p) == timedelta(days=1)
    except ValueError:
        return False


def coaching_signals() -> list:
    """Proactive source (Phase 2): in the evening, offer a review of a field due today, naming the
    owner's level. urgency 0.62 → above the base threshold but below context_override, so it's HELD
    while he's busy (Phase 1) and BACKS OFF if he keeps dismissing it (kind 'coaching' feedback).
    One offer per field per day. Returns [] outside the window / when nothing's due. Fail-quiet."""
    from afon.brain.proactive import Signal

    if not settings.coaching_enabled:
        return []
    now = datetime.now(_tz())
    if not _in_window(now, settings.coaching_evening_hours):
        return []
    try:
        field = COACH.due_field(now)
        if not field:
            return []
        p = COACH.progress(field)
        said = field.title() if field.isalpha() else field   # spoken aloud: "German", not "german"
        if p.level != "not set":
            msg = (f"Evening, sir — fancy a quick {said} review? You're at {p.level}"
                   + (f", {p.streak}-day streak" if p.streak > 1 else "")
                   + ". Say 'quiz me' and I'll run a few.")
        else:
            msg = (f"Evening, sir — want to do a quick {said} check? Tell me roughly your level and "
                   "I'll pitch it right.")
        return [Signal(key=f"coaching-{field}-{_today(now)}", message=msg, urgency=0.62,
                       kind="coaching")]
    except Exception as e:  # noqa: BLE001
        logger.debug(f"coaching_signals skipped: {e}")
        return []


def _in_window(now: datetime, spec: str) -> bool:
    try:
        a, b = spec.split("-")
        ah, am = (int(x) for x in a.strip().split(":"))
        bh, bm = (int(x) for x in b.strip().split(":"))
    except Exception:  # noqa: BLE001
        return False
    minute = now.hour * 60 + now.minute
    start, end = ah * 60 + am, bh * 60 + bm
    return start <= minute < end if start <= end else (minute >= start or minute < end)


# Process-wide singleton.
COACH = Coaching()
