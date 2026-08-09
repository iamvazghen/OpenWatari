"""Phase 4 companion — conflict-only interventions (pause media on a real, imminent commitment).

Proves the single, narrow interrupt the owner asked for: WHEN he's watching/listening to something
AND a real timed calendar event is about to start, Afon pauses the media and flags the concrete
conflict — and NOTHING otherwise. It rides the same engine gates as every nudge (kind 'conflict'),
but its urgency clears the context-override so it may interrupt media (which normally holds a nudge),
while staying below the quiet-hours override. The media-pause side-effect travels on the Signal's
`action` and runs only when the engine actually interjects.

Hermetic: fake presence + injected events, an in-memory engine, a capturing emitter. No network.

    uv run python bench/test_interventions.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


async def test_media_detection() -> None:
    print("[1] media detection: browser video + player app yes; work/idle no")
    from afon.brain import interventions as iv

    check("YouTube in a browser reads as media", iv._looks_like_media("chrome", "Lofi beats - YouTube", "browsing"))
    check("Netflix in a browser reads as media", iv._looks_like_media("msedge", "Stranger Things - Netflix", "browsing"))
    check("VLC (media category) reads as media", iv._looks_like_media("vlc", "movie.mkv", "media"))
    check("plain coding is NOT media", not iv._looks_like_media("code", "agent.py - Visual Studio Code", "coding"))
    check("a normal web page is NOT media", not iv._looks_like_media("chrome", "Gmail - Inbox", "browsing"))

    from types import SimpleNamespace
    snap = SimpleNamespace(app="chrome", title="The Matrix - YouTube")
    check("_what -> 'video' for a video title", iv._what(snap) == "video", iv._what(snap))
    snap2 = SimpleNamespace(app="spotify", title="Daft Punk - Spotify")
    check("_what -> 'track' for music", iv._what(snap2) == "track", iv._what(snap2))
    msg = iv._conflict_message(snap, 8, "Dentist")
    check("conflict message names the event + minutes + paused + a choice",
          "Dentist" in msg and "8 minute" in msg and "paused" in msg and "head off" in msg, msg)


async def test_context_and_signal() -> None:
    print("\n[2] _media_context + conflict_signals (armed / off / gated)")
    from afon.brain import interventions as iv
    from afon.brain.presence import PRESENCE
    from afon.config import settings

    # A fresh, active media sample -> _media_context sees it.
    PRESENCE.set_enabled(True)
    PRESENCE.record("chrome", "Interstellar - YouTube", idle=2.0)
    now = time.time()
    check("_media_context detects live media", iv._media_context(now) is not None)
    # Away from the keyboard (idle past threshold) -> nothing to interrupt.
    PRESENCE.record("chrome", "Interstellar - YouTube", idle=settings.presence_idle_threshold_seconds + 30)
    check("idle/away -> no media context", iv._media_context(time.time()) is None)

    # Re-arm a live media sample and inject an imminent event.
    PRESENCE.record("chrome", "Interstellar - YouTube", idle=2.0)
    iv._imminent_events = _fake_events([(9, "Team standup", "evt-1")])  # patch the module global

    settings.interventions_enabled = False
    check("OFF by default -> no signal", await iv.conflict_signals() == [])

    settings.interventions_enabled = True
    sigs = await iv.conflict_signals()
    check("armed + media + imminent event -> exactly one signal", len(sigs) == 1, str(len(sigs)))
    s = sigs[0]
    check("kind is 'conflict' (feedback-learned like any nudge)", s.kind == "conflict")
    check("urgency clears context-override (interrupts media)",
          s.urgency >= settings.proactive_context_override_urgency, str(s.urgency))
    check("urgency stays BELOW quiet-override (never fires in quiet hours)",
          s.urgency < settings.proactive_quiet_override_urgency, str(s.urgency))
    check("message names the event", "Team standup" in s.message, s.message)
    check("carries a media-pause action", callable(s.action))
    check("key is stable per event (repeat-suppression)", s.key == "conflict-evt-1", s.key)

    # No imminent event -> no intervention even while watching.
    iv._imminent_events = _fake_events([])
    check("media but NO commitment -> no signal", await iv.conflict_signals() == [])


def _fake_events(items):
    async def _f(lead_minutes):
        return list(items)
    return _f


async def test_engine_interrupts_only_for_conflict() -> None:
    print("\n[3] the engine interrupts a BUSY owner for a conflict, holds a routine, runs the action")
    from afon.brain.proactive import ProactiveEngine, Signal

    tz = ZoneInfo("Europe/Amsterdam")
    noon = datetime(2026, 7, 14, 12, 0, tzinfo=tz)  # outside quiet hours
    paused = {"n": 0}

    async def pause_action():
        paused["n"] += 1

    emitted: list[str] = []

    async def emit(message, urgency, listening):
        emitted.append(message)
        return "voice" if listening else "push"

    # A conflict signal (0.9) while the owner is BUSY (watching media) -> must interrupt + pause.
    conflict = Signal(key="conflict-x", kind="conflict", urgency=0.9,
                      message="'Standup' starts in 5, sir — I've paused it.", action=pause_action)
    eng = ProactiveEngine(emit=emit, sources=[lambda: [conflict]],
                          is_listening=lambda: True, is_busy=lambda: True,
                          clock=lambda: noon, quiet_hours="")
    out = await eng.maybe_interject(noon)
    check("conflict interjected despite the owner being busy", out is not None and out.channel == "voice", str(out))
    check("the media-pause action ran exactly once", paused["n"] == 1, str(paused))
    check("the message was emitted", emitted and "paused it" in emitted[0], str(emitted))

    # A ROUTINE nudge (0.7) while busy -> HELD (proves conflict is the deliberate exception).
    ran = {"n": 0}

    async def routine_action():
        ran["n"] += 1

    routine = Signal(key="routine-y", kind="routine", urgency=0.7,
                     message="Fancy a break, sir?", action=routine_action)
    eng2 = ProactiveEngine(emit=emit, sources=[lambda: [routine]],
                           is_listening=lambda: True, is_busy=lambda: True,
                           clock=lambda: noon, quiet_hours="")
    out2 = await eng2.maybe_interject(noon)
    check("routine nudge is HELD while busy (not interjected)", out2 is None, str(out2))
    check("a held signal's action does NOT run", ran["n"] == 0, str(ran))

    # Same conflict repeatedly dismissed -> learning raises its threshold until it stops firing.
    eng3 = ProactiveEngine(emit=emit, sources=[lambda: [Signal(key=f"c-{time.time()}", kind="conflict",
                           urgency=0.9, message="x")]],
                           is_listening=lambda: True, is_busy=lambda: True,
                           clock=lambda: noon, quiet_hours="", daily_budget=99)
    for _ in range(4):
        eng3.record_feedback("conflict", "dismiss", now=noon)
    check("after repeated dismissals the conflict kind is muted (learns to back off)",
          eng3.effective_threshold("conflict", noon) > 0.9, str(eng3.effective_threshold("conflict", noon)))


async def test_media_pause_tool_wired() -> None:
    print("\n[4] media_pause is wired as a PC op + an LLM tool")
    from afon.brain.tools import system

    check("media_pause in LOCAL_HANDLERS (runs on the laptop)", "media_pause" in system.LOCAL_HANDLERS)
    check("media_pause in HANDLERS (callable by the agent)", "media_pause" in system.HANDLERS)
    check("media_pause has a tool schema", any(s["function"]["name"] == "media_pause" for s in system.SCHEMAS))

    from afon.brain.interventions import conflict_signals
    from afon.brain.proactive import default_signal_sources
    names = {getattr(s, "__name__", "") for s in default_signal_sources()}
    check("conflict_signals registered as a default source", "conflict_signals" in names, str(names))


async def main() -> None:
    await test_media_detection()
    await test_context_and_signal()
    await test_engine_interrupts_only_for_conflict()
    await test_media_pause_tool_wired()
    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
