"""Guard: every registered proactive SOURCE, when it fires, must emit an urgency that can clear the
engine's relevance threshold — otherwise the capability is DORMANT (built + registered but silently
filtered out every tick). This is the 2026-07-25 finding: anticipation/wellbeing/presence/pattern/
memory-resurface were all authored on a ~0.5 scale while the threshold is 0.60, so none could speak.
"""
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.config import settings  # noqa: E402

THRESH = settings.proactive_relevance_threshold
_ok = _fail = 0


def check(cond, label):
    global _ok, _fail
    if cond:
        _ok += 1
    else:
        _fail += 1
        print(f"  FAIL: {label}")


#: Every signal kind seen firing above threshold during this run (14.F3). Recording it here turns
#: "is this capability reachable?" from something a person remembers to ask into something the
#: suite asserts — which is the whole lesson of five companion sources sitting dormant for weeks.
SEEN_KINDS: set[str] = set()


def clears(sigs, label):
    for s in sigs or ():
        if getattr(s, "urgency", 0) >= THRESH:
            SEEN_KINDS.add(getattr(s, "kind", "?"))
    check(bool(sigs) and all(s.urgency >= THRESH for s in sigs),
          f"{label}: fires AND clears threshold {THRESH} (got {[round(s.urgency,2) for s in sigs]})")


# --- wellbeing: force a long unbroken session -----------------------------------------------
import afon.brain.presence as presence_mod  # noqa: E402
import afon.brain.proactive_signals as ps  # noqa: E402


class _FakePresence:
    # Shaped like the real Presence, including the fields a caller reads — a stub that matches a
    # broken caller instead of the class is how a defect survives its own test.
    enabled = True
    last_absence_s = 0.0

    def continuous_active_minutes(self):
        return settings.wellbeing_session_minutes + 60

    def arrival(self):
        return True


ps.PRESENCE = _FakePresence()               # wellbeing_signals imports PRESENCE from presence
presence_mod.PRESENCE = _FakePresence()     # presence_signals uses the module-level PRESENCE
clears(ps.wellbeing_signals(datetime(2026, 7, 15, 14, tzinfo=timezone.utc)), "wellbeing (long session)")
clears(ps.wellbeing_signals(datetime(2026, 7, 15, 2, tzinfo=timezone.utc)), "wellbeing (small hours)")
import asyncio as _aio  # noqa: E402 — presence_signals is async since 43.F2
clears(_aio.run(presence_mod.presence_signals()), "presence (welcome back)")

# --- memory_resurface: force one salient, un-resurfaced note --------------------------------
import afon.brain.memory as memory_mod  # noqa: E402
import tempfile  # noqa: E402

ps._RESURFACED_PATH = Path(tempfile.gettempdir()) / "test_resurfaced.json"
ps._RESURFACED_PATH.unlink(missing_ok=True)
memory_mod.STORE.salient_notes = lambda now=None: [{"note_id": "n1", "text": "ship the rabbit-farm plan"}]
clears(ps.memory_resurface_signals(), "memory_resurface")

# --- pattern_suggestion: force a note matching the current UTC hour --------------------------
hour = datetime.now(timezone.utc).hour
note = types.SimpleNamespace(text=f"user often mentions 'lofi' around {hour:02d}:00 utc")
memory_mod.STORE._iter_notes = lambda: [note]
clears(ps.pattern_suggestion(), "pattern_suggestion")

# --- anticipation: stub the LLM to return one useful line -----------------------------------
import asyncio  # noqa: E402
from afon.brain.anticipation import make_anticipation_source  # noqa: E402


class _FakeLLM:
    async def complete(self, messages, **kw):
        return types.SimpleNamespace(content="Your visa deadline is Friday, sir — worth starting today.")


async def _noop():
    return None


fake_world = types.SimpleNamespace(render=lambda owner="": "GOAL: file the visa (due Friday)")
src = make_anticipation_source(llm=_FakeLLM(), world=fake_world, recent=lambda: "coding",
                               refresh=_noop, clock=lambda: 10_000.0)
clears(asyncio.run(src()), "anticipation (LLM-reasoned)")

# --- routine anchors: in-window fires once-per-day-keyed, out-of-window is silent ------------
import json  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import afon.brain.tools.routines as routines_mod  # noqa: E402

ps._ROUTINES_PATH = Path(tempfile.gettempdir()) / "test_routines.json"
ps._ROUTINES_PATH.write_text(json.dumps([
    {"key": "morning-stretch", "window": "07:45-09:45", "message": "stretch", "urgency": 0.65},
    {"key": "training", "dynamic": True, "message": "train", "prep_message": "stretch first",
     "lead_minutes": 40},
]), encoding="utf-8")
routines_mod._DAY_PLAN = Path(tempfile.gettempdir()) / "test_day_plan.json"
routines_mod._DAY_PLAN.unlink(missing_ok=True)
_tz = ZoneInfo(settings.user_tz)
morning = datetime(2026, 7, 15, 8, 30, tzinfo=_tz)
night = datetime(2026, 7, 15, 23, 30, tzinfo=_tz)
sigs = ps.routine_signals(morning)
clears(sigs, "routine (windowed entry, in window)")
check(sigs and sigs[0].key == f"routine-morning-stretch-{morning.date()}",
      f"routine key carries the date for once-per-day dedupe (got {sigs[0].key if sigs else None})")
check(all("training" not in s.key for s in sigs),
      "dynamic commitments NEVER fire from a window (their time varies by day)")
check(ps.routine_signals(night) == [],
      "routine NEVER fires outside its window (the 'stretch at night' complaint)")

# --- morning planning prompt: asks while a dynamic commitment is unplanned, then goes quiet --
plan_sigs = ps.routine_planning_signal(morning)
clears(plan_sigs, "routine planning prompt (morning, training unplanned)")
check(plan_sigs and "training" in plan_sigs[0].message, "planning prompt names the commitment")
check(ps.routine_planning_signal(night) == [], "planning prompt never fires at night")
routines_mod._DAY_PLAN.write_text(json.dumps({"date": str(morning.date()), "training": "12:30"}),
                                  encoding="utf-8")
check(ps.routine_planning_signal(morning) == [], "planning prompt silent once today is planned")

# --- calendar: an imminent event, with the API stubbed --------------------------------------
# Found by the coverage check below on its first run: this kind is registered as a tick source and
# perfectly reachable, but no fixture had ever driven it — which is the state five companion
# capabilities were in before anyone noticed.
import afon.brain.tools.calendar as cal_mod  # noqa: E402


async def _one_imminent_event(url, params=None, **kw):
    soon = datetime.now(timezone.utc) + timedelta(minutes=7)
    return {"items": [{"id": "ev1", "summary": "dentist",
                       "start": {"dateTime": soon.isoformat()}}]}


_saved_api_get = cal_mod.api_get
_saved_configured = cal_mod.configured
cal_mod.api_get = _one_imminent_event
cal_mod.configured = lambda: True   # this box has no Google creds; the SIGNAL is what is under test
try:
    _cal_sigs = asyncio.run(cal_mod.calendar_signals())
    clears(_cal_sigs, "calendar (imminent event)")
    check(_cal_sigs and "dentist" in _cal_sigs[0].message, "calendar signal names the event")
finally:
    cal_mod.api_get, cal_mod.configured = _saved_api_get, _saved_configured

# --- the invariant, stated directly ---------------------------------------------------------
check(THRESH <= 0.66, f"threshold {THRESH} is not above the companion sources' ceiling")

# --- 14.F3: EVERY declared kind is accounted for ---------------------------------------------
# A dormant kind is a bug, not a preference. The declared set is read out of the source rather
# than written down here, so adding a new kind and forgetting to exercise it fails this file
# instead of going quietly dormant — which is exactly how five capabilities were lost for weeks.
# Read from the SIGNAL CONSTRUCTIONS themselves, via the parser, not by grepping for `kind="..."`.
# `kind` is a word several unrelated things in this brain use — a background task has one, and so
# does a created document — and the text scan counted all of them. That is not a tidiness point:
# `fleet`, `work` and `todo` are TaskQueue kinds that were never proactive signals at all, and the
# way the false positives got silenced was by adding them to the hand-off list below, where they
# then read as real capabilities covered by another file. A gate that can be quietened by writing
# a sentence in the gate is the failure this file exists to prevent, one level up.
import ast  # noqa: E402

_BRAIN = Path(__file__).resolve().parents[1] / "src" / "afon" / "brain"
DECLARED = set()
for _py in list(_BRAIN.glob("*.py")) + list((_BRAIN / "tools").glob("*.py")):
    try:
        _tree = ast.parse(_py.read_text(encoding="utf-8"))
    except SyntaxError:                      # a file mid-edit is not this test's business
        continue
    for _node in ast.walk(_tree):
        if not (isinstance(_node, ast.Call) and getattr(_node.func, "id", "") == "Signal"):
            continue
        for _kw in _node.keywords:
            if _kw.arg == "kind" and isinstance(_kw.value, ast.Constant):
                DECLARED.add(_kw.value.value)

#: Kinds this file cannot drive, each with the reason and the file that does cover it. An entry
#: here is a deliberate hand-off, not an excuse — the check below still fails if a kind is in
#: neither set, so a new capability cannot be added silently.
COVERED_ELSEWHERE = {
    "health": "needs a failing component — test_health_agreement.py / test_phase11_notion.py",
    "news": "needs the feed reader — test_mynews.py",
    "weekly-digest": "weekly cadence — test_daily_digest.py",
    "calendar-prep": "needs a real calendar event — test_calendar_dates.py",
    "conflict": "needs a booked conflict — test_interventions.py",
    "coaching": "needs a skill review due — test_coaching.py",
    "reraise": "needs an unacknowledged urgent delivery — test_delivery_ledger.py",
    "routine-plan": "planning prompt, asserted above by message rather than kind",
    "resurface": "memory resurfacing, asserted above by message rather than kind",
}

_unaccounted = sorted(DECLARED - SEEN_KINDS - set(COVERED_ELSEWHERE))
check(not _unaccounted,
      f"every declared signal kind is exercised here or handed off explicitly "
      f"(unaccounted: {_unaccounted})")

# The hand-off list must not rot into a dumping ground for kinds that no longer exist.
_stale = sorted(set(COVERED_ELSEWHERE) - DECLARED)
check(not _stale, f"the hand-off list names only kinds that still exist (stale: {_stale})")

print(f"  kinds fired here: {sorted(SEEN_KINDS)}")
print(f"  kinds handed off: {sorted(set(COVERED_ELSEWHERE) & DECLARED)}")

print(f"=== {_ok}/{_ok + _fail} checks passed ===")
sys.exit(1 if _fail else 0)
