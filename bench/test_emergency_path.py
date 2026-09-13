"""35.F1/35.F2 — the emergency path fires when it must and stays silent the rest of the time.

What existed was system-level: `ragnarok` and `phoenix` recover *Afon*. Nothing recovered the
owner. "Call an ambulance" reached the same machinery as "what's the weather" — a model round-trip,
a tool choice, a provider call — every one of which can be slow, wrong or down at the moment it
matters.

The plan's own test is stated here and this file is it:

  [false positives]   zero alarms across fifty ordinary prompts, including the near-misses that a
                      keyword matcher gets wrong ("this bug is killing me", "what do I do if
                      there's a fire", "in the film they call an ambulance");
  [contacts offline]  the ladder resolves from a local file with no network and no provider, and a
                      rung that cannot be reached is reported as not reached, never as done;
  [no LLM]            the turn path runs the classifier before it prepares a model call at all.

Hermetic: a temporary contact file and a stubbed transport. **Nothing is ever sent** — an earlier
version of the module's own self-check bound its stub to the wrong copy of the module and put two
real pushes on a real phone, so every exercise of the ladder here replaces `_do` in the module's
own globals and asserts it did.

    uv run python bench/test_emergency_path.py
"""

from __future__ import annotations

import asyncio
import inspect
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


#: Fifty ordinary things to say to an assistant. Roughly half are deliberate near-misses: they
#: contain an emergency word in a sentence that is not one. A keyword matcher fails most of these.
ORDINARY = [
    "what's the weather like today",
    "play something quiet",
    "remind me to call the dentist on Tuesday",
    "how long to the office right now",
    "this bug is killing me",
    "that deadline is giving me a heart attack",
    "I'm dying of laughter at this",
    "what do I do if there's a fire in the flat",
    "what should I do if someone breaks in while I'm away",
    "in the film they call an ambulance and nobody comes",
    "he said there's a fire at the depot",
    "the news said there was a crash on the A3",
    "I was in a car accident last year",
    "yesterday I thought I was having a heart attack but it was heartburn",
    "remind me to call the police about the parking",
    "what's the number for the fire brigade here",
    "should I call an ambulance for a nosebleed",
    "hypothetically, if I can't breathe, what do you do",
    "pretend there's an emergency and tell me what happens",
    "I want to practice the emergency drill some time",
    "add fire extinguisher to the shopping list",
    "how do I call the police in Germany",
    "read me the news",
    "summarise my inbox",
    "what's on my calendar tomorrow",
    "turn the lights down",
    "is the heating on",
    "how did the rabbit farm numbers look",
    "book me a table for Friday",
    "what did we decide about the water system",
    "I'm out of breath after those stairs",
    "my laptop crashed again",
    "the build crashed on CI",
    "I crashed out on the sofa",
    "my phone is on fire, it's so hot",
    "this track is fire",
    "the server's dying, can you check it",
    "I'm bleeding money on that subscription",
    "the meeting was a car crash",
    "my back is killing me after that flight",
    "someone broke into my Notion apparently",
    "did anyone break into the account last week",
    "what's an intruder detection system",
    "what time is my first meeting",
    "send a message about Thursday",
    "how many steps did I do",
    "what's the exchange rate",
    "tell me a joke about emergencies",
    "how do I stop the smoke alarm going off",
    "put the kettle on",
]

#: The real thing. Each of these must fire, and each names a category.
REAL = [
    ("call an ambulance, she's collapsed", "medical"),
    ("I can't breathe", "medical"),
    ("he's not breathing", "medical"),
    ("I think I'm having a heart attack", "medical"),
    ("I'm bleeding badly", "medical"),
    ("there's a fire", "fire"),
    ("the kitchen is on fire", "fire"),
    ("someone's breaking in", "intruder"),
    ("there's someone in the house", "intruder"),
    ("I've been in a car accident", "accident"),
]


def main() -> None:
    import afon.brain.emergency as E

    print(f"[false positives] 35.F1 — {len(ORDINARY)} ordinary prompts, zero alarms")
    check(f"the corpus is the {len(ORDINARY)} the plan asks for", len(ORDINARY) >= 50, len(ORDINARY))
    tripped = [(t, E.classify(t)) for t in ORDINARY]
    wrong = [(t, a.category, a.why) for t, a in tripped if a is not None]
    check("not one of them raises an alarm", not wrong, wrong)

    print("\n[false positives] ...and it still fires on the real thing")
    for text, want in REAL:
        got = E.classify(text)
        check(f"{text!r} -> {want}", got is not None and got.category == want,
              got.category if got else "no alarm")
    check("every alarm carries the phrase that tripped it",
          all(E.classify(t).why for t, _ in REAL))
    check("...quoted, so the owner can read what the rule matched",
          "'" in E.classify("call an ambulance, she's collapsed").why)

    print("\n[false positives] the manual phrase always works, guards and all")
    for text in ("Afon, emergency", "afon emergency", "emergency protocol",
                 "Afon, emergency — pretend I said nothing"):
        got = E.classify(text)
        check(f"{text!r} fires", got is not None and got.manual, got)
    check("a manual trigger with no category says so",
          E.classify("Afon, emergency").category == E.UNKNOWN)
    check("...and one that names a category uses it",
          E.classify("Afon emergency, there's a fire").category == E.FIRE)
    check("silence on an empty utterance", E.classify("") is None and E.classify("  ") is None)

    print("\n[contacts offline] 35.F2 — the ladder comes off disk, not off a network")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        p = Path(d) / "emergency.md"
        p.write_text(
            "# who to reach\n"
            "medical: call +491700000001 (next of kin), telegram @nextofkin, push\n"
            "fire: call 112 (fire service)\n"
            "default: push\n", encoding="utf-8")
        med = E.ladder(E.MEDICAL, path=p)
        check("the category's rungs come back in order",
              [s.action for s in med] == ["call", "telegram", "push"], med)
        check("...with the number to ring", med[0].target == "+491700000001", med[0])
        check("...and the name to say", med[0].label == "next of kin", med[0])
        check("an unlisted category falls back to default",
              [s.action for s in E.ladder("something else", path=p)] == ["push"])
        check("the file is what says it is configured", E.configured(p))
        check("no file at all still yields a rung rather than silence",
              [s.action for s in E.ladder(E.MEDICAL, path=Path(d) / "nope.md")] == ["push"])
        check("...and that host is reported as unconfigured",
              not E.configured(Path(d) / "nope.md"))
        check("a comment line is not a category", "# who to reach" not in E._read(p))

        print("\n[contacts offline] what was reached and what was not are never blurred")
        g = vars(E)
        real_do = g["_do"]
        calls: list[str] = []

        async def only_push_works(step, alarm, text):
            calls.append(f"{step.action}:{step.target}")
            return (step.action == "push", "" if step.action == "push" else "not set up here")

        try:
            g["_do"] = only_push_works
            said = asyncio.run(E.raise_alarm(E.Alarm(E.MEDICAL, "'call an ambulance'"),
                                             "call an ambulance", path=p))
        finally:
            g["_do"] = real_do

        check("nothing left this machine", calls and all(":" in c for c in calls), calls)
        check("every rung was tried, not just the first", len(calls) == 3, calls)
        check("the one that worked is claimed", "pushed to your phone" in said, said)
        check("the ones that did not are named",
              "could not" in said and "called next of kin" in said, said)
        check("...with the reason", "not set up here" in said, said)
        check("the alarm says what it heard", "'call an ambulance'" in said, said)
        check("...and what it is treating it as", "medical" in said, said)

        print("\n[contacts offline] a ladder that reaches nobody says so first")

        async def nothing_works(step, alarm, text):
            return (False, "not set up here")

        try:
            g["_do"] = nothing_works
            alone = asyncio.run(E.raise_alarm(E.Alarm(E.FIRE, "'there is a fire'"), "fire",
                                              path=p))
        finally:
            g["_do"] = real_do
        check("it does not report success of any kind",
              "could not reach anyone" in alone, alone)
        check("...and never claims a rung it did not reach",
              "I've called" not in alone and "I've pushed" not in alone, alone)

        print("\n[contacts offline] a broken rung does not stop the ladder")
        seen: list[str] = []

        async def second_explodes(step, alarm, text):
            seen.append(step.action)
            if step.action == "telegram":
                raise RuntimeError("transport blew up")
            return (step.action == "push", "" if step.action == "push" else "no")

        try:
            g["_do"] = second_explodes              # a bug in `_do` itself, not in a transport
            out = asyncio.run(E.raise_alarm(E.Alarm(E.MEDICAL, "'x'"), "help", path=p))
        finally:
            g["_do"] = real_do
        check("a rung that raises is caught by the ladder, not by the turn",
              seen == ["call", "telegram", "push"], seen)
        check("...and the last rung still reached the owner", "pushed to your phone" in out, out)

    print("\n[contacts offline] the example file parses with the real parser")
    # A gitignored file with no worked example is a feature nobody turns on, and an example that has
    # drifted from the parser is worse than none.
    example = Path(__file__).resolve().parents[1] / "emergency.example.md"
    rows = E._read(example)
    check("the shipped example exists", example.is_file())
    check("...and parses into real categories",
          {"medical", "fire", "intruder", "default"} <= set(rows), sorted(rows))
    check("...with rungs the ladder understands",
          all(st.action in E.ACTIONS for steps in rows.values() for st in steps),
          [st.action for steps in rows.values() for st in steps])
    check("...and it names the manual phrase",
          any(m in example.read_text(encoding="utf-8").lower() for m in E.MANUAL))

    print("\n[no LLM] the classifier runs before the turn prepares a model call")
    src = (Path(__file__).resolve().parents[1]
           / "src/afon/brain/agent.py").read_text(encoding="utf-8")
    for fn in ("_respond_impl", "_respond_stream_impl"):
        body = src.split(f"async def {fn}(", 1)[1]
        i_alarm = body.find("emergency.classify(")
        i_model = body.find("_prepare_turn(")
        check(f"{fn}: the alarm is checked", i_alarm > 0)
        check(f"{fn}: ...before the model turn is prepared", 0 < i_alarm < i_model,
              f"{i_alarm} vs {i_model}")
    check("the module imports nothing from the LLM layer",
          "llm" not in inspect.getsource(E).lower().split('"""')[2],
          "the critical path must not depend on the model chain")
    check("...and reads its contacts from a plain file",
          "read_text" in inspect.getsource(E._read))

    print("\n[no LLM] the ladder is bounded, so one dead transport cannot eat the budget")
    check("a rung has a timeout", E.STEP_TIMEOUT_S > 0 and E.STEP_TIMEOUT_S <= 3, E.STEP_TIMEOUT_S)
    check("...inside the plan's three seconds to first outward action", E.STEP_TIMEOUT_S <= 3)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
