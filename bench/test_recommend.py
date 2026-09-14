"""15.F1-F3 — one domain, a reason that cites its data, and a refusal to invent taste.

The plan's instruction is log first, and it is right: a recommender trained on a handful of events
is a random number generator with a confidence interval. So there is no ranker — there are rules
the owner can read, and a decisions table so that in six months something can be trained, replayed
or argued with.

  [reason]   every recommendation names the rule that decided it AND the data that rule read, so it
             can be checked rather than merely believed;
  [logged]   the choice, the rule, and everything it was chosen over go into a ledger before the
             answer is spoken, with a slot for whether the owner actually did it;
  [no-basis] when nothing distinguishes the open work, the answer is that there is no basis — not
             the first item in list order, presented as judgement.

Hermetic: fake to-dos and a temporary ledger. No network, no model.

    uv run python bench/test_recommend.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from dataclasses import dataclass, field
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


@dataclass
class T:
    """The shape `TaskQueue.todos()` returns, no more of it than the ranker reads."""

    id: str
    title: str
    status: str = "open"
    deadline: float | None = None
    priority: str = "normal"
    prio_rank: int = 1
    meta: dict = field(default_factory=dict)


def main() -> None:
    from afon.brain import recommend as R

    now = time.time()
    plain = [T("a", "tidy the garage"), T("b", "read the manual")]

    print("[no-basis] 15.F3 — nothing to go on is an answer, not a silence to fill")
    empty = R.next_task([], now=now)
    check("an empty list declines", isinstance(empty, R.NoBasis), empty)
    check("...saying what is missing", "nothing open" in empty.why, empty.why)
    check("...in the owner's words", "no basis" in empty.spoken(), empty.spoken())

    flat = R.next_task(plain, now=now)
    check("indistinguishable work declines too", isinstance(flat, R.NoBasis), flat)
    check("...naming why they cannot be told apart", "look the same" in flat.why, flat.why)
    check("...and listing what it looked at", "2 open items" in flat.why, flat.why)
    check("it does NOT fall back to the first in list order",
          "tidy the garage" not in flat.spoken(), flat.spoken())
    check("done items are not open work",
          isinstance(R.next_task([T("a", "x", status="done")], now=now), R.NoBasis))

    print("\n[reason] 15.F1 — the rule that decided it, and the data it read")
    late = T("c", "the water system", deadline=now - 2 * 86400)
    rec = R.next_task([*plain, late], now=now)
    check("an overdue item wins", rec.choice == "the water system", rec)
    check("...by the overdue rule", rec.rule == R.OVERDUE, rec.rule)
    check("...and the reason says how overdue", "overdue by 2 days" in rec.reason, rec.reason)
    check("the basis cites the field it read", "deadline" in rec.basis[0], rec.basis)
    check("...naming the item", "the water system" in rec.basis[0], rec.basis)
    check("what it was chosen over is kept", len(rec.candidates) == 3, rec.candidates)
    check("the spoken answer carries the reason", "overdue" in rec.spoken(), rec.spoken())
    check("...and says how much it considered", "3 open items" in rec.spoken(), rec.spoken())

    print("\n[reason] the rules run strongest first, and each says which one it was")
    soon = T("d", "the feed order", deadline=now + 2 * 3600)
    r2 = R.next_task([*plain, soon], now=now)
    check("with nothing overdue, due-soonest wins", r2.rule == R.DUE_SOON, r2)
    check("...and says when", "due in 2 hours" in r2.reason, r2.reason)
    r3 = R.next_task([*plain, soon, late], now=now)
    check("overdue still beats due-soon", r3.choice == "the water system", r3)

    obj = T("e", "draft the brief", meta={"objective": "obj-1"})
    r4 = R.next_task([*plain, obj], active_objectives={"obj-1"}, now=now)
    check("with no deadlines at all, objective work wins", r4.rule == R.SERVES_OBJECTIVE, r4)
    check("...naming the objective", "obj-1" in r4.reason, r4.reason)
    check("an objective nobody is driving does not count",
          isinstance(R.next_task([*plain, obj], active_objectives=set(), now=now), R.NoBasis))
    check("a deadline still outranks an objective",
          R.next_task([*plain, obj, soon], active_objectives={"obj-1"}, now=now).rule == R.DUE_SOON)

    urgent = T("f", "call the vet", priority="urgent", prio_rank=3)
    r5 = R.next_task([*plain, urgent], now=now)
    check("priority is the last rule, not the first", r5.rule == R.PRIORITY, r5)
    check("...and says what was marked", "urgent" in r5.reason, r5.reason)
    check("equal priorities are not a reason",
          isinstance(R.next_task([T("a", "x", prio_rank=2), T("b", "y", prio_rank=2)], now=now),
                     R.NoBasis))

    print("\n[logged] 15.F2 — written down before it is spoken")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        led = Path(d) / "rec.jsonl"
        R.log(rec, led)
        rows = R.rows(led)
        check("the recommendation is on the record", len(rows) == 1, rows)
        check("...with the rule that decided it", rows[0]["rule"] == R.OVERDUE, rows[0])
        check("...with its reason", rows[0]["reason"] == rec.reason, rows[0])
        check("...with everything it was chosen over", rows[0]["candidates"] == rec.candidates)
        check("...and an empty outcome, waiting to be filled", rows[0]["outcome"] == "", rows[0])
        check("nothing is scored until it is", R.acceptance(led)["scored"] == 0)

        check("recording that he took it works", R.outcome(rec.key, True, led))
        check("...and it counts", R.acceptance(led)["taken"] == 1, R.acceptance(led))
        check("an unknown key changes nothing", not R.outcome("next-task:nope", True, led))
        check("the same one is not scored twice", not R.outcome(rec.key, False, led))

        R.log(r2, led)
        R.outcome(r2.key, False, led)
        acc = R.acceptance(led)
        check("acceptance is broken down by rule", set(acc["by_rule"]) == {R.OVERDUE, R.DUE_SOON},
              acc)
        check("...which is the column a ranker would train on",
              acc["by_rule"][R.OVERDUE]["taken"] == 1 and acc["by_rule"][R.DUE_SOON]["taken"] == 0,
              acc)
        with led.open("a", encoding="utf-8") as fh:
            fh.write('{"key": "torn')
        check("a torn line does not lose the log", len(R.rows(led)) == 2, R.rows(led))

    print("\n[logged] the tool logs before it speaks, and declines out loud")
    from afon.brain.tools import groups_for_text, tool_handlers
    from afon.brain.tools.day_shape import recommend_next

    check("the tool is registered", "recommend_next" in tool_handlers())
    for phrase in ("what should I do next", "where do I start", "what should I work on",
                   "what's most important right now"):
        check(f"'{phrase}' reaches it", "dayshape" in groups_for_text(phrase.lower()),
              groups_for_text(phrase.lower()))
    check("an ordinary turn does not load it", "dayshape" not in groups_for_text("what's the time"))

    src = (Path(__file__).resolve().parents[1]
           / "src/afon/brain/tools/day_shape.py").read_text(encoding="utf-8")
    body = src.split("async def recommend_next", 1)[1].split("SCHEMAS = [")[0]
    check("it logs the recommendation", "recommend.log(out)" in body)
    check("...only when there was one to log", "isinstance(out, recommend.Recommendation)" in body)
    check("the schema tells the model to relay the reason", "Relay the REASON" in src)
    check("...and not to substitute its own pick when there is no basis",
          "rather than picking something yourself" in src)

    said = asyncio.run(recommend_next({}))
    check("calling it with a real (possibly empty) list still answers in prose",
          isinstance(said, str) and said.strip() != "", said)

    print("\n[logged] the log is declared, and no ranker was smuggled in")
    from afon.brain.inventory import by_name

    check("the decisions log is in the data inventory",
          by_name("recommendations.jsonl") is not None)
    rsrc = (Path(__file__).resolve().parents[1]
            / "src/afon/brain/recommend.py").read_text(encoding="utf-8")
    check("no model is called to rank", "llm" not in rsrc.lower().split('"""')[2])
    check("the declined options are recorded, with why",
          "random number generator" in rsrc and "log first" in rsrc.lower())

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
