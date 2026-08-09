"""Phase 6 — relational intelligence: affect sensing (6.1), relationship memory (6.2), pushback (6.3).

Hermetic. Locks: affect is read from text + time of day and turned into a manner directive; the
relationship store logs mood over time / sensitivities / running jokes and renders them; the presence
continuous-session accessor measures an unbroken active stretch; and the wellbeing source pushes back on a
long session / the small hours (and stays quiet otherwise). Also checks the agent injects a relational
note without a full init.

    uv run python bench/test_relational.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain.affect import infer_affect, manner_note  # noqa: E402
from afon.brain.relationship import RelationshipMemory  # noqa: E402
from afon.config import settings  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> None:
    print("[1] 6.1 affect sensing from text + time")
    day = datetime(2026, 7, 22, 14, 0, tzinfo=ZoneInfo("UTC"))
    check("frustration detected", infer_affect("ugh this is still not working", now=day).stress)
    check("double-bang reads as stress", infer_affect("fix it now!!", now=day).stress)
    check("tired detected", infer_affect("I'm exhausted, long day", now=day).tired)
    check("low mood detected", infer_affect("honestly feeling down today", now=day).low)
    check("upbeat detected", infer_affect("we did it, amazing!", now=day).upbeat)
    check("terse detected", infer_affect("just stop", now=day).terse)
    check("normal question is neutral", infer_affect("what's on my calendar tomorrow", now=day).neutral)
    small_hours = datetime(2026, 7, 22, 3, 0, tzinfo=ZoneInfo("UTC"))
    check("small hours nudges tired", infer_affect("still going", now=small_hours).tired)

    print("\n[2] 6.1 manner note adapts, silent when neutral")
    check("neutral -> no note", manner_note(infer_affect("what time is it", now=day)) is None)
    stressed = manner_note(infer_affect("this is broken and I'm so annoyed", now=day))
    check("stressed -> a manner directive", stressed is not None and "concise" in stressed, str(stressed))
    check("note tells him NOT to announce it", "without mentioning" in (stressed or ""))

    print("\n[3] 6.2 relationship memory: mood over time / sensitivities / jokes")
    rel = RelationshipMemory(Path(tempfile.mkdtemp()) / "relationship.json")
    check("too little signal -> no mood yet", rel.recent_mood() == "")
    for _ in range(6):
        rel.note_affect(["stress"])
    check("a sustained mood is detected", rel.recent_mood() == "stressed", rel.recent_mood())
    check("neutral/empty tags are not logged", (rel.note_affect([]) or True) and len(rel._r.affect_log) == 6)
    check("add sensitivity", rel.add_sensitivity("his father's health"))
    check("sensitivity dedups (case-insensitive)", rel.add_sensitivity("His Father's Health") is False)
    check("add running joke", rel.add_running_joke("the 'it's always DNS' bit"))
    r = rel.render()
    check("render surfaces mood", "stressed" in r, r)
    check("render surfaces sensitivity", "father's health" in r, r)
    check("render surfaces the joke", "DNS" in r, r)
    reloaded = RelationshipMemory(rel._path)
    check("persists + reloads", reloaded.recent_mood() == "stressed" and reloaded._r.sensitivities)

    print("\n[4] affect log is capped (no unbounded growth)")
    rel2 = RelationshipMemory(Path(tempfile.mkdtemp()) / "r2.json")
    for _ in range(80):
        rel2.note_affect(["tired"])
    check("affect log capped at 60", len(rel2._r.affect_log) == 60, str(len(rel2._r.affect_log)))

    print("\n[5] 6.3 presence continuous-session accessor")
    from afon.brain.presence import Presence

    poll = settings.presence_poll_seconds
    pres = Presence(db_path=Path(tempfile.mkdtemp()) / "activity.db")
    now = time.time()
    n = 12
    for i in range(n):                      # newest at `now`, spaced one poll apart, all active
        pres.record("Code.exe", "work", idle=0, ts=now - i * poll)
    mins = pres.continuous_active_minutes(now=now)
    check("continuous run ~= span", abs(mins - (n - 1) * poll / 60.0) < 0.2, str(mins))
    # An idle sample at the head means he's not at the screen -> 0.
    pres.record("Code.exe", "away", idle=settings.presence_idle_threshold_seconds + 5, ts=now + 1)
    check("idle head -> zero", pres.continuous_active_minutes(now=now + 1) == 0.0)

    print("\n[6] 6.3 wellbeing pushback fires on a long session, quiet otherwise")
    import afon.brain.presence as presence_mod
    from afon.brain.proactive_signals import wellbeing_signals

    class FakePresence:
        def __init__(self, m):
            self._m = m

        def continuous_active_minutes(self, now=None):
            return self._m

    saved = presence_mod.PRESENCE
    try:
        midday = datetime(2026, 7, 22, 15, 0, tzinfo=ZoneInfo(settings.user_tz))
        presence_mod.PRESENCE = FakePresence(settings.wellbeing_session_minutes + 30)
        sig = wellbeing_signals(now=midday)
        check("long session -> a wellbeing nudge", len(sig) == 1 and sig[0].kind == "wellbeing", str(sig))
        check("nudge suggests a break", "step" in sig[0].message.lower() or "away" in sig[0].message.lower())
        presence_mod.PRESENCE = FakePresence(20)
        check("short session -> silent", wellbeing_signals(now=midday) == [])
        # Small hours + a modest session -> a wrap-up nudge.
        night = datetime(2026, 7, 22, 3, 0, tzinfo=ZoneInfo(settings.user_tz))
        presence_mod.PRESENCE = FakePresence(30)
        night_sig = wellbeing_signals(now=night)
        check("small hours -> wrap-up nudge", len(night_sig) == 1 and "sleep" in night_sig[0].message.lower())
    finally:
        presence_mod.PRESENCE = saved

    print("\n[7] the source is registered on the live proactive tick")
    from afon.brain.proactive import default_signal_sources
    names = {getattr(s, "__name__", "") for s in default_signal_sources()}
    check("wellbeing_signals registered", "wellbeing_signals" in names, str(sorted(names)))

    print("\n[8] the agent injects a relational note (no full init)")
    from afon.brain.agent import AfonAgent
    # A deterministically-stressed utterance (matches a real cue) so the manner directive is always
    # present — not leaning on whatever the on-disk relationship singleton happens to hold.
    note = AfonAgent._relational_note(object.__new__(AfonAgent), "ugh this is still not working and I'm so annoyed")
    check("stressed utterance -> a manner note", bool(note) and "concise" in note, repr(note))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
