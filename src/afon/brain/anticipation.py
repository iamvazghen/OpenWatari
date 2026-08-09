"""Phase 2.3 — reasoned anticipation.

The old ``pattern_suggestion`` matched keyword regexes ("you mention lofi around 20:00"). AFON
anticipates by *reasoning*: given everything he knows about what the owner is trying to do and what
just happened, is there ONE genuinely useful, non-obvious, timely thing to say right now? This is a
proactive ``SignalSource`` that renders the world-model (Phase 2.1) + recent activity into a prompt
and asks the LLM exactly that — returning at most one Signal, or none.

Two guards keep it cheap and calm:
  * an **interval gate** so it reasons at most once every ``min_interval_s`` (it's wired into a tick
    that may run every few minutes; an LLM call per tick would be wasteful) — the O1/efficiency knob;
  * it must answer ``NONE`` unless something clears the bar, and the engine's own budget +
    repeat-suppression (keyed on the message) stop it from nagging.

Everything is injectable (llm / world / clock / recent) so the whole thing is hermetically testable
with a fake LLM, and every failure is swallowed — a thrown source is just skipped by the tick.
"""

from __future__ import annotations

import hashlib
import re
from typing import Awaitable, Callable

from loguru import logger

from afon.brain.proactive import Signal
from afon.config import settings

_TAG_RE = re.compile(r"<[^>]+>|<\|[^>]*\|>")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text or "")).strip()


_SYSTEM = (
    "You are the anticipation module of {name}, {owner}'s always-on assistant. You are given a model "
    "of what {owner} is currently working toward (goals, projects, deadlines) and a note of recent "
    "activity. Decide whether there is exactly ONE genuinely useful, non-obvious, well-timed thing to "
    "say to {owner} UNPROMPTED right now — an imminent deadline they may have lost track of, a "
    "dependency between two things they're doing, a natural next step, a risk. Be conservative: an "
    "interruption must earn its place. If nothing clears that bar, reply with exactly NONE. Otherwise "
    "reply with ONE short spoken sentence in {name}'s voice (no markdown), leading with the reason."
)


def _recent_activity(limit: int = 8) -> str:
    """A one-line digest of the last few logged command topics (best-effort, empty on any issue)."""
    try:
        from afon.brain.patterns import _read_window

        entries = _read_window(days=2)[-limit:]
        topics = [e.get("topic", "") for e in entries if e.get("topic")]
        return ", ".join(dict.fromkeys(topics)) if topics else ""
    except Exception:  # noqa: BLE001
        return ""


async def _default_refresh(world) -> None:
    """Populate the world-model from live sources before reasoning (Phase 2.2): the owner's Notion
    tasks become goals. Best-effort — a Notion outage just leaves the model as-is."""
    from afon.brain.tools.notion import notion_tasks_structured

    tasks = await notion_tasks_structured()
    if tasks:
        world.refresh_from_tasks(tasks)


def make_anticipation_source(
    *,
    llm=None,
    world=None,
    recent: Callable[[], str] | None = None,
    clock: Callable[[], float] | None = None,
    refresh: Callable[[], Awaitable[None]] | None = None,
    min_interval_s: float = 1800.0,
) -> Callable[[], Awaitable[list[Signal]]]:
    """Build the anticipation ``SignalSource``. Args are injected in tests; defaults wire the live
    world-model + LLM + a Notion-task refresh. ``min_interval_s`` throttles the whole thing (default
    30 min) so neither the refresh nor the LLM call runs every tick."""
    import time as _time

    _clock = clock or _time.monotonic
    _state = {"last": 0.0}

    async def anticipation_signals() -> list[Signal]:
        now = _clock()
        if now - _state["last"] < min_interval_s:
            return []  # interval gate: don't refresh/reason (or pay for an LLM call) every tick
        w = world
        if w is None:
            from afon.brain.world_model import WORLD as w  # noqa: N813
        # Refresh the model from live sources (Notion tasks -> goals) before reasoning. Guarded so a
        # source outage never breaks the tick.
        try:
            await (refresh() if refresh is not None else _default_refresh(w))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"anticipation refresh skipped ({type(e).__name__})")
        owner = (settings.user_name or "the owner").strip()
        name = (settings.assistant_name or "Afon").strip()
        rendered = w.render(owner=owner)
        activity = (recent or _recent_activity)()
        if not rendered and not activity:
            return []  # nothing to reason about — skip without touching the LLM
        _state["last"] = now
        client = llm
        if client is None:
            from afon.brain.llm import LLMClient

            client = LLMClient()
        try:
            user = rendered or ""
            if activity:
                user += f"\n\nRecent activity: {activity}"
            msg = await client.complete(
                [{"role": "system", "content": _SYSTEM.format(owner=owner, name=name)},
                 {"role": "user", "content": user}],
                temperature=0.4,
            )
            text = _clean(getattr(msg, "content", "") or "")
        except Exception as e:  # noqa: BLE001 — a reasoning failure must never break the tick
            logger.debug(f"anticipation: skipped ({type(e).__name__})")
            return []
        if not text or text.strip().upper().startswith("NONE"):
            return []
        key = "anticipation-" + hashlib.sha1(text.lower().encode()).hexdigest()[:10]
        # 0.66 clears the 0.60 threshold (was 0.55 = dormant, so this whole LLM-reasoned source never
        # fired). The LLM already gates hard (replies NONE unless one thing truly earns an interruption),
        # so when it DOES speak it's worth voicing. Stays < 0.85 context-override (waits for a pause).
        return [Signal(key=key, message=text, urgency=0.66, kind="anticipation")]

    return anticipation_signals
