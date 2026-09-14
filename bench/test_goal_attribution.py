"""33.F3 — every task can name the objective it serves, or is explicitly ad-hoc.

Afon held objectives and he held a to-do list, and nothing connected them. So "am I actually
working towards anything" had no answer: a week of tasks could be entirely unrelated to every
objective he was driving and no surface would have said so.

What this asserts:

  * a task carries the objective it serves, and the attribution is never guessed from the title;
  * an objective named but not resolvable is REFUSED, not silently dropped — a discarded
    attribution reads afterwards as ad-hoc work, and the owner has no way to see it went missing;
  * "ad-hoc" is a real answer rather than a blank, because not everything he does should serve a
    standing objective;
  * the review names an objective nothing on the list is serving, which is the reading that
    changes what he does next.

01.R3 adds the TURN half. A task carries a declared `meta.objective` and the attribution above
refuses to guess from the title — rightly, because a task is filed once and read for months. A turn
has no such field and never will, so the choice there is between inferring and having no answer at
all, and "why are you doing that?" answered with plausible prose is the failure. So the inference is
dull on purpose (two or more distinctive words shared with the objective's own text), ad-hoc is a
real answer rather than a blank, and an attributed turn carries the objective's words into the
prompt while an ad-hoc one carries nothing.

Hermetic: a temp objective book and hand-built task stand-ins. No store, no LLM, no network.

    uv run python bench/test_goal_attribution.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
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


class FakeTask:
    """Just enough of a Task for attribution: a title and a meta dict."""

    def __init__(self, title: str, meta: dict | None = None) -> None:
        self.title = title
        self.meta = meta or {}
        self.id = "fake1234"
        self.priority = "normal"

    def human_deadline(self) -> str:
        return ""


async def main() -> None:
    from afon.brain.objectives import AD_HOC, ObjectiveBook, attribution, attribution_report

    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    book = ObjectiveBook(Path(tmp.name) / "objectives.json")
    party = book.assign("Get Party Map beta launch-ready")
    rently = book.assign("Ship the Rently migration")

    print("[1] a task names its objective, and an unattributed one says so")
    served = FakeTask("write the store listing", {"objective": party.id})
    loose = FakeTask("book the dentist")
    check("an attributed task names its objective", attribution(served) == party.id)
    check("an unattributed task is ad-hoc, not blank", attribution(loose) == AD_HOC)
    check("ad-hoc is a named value, not the empty string", AD_HOC and AD_HOC != "")
    # The attribution must come from the record, never from the words. A title that happens to
    # contain an objective's name is not evidence that it serves it, and guessing would make the
    # review confidently wrong in exactly the cases the owner would not check.
    looks_related = FakeTask("cancel my Party Map subscription")
    check("attribution is never inferred from the title", attribution(looks_related) == AD_HOC)
    check("a meta dict with an empty objective is still ad-hoc",
          attribution(FakeTask("x", {"objective": ""})) == AD_HOC)

    print("\n[2] the review groups open work under what it serves")
    todos = [served, loose, FakeTask("upload the screenshots", {"objective": party.id})]
    report = attribution_report(book, todos)
    check("the objective's own words head its group", "Get Party Map beta launch-ready" in report)
    check("both of its tasks are under it",
          "write the store listing" in report and "upload the screenshots" in report)
    check("the ad-hoc pile is counted and named",
          "1 not tied to an objective" in report and "book the dentist" in report, report)

    print("\n[3] an objective nothing is serving is the finding that matters")
    check("...and it is named outright",
          "Nothing on your list serves: Ship the Rently migration" in report, report)
    both = attribution_report(book, todos + [FakeTask("run 0008", {"objective": rently.id})])
    check("once served, it stops being reported as unserved",
          "Nothing on your list serves" not in both, both)
    check("an empty list says so rather than reporting nothing",
          "Nothing open" in attribution_report(book, []))

    print("\n[4] a task pointing at an objective Afon no longer holds is not silently dropped")
    orphan = attribution_report(book, [FakeTask("old work", {"objective": "some-dead-goal"})])
    check("the dead objective id is still shown", "some-dead-goal" in orphan, orphan)
    check("...and flagged as no longer held", "no longer hold" in orphan, orphan)

    print("\n[5] the write path refuses an attribution it cannot resolve")
    import afon.brain.objectives as objmod
    import afon.brain.tools.tasks as ttools

    objmod.OBJECTIVES = book
    captured: dict = {}

    class FakeStore:
        def add_todo(self, title, **kw):
            captured.update(kw)
            captured["title"] = title
            return FakeTask(title, kw.get("meta"))

        def edit_todo(self, *a, **k):
            return None

    real_store = ttools.TASKS
    ttools.TASKS = FakeStore()
    try:
        r = await ttools.add_task({"title": "write the listing", "objective": "party"})
        check("a resolvable objective is stored on the task",
              captured.get("meta", {}).get("objective") == party.id, str(captured))
        check("...and the task is confirmed as added", "Added to your list" in r, r)

        captured.clear()
        r2 = await ttools.add_task({"title": "something", "objective": "a goal I never set"})
        check("an unresolvable objective is REFUSED, not dropped",
              "couldn't match" in r2 and "title" not in captured, r2)
        check("...and the refusal offers the ad-hoc route", "ad-hoc" in r2, r2)

        captured.clear()
        await ttools.add_task({"title": "book the dentist"})
        check("no objective given is fine — the task is filed ad-hoc",
              captured.get("meta") == {}, str(captured))
    finally:
        ttools.TASKS = real_store

    print("\n[6] 01.R3 — the same question asked of a TURN rather than a task")
    from afon.brain.objectives import turn_attribution, why_this_turn

    # `book` above already holds "Get Party Map beta launch-ready" and "Ship the Rently migration".
    for text, want in [
        ("what's left before the Party Map beta ships", party.id),
        ("any blockers on the party map launch", party.id),
        ("how far along is the Rently migration", rently.id),
    ]:
        got = turn_attribution(text, book)
        check(f"{text!r} names its objective", got == want, f"got {got}")
    for text in ["what time is it", "when is the spacex launch", "remind me to call mum",
                 "thanks", ""]:
        check(f"{text!r} is ad-hoc", turn_attribution(text, book) == AD_HOC,
              f"got {turn_attribution(text, book)}")
    check("one shared word is never enough on its own",
          turn_attribution("when is the launch", book) == AD_HOC,
          "a classifier that finds an objective for every turn tells him what he wants to hear")
    check("identical turns attribute identically",
          len({turn_attribution("any blockers on the party map launch", book)
               for _ in range(5)}) == 1)

    note = why_this_turn("what's left before the Party Map beta ships", book)
    check("an attributed turn carries the objective's OWN WORDS, not its id",
          "Party Map beta launch-ready" in note and party.id not in note)
    check("...and says to answer 'why are you doing that' with it", "if he asks" in note.lower())
    check("an ad-hoc turn carries nothing — most turns serve nothing standing",
          why_this_turn("what time is it", book) == "")

    class _Dropped:
        """active() still lists it; get() can no longer produce it — the daily driver can
        complete or drop an objective between the two calls, and naming an objective he no longer
        holds is worse than saying nothing."""

        def active(self):
            return book.active()

        def get(self, _oid):
            return None

    check("an objective dropped mid-turn degrades to silence, not a wrong name",
          why_this_turn("what's left before the Party Map beta ships", _Dropped()) == "")

    src = (Path(__file__).resolve().parents[1] / "src/afon/brain/agent.py").read_text(
        encoding="utf-8")
    check("_prepare_turn consults it", "why_this_turn(user_text)" in src)
    _i = src.index("why_this_turn(user_text)")
    check("...inside a try/except — an unreadable book is not a reason to lose the turn",
          "except Exception" in src[_i:_i + 400])
    check("...and appends only when there is something to say", "if goal_note:" in src[_i:_i + 400])

    print("\n[7] the tools are registered")
    from afon.brain.tools import tool_names

    names = set(tool_names())
    for t in ("set_milestone", "objective_review"):
        check(f"{t} is registered", t in names)
    schema = next(s for s in ttools.SCHEMAS if s["function"]["name"] == "add_task")
    check("add_task advertises the objective field",
          "objective" in schema["function"]["parameters"]["properties"])

    tmp.cleanup()
    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
