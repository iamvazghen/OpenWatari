"""J2.8 — every autonomous job must REPORT what it did, and one of them never has.

The owner's standing rule is that an autonomous action is never silent: what Afon did, why, and his
reasoning. The scheduler's six `_fire_*` jobs are where that rule is actually kept or broken, and it
has been broken twice now in the same shape — work happens, a line goes into the log, the job counts
itself done, and the owner is told nothing.

`_fire_backlog` was the first (fixed earlier). Writing this test found the second, still live:

    sched = get_scheduler()
    if hasattr(sched, "_proactive") and sched._proactive: ...

`get_scheduler` does not exist in `brain/scheduler.py` and never has — the only reference to that
name in the whole repo was this call. The `NameError` was swallowed by a bare `except Exception:
pass`, so every weekly review fell through to a "fallback" whose comment said "log so the next
session surfaces it" — which nobody implemented. **The weekly memory review has never reached the
owner.** (`_proactive` is an attribute of BrainServer, not of the scheduler, so even a correct
`get_scheduler()` would not have found it.)

So this file checks the property rather than the instance:

  * every `_fire_*` job is DECLARED here as reporting or not — a new job cannot be added without
    someone deciding which it is;
  * each reporting job, given something to report, actually delivers;
  * no job hand-rolls the delivery ladder — that duplication is what let the three copies drift.

    uv run python bench/test_fire_paths_report.py
"""

from __future__ import annotations

import asyncio
import inspect
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

passed = failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


#: job -> (must it tell the owner?, why)
DECLARED = {
    "_fire_briefing":     (True,  "the morning catch-up IS the report"),
    "_fire_backlog":      (True,  "he did the owner's tasks unprompted — never silent"),
    "_fire_objectives":   (True,  "multi-day objectives advanced without being asked"),
    "_fire_weekly_review": (True, "it ASKS a question ('anything to correct?') — a question "
                                  "delivered to a log file is not a question"),
    "_fire_pattern_scan": (False, "internal learning; the facts surface when they're used"),
    "_fire_backup":       (False, "housekeeping — announcing a successful zip is noise"),
    # Reports only when it FAILS, which is the honest reading of J2.8 for a check rather than an
    # action. A daily "your backups still restore" is noise that trains the owner to skim past the
    # one morning it says the opposite; a failed restore drill means the archives are not
    # trustworthy, and that he must hear. `must_report` tracks the silent-success path, so False.
    "_fire_restore_drill": (False, "silent on success by design; it speaks up when a restore fails"),
}


def main() -> None:
    import afon.brain.scheduler as S

    src = inspect.getsource(S)
    jobs = re.findall(r"async def (_fire_\w+)", src)

    print("[1] every fire job has a declared reporting intent")
    check(set(jobs) == set(DECLARED),
          f"the {len(jobs)} jobs in the module are exactly the {len(DECLARED)} declared here",
          f"undeclared={sorted(set(jobs) - set(DECLARED))} stale={sorted(set(DECLARED) - set(jobs))}")

    print("\n[2] the delivery ladder lives in ONE place")
    # Three hand-rolled copies of emit -> speak -> push existed; the copies are how they drift, and
    # a job that grows its own copy is a job that can quietly stop delivering. Read each job's OWN
    # source — slicing the module from the first `_fire_` to the end also swallows the setter that
    # legitimately assigns _BRIEFING_EMIT.
    bodies = {j: inspect.getsource(getattr(S, j)) for j in jobs}
    for job, text in sorted(bodies.items()):
        check("_BRIEFING_EMIT" not in text and "notify import push" not in text,
              f"{job} delegates delivery to _emit_proactive",
              "it is hand-rolling the ladder again")

    print("\n[3] each reporting job actually delivers, given something to report")
    sent: list[tuple[str, float]] = []

    async def fake_emit(msg, urgency, speak):
        sent.append((msg, urgency))
        return "ok"

    class _Store:
        base = Path(ROOT / "does-not-exist")

        def recent_digest(self, limit=20):
            return ["he prefers oat milk", "the rent is due on the 3rd"]

    import afon.brain.daily_digest as DD
    import afon.brain.memory as M
    import afon.brain.objectives as OBJ

    saved = (S._BRIEFING_EMIT, S._LIVE_SPEAK, S._BACKLOG_RUNNER, S._BACKLOG_REPORTER,
             S._OBJECTIVES_RUNNER, DD.build_body, DD.mark_delivered, M.STORE,
             OBJ.spoken_objectives_report)
    try:
        S._BRIEFING_EMIT = fake_emit
        S._LIVE_SPEAK = None
        S._BACKLOG_RUNNER = lambda: [{"task": "pay the rent"}]
        S._BACKLOG_REPORTER = lambda done: "I cleared one task, sir."
        S._OBJECTIVES_RUNNER = lambda: [{"objective": "learn German"}]
        OBJ.spoken_objectives_report = lambda adv: "One objective moved, sir."
        DD.build_body = lambda: _coro("two things are past due")
        DD.mark_delivered = lambda *a, **k: None
        M.STORE = _Store()

        for job, (must_report, why) in sorted(DECLARED.items()):
            if not must_report:
                continue
            sent.clear()
            asyncio.run(getattr(S, job)())
            check(bool(sent), f"{job} reports to the owner  ({why})",
                  "it ran, logged, and told him nothing")

        print("\n[4] a job with nothing to say stays quiet")
        # The rule is "never silent about what he DID", not "always noisy".
        sent.clear()
        S._BACKLOG_RUNNER = lambda: []
        asyncio.run(S._fire_backlog())
        check(not sent, "a backlog pass that did nothing does not interrupt him")
        sent.clear()
        M.STORE = _Empty()
        asyncio.run(S._fire_weekly_review())
        check(not sent, "a week with no new facts does not ask him to review nothing")

        print("\n[5] a broken delivery must not take the job down with it")
        async def boom(msg, urgency, speak):
            raise RuntimeError("telegram is down")

        S._BRIEFING_EMIT = boom
        M.STORE = _Store()
        try:
            asyncio.run(S._fire_weekly_review())
            check(True, "a failing emitter is caught, not raised into the scheduler loop")
        except Exception as e:  # noqa: BLE001
            check(False, "a failing emitter is caught", f"{type(e).__name__}: {e}")
    finally:
        (S._BRIEFING_EMIT, S._LIVE_SPEAK, S._BACKLOG_RUNNER, S._BACKLOG_REPORTER,
         S._OBJECTIVES_RUNNER, DD.build_body, DD.mark_delivered, M.STORE,
         OBJ.spoken_objectives_report) = saved

    print("\n[6] the name that caused this is really gone")
    # A CALL, not the word: the comment recording what went wrong necessarily names it.
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    check("get_scheduler(" not in code,
          "no call to the undefined get_scheduler() remains",
          "the NameError would be swallowed and the report lost again")
    # The GENERAL case — any other undefined name — is gated by ruff F821 in
    # bench/test_static_correctness.py. A hand-rolled regex for it here emitted zero checks, which
    # is the silently-skipped test this repo already forbids elsewhere.

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


class _Empty:
    base = Path(ROOT / "does-not-exist")

    def recent_digest(self, limit=20):
        return []


async def _coro(v):
    return v


if __name__ == "__main__":
    main()
