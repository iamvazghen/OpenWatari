"""48.F1-F3 — the shared work record: one owner per unit, and merges that keep disagreement.

There was no record. A delegation was a string in and a string out, and the background wrapper's
row is deleted the instant the work finishes, so the evidence was erased exactly when there was
something to record. This gate holds the replacement to three promises.

  [record]          every unit has an id, an owner, a state and a result slot, and only the holder
                    writes that slot;
  [no double claim] two workers racing for one unit produce one winner — asserted with real
                    threads, not by reading the SQL;
  [merge]           several answers become one result deterministically, and two workers who
                    disagree are REPORTED as disagreeing rather than averaged, picked between, or
                    quietly reduced to whichever row came back last.

Hermetic: a temporary sqlite file. No network, no fleet.

    uv run python bench/test_coordination.py
"""

from __future__ import annotations

import sys
import tempfile
import threading
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


def main() -> None:
    import time

    from afon.brain import coordination as C

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = Path(d) / "work.sqlite"

        print("[record] 48.F1 — an id, an owner, a state, a result slot")
        ids = C.post(["price the feed", "draft the order"], job="j1", deadline_s=60, path=db)
        check("posting returns one id per brief, in order", ids == ["j1#0", "j1#1"], ids)
        u = C.get("j1#0", path=db)
        check("a fresh unit is open and unowned", u.state == C.OPEN and u.owner == "", u)
        check("...with the brief it was posted with", u.brief == "price the feed", u.brief)
        check("...and a deadline", u.deadline_at > time.time(), u.deadline_at)
        check("...and an empty result slot", u.result == "" and u.verdict == "")
        check("an empty brief posts nothing", C.post(["", "   "], job="j0", path=db) == [])

        print("\n[record] the result slot belongs to the holder, and nobody else")
        held = C.claim("j1", "worker-a", path=db)
        check("claiming marks the owner", held.owner == "worker-a" and held.state == C.HELD, held)
        check("...and stamps when", held.claimed_at > 0)
        check("a non-holder cannot write the result",
              not C.finish(held.id, "worker-b", "hijacked", path=db))
        check("...and the slot is untouched", C.get(held.id, path=db).result == "")
        check("the holder can", C.finish(held.id, "worker-a", "nineteen euros a sack", path=db))
        done = C.get(held.id, path=db)
        check("...and the unit is done with its answer",
              done.state == C.DONE and done.result == "nineteen euros a sack", done)
        check("a finished unit cannot be finished again",
              not C.finish(held.id, "worker-a", "changed my mind", path=db))
        check("...so the first answer stands",
              C.get(held.id, path=db).result == "nineteen euros a sack")

        print("\n[record] a deadline makes 'late' a different answer from 'running'")
        late_ids = C.post("something slow", job="late", deadline_s=0.01, path=db)
        C.claim("late", "worker-a", path=db)
        time.sleep(0.05)
        check("a unit past its deadline is late", C.get(late_ids[0], path=db).late())
        check("...and is still counted as outstanding",
              late_ids[0] in [x.id for x in C.unfinished(path=db)])
        C.finish(late_ids[0], "worker-a", "took a while but here it is", path=db)
        check("a finished unit is never late, however slow",
              not C.get(late_ids[0], path=db).late())
        check("...and drops off the outstanding list",
              late_ids[0] not in [x.id for x in C.unfinished(path=db)])
        no_dl = C.post("no deadline given", job="nd", path=db)
        check("a unit with no deadline is not silently late", not C.get(no_dl[0], path=db).late())

        print("\n[no double claim] 48.F2 — eight threads, twenty units, no unit twice")
        # The load-bearing check. Reading the UPDATE and agreeing it looks atomic is not evidence;
        # this actually races it. A regression here (a read-then-write claim, say) shows up as the
        # same unit id appearing in two workers' lists.
        C.post([f"unit {i}" for i in range(20)], job="race", deadline_s=60, path=db)
        grabbed: list[tuple[str, str]] = []
        lock = threading.Lock()

        def worker(name: str) -> None:
            while True:
                got = C.claim("race", name, path=db)
                if got is None:
                    return
                with lock:
                    grabbed.append((got.id, name))

        threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        claimed = [uid for uid, _ in grabbed]
        check("every unit was claimed", len(claimed) == 20, len(claimed))
        check("no unit was claimed twice", len(set(claimed)) == len(claimed),
              sorted(u for u in set(claimed) if claimed.count(u) > 1))
        owners = {u.id: u.owner for u in C.units("race", path=db)}
        check("the record agrees with who claimed what",
              all(owners[uid] == who for uid, who in grabbed),
              [(uid, who, owners[uid]) for uid, who in grabbed if owners[uid] != who])
        check("more than one worker actually got some", len({w for _, w in grabbed}) > 1,
              {w for _, w in grabbed})
        check("claiming an exhausted job returns nothing, it does not block",
              C.claim("race", "w9", path=db) is None)
        check("claiming a job that does not exist returns nothing",
              C.claim("no-such-job", "w1", path=db) is None)

        print("\n[merge] 48.F3 — deterministic, and disagreement is surfaced not averaged")
        C.post(["what does the feed cost", "what does the feed cost", "write the summary"],
               job="m1", deadline_s=60, path=db)
        a = C.claim("m1", "alice", path=db)
        b = C.claim("m1", "bob", path=db)
        c = C.claim("m1", "carol", path=db)
        C.finish(a.id, "alice", "nineteen euros", path=db)
        C.finish(b.id, "bob", "twenty-one euros", path=db)
        C.finish(c.id, "carol", "the season looks dearer than last", path=db)

        m = C.merge("m1", path=db)
        check("a brief two workers answered differently is a conflict",
              [b for b, _ in m.conflicts] == ["what does the feed cost"], m.conflicts)
        check("BOTH answers survive", m.conflicts[0][1] == ["nineteen euros", "twenty-one euros"],
              m.conflicts)
        check("no third answer is invented", "twenty euros" not in m.report(), m.report())
        check("the conflicted brief is not also reported as settled",
              "what does the feed cost" not in [b for b, _ in m.parts], m.parts)
        check("the job is not called agreed", not m.agreed)
        check("the report says so in words", "DISAGREE" in m.report(), m.report())
        check("...and the spoken line refuses to split the difference",
              "split the difference" in m.spoken(), m.spoken())
        check("the brief only one worker answered is a plain part",
              m.parts == [("write the summary", "the season looks dearer than last")], m.parts)

        print("\n[merge] two workers who agree are one answer, not a conflict")
        C.post(["is the water system in", "is the water system in"], job="m2", deadline_s=60,
               path=db)
        for who in ("alice", "bob"):
            got = C.claim("m2", who, path=db)
            C.finish(got.id, who, "yes, it went in last week", path=db)
        m2 = C.merge("m2", path=db)
        check("identical answers collapse to one", m2.parts == [
            ("is the water system in", "yes, it went in last week")], m2.parts)
        check("...and the job is agreed", m2.agreed and m2.complete, m2)

        print("\n[merge] the order is the posting order, every time")
        C.post(["first", "second", "third"], job="m3", deadline_s=60, path=db)
        got = [C.claim("m3", f"w{i}", path=db) for i in range(3)]
        # finished in reverse, so posting order cannot be arrival order by luck
        for u, text in zip(reversed(got), ("third answer", "second answer", "first answer")):
            C.finish(u.id, u.owner, text, path=db)
        m3 = C.merge("m3", path=db)
        check("parts come back in the order the work was posted",
              [b for b, _ in m3.parts] == ["first", "second", "third"], m3.parts)
        check("...regardless of what order the answers arrived in",
              [r for _, r in m3.parts] == ["first answer", "second answer", "third answer"],
              m3.parts)
        check("merging twice gives the same text", C.merge("m3", path=db).report() == m3.report())

        print("\n[merge] what is missing and what failed are different things")
        C.post(["done one", "failed one", "never started"], job="m4", deadline_s=60, path=db)
        d1 = C.claim("m4", "alice", path=db)
        C.finish(d1.id, "alice", "here it is", path=db)
        d2 = C.claim("m4", "bob", path=db)
        C.fail(d2.id, "bob", "the source was down", path=db)
        m4 = C.merge("m4", path=db)
        check("a failed unit is named as failed, with why",
              m4.failed == [("failed one", "the source was down")], m4.failed)
        check("...not reported as missing", "failed one" not in m4.missing, m4.missing)
        check("an unclaimed unit is missing", m4.missing == ["never started"], m4.missing)
        check("the job is not called complete", not m4.complete)
        check("but the answer that did come back is still there",
              m4.parts == [("done one", "here it is")], m4.parts)
        check("one worker's failure does not lose the others",
              "here it is" in m4.report() and "the source was down" in m4.report(), m4.report())
        check("an empty job merges to nothing rather than raising",
              C.merge("no-such-job", path=db).report() == "")

    print("\n[layering] the record opens its database the one permitted way")
    src = (Path(__file__).resolve().parents[1]
           / "src/afon/brain/coordination.py").read_text(encoding="utf-8")
    check("it goes through dbconn.connect", "from afon.brain.dbconn import connect" in src)
    check("...and not sqlite3.connect directly", "sqlite3.connect" not in src)
    check("the store is declared in the data inventory",
          __import__("afon.brain.inventory", fromlist=["x"]).by_name("afon_work.sqlite") is not None)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
