"""Phase 2.1 — the owner's world-model.

Afon's proactivity was reacting to hard-coded signals (a calendar event, a health probe) and
keyword-regex "patterns". AFON anticipates because he holds a *model of what the owner is trying to
do* and reasons forward from it. This is that model: a small, durable store of the owner's active
GOALS / PROJECTS / DEADLINES plus a free-form CURRENT-CONTEXT note.

It's deliberately dumb storage — the intelligence lives in the anticipation source (Phase 2.3) that
renders this into a prompt and asks the LLM "given all this, what would genuinely help right now?".
Persisted as one JSON file so it survives restarts; every method is fail-quiet and the store degrades
to empty rather than ever raising into the tick loop.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Goal:
    id: str                       # stable key (a slug, or a Notion page id) — upsert dedups on it
    text: str
    project: str = ""
    status: str = "active"        # active | done | dropped
    deadline: str | None = None   # ISO date/datetime, owner's intent — may be None
    updated: str = field(default="")

    def __post_init__(self) -> None:
        if not self.updated:
            self.updated = _now().isoformat(timespec="seconds")


def _days_until(deadline: str | None, now: datetime) -> float | None:
    if not deadline:
        return None
    try:
        dt = datetime.fromisoformat(deadline)
    except ValueError:
        # bare date like '2026-07-24'
        try:
            dt = datetime.fromisoformat(deadline + "T00:00:00")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - now).total_seconds() / 86400.0


class WorldModel:
    """Durable goals + context. One instance per brain; ``path`` is injectable for tests."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else Path.home() / ".afon" / "world_model.json"
        self._goals: dict[str, Goal] = {}
        self._context: str = ""
        self._events: list[dict] = []   # time-bounded integration events (Phase 2.4)
        self._load()

    # ---- persistence -------------------------------------------------------------------------
    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._context = raw.get("context", "") or ""
            self._events = [e for e in raw.get("events", []) if isinstance(e, dict)]
            for g in raw.get("goals", []):
                try:
                    goal = Goal(**{k: g.get(k) for k in ("id", "text", "project", "status",
                                                          "deadline", "updated")})
                    self._goals[goal.id] = goal
                except (TypeError, ValueError):
                    continue
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001 — a corrupt file starts empty, never crashes the brain
            logger.warning(f"world_model: could not load ({type(e).__name__}); starting empty")

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"context": self._context,
                       "events": self._events,
                       "goals": [asdict(g) for g in self._goals.values()]}
            self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"world_model: save failed ({type(e).__name__})")

    # ---- mutation ----------------------------------------------------------------------------
    def upsert_goal(self, id: str, text: str, *, project: str = "", deadline: str | None = None,
                    status: str = "active") -> Goal:
        """Add or update a goal by id. An update refreshes ``updated`` and any provided fields."""
        id = (id or "").strip() or (text or "").strip().lower().replace(" ", "-")[:48]
        existing = self._goals.get(id)
        if existing:
            existing.text = text or existing.text
            existing.project = project or existing.project
            if deadline is not None:
                existing.deadline = deadline
            existing.status = status or existing.status
            existing.updated = _now().isoformat(timespec="seconds")
            goal = existing
        else:
            goal = Goal(id=id, text=text, project=project, deadline=deadline, status=status)
            self._goals[id] = goal
        self._save()
        return goal

    def complete_goal(self, id: str) -> bool:
        return self._set_status(id, "done")

    def drop_goal(self, id: str) -> bool:
        return self._set_status(id, "dropped")

    def _set_status(self, id: str, status: str) -> bool:
        g = self._goals.get(id)
        if not g:
            return False
        g.status = status
        g.updated = _now().isoformat(timespec="seconds")
        self._save()
        return True

    def refresh_from_tasks(self, tasks: list[dict], now: datetime | None = None) -> tuple[int, int]:
        """Populate goals from the owner's task system (Phase 2.2) so the world-model self-fills
        instead of needing manual upserts. Each task dict: ``{id, title, due?, project?, done?}``.
        Open tasks become/refresh active goals; done tasks are completed. Returns (upserted, completed).
        The caller supplies the tasks (a Notion adapter in prod, a fake in tests), so the mapping is
        pure and verifiable and a Notion outage can't corrupt the model.
        """
        now = now or _now()
        upserted = completed = 0
        for t in tasks:
            tid = str(t.get("id") or t.get("title") or "").strip()
            if not tid:
                continue
            if t.get("done"):
                if self.complete_goal(tid):
                    completed += 1
                continue
            self.upsert_goal(tid, (t.get("title") or tid), project=(t.get("project") or ""),
                             deadline=t.get("due"))
            upserted += 1
        return upserted, completed

    def set_context(self, text: str) -> None:
        self._context = (text or "").strip()
        self._save()

    def note_event(self, text: str, *, ttl_hours: float = 24.0, now: datetime | None = None) -> None:
        """Record a time-bounded event from an integration (Phase 2.4) so the anticipation reasoner
        sees it — "Stripe payout of €420 cleared", "CI failed on party-map main", "reply from the
        landlord arrived". This is how integrations FEED the proactive loop without each becoming a
        noisy tick source (the anti-spam design): they drop a fact into the world, the reasoner
        decides if it's worth surfacing. Events auto-expire after ``ttl_hours``.
        """
        text = (text or "").strip()
        if not text:
            return
        now = now or _now()
        self._events = self._live_events(now)  # prune expired first
        self._events.append({
            "text": text,
            "ts": now.isoformat(timespec="seconds"),
            "expires": (now + timedelta(hours=ttl_hours)).isoformat(timespec="seconds"),
        })
        self._save()

    def _live_events(self, now: datetime) -> list[dict]:
        out = []
        for e in self._events:
            try:
                exp = datetime.fromisoformat(e["expires"])
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                continue
            if exp > now:
                out.append(e)
        return out

    def recent_events(self, *, within_hours: float = 6.0, limit: int = 3,
                      now: datetime | None = None) -> list[str]:
        """Just the FRESH integration-event texts (newest last), for folding into the reactive
        per-turn context. Connectivity fix (G5): webhooks/integrations drop facts here, and now the
        agent — not only the proactive loop — can mention them when the owner asks 'anything new?'.
        Freshness-gated so a live-but-old event doesn't spam every turn for its whole TTL."""
        now = now or _now()
        cutoff = now - timedelta(hours=within_hours)
        out: list[str] = []
        for e in self._live_events(now):
            try:
                ts = datetime.fromisoformat(e["ts"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                continue
            if ts >= cutoff:
                out.append(e["text"])
        return out[-limit:]

    def prune(self, keep_done_days: int = 14, now: datetime | None = None) -> int:
        """Drop done/dropped goals whose last update is older than ``keep_done_days``. Returns count."""
        now = now or _now()
        stale = []
        for gid, g in self._goals.items():
            if g.status == "active":
                continue
            age = _days_until(g.updated, now)
            if age is not None and age < -float(keep_done_days):
                stale.append(gid)
        for gid in stale:
            del self._goals[gid]
        if stale:
            self._save()
        return len(stale)

    # ---- read --------------------------------------------------------------------------------
    @property
    def context(self) -> str:
        return self._context

    def active_goals(self, now: datetime | None = None) -> list[Goal]:
        """Active goals, soonest deadline first; deadline-less goals last (by most-recent update)."""
        now = now or _now()

        def _key(g: Goal):
            d = _days_until(g.deadline, now)
            return (0, d) if d is not None else (1, -_stamp(g.updated))

        return sorted((g for g in self._goals.values() if g.status == "active"), key=_key)

    def render(self, *, now: datetime | None = None, max_goals: int = 12, owner: str = "the owner") -> str:
        """Compact text for the anticipation prompt. Empty string when there's nothing to model."""
        now = now or _now()
        goals = self.active_goals(now)[:max_goals]
        events = self._live_events(now)
        if not goals and not self._context and not events:
            return ""
        lines = [f"# {owner}'s current world — goals, projects, deadlines"]
        for g in goals:
            proj = f"[{g.project}] " if g.project else ""
            d = _days_until(g.deadline, now)
            when = ""
            if d is not None:
                if d < 0:
                    when = f" (OVERDUE by {abs(int(d))}d)"
                elif d < 1:
                    when = " (due today)"
                else:
                    when = f" (due in {int(d)}d)"
            lines.append(f"- {proj}{g.text}{when}")
        if events:
            lines.append("\nRecent events:")
            lines += [f"- {e['text']}" for e in events[-6:]]
        if self._context:
            lines.append(f"\nCurrent context: {self._context}")
        return "\n".join(lines)


def _stamp(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return 0.0


# Process-wide singleton (mirrors STORE / SCHEDULER). Tests build their own with a temp path.
WORLD = WorldModel()
