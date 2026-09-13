"""39.F2/F3 — nothing is handed off untracked, and nothing comes back unchecked.

Two real defects sit behind this gate.

**Untracked.** `delegate_to_fleet` was a string in and a string out. The background path wrapped it
in a `TaskQueue` row, and `TaskQueue.drop` deletes that row the instant the task finishes — so a
delegation that SUCCEEDED left no trace at all. Nothing could say what had been handed over, to
whom, or whether an answer ever came back.

**Unchecked.** Whatever the team lead returned went straight on: into the model's context, or into
`server._announce_task`, which reads `t.result` out loud. "I don't have access to that" therefore
reached the owner as Afon's own finished answer, and a figure the fleet invented reached him in
Afon's voice with nothing marking it as somebody else's claim.

  [tracking]     every delegation opens a unit with a deadline and closes it — answered or failed;
                 a unit still open past its deadline is the fire-and-forget detector.
  [verification] the answer is graded before any of it is reported as fact, and a rejected answer
                 is never dressed up as a finding.

Hermetic: a temporary sqlite file and a fake delegate. No fleet, no network.

    uv run python bench/test_delegation_tracking.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

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


BRIEF = "find out what rabbit feed costs at the Lpstrak farm this season"


def main() -> None:
    from afon.brain import coordination as C
    from afon.brain import delegation as D

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = Path(d) / "work.sqlite"

        print("[tracking] 39.F2 — a delegation is a unit with an owner and a deadline")
        uid = D.start(BRIEF, timeout_s=30, path=db)
        check("handing work over opens a tracked unit", bool(uid), uid)
        unit = C.get(uid, path=db)
        check("...recording the brief verbatim", unit.brief == BRIEF, unit.brief)
        check("...with an owner, not sitting unclaimed",
              unit.owner == D.FLEET and unit.state == C.HELD, unit)
        check("...and a deadline beyond the transport's own ceiling",
              unit.deadline_at > time.time() + 30, unit.deadline_at - time.time())
        check("it is outstanding while it runs", [x.id for x in D.outstanding(db)] == [uid])
        check("...but not yet late", not C.get(uid, path=db).late())

        print("\n[tracking] the unit closes either way — that is what makes 'late' mean something")
        v = D.finish(uid, BRIEF, "Feed is a shade dearer than last season, sir.", path=db)
        check("an answered delegation closes", C.get(uid, path=db).state == C.DONE)
        check("...with the answer in the result slot", C.get(uid, path=db).result.startswith("Feed"))
        check("...and the verdict alongside it", C.get(uid, path=db).verdict == v.grade, v)
        check("...and it is no longer outstanding", D.outstanding(db) == [])

        bad = D.start(BRIEF, timeout_s=30, path=db)
        D.failed(bad, "FleetUnavailable: gateway refused", path=db)
        closed = C.get(bad, path=db)
        check("a delegation that errored closes as failed", closed.state == C.FAILED, closed.state)
        check("...naming what went wrong", "gateway refused" in closed.result, closed.result)
        check("...and is not left outstanding forever", D.outstanding(db) == [])

        print("\n[tracking] a runner that never writes back is caught by the deadline")
        lost = C.post(BRIEF, job="lost", deadline_s=0.01, path=db)[0]
        C.claim("lost", D.FLEET, path=db)
        time.sleep(0.05)
        late = [x for x in D.outstanding(db) if x.late()]
        check("an abandoned delegation shows up as late", [x.id for x in late] == [lost], late)
        check("...and says how long ago it was due", C.get(lost, path=db).deadline_at > 0)

        print("\n[tracking] the wrapper closes the unit on every path")

        async def _ok():
            return "Feed is a shade dearer than last season, sir."

        async def _boom():
            raise RuntimeError("the gateway went away")

        # `lost#0` above was abandoned on purpose and must stay outstanding; nothing else may.
        still_open = lambda: [x.id for x in D.outstanding(db) if x.id != lost]  # noqa: E731
        out = asyncio.run(D.tracked(BRIEF, _ok, timeout_s=5, path=db))
        check("a successful delegation returns a graded result", "DELEGATED_RESULT" in out, out[:80])
        check("...and leaves nothing outstanding", still_open() == [], still_open())

        raised = False
        try:
            asyncio.run(D.tracked(BRIEF, _boom, timeout_s=5, path=db))
        except RuntimeError:
            raised = True
        check("a failing delegation still reaches the caller", raised)
        check("...and still leaves nothing outstanding", still_open() == [], still_open())
        check("the failure is on the record, not just in a log",
              any(u.state == C.FAILED and "gateway went away" in u.result
                  for u in C.recent(path=db)),
              [(u.state, u.result[:40]) for u in C.recent(path=db)])

        print("\n[tracking] the record survives the task queue that used to erase it")
        # TaskQueue.drop deletes the background row on completion. The point of a separate record
        # is that a finished delegation is still there afterwards to be reviewed (39.E1).
        log = C.recent(path=db)
        check("every delegation this run is still on the record", len(log) >= 5, len(log))
        check("...newest first", [u.created_at for u in log] == sorted(
            (u.created_at for u in log), reverse=True))
        check("...each naming what was asked and what came of it",
              all(u.brief and u.state for u in log))

    print("\n[verification] 39.F3 — graded before any of it is reported as fact")
    check("an empty answer is rejected", D.check(BRIEF, "").grade == D.REJECTED)
    check("...and a one-word one", D.check(BRIEF, "ok").grade == D.REJECTED)
    for excuse in ("I don't have access to the farm's records.",
                   "I'm unable to retrieve that information right now.",
                   "As an AI, I cannot browse supplier pricing.",
                   "Error: the search tool returned nothing."):
        check(f"a refusal is not an answer: {excuse[:34]!r}",
              D.check(BRIEF, excuse).grade == D.REJECTED, D.check(BRIEF, excuse))
    off = "Berlin will be mild and dry through the weekend, with light winds."
    check("an answer to a different question is rejected",
          D.check(BRIEF, off).grade == D.REJECTED, D.check(BRIEF, off))
    check("...and says why", "doesn't address" in D.check(BRIEF, off).why)

    figures = "Rabbit feed at the Lpstrak farm runs about 19.50 a sack this season."
    check("an answer carrying figures is attributed, not asserted",
          D.check(BRIEF, figures).grade == D.ATTRIBUTED, D.check(BRIEF, figures))
    linked = "Feed for the rabbit farm is listed at https://example.com/feed, sir."
    check("...so is one carrying a link", D.check(BRIEF, linked).grade == D.ATTRIBUTED)
    plain = "Feed at the rabbit farm is a shade dearer than it was last season, sir."
    check("a plain answer to the brief is verified", D.check(BRIEF, plain).grade == D.VERIFIED,
          D.check(BRIEF, plain))
    check("a short brief is not used to reject a long answer",
          D.check("go", "Everything is in hand and the order has gone out, sir.").grade
          != D.REJECTED)

    print("\n[verification] the grade changes what the model is allowed to say")
    rejected = D.for_model(D.check(BRIEF, "I cannot do that."), "I cannot do that.")
    check("a rejected answer is not handed over as a result",
          "DELEGATED_RESULT" not in rejected, rejected[:90])
    check("...it is named as no answer", "RETURNED_NOTHING" in rejected)
    check("...and the model is told not to invent one", "do NOT invent" in rejected.replace(
        "Do NOT invent", "do NOT invent"), rejected)
    attributed = D.for_model(D.check(BRIEF, figures), figures)
    check("an attributed answer carries the words themselves", figures in attributed)
    check("...marked as the team lead's, not Afon's",
          "[attributed]" in attributed and "not your own" in attributed, attributed[:120])
    check("...with an instruction not to restate a figure as checked",
          "restate a number" in attributed, attributed)
    verified = D.for_model(D.check(BRIEF, plain), plain)
    check("a verified answer may be relayed in his own words",
          "[verified]" in verified and plain in verified)

    print("\n[verification] the spoken version is the one that used to leak")
    said = D.for_owner(D.check(BRIEF, "I don't have access to that."), "I don't have access to that.")
    check("a refusal is not read out as a finished answer",
          "I don't have access" not in said, said)
    check("...the owner is told it came back empty", "without an answer" in said, said)
    said_attr = D.for_owner(D.check(BRIEF, figures), figures)
    check("figures are read out in the team lead's name",
          "team lead's report" in said_attr and figures in said_attr, said_attr[:120])
    said_ok = D.for_owner(D.check(BRIEF, plain), plain)
    check("a verified answer is spoken plainly, with no ceremony", said_ok == plain, said_ok)

    print("\n[tracking] every delegation path in the agent goes through it")
    agent_src = (Path(__file__).resolve().parents[1]
                 / "src/afon/brain/agent.py").read_text(encoding="utf-8")
    check("the blocking path is tracked",
          agent_src.count("delegation.tracked(") >= 3, agent_src.count("delegation.tracked("))
    raw = [ln.strip() for ln in agent_src.splitlines()
           if "delegate_to_fleet(" in ln and "lambda" not in ln and "import" not in ln
           and "FLEET_TOOL" not in ln]
    check("no call site reaches the fleet untracked", raw == [], raw)
    check("the background result is graded before it is announced",
          "spoken=True" in agent_src, "server._announce_task reads t.result out loud verbatim")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
