"""G5 connectivity test — fresh integration events reach the REACTIVE turn (no network).

Traces the wire the audit added: an integration (webhook, etc.) calls ``WORLD.note_event`` and the
agent's per-turn context now includes it via ``_world_note`` — so "anything new?" can mention a Stripe
payout or CI failure, not just the proactive loop. Asserts freshness-gating (old-but-live events don't
spam every turn) and that the note is empty when there's nothing fresh (near-zero token cost).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain.world_model import WorldModel  # noqa: E402

_ok = _fail = 0


def check(cond, label):
    global _ok, _fail
    if cond:
        _ok += 1
    else:
        _fail += 1
        print(f"  FAIL: {label}")


now = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
tmp = Path(__file__).resolve().parents[1] / ".test_world_model.json"
if tmp.exists():
    tmp.unlink()
w = WorldModel(path=str(tmp))

# 1) a just-arrived event is fresh -> surfaced
w.note_event("Stripe payout of EUR 420 cleared", ttl_hours=48.0, now=now)
fresh = w.recent_events(now=now + timedelta(minutes=5))
check(fresh == ["Stripe payout of EUR 420 cleared"], f"fresh event surfaced (got {fresh})")

# 2) still LIVE (48h TTL) but old (>6h) -> NOT surfaced reactively (proactive loop still has it)
old = w.recent_events(now=now + timedelta(hours=8))
check(old == [], f"live-but-old event is freshness-gated out of the reactive turn (got {old})")

# 3) newest-last, capped to the limit
for i in range(5):
    w.note_event(f"event {i}", ttl_hours=48.0, now=now + timedelta(minutes=10 + i))
capped = w.recent_events(now=now + timedelta(minutes=20), limit=3)
check(len(capped) == 3 and capped[-1] == "event 4", f"capped, newest-last (got {capped})")

# 4) agent._world_note reflects the world model (patched to our temp instance) and is None when empty
import afon.brain.world_model as wm  # noqa: E402
from afon.brain.agent import AfonAgent  # noqa: E402

wm.WORLD = w  # the note reads module-level WORLD
a = AfonAgent()
a._self_improve = False
# monkeypatch recent_events to a deterministic fresh list (avoid clock coupling)
w.recent_events = lambda **_k: ["CI failed on party-map main"]
note = a._world_note()
check(note is not None and "CI failed on party-map main" in note, f"agent surfaces fresh events (got {note!r})")
w.recent_events = lambda **_k: []
check(a._world_note() is None, "no fresh events -> no note (near-zero token cost)")

if tmp.exists():
    tmp.unlink()
print(f"=== {_ok}/{_ok + _fail} checks passed ===")
sys.exit(1 if _fail else 0)
