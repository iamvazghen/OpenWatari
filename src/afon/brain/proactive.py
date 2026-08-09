"""Phase 10 — the proactive engine: Afon decides when to speak *unprompted*.

The owner's ask, verbatim: proactivity means Afon can remind, pause, ask for context, re-ask /
confirm, interrupt, and *start speaking on his own* when he judges it helpful. This module is the
"start speaking on his own" brain: a background **tick** that, on an interval, gathers signals,
asks "is anything worth saying right now, and how urgent?", and acts — but inside a strict
**interruption budget** and **quiet hours**, so he's a companion, not a nag.

Design goals:
  * **Decoupled + testable.** The decision core (budget, quiet hours, relevance threshold,
    repeat-suppression, day rollover) is pure and driven by an injectable clock + signal sources,
    so the whole policy is verified offline with no mic, no network, no real time passing.
  * **Fail-quiet.** A broken signal source or emit channel never throws into the tick loop; worst
    case Afon simply stays silent.
  * **Two channels.** If a device is listening, an interjection is *spoken* (interrupt). If not, it
    falls back to an ntfy push so it still reaches his phone. Routine-grade signals stay silent in
    quiet hours; only a near-emergency (>= quiet_override) gets through then, and only as a push.

The clarify/confirm verbs live alongside as small policy helpers (`needs_clarification`,
`confirm_required`) the agent loop and persona use before acting on ambiguous or consequential
requests.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from loguru import logger

from afon.config import settings

USER_TZ = ZoneInfo(settings.user_tz)


@dataclass
class Signal:
    """A candidate thing-to-say the tick may surface."""

    key: str                 # stable id for repeat-suppression (e.g. 'standup-0915')
    message: str             # what Afon would say, in his voice
    urgency: float           # 0..1 — clears the relevance threshold to be voiced
    kind: str = "note"       # reminder | routine | thread | calendar | health | conflict | …
    # Optional side-effect run WHEN this signal is actually interjected (Phase 4). A conflict
    # intervention uses it to pause the owner's media before speaking, so "I've paused it" is true.
    # Sync or async; a failure never blocks the message. Excluded from equality (callables differ).
    action: Callable[[], "None | Awaitable[None]"] | None = field(default=None, compare=False)


@dataclass
class Interjection:
    """The outcome of one tick — what was said and how (for logging/tests)."""

    signal: Signal
    channel: str             # 'voice' | 'push' | 'suppressed'


# A signal source is a 0-arg callable returning a list of Signals (sync or async).
SignalSource = Callable[[], "list[Signal] | Awaitable[list[Signal]]"]
# An emitter takes (message, urgency, listening) and returns the channel actually used.
Emitter = Callable[[str, float, bool], Awaitable[str]]


def _parse_quiet(spec: str) -> tuple[int, int] | None:
    """'23:00-07:00' -> (start_minute, end_minute) of day. None if unparseable/blank."""
    try:
        a, b = spec.split("-")
        ah, am = (int(x) for x in a.strip().split(":"))
        bh, bm = (int(x) for x in b.strip().split(":"))
        return ah * 60 + am, bh * 60 + bm
    except Exception:  # noqa: BLE001
        return None


def in_quiet_hours(now: datetime, spec: str | None = None) -> bool:
    spec = spec if spec is not None else settings.proactive_quiet_hours
    rng = _parse_quiet(spec or "")
    if rng is None:
        return False
    start, end = rng
    minute = now.hour * 60 + now.minute
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end   # window wraps past midnight


# ---- clarify / confirm policy (the "ask for context" + "re-ask/confirm" verbs) -----------

# Tools whose effects are outward-facing, costly, or hard to undo — Afon confirms before these.
CONFIRM_TIER = {
    "send_telegram", "send_email", "send_push",
    # Real-world outward reach (Twilio) — a wrong number or wrong words is unrecallable.
    "place_call",
    "file_op", "process_op", "run_powershell", "browser",
    "run_protocol", "ha_call",
    "create_event",
    # Phase 13 — self-improvement writes are reversible via git, but still consequential.
    "write_source", "git_commit", "git_push", "git_revert",
    # Filing a GitHub issue is outward-facing (posts to a public/shared repo).
    "create_github_issue",
    # Notion writes modify the owner's shared docs.
    "notion_append", "notion_comment", "notion_create_page",
    # Deleting a task is destructive (archives the row) — confirm. Creating/updating/completing a
    # task is frictionless by design (capture-by-voice), so those are intentionally NOT gated.
    "notion_delete_task",
    # ...with one exception: "mark everything done" sweeps the whole open list in one call, which is
    # a bigger loss than the single archive above and has no undo. Gated DYNAMICALLY on the same
    # bulk test the tool itself uses, so completing one task by name stays frictionless.
    "notion_complete_task",
    # Composio app actions: gated only when the slug is a WRITE (see _composio_write below).
    "composio_run_tool",
    # Things that destroy owner state with no undo. A macro, a task row and a memory note are gone
    # the moment they're dropped, and an objective that stops being driven stops silently — the
    # owner only finds out by noticing nothing happened. write_vault lands in the durable knowledge
    # base, where a wrong note is worse than no note because it gets trusted later.
    "delete_macro", "delete_task", "forget", "write_vault",
    "drop_objective", "complete_objective",
    # Updates are gated DYNAMICALLY (see _update_is_destructive): adding a note or nudging a
    # deadline stays frictionless, but overwriting a title or blanking a field loses what was there.
    "update_task", "notion_update_task",
    # A macro/skill is a stored sequence that runs tools on the owner's behalf. Gated DYNAMICALLY:
    # only when its own steps contain a confirm-gated tool (see _sequence_needs_confirm). Without
    # this, a saved macro was an unguarded path to every destructive tool in the system, since the
    # steps executed straight out of the registry and run_macro itself asked nothing.
    "run_macro", "invoke_skill",
}


def _sequence_needs_confirm(tool_name: str, args: dict | None) -> bool:
    """True when a macro/skill's own steps include a confirm-gated tool.

    Keeps the frictionless case frictionless — "run my morning macro" that only reads the weather
    and the calendar should not interrogate him — while a macro that sends mail or pushes code reads
    itself back first. Unknown/unreadable sequences gate, because failing closed is the safe side."""
    name = (args or {}).get("name") or ""
    if not name:
        return True
    try:
        if tool_name == "run_macro":
            from afon.brain.tools.macros import _load

            steps = (_load().get(str(name).strip().lower()) or {}).get("steps") or []
        else:
            from afon.brain.tools.skills import skill_steps

            steps = skill_steps(str(name)) or []
    except Exception:  # noqa: BLE001 — can't read it -> assume the worst
        return True
    return any(confirm_required(str(s.get("tool") or ""), s.get("args") or {})
               for s in steps if isinstance(s, dict))

# Composio tool slugs encode the verb (GITHUB_CREATE_AN_ISSUE, SLACKBOT_CHAT_POST_MESSAGE). Reads run
# freely; anything that writes/sends/changes the owner's external apps is confirm-gated. Unknown = gate.
_COMPOSIO_READ = ("GET", "LIST", "FETCH", "SEARCH", "RETRIEVE", "FIND", "READ", "VIEW", "COUNT")
_COMPOSIO_WRITE = ("CREATE", "SEND", "POST", "UPDATE", "DELETE", "ADD", "REMOVE", "CHARGE", "REFUND",
                   "MERGE", "CLOSE", "SET", "EDIT", "UPLOAD", "INVITE", "ARCHIVE", "CANCEL", "ASSIGN",
                   "MOVE", "RENAME", "REPLY", "COMMENT", "WRITE", "INSERT", "APPEND", "PUT", "PATCH")


def _composio_write(slug: str) -> bool:
    """True (confirm) if a Composio tool slug writes/changes an app; False for clear reads."""
    s = (slug or "").upper()
    if any(w in s for w in _COMPOSIO_WRITE):
        return True
    if any(r in s for r in _COMPOSIO_READ):
        return False
    return True  # unknown verb -> gate (safe default)

_PRONOUN_ONLY = {"it", "that", "this", "them", "those", "these", "him", "her", "they"}


#: Fields whose old value is simply gone once an update writes over them. Progress, priority and
#: status aren't here: they're small, visible and trivially re-stated, which is the frictionless
#: voice path the task tools were built for.
_OVERWRITES = ("title", "description")
_CLEARABLE = ("title", "description", "deadline", "reminder", "note", "priority", "status")


def _update_is_destructive(args: dict | None) -> bool:
    """True when an update_* call would lose something rather than add to it.

    Two shapes qualify: replacing prose that was already written (a title or a description), and
    passing a blank value, which both task backends treat as 'clear this field'. Everything else —
    moving a deadline, bumping priority, appending a note — flows without asking.
    """
    a = args or {}
    if any(a.get(k) for k in _OVERWRITES):
        return True
    return any(k in a and not str(a[k] or "").strip() for k in _CLEARABLE)


def confirm_required(tool_name: str, args: dict | None = None) -> bool:
    """True if Afon should re-ask for confirmation before running this tool.

    file_op deletes and ha_call locks are the dangerous edges; sends spend the owner's voice to
    third parties. Reads (recall, web_search, get_time, list_*) are never gated.
    """
    name = (tool_name or "").strip()
    if name not in CONFIRM_TIER:
        return False
    if name == "file_op":
        action = (args or {}).get("action", "")
        return str(action).startswith("delete")     # create/list need no confirm
    if name == "process_op":
        return (args or {}).get("action") in {"kill", "start"}
    if name == "composio_run_tool":
        return _composio_write(str((args or {}).get("tool_slug", "")))
    if name in {"run_macro", "invoke_skill"}:
        return _sequence_needs_confirm(name, args)
    if name in {"update_task", "notion_update_task"}:
        return _update_is_destructive(args)
    if name == "notion_complete_task":
        # Reuse the tool's own bulk test rather than re-deriving it here — two copies of "did he mean
        # all of them?" would drift, and the copy that drifts is the one guarding the sweep.
        try:
            from afon.brain.tools.notion import _is_bulk
        except Exception:  # noqa: BLE001 — tool module unavailable: gate rather than wave it through
            return True
        return _is_bulk(args or {})
    if name == "ha_call":
        # Only security-sensitive actuation confirms (locks/alarms/covers/garage). Turning on a
        # light or a scene should flow without friction — that's the whole point of a voice home.
        # Mirrors smarthome.SENSITIVE_DOMAINS (kept local to avoid importing a tool module here).
        return (args or {}).get("domain", "") in {"lock", "alarm_control_panel", "cover", "garage_door"}
    return True


def needs_clarification(text: str) -> bool:
    """Heuristic: is the request too thin to act on without asking a question first?

    Empty, a bare pronoun ('do it'), or a lone vague verb gets a clarifying question rather than a
    guess. Deliberately conservative — false negatives (acting) are cheaper than nagging.
    """
    t = (text or "").strip().lower().rstrip("?.!")
    if not t:
        return True
    words = [w for w in t.split() if w]
    if len(words) == 1 and words[0] in _PRONOUN_ONLY:
        return True
    # "do it" / "handle that" / "sort this" — verb + bare pronoun, no object.
    if len(words) == 2 and words[1] in _PRONOUN_ONLY:
        return True
    return False


class ProactiveEngine:
    """The tick + interruption budget + quiet-hours gate. Pure decision core, injectable I/O."""

    def __init__(
        self,
        emit: Emitter | None = None,
        sources: list[SignalSource] | None = None,
        is_listening: Callable[[], bool] | None = None,
        clock: Callable[[], datetime] | None = None,
        is_busy: Callable[[], bool] | None = None,
        *,
        quiet_hours: str | None = None,
        daily_budget: int | None = None,
        threshold: float | None = None,
        repeat_suppress_minutes: int | None = None,
        quiet_override: float | None = None,
        context_override: float | None = None,
        state_path: str | None = None,
    ) -> None:
        self._emit = emit or self._default_emit
        self._sources = sources or []
        self._is_listening = is_listening or (lambda: False)
        # Phase 1: "is the owner busy right now?" (deep work / meeting / media). Default: never busy,
        # so tests and non-presence callers behave as before; the server wires PRESENCE.busy.
        self._is_busy = is_busy or (lambda: False)
        self._clock = clock or (lambda: datetime.now(USER_TZ))
        self._quiet = quiet_hours if quiet_hours is not None else settings.proactive_quiet_hours
        self._budget = daily_budget if daily_budget is not None else settings.proactive_daily_budget
        self._threshold = threshold if threshold is not None else settings.proactive_relevance_threshold
        self._suppress_min = (
            repeat_suppress_minutes if repeat_suppress_minutes is not None
            else settings.proactive_repeat_suppress_minutes
        )
        self._override = (
            quiet_override if quiet_override is not None else settings.proactive_quiet_override_urgency
        )
        self._context_override = (
            context_override if context_override is not None
            else settings.proactive_context_override_urgency
        )
        self._spoken_at: dict[str, datetime] = {}   # signal.key -> last emitted time
        self._day: str | None = None
        self._used_today = 0
        self._task: asyncio.Task | None = None
        # Phase 1 feedback learning: per-signal-KIND penalty in [0, 0.45] that raises that kind's
        # effective threshold when the owner keeps dismissing/ignoring it (and eases when he acts on
        # it). Half-life decay so a bad week doesn't mute a kind forever. `_pending` tracks the last
        # interjection awaiting a reaction (the next turn, or an 'ignore' at the next emit).
        self._penalty: dict[str, float] = {}
        self._fb_updated: dict[str, datetime] = {}
        self._fb_stats: dict[str, dict[str, int]] = {}
        self._pending: dict | None = None
        # Counters for signals that WERE generated and then held back. Without them, a kind that
        # is correctly restrained and a kind whose signal source has silently broken both read as
        # zero everywhere — which is how five companion capabilities looked "dormant" for weeks.
        self._stats_dirty = False
        # Persist suppression + today's budget so a 24/7 brain RESTART doesn't reset them and
        # re-fire the same nudges (the class of bug behind the duplicate morning messages). Opt-in:
        # tests construct the engine without a path and stay purely in-memory.
        self._state_path = state_path
        self._load_state()

    # ---- persistence (opt-in) ---------------------------------------------------------
    def _load_state(self) -> None:
        if not self._state_path:
            return
        try:
            import json

            data = json.loads(Path(self._state_path).read_text("utf-8"))
        except Exception:  # noqa: BLE001 — missing/corrupt = fresh start
            return
        self._day = data.get("day")
        self._used_today = int(data.get("used_today") or 0)
        for key, iso in (data.get("spoken_at") or {}).items():
            try:
                self._spoken_at[key] = datetime.fromisoformat(iso)
            except (ValueError, TypeError):
                continue
        fb = data.get("feedback") or {}
        for kind, rec in fb.items():
            try:
                self._penalty[kind] = float(rec.get("penalty") or 0.0)
                if rec.get("updated"):
                    self._fb_updated[kind] = datetime.fromisoformat(rec["updated"])
                self._fb_stats[kind] = {k: int(v) for k, v in (rec.get("stats") or {}).items()}
            except (ValueError, TypeError):
                continue

    def _flush_stats(self) -> None:
        """Persist held_* counters, but only on the ticks that actually changed one. The engine
        ticks continuously and most ticks hold nothing; writing unconditionally would turn a
        diagnostic counter into a per-tick disk write for no new information."""
        if self._stats_dirty:
            self._save_state()

    def _save_state(self) -> None:
        self._stats_dirty = False
        if not self._state_path:
            return
        try:
            import json

            # Keep the file small: only retain keys still inside the suppression window.
            now = self._clock()
            live = {k: t.isoformat() for k, t in self._spoken_at.items()
                    if (now - t) < timedelta(minutes=self._suppress_min)}
            feedback = {
                kind: {"penalty": round(self._penalty.get(kind, 0.0), 4),
                       "updated": self._fb_updated[kind].isoformat() if kind in self._fb_updated else None,
                       "stats": self._fb_stats.get(kind, {})}
                for kind in set(self._penalty) | set(self._fb_stats)
            }
            Path(self._state_path).write_text(
                json.dumps({"day": self._day, "used_today": self._used_today,
                            "spoken_at": live, "feedback": feedback}),
                encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"proactive state save failed: {e}")

    # ---- budget / day rollover --------------------------------------------------------
    def _roll_day(self, now: datetime) -> None:
        today = now.strftime("%Y-%m-%d")
        if today != self._day:
            self._day = today
            self._used_today = 0
            self._save_state()

    @property
    def budget_remaining(self) -> int:
        return max(0, self._budget - self._used_today)

    # ---- feedback learning (Phase 1) --------------------------------------------------
    # Penalty deltas per reaction. Dismissals bite hard (protect trust); ignores nudge; acting on a
    # nudge eases the kind back toward its base threshold.
    _FB_DELTA = {"act": -0.09, "dismiss": +0.15, "ignore": +0.05}
    _PENALTY_CAP = 0.45
    _FB_HALF_LIFE_DAYS = 7.0

    def _decayed_penalty(self, kind: str, now: datetime) -> float:
        """The kind's penalty after half-life decay since it was last updated (never mutates)."""
        base = self._penalty.get(kind, 0.0)
        if base <= 0:
            return 0.0
        last = self._fb_updated.get(kind)
        if last is None:
            return base
        days = max(0.0, (now - last).total_seconds() / 86400.0)
        return base * (0.5 ** (days / self._FB_HALF_LIFE_DAYS))

    def effective_threshold(self, kind: str, now: datetime) -> float:
        """Base relevance threshold raised by what we've learned about this KIND (capped < 1)."""
        return min(0.99, self._threshold + self._decayed_penalty(kind, now))

    def _register_outcome(self, kind: str, outcome: str, now: datetime | None = None) -> None:
        """Fold a reaction ('act'|'dismiss'|'ignore') into the kind's penalty + stats."""
        now = now or self._clock()
        delta = self._FB_DELTA.get(outcome, 0.0)
        # decay first so the update compounds from the current effective value, then apply.
        cur = self._decayed_penalty(kind, now)
        self._penalty[kind] = max(0.0, min(self._PENALTY_CAP, cur + delta))
        self._fb_updated[kind] = now
        st = self._fb_stats.setdefault(kind, {})
        st[outcome] = st.get(outcome, 0) + 1
        logger.info(f"proactive feedback [{kind}] {outcome} -> penalty {self._penalty[kind]:.2f}")
        self._save_state()

    def record_feedback(self, kind: str, outcome: str, now: datetime | None = None) -> None:
        """Public: record the owner's reaction to the last interjection of ``kind``."""
        if outcome not in self._FB_DELTA:
            return
        if self._pending and self._pending.get("kind") == kind:
            self._pending = None  # resolved
        self._register_outcome(kind, outcome, now)

    def pending_feedback(self, now: datetime | None = None) -> str | None:
        """The kind of a recent interjection still awaiting a reaction (within ~6 min), else None.
        The server uses this to attribute the owner's next turn as act/dismiss."""
        if not self._pending:
            return None
        now = now or self._clock()
        if (now - self._pending["at"]) > timedelta(minutes=6):
            return None
        return self._pending["kind"]

    def _note_emitted(self, signal: Signal, now: datetime) -> None:
        """Record that we just interjected: close out a prior unaddressed one as an 'ignore', count
        the 'shown', and arm the pending-reaction slot for this one."""
        if self._pending is not None:
            self._register_outcome(self._pending["kind"], "ignore", now)  # never got a reaction
        st = self._fb_stats.setdefault(signal.kind, {})
        st["shown"] = st.get("shown", 0) + 1
        self._pending = {"kind": signal.kind, "key": signal.key, "at": now}

    # ---- suppression accounting -------------------------------------------------------
    # Reasons a signal can be generated and still not reach the owner. Recorded per KIND next to
    # `shown` so the two indistinguishable zeroes — "restrained" and "never generated" — separate.
    # `_ENGINE_KIND` holds the gates that fire BEFORE any signal exists (budget, focus modes), so
    # there is no kind to attribute them to; keeping one storage shape beats a second field.
    _ENGINE_KIND = "_engine"

    def _hold(self, kind: str, reason: str) -> None:
        st = self._fb_stats.setdefault(kind, {})
        key = f"held_{reason}"
        st[key] = st.get(key, 0) + 1
        self._stats_dirty = True

    def held_counts(self, kind: str) -> dict[str, int]:
        """The held_* counters for one kind (diagnostics; empty dict if nothing was ever held)."""
        return {k: v for k, v in self._fb_stats.get(kind, {}).items() if k.startswith("held_")}

    # ---- selection --------------------------------------------------------------------
    def _recently_said(self, key: str, now: datetime) -> bool:
        last = self._spoken_at.get(key)
        return last is not None and (now - last) < timedelta(minutes=self._suppress_min)

    def select(self, signals: list[Signal], now: datetime) -> Signal | None:
        """Most urgent signal that clears its KIND's (learned) threshold and isn't repeat-suppressed."""
        eligible = []
        for s in signals:
            if s.urgency < self.effective_threshold(s.kind, now):
                self._hold(s.kind, "threshold")
            elif self._recently_said(s.key, now):
                self._hold(s.kind, "repeat")
            else:
                eligible.append(s)
        if not eligible:
            return None
        best = max(eligible, key=lambda s: s.urgency)
        for s in eligible:
            if s is not best:
                self._hold(s.kind, "outranked")
        return best

    async def _gather(self) -> list[Signal]:
        out: list[Signal] = []
        from afon.shared import errors as _err

        for src in self._sources:
            # Name the source, not just "a source": these run every 5 minutes and are the engine's
            # only eyes, so a single one failing silently (which is what the old catch-all did —
            # it never said WHICH) means a whole capability quietly stops being proactive.
            label = getattr(src, "__name__", None) or type(src).__name__
            started = time.monotonic()
            try:
                res = src()
                if inspect.isawaitable(res):
                    res = await res
                for s in res or []:
                    if isinstance(s, Signal) and s.message.strip():
                        out.append(s)
                _err.record_op("signal", label, ok=True,
                               duration_ms=(time.monotonic() - started) * 1000, slow_ms=15_000)
            except Exception as e:  # noqa: BLE001 — a bad source must never break the tick
                logger.warning(f"proactive source '{label}' failed: {type(e).__name__}: {e}")
                _err.record_op("signal", label, ok=False, detail=f"{type(e).__name__}: {e}",
                               duration_ms=(time.monotonic() - started) * 1000)
        return out

    # ---- the tick ---------------------------------------------------------------------
    async def maybe_interject(self, now: datetime | None = None) -> Interjection | None:
        """One evaluation: gather → select → (quiet-hours/budget gate) → emit. Returns what it did."""
        now = now or self._clock()
        self._roll_day(now)
        if self.budget_remaining <= 0:
            self._hold(self._ENGINE_KIND, "budget")
            self._flush_stats()
            return None
        # Behavioural modes (focus/lockdown) mute unprompted speech regardless of signals.
        try:
            from afon.brain.modes import MODES

            if MODES.proactivity_suppressed(now):
                self._hold(self._ENGINE_KIND, "mode")
                self._flush_stats()
                return None
        except Exception:  # noqa: BLE001
            pass
        signal = self.select(await self._gather(), now)
        if signal is None:
            self._flush_stats()
            return None

        quiet = in_quiet_hours(now, self._quiet)
        if quiet and signal.urgency < self._override:
            self._hold(signal.kind, "quiet")
            self._flush_stats()
            return None  # routine-grade: hold it until quiet hours end

        # Phase 1 context gate: if the owner is busy (deep work / meeting / watching something), HOLD
        # a routine interjection for a better moment — don't consume budget, don't record it, just
        # skip this tick. Only a signal important enough (>= context_override) interrupts him anyway.
        if signal.urgency < self._context_override:
            try:
                busy = bool(self._is_busy())
            except Exception:  # noqa: BLE001
                busy = False
            if busy:
                logger.debug(f"proactive: holding [{signal.kind}] — owner is busy")
                self._hold(signal.kind, "busy")
                self._flush_stats()
                return None

        listening = False
        try:
            listening = bool(self._is_listening())
        except Exception:  # noqa: BLE001
            listening = False
        # In quiet hours even an override goes by silent push, never spoken.
        speak = listening and not quiet
        # Phase 4: a conflict intervention carries a side-effect (pause the media). Run it now — after
        # every gate has passed and we're committed to interjecting — so the spoken line is truthful.
        if signal.action is not None:
            try:
                res = signal.action()
                if inspect.isawaitable(res):
                    await res
            except Exception as e:  # noqa: BLE001 — the side-effect must never block the message
                logger.warning(f"proactive action failed: {type(e).__name__}: {e}")
        try:
            channel = await self._emit(signal.message, signal.urgency, speak)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"proactive emit failed: {type(e).__name__}: {e}")
            self._hold(signal.kind, "emit_error")
            self._flush_stats()
            return None
        if channel == "suppressed":
            self._hold(signal.kind, "emit")
            self._flush_stats()
            return Interjection(signal, "suppressed")
        self._spoken_at[signal.key] = now
        self._used_today += 1
        self._note_emitted(signal, now)  # arm reaction tracking (feedback learning)
        self._save_state()  # persist so a restart keeps the budget + suppression + learning
        logger.info(f"proactive [{signal.kind}] via {channel}: {signal.message[:60]!r}")
        return Interjection(signal, channel)

    async def _default_emit(self, message: str, urgency: float, listening: bool) -> str:
        """No-voice fallback emitter: push to the phone via ntfy (used when no server is wired)."""
        from afon.brain.tools.notify import push

        ok = await push(message, title="Afon")
        return "push" if ok else "suppressed"

    # ---- background loop --------------------------------------------------------------
    async def run(self) -> None:
        """Tick forever on the configured interval. Cancelled on shutdown."""
        interval = max(15, settings.proactive_tick_seconds)
        logger.info(
            f"proactive engine on: every {interval}s, budget {self._budget}/day, "
            f"quiet {self._quiet}, threshold {self._threshold}"
        )
        try:
            while True:
                await asyncio.sleep(interval)
                try:
                    await self.maybe_interject()
                except Exception:  # noqa: BLE001
                    logger.exception("proactive tick error (continuing)")
        except asyncio.CancelledError:
            logger.info("proactive engine stopped")
            raise

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self.run())
        return self._task


# Reaction classifier for feedback learning. Deliberately HIGH-PRECISION: only clear yes/no signals
# move the needle, so we never wrongly suppress a useful nudge from an ambiguous reply. Everything
# else is 'neutral' (no learning). Used by the server to grade the owner's turn after an interjection.
_NEG_CUES = ("stop", "not now", "leave me alone", "shut up", "be quiet", "go away", "later",
             "no thanks", "not interested", "don't", "dont", "mute", "enough", "quit it",
             "stop reminding", "stop telling me", "i know", "already know")
_POS_CUES = ("yes", "yeah", "yep", "sure", "do it", "go ahead", "please do", "sounds good",
             "queue it", "line it up", "good idea", "let's do", "lets do", "okay do", "ok do",
             "thanks", "thank you", "will do", "on it", "good call", "makes sense")


def classify_reaction(text: str) -> str:
    """'positive' | 'negative' | 'neutral' — how the owner reacted to the last interjection."""
    t = (text or "").strip().lower()
    if not t:
        return "neutral"
    if any(c in t for c in _NEG_CUES):
        return "negative"
    # Short, affirmative replies right after a nudge read as acceptance ("yes", "sure, do it").
    if any(c in t for c in _POS_CUES) and len(t.split()) <= 8:
        return "positive"
    return "neutral"


def default_signal_sources() -> list[SignalSource]:
    """The signal sources the live brain ticks over. Extended as capabilities land:

    * self-health (brain/vault/ticker reachability) — Phase X cross-cutting,
    * calendar (upcoming events) — Phase 11,
    * open threads from the journal/learned memory.

    Kept deliberately small and additive so each source can fail independently.
    """
    sources: list[SignalSource] = []
    try:
        from afon.brain.health import health_signals

        sources.append(health_signals)
    except Exception:  # noqa: BLE001 — health module optional until Phase X
        pass
    try:
        from afon.brain.tools.calendar import calendar_signals

        sources.append(calendar_signals)  # imminent-event heads-up (the proactive 'backbone')
    except Exception:  # noqa: BLE001 — fail-quiet if calendar/login isn't available
        pass
    # NOTE: overdue/due-today tasks and important-email nudges are deliberately NOT tick sources.
    # They used to re-fire every repeat-suppress window (and reset on restart), which spammed the
    # owner with the same two lines 7-8x a day. They're now a single once-daily digest delivered by
    # the 06:00 briefing + the first live-edge turn of the day (see brain/daily_digest.py).
    try:
        from afon.brain.mynews import news_signals   # brain/, not tools/ — it is not a tool

        sources.append(news_signals)      # morning news-of-interest brief (MyNews)
    except Exception:  # noqa: BLE001
        pass
    # T4a — weekly digest signal (fires Sun 20:00 only)
    try:
        from afon.brain.proactive_signals import weekly_digest
        sources.append(weekly_digest)
    except Exception:  # noqa: BLE001
        pass
    # T4b — anticipatory prep (next 30 min calendar)
    try:
        from afon.brain.proactive_signals import anticipatory_prep
        sources.append(anticipatory_prep)
    except Exception:  # noqa: BLE001
        pass
    # T4c — pattern-triggered suggestion (L1 patterns matching now)
    try:
        from afon.brain.proactive_signals import pattern_suggestion
        sources.append(pattern_suggestion)
    except Exception:  # noqa: BLE001
        pass
    # Phase 2.3 — reasoned anticipation: reason over the world-model (goals/deadlines) + recent
    # activity with the LLM ("what would genuinely help right now?"), the intelligent successor to the
    # keyword pattern-matcher above. Self-throttled (reasons at most every 30 min) + fail-quiet.
    try:
        from afon.brain.anticipation import make_anticipation_source
        sources.append(make_anticipation_source())
    except Exception:  # noqa: BLE001
        pass
    # Phase 3 — presence-aware: greet the owner once when they return to the desk (idle transition,
    # gated by the activity-tracking privacy switch). The "Afon notices you're back" touch.
    try:
        from afon.brain.presence import presence_signals
        sources.append(presence_signals)
    except Exception:  # noqa: BLE001
        pass
    # Phase 2 — evening field-coaching offer (e.g. a German check at your level)
    try:
        from afon.brain.coaching import coaching_signals
        sources.append(coaching_signals)
    except Exception:  # noqa: BLE001
        pass
    # Phase 4 — conflict-only intervention (pause media when a real commitment is imminent). OFF by
    # default; the source itself no-ops until armed, so it's harmless to always register.
    try:
        from afon.brain.interventions import conflict_signals
        sources.append(conflict_signals)
    except Exception:  # noqa: BLE001
        pass
    # Phase 6.3 — calibrated wellbeing pushback (long unbroken session / small-hours). Rides the same
    # etiquette-learning + quiet-hours + budget gates, so it stays gentle and backs off when dismissed.
    try:
        from afon.brain.proactive_signals import wellbeing_signals
        sources.append(wellbeing_signals)
    except Exception:  # noqa: BLE001
        pass
    # Routine anchors — owner-defined time-windowed reminders (morning stretch before training, etc).
    # The purposeful-timing replacement for state-triggered nudges landing at random-feeling times.
    try:
        from afon.brain.proactive_signals import routine_signals
        sources.append(routine_signals)
    except Exception:  # noqa: BLE001
        pass
    # Morning planning prompt — asks WHEN today's dynamic commitments (training/reading) happen,
    # so plan_today can time their reminders purposefully instead of guessing a fixed window.
    try:
        from afon.brain.proactive_signals import routine_planning_signal
        sources.append(routine_planning_signal)
    except Exception:  # noqa: BLE001
        pass
    # Memory-util — proactively resurface a durable commitment he may have let slip (not just recall it
    # when asked). Rotates through open commitments, each raised at most once, on the same gates.
    try:
        from afon.brain.proactive_signals import memory_resurface_signals
        sources.append(memory_resurface_signals)
    except Exception:  # noqa: BLE001
        pass
    return sources
