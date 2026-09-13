"""45.F3 — "why did you say that" is answered from the record, not from the model's memory.

Asked of the model, "why did you say that" is answered by a model reconstructing its own reasoning
after the fact — the one source on the subject with no access to the facts. It will name a tool it
did not call and a site it did not read, fluently, because the question invites a story and nothing
contradicts it.

The turn row has the facts: which tools actually fired, which sites were actually retrieved, and
whether the answer was hedged or put flat. It had the first of those already; 45.F3 adds the other
two, because "what did you do" and "should I believe it" are different questions and only the
first was answerable.

What this asserts:

  * the row records sources and a confidence label, and both survive into the persisted row;
  * an answer reached with NO tool says so — the single most useful thing when the owner doubts one;
  * confidence is read off the reply, never invented;
  * a failed turn says it failed;
  * asking before any turn has finished gets an honest nothing, not a fabricated account;
  * it costs no per-turn tool surface: `why` rides the diagnose tool the owner already has.

Hermetic: no LLM, no network.

    uv run python bench/test_why_last_turn.py
"""

from __future__ import annotations

import asyncio
import sys
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


def why() -> str:
    from afon.brain.tools.diagnose import diagnose

    return asyncio.run(diagnose({"about": "last_turn"}))


def main() -> None:
    from afon.brain import citations as C
    from afon.brain import turn_trace as tt

    print("[1] before anything has happened, he says so rather than inventing an account")
    tt._RING.clear()
    said = why()
    check("no finished turn means no explanation", "nothing to explain" in said, said)
    check("...and no tool is named anyway", "used" not in said, said)

    print("\n[2] a turn with tools and sources answers all three questions")
    C.start()
    with tt.turn("what did the CPI do"):
        tt.fired("web_search")
        tt.fired("scrape_url")
        tt.fired("web_search")           # repeated: named once, not three times
        C.record("https://www.reuters.com/markets/cpi", "CPI")
        C.record("https://ec.europa.eu/eurostat/x")
        tt.note_answer("Inflation eased to 2.1 percent, sir.")
    said = why()
    check("the tools that fired are named", "web_search" in said and "scrape_url" in said, said)
    check("...each once, however often it fired", said.count("web_search") == 1, said)
    check("the sites actually read are named", "reuters.com" in said, said)
    check("...all of them", "europa.eu" in said, said)
    check("a flat answer is reported as meant-as-fact", "put it flatly" in said, said)
    check("...and how long the turn took", "seconds" in said, said)

    print("\n[3] an answer with NO tool behind it says exactly that")
    # The most useful sentence in this whole feature. An answer the model produced from its own
    # weights looks identical to one it looked up, and the owner has no other way to tell.
    C.start()
    with tt.turn("what is the capital of Japan"):
        tt.note_answer("Tokyo, sir.")
    said = why()
    check("no tools is stated, not omitted", "no tools" in said, said)
    check("...and credited to what he already knew", "already knew" in said, said)
    check("...and nothing was read", "read nothing" in said, said)

    print("\n[4] confidence is READ off the reply, not invented")
    C.start()
    with tt.turn("will it rain on Friday"):
        tt.note_answer("I'd be guessing, sir — the forecast that far out isn't reliable.")
    check("a hedged answer is reported as a guess", "best guess" in why(), why())
    C.start()
    with tt.turn("what time is it"):
        tt.note_answer("Ten past four, sir.")
    check("a flat answer is not called a guess", "best guess" not in why(), why())
    row = tt.last()
    check("the label is one of two honest values, never a made-up number",
          row["confidence"] in ("hedged", "flat"), row["confidence"])

    print("\n[5] a turn that went wrong says it went wrong")
    C.start()
    try:
        with tt.turn("do the thing"):
            tt.fired("run_powershell")
            raise RuntimeError("the laptop dropped")
    except RuntimeError:
        pass
    said = why()
    check("the failure is surfaced", "went wrong" in said, said)
    check("...naming it", "laptop dropped" in said, said)

    print("\n[6] the record is complete and persists")
    C.start()
    with tt.turn("x"):
        C.record("https://example.com/a")
        tt.note_answer("Yes, sir.")
    row = tt.last()
    for key in ("tools_fired", "sources", "confidence", "total_ms", "ok"):
        check(f"the row carries `{key}`", key in row and row[key] is not None, row)
    check("sources are hosts, not full URLs — that is what a spoken citation names",
          row["sources"] == ["example.com"], row["sources"])
    check("the row still passes the completeness gate 01.F3 applies",
          tt.is_complete(row), row)

    print("\n[7] it costs nothing per turn")
    from afon.brain.tools import core_tool_schemas

    names = {s["function"]["name"] for s in core_tool_schemas()}
    check("no new tool was added for it", "why" not in names and "why_last_turn" not in names)
    check("...it rides the diagnostics tool he already has", "diagnose" in names)
    schema = next(s for s in core_tool_schemas() if s["function"]["name"] == "diagnose")
    props = schema["function"]["parameters"]["properties"]
    check("the parameter is documented for the model", "about" in props, sorted(props))
    check("...and the description tells it when to reach for it",
          "why did you say that" in schema["function"]["description"].lower(),
          schema["function"]["description"])

    print("\n[8] explaining a turn can never break one")
    saved = tt.last
    try:
        tt.last = _boom
        out = asyncio.run(_diag())
        check("a broken record degrades to spoken prose, not an exception",
              "couldn't complete" in out and "last answer" in out, out)
        check("...and never raises into the turn", isinstance(out, str))
    finally:
        tt.last = saved

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


def _boom():
    raise RuntimeError("ring buffer gone")


async def _diag():
    from afon.brain.tools.diagnose import diagnose

    return await diagnose({"about": "last_turn"})


if __name__ == "__main__":
    main()
