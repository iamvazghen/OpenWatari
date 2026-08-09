"""Phase 4.1 — multi-day objectives Afon owns and drives, hermetic.

Locks the store (assign/dedup/status/progress/persistence), the daily driver (advance one safe step,
capture deferred approvals, isolate failures) and the owner-facing tools — all with an injected fake
worker, no LLM.

    uv run python bench/test_objectives.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain.objectives import (  # noqa: E402
    ObjectiveBook, advance_objectives, spoken_objectives_report, _split_result,
)
import afon.brain.objectives as objmod  # noqa: E402
import afon.brain.tools.objectives as otools  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class FakeWorker:
    """Stands in for TaskWorker: returns a canned result, or raises if result is an Exception."""

    def __init__(self, result):
        self._r = result

    async def run(self, brief, on_progress=None):
        if isinstance(self._r, Exception):
            raise self._r
        return self._r


def tmp_book() -> ObjectiveBook:
    d = tempfile.mkdtemp()
    return ObjectiveBook(Path(d) / "objectives.json")


async def main() -> None:
    print("[1] store: assign / dedup / status / progress / persistence")
    b = tmp_book()
    o = b.assign("Get Party Map beta launch-ready", project="party")
    check("assign creates an active objective", o.status == "active" and o.project == "party")
    check("slug id is stable", o.id == "get-party-map-beta-launch-ready")
    o2 = b.assign("Get Party Map beta launch-ready")
    check("same text re-assigns, no duplicate", o2.id == o.id and len(b.active()) == 1)
    b.assign("Ship Rently migration 0008")
    check("second objective tracked", len(b.active()) == 2)

    b.append_progress(o.id, "Drafted the store listing copy", deferred=["publish_page(id=party)"])
    check("progress appended", b.get(o.id).progress[-1]["note"] == "Drafted the store listing copy")
    check("deferred captured", b.get(o.id).deferred == ["publish_page(id=party)"])
    b.append_progress(o.id, "again", deferred=["publish_page(id=party)"])
    check("deferred dedups", b.get(o.id).deferred == ["publish_page(id=party)"])
    for i in range(25):
        b.append_progress(o.id, f"note {i}")
    check("progress log capped at 20", len(b.get(o.id).progress) == 20)

    b.set_status(o.id, "done")
    check("done removes from active", all(x.id != o.id for x in b.active()))
    reloaded = ObjectiveBook(b._path)
    check("persists + reloads", reloaded.get(o.id) is not None and reloaded.get(o.id).status == "done")

    print("\n[2] find + active ordering")
    b2 = tmp_book()
    a = b2.assign("Launch the podcast studio")
    c = b2.assign("Finish the crypto audit")
    a.updated = "2026-07-21T10:00:00"; c.updated = "2026-07-21T09:00:00"; b2._save()
    check("active() least-recently-advanced first", [x.id for x in b2.active()] == [c.id, a.id])
    check("find by word resolves one", b2.find("crypto") is not None and b2.find("crypto").id == c.id)
    check("find ambiguous/none -> None", b2.find("the") is None and b2.find("nope") is None)

    print("\n[3] _split_result")
    s, d = _split_result("Drafted copy and tested the build. Needs your approval: publish_page(id=x); send_email(to=y)")
    check("summary split from approvals", s == "Drafted copy and tested the build." and d == ["publish_page(id=x)", "send_email(to=y)"])
    s2, d2 = _split_result("Just did some safe research.")
    check("no marker -> empty deferred", s2 == "Just did some safe research." and d2 == [])

    print("\n[4] driver: advance one safe step, capture deferred, isolate failures")
    b3 = tmp_book()
    ob = b3.assign("Get Party Map beta launch-ready")
    moved = await advance_objectives(
        None, {}, [], book=b3,
        worker_factory=lambda o: FakeWorker("Wrote the FAQ page. Needs your approval: publish_page(id=party)"),
    )
    check("driver returns what moved", len(moved) == 1 and moved[0]["id"] == ob.id)
    check("progress logged from worker", b3.get(ob.id).progress[-1]["note"] == "Wrote the FAQ page.")
    check("deferred recorded on objective", b3.get(ob.id).deferred == ["publish_page(id=party)"])

    b4 = tmp_book()
    for i in range(3):
        b4.assign(f"Objective number {i}")
    moved2 = await advance_objectives(None, {}, [], book=b4, max_objectives=2,
                                      worker_factory=lambda o: FakeWorker("did a step"))
    check("max_objectives caps the pass", len(moved2) == 2)

    b5 = tmp_book()
    b5.assign("First objective")
    b5.assign("Second objective")
    calls = {"n": 0}

    def flaky(o):
        calls["n"] += 1
        return FakeWorker(RuntimeError("boom") if calls["n"] == 1 else "second advanced")

    moved3 = await advance_objectives(None, {}, [], book=b5, max_objectives=2, worker_factory=flaky)
    check("one worker failure doesn't stop the rest", len(moved3) == 1 and moved3[0]["summary"] == "second advanced")

    print("\n[5] spoken report")
    check("empty -> no report", spoken_objectives_report([]) == "")
    one = spoken_objectives_report([{"id": "a", "text": "Launch X", "summary": "drafted copy"}])
    check("one objective -> single line", "Launch X" in one and "drafted copy" in one)
    many = spoken_objectives_report([{"id": "a", "text": "X", "summary": "s1"}, {"id": "b", "text": "Y", "summary": "s2"}])
    check("many -> combined line", "X" in many and "Y" in many)

    print("\n[6] owner-facing tools")
    tb = tmp_book()
    objmod.OBJECTIVES = tb
    otools.OBJECTIVES = tb  # tools bound the singleton at import — repoint both
    r = await otools.assign_objective({"objective": "Get the NBA portfolio deployed", "project": "nba"})
    check("assign tool confirms ownership", "take that on" in r.lower() and len(tb.active()) == 1)
    r = await otools.list_objectives({})
    check("list tool shows the objective", "NBA portfolio" in r)
    tb.append_progress(tb.active()[0].id, "Set up the Vercel project", deferred=["push(repo=nba)"])
    r = await otools.objective_status({"topic": "nba"})
    check("status tool reads latest + approvals", "Set up the Vercel project" in r and "push(repo=nba)" in r)
    r = await otools.complete_objective({"topic": "nba"})
    check("complete tool marks done", "done" in r.lower() and len(tb.active()) == 0)
    r = await otools.assign_objective({})
    check("assign with no text asks", "what objective" in r.lower())
    r = await otools.list_objectives({})
    check("list empty -> guidance", "haven't handed me any" in r.lower())

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
