"""32.F3 — three declared degraded modes, one table, and each announced once.

Model failover was real; nothing above it was. Losing a host produced whatever each component
happened to do on its own — a tool that timed out, a socket that reconnected forever, an answer
that quietly came back without the half of Afon that was gone. None of it was written down, so
"what works right now" had no answer and there was no moment at which the owner was told.

  [matrix]    the three modes exist, each naming what is LOST and what is KEPT in the owner's
              terms, in ONE place both hosts read — two copies of this table would agree on the
              day they were written and drift silently afterwards;
  [severity]  a dead network is reported as a dead network, not as three coincidental failures;
  [announce]  a mode is said once on the way in and once on the way out, and never in between. The
              recovery is the half that matters: without it he keeps working around a limitation
              that has been gone for an hour.

Hermetic: pure state machine, no hosts involved.

    uv run python bench/test_degraded_modes.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

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
    from afon.shared import degraded as D

    print("[matrix] 32.F3 — the three modes the plan names, and a full one")
    check("all three are declared",
          {D.BRAIN_DOWN, D.EDGE_DOWN, D.NETWORK_DOWN} <= set(D.MATRIX), sorted(D.MATRIX))
    for name in (D.BRAIN_DOWN, D.EDGE_DOWN, D.NETWORK_DOWN):
        m = D.MATRIX[name]
        check(f"{name} says what is lost", len(m.lost.split()) >= 3, m.lost)
        check(f"{name} says what is KEPT", len(m.kept.split()) >= 3, m.kept)
        check(f"{name} has something to say out loud", m.said.startswith("Sir,"), m.said[:60])
    check("the full mode is falsy, the degraded ones are not",
          not D.MATRIX[D.FULL] and all(D.MATRIX[n] for n in
                                       (D.BRAIN_DOWN, D.EDGE_DOWN, D.NETWORK_DOWN)))

    print("\n[matrix] each mode matches what the plan says that host can still do")
    check("brain-down keeps the laptop's own ears and voice",
          "hear you" in D.MATRIX[D.BRAIN_DOWN].said, D.MATRIX[D.BRAIN_DOWN].said)
    check("...and admits it has no memory", "no memory" in D.MATRIX[D.BRAIN_DOWN].said)
    check("edge-down keeps the text channels", "Telegram" in D.MATRIX[D.EDGE_DOWN].said)
    check("...and admits it cannot hear or speak", "can't hear" in D.MATRIX[D.EDGE_DOWN].said)
    check("network-down keeps the local model", "local model" in D.MATRIX[D.NETWORK_DOWN].said)
    check("...and warns that nothing about the world is current",
          "current" in D.MATRIX[D.NETWORK_DOWN].said)

    print("\n[severity] one cause is reported once")
    check("everything up is the full mode", D.current().name == D.FULL)
    check("a dead laptop is edge-down", D.current(edge_up=False).name == D.EDGE_DOWN)
    check("an unreachable brain is brain-down", D.current(brain_up=False).name == D.BRAIN_DOWN)
    check("no network is network-down", D.current(network_up=False).name == D.NETWORK_DOWN)
    check("a dead network that also took the brain reports the CAUSE",
          D.current(brain_up=False, network_up=False).name == D.NETWORK_DOWN,
          "otherwise one outage is announced as three")
    check("...and so does one that took everything",
          D.current(brain_up=False, edge_up=False, network_up=False).name == D.NETWORK_DOWN)

    print("\n[announce] once in, once out, nothing in between")
    a = D.Announcer()
    check("a healthy start says nothing", a.update(D.MATRIX[D.FULL]) == "")
    first = a.update(D.MATRIX[D.EDGE_DOWN])
    check("entering a mode announces it", first == D.MATRIX[D.EDGE_DOWN].said, first)
    check("...and does not repeat", a.update(D.MATRIX[D.EDGE_DOWN]) == "")
    check("...even after several ticks",
          [a.update(D.MATRIX[D.EDGE_DOWN]) for _ in range(5)] == [""] * 5)
    worse = a.update(D.MATRIX[D.NETWORK_DOWN])
    check("getting worse is announced", "lost the network" in worse, worse)
    back = a.update(D.MATRIX[D.FULL])
    check("recovery is announced", back.startswith("Back to normal"), back)
    check("...naming what came back", "internet" in back or "cloud models" in back, back)
    check("...and then goes quiet again", a.update(D.MATRIX[D.FULL]) == "")

    print("\n[matrix] one table, read by both hosts")
    shared = ROOT / "src/afon/shared/degraded.py"
    check("it lives in shared/, not in either host", shared.is_file())
    src = shared.read_text(encoding="utf-8")
    check("...and imports neither host", "afon.brain" not in src and "afon.edge" not in src,
          "a shared module that imports a host is not shared")
    check("the reason for one copy is written down", "Two copies" in src)

    brain = (ROOT / "src/afon/brain/health.py").read_text(encoding="utf-8")
    edge = (ROOT / "src/afon/edge/brain_client.py").read_text(encoding="utf-8")
    check("the brain reads the shared matrix", "from afon.shared.degraded import" in brain)
    check("the laptop reads the same one", "from afon.shared.degraded import" in edge)
    check("neither declares its own modes",
          "BRAIN_DOWN =" not in brain and "EDGE_DOWN =" not in edge)

    print("\n[announce] the brain announces within one turn, through the proactive tick")
    proactive = (ROOT / "src/afon/brain/proactive.py").read_text(encoding="utf-8")
    check("the degraded source is registered", "degraded_signals" in proactive)
    check("...as a source, not a one-off call", "sources.append(degraded_signals)" in proactive)

    from afon.brain import health as H

    check("the brain maps its own snapshot onto the matrix", hasattr(H, "mode_from"))
    check("a dead laptop reads as edge-down",
          H.mode_from({"pc_link": {"ok": False}, "ticker": {"ok": True},
                       "task_queue": {"ok": True}}).name == D.EDGE_DOWN)
    check("everything remote failing at once reads as the network",
          H.mode_from({"pc_link": {"ok": True}, "ticker": {"ok": False},
                       "task_queue": {"ok": False}}).name == D.NETWORK_DOWN,
          "three remote checks failing together is one cause, not three")
    check("a healthy host is in no mode at all",
          H.mode_from({"pc_link": {"ok": True}, "ticker": {"ok": True},
                       "task_queue": {"ok": True}}).name == D.FULL)

    # It must announce on the transition and then stop, driven by the real source.
    real_check = H.check
    try:
        async def down():
            return {"pc_link": {"ok": False}, "ticker": {"ok": True}, "task_queue": {"ok": True}}

        async def up():
            return {"pc_link": {"ok": True}, "ticker": {"ok": True}, "task_queue": {"ok": True}}

        H._DEGRADED = D.Announcer()
        H.check = down
        first = asyncio.run(H.degraded_signals())
        again = asyncio.run(H.degraded_signals())
        H.check = up
        recovered = asyncio.run(H.degraded_signals())
        check("entering a mode emits one signal", len(first) == 1, first)
        check("...carrying the spoken line", "laptop side is down" in first[0].message, first)
        check("...at an urgency that will actually be heard", first[0].urgency >= 0.7,
              first[0].urgency)
        check("the next tick emits nothing", again == [], again)
        check("recovery emits one more", len(recovered) == 1, recovered)
        check("...saying it is back", "Back to normal" in recovered[0].message, recovered)

        async def broken():
            raise RuntimeError("health check itself fell over")

        H.check = broken
        check("a failing health check degrades to silence, not to a crash",
              asyncio.run(H.degraded_signals()) == [])
    finally:
        H.check = real_check
        H._DEGRADED = D.Announcer()

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
