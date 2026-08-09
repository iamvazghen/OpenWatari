"""G1 hermetic test — acknowledgement responses are varied and don't stack (no network).

Asserts the polish from G1:
  * a router-narrowed ZERO-ARG read suppresses the generic immediate ack (the specific per-tool ack
    "Checking your calendar, sir." lands in milliseconds — a generic "Right away" would just double up);
  * an arg-bearing command still gets an instant ack (real dead air during arg-extraction);
  * the immediate ack rotates — never the identical line twice in a row.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.config import settings
settings.ack_before_tools = True  # make the behaviour deterministic regardless of env

from afon.brain.agent import AfonAgent, _WORK_ACKS, _CHAT_ACKS  # noqa: E402

_ok = _fail = 0


def check(cond, label):
    global _ok, _fail
    if cond:
        _ok += 1
    else:
        _fail += 1
        print(f"  FAIL: {label}")


a = AfonAgent()
a._self_improve = False

# 1) zero-arg read -> generic ack SUPPRESSED (specific per-tool ack follows near-instantly)
seen: list[str] = []
a._immediate_ack("what's on my calendar today?", seen.append)
check(seen == [], f"zero-arg read suppresses the generic immediate ack (got {seen})")

# 2) arg-bearing command -> exactly one ack, from the WORK pool
seen.clear()
a._immediate_ack("set a reminder to stretch in 90 minutes", seen.append)
check(len(seen) == 1 and seen[0] in _WORK_ACKS, f"arg-bearing command gets one work ack (got {seen})")

# 3) plain-ish chat that trips a group -> one ack from a known pool
seen.clear()
a._immediate_ack("how am I doing on my german?", seen.append)   # 'coaching' group trigger
check(len(seen) == 1 and seen[0] in (_WORK_ACKS + _CHAT_ACKS), f"group turn gets an ack (got {seen})")

# 4) rotation: never the identical line twice in a row across many turns
lines: list[str] = []
for _ in range(30):
    a._immediate_ack("send an email to bob saying hi", lines.append)   # arg-bearing WORK turn
check(len(lines) == 30, f"every non-suppressed turn produced an ack (got {len(lines)})")
check(all(lines[i] != lines[i + 1] for i in range(len(lines) - 1)),
      "immediate ack never repeats the same line back-to-back")
check(len(set(lines)) > 1, "immediate ack actually varies (not stuck on one phrase)")

print(f"=== {_ok}/{_ok + _fail} checks passed ===")
sys.exit(1 if _fail else 0)
