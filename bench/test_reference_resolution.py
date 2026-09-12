"""Twenty references, and what each one actually points at (SYSTEMS.md 07.F3).

The floor says reference resolution is *tested, not assumed*. It was assumed: the whole history
went to the model and the model worked out what "the second one" meant, which is usually right and
occasionally confidently wrong — and when it was wrong nothing recorded which reading was taken, so
opening the wrong file looked like a tool bug rather than a misread pronoun.

**Two scores, and the second one is the real gate.** The floor asks for ≥90% on twenty cases, so
`CORRECT` must reach 18. But a declined reference and a wrongly-bound one are not the same failure:
declining leaves the model exactly where it was, while a wrong binding actively misleads it. So
`WRONG` must be **zero**, always, and that is the assertion that should fail a careless change to
the rules.

An honest note about this corpus: it was written by the author of the resolver, so it measures the
rules against cases that seemed realistic — not against language. Cases 13 to 20 are deliberately
adversarial for that reason: every one of them is a shape a naive resolver guesses at, where the
right answer is to decline.

    uv run python bench/test_reference_resolution.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain.references import resolve  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def u(t: str) -> dict:
    return {"role": "user", "content": t}


def a(t: str) -> dict:
    return {"role": "assistant", "content": t}


LIST3 = a("1. Paris\n2. Lyon\n3. Nice")

#: (label, history, utterance, expected antecedent or None)
CASES = [
    # --- ordinals into a list Afon just produced -------------------------------------------
    ("ordinal: second", [LIST3], "book the second one", "Lyon"),
    ("ordinal: first, no 'one'", [LIST3], "tell me about the first", "Paris"),
    ("ordinal: the last one", [LIST3], "the last one", "Nice"),
    ("ordinal: number 3", [LIST3], "number 3", "Nice"),
    ("ordinal: bulleted list", [a("- red\n- green\n- blue")], "pick the second", "green"),
    ("ordinal: paren-numbered", [a("Here are your options:\n1) Train\n2) Bus")],
     "the first option", "Train"),
    ("ordinal: inline series", [a("I found three files: notes.md, plan.md and todo.md")],
     "open the third one", "todo.md"),
    ("ordinal: multi-word item", [a("1. Buy milk\n2. Call the dentist")],
     "do the second one", "Call the dentist"),
    ("ordinal: series of phrases",
     [a("Your tasks: write the report, call the bank, book a flight")],
     "do the second one", "call the bank"),
    # --- a pronoun with one clear candidate ------------------------------------------------
    ("pronoun: filename", [u("read notes.md"), a("Done.")], "summarise it", "notes.md"),
    ("pronoun: 'that'", [u("open plan.md"), a("Opened.")], "delete that", "plan.md"),
    ("pronoun: quoted name", [a('I saved it as "Q3 revenue plan".')], "send that",
     "Q3 revenue plan"),
    # --- the adversarial half: a naive resolver guesses, the right answer is to decline -----
    ("decline: out of range", [LIST3], "the fourth one", None),
    ("decline: two candidates", [a("I read notes.md and plan.md.")], "open it", None),
    ("decline: no history at all", [], "open it", None),
    ("decline: no list to index", [], "the second one", None),
    ("decline: non-referential 'it's'", [u("read notes.md"), a("Done.")], "it's raining", None),
    ("decline: idiomatic 'make it'", [u("read notes.md"), a("Done.")], "make it quick", None),
    ("decline: a cardinal is not an ordinal", [LIST3], "give me one more", None),
    ("decline: no reference present", [LIST3], "what about Rome", None),
]

passed = failed = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label}  {detail}")


def main() -> None:
    assert len(CASES) == 20, f"the floor asks for twenty cases, this is {len(CASES)}"

    correct = declined = wrong = 0
    wrongs: list[str] = []

    print("[1] the corpus")
    for label, history, text, want in CASES:
        got = resolve(text, history)
        got_ant = got.antecedent if got else None
        if got_ant == want:
            correct += 1
            print(f"  [ok  ] {label}: {text!r} -> {got_ant!r}")
        elif got_ant is None:
            declined += 1
            print(f"  [--  ] {label}: {text!r} -> declined (wanted {want!r})")
        else:
            wrong += 1
            wrongs.append(f"{label}: {text!r} -> {got_ant!r}, wanted {want!r}")
            print(f"  [WRONG] {label}: {text!r} -> {got_ant!r}, wanted {want!r}")

    pct = 100.0 * correct / len(CASES)
    print(f"\n[2] the scores — {correct} correct, {declined} declined, {wrong} wrong ({pct:.0f}%)")
    check(pct >= 90.0, f"≥90% of twenty cases resolve as expected ({pct:.0f}%)",
          f"{correct}/{len(CASES)}")
    check(wrong == 0, "NOTHING is bound to the wrong antecedent", "; ".join(wrongs))

    print("\n[3] the properties the rules must keep")
    # An out-of-range ordinal must decline rather than clamp. Clamping is how "the fourth one"
    # against three items acts on the third.
    check(resolve("the fourth one", [LIST3]) is None, "an out-of-range ordinal declines, not clamps")
    check(resolve("the tenth one", [LIST3]) is None, "and so does one far out of range")

    # The antecedent must come from BEFORE the reference, never from the sentence itself.
    r = resolve("open notes.md and then read it", [u("read plan.md"), a("Done.")])
    check(r is None, "a pronoun with its own antecedent in the same breath needs no binding",
          str(r))

    # A resolved reference must say which rule produced it, or a wrong binding is undiagnosable.
    r2 = resolve("the second one", [LIST3])
    check(r2 is not None and r2.source == "list:ordinal", "a binding names the rule that made it")
    check(r2 is not None and r2.phrase in "the second one", "and the phrase it bound")
    check("assuming" in r2.note(), "the note is phrased as an assumption, because it is one",
          r2.note())

    # Lookback must be bounded: a referent five turns back is not what "it" means.
    far = [u("read notes.md"), a("Done."), u("what's the time"), a("Two o'clock."),
           u("thanks"), a("Any time.")]
    check(resolve("summarise it", far) is None,
          "a referent beyond the lookback window is not reached for")

    print("\n[4] the agent actually consults it")
    src = (ROOT / "src/afon/brain/agent.py").read_text(encoding="utf-8")
    check("_resolve_reference(" in src, "the agent resolves a reference on the turn path")
    prep = src.split("_prep_started = time.monotonic()", 1)
    check(len(prep) == 2 and "_resolve_reference" in prep[1][:600],
          "and it does so BEFORE the turn's own message joins the history")
    check(".note()" in src, "a resolved reference reaches the model as a note")
    # The owner's words must reach history verbatim. A resolver that edited the user message in
    # place would make a wrong binding unrecoverable from the transcript — you could no longer see
    # what was actually said, only what Afon decided it meant.
    check('self._history.append({"role": "user", "content": user_text})' in src,
          "the owner's message enters history verbatim, never rewritten")
    check('{"role": "system", "content": self._reference.note()}' in src,
          "the binding travels as a separate system note beside it")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
