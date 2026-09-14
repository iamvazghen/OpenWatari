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

# --- 32.F4: health of the OTHER host is known to each host, not assumed ------------------------
# Each side used to infer the other from whatever it happened to be doing: the brain from a socket
# being registered, the laptop from whether its last send raised. A registered socket is not a live
# host — a laptop that closes its lid leaves one open for over a minute — and reporting "connected"
# on the strength of it is the same overclaim as a status page that is green because it never
# asked. Both sides now ASK, and both expose the answer so a degraded mode can be decided from it.
print("\n[32.F4] each host asks about the other rather than assuming")
import asyncio as _aio  # noqa: E402
import inspect as _inspect  # noqa: E402

from afon.brain.pc_link import PC_LINK  # noqa: E402

check(hasattr(PC_LINK, "reachable"), "the brain can ASK whether the laptop is alive")
check(_inspect.iscoroutinefunction(PC_LINK.reachable),
      "...over the wire, not from a cached flag")
check(hasattr(PC_LINK, "silent_for"), "the brain knows HOW LONG the laptop has been quiet")
check(hasattr(PC_LINK, "active"), "...and whether a socket is even registered")

_hp = (Path(__file__).resolve().parents[1] / "src/afon/brain/health.py").read_text(encoding="utf-8")
check("PC_LINK.reachable()" in _hp,
      "the health check asks, instead of trusting `active`")
check("not a live host" in _hp or "closed its lid" in _hp,
      "...and the reason a registered socket is not proof is written down")

from afon.edge.brain_client import BrainClient  # noqa: E402

check(hasattr(BrainClient, "state"), "the laptop knows the state of its link to the brain")
check(hasattr(BrainClient, "degraded"), "...and turns it into a declared mode")

_c = BrainClient.__new__(BrainClient)
_c._state = "connected"
from afon.shared.degraded import Announcer as _Ann  # noqa: E402
from afon.shared.degraded import BRAIN_DOWN as _BD  # noqa: E402

_c._degraded = _Ann()
check(_c.degraded() == "", "a connected laptop announces nothing")
_c._state = "reconnecting"
_said = _c.degraded()
check("can't reach my brain host" in _said, f"a dropped link is announced by the side that can speak ({_said[:50]!r})")
check(_c.degraded() == "", "...once, not on every reconnect attempt")
_c._state = "connected"
check("Back to normal" in _c.degraded(), "and the recovery is announced too")
check(_c._degraded.mode.name != _BD, "...leaving the mode cleared")

# Neither side may decide the other is fine by default.
_bc = (Path(__file__).resolve().parents[1] / "src/afon/edge/brain_client.py").read_text(encoding="utf-8")
check("brain_up=self._state == \"connected\"" in _bc,
      "the laptop derives brain-up from the real socket state, not from a constant")

print(f"=== {_ok}/{_ok + _fail} checks passed ===")
sys.exit(1 if _fail else 0)
