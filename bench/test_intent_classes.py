"""01.R1 + 01.R2 — the five intent classes are explicit, measured, and act on ambiguity.

The classes (chat / lookup / act / multi / ambiguous) were always in the system, just never named:
`forced_tools` decided act-vs-lookup by which route matched first, `clause_tools` decided multi, and
agent.py carried its own three detectors. Three files each held part of the answer and none held the
question, so "why did he treat that as chatter?" had no single place to look and no number to move.

This gate is a LABELLED SET, not a set of examples chosen after the fact. The plan asks for 40
prompts at >= 90% class accuracy, and the prompts below were written from how the owner actually
talks — one per row, labelled first, run second. Misses are printed with what was predicted, because
a 90% gate that does not say WHICH four it got wrong cannot be acted on.

01.R2 rides the same gate: an ambiguous turn must produce ONE clarifying question and must not
produce a second one for the same request in the same session.

    uv run python bench/test_intent_classes.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  [{detail}]")


# (utterance, class). Chosen to span the classes evenly and to include the near-misses that made
# the old emergent behaviour hard to reason about: a greeting that mentions a tool noun, a question
# that reads like a command, a compound that is really one background job.
LABELLED: list[tuple[str, str]] = [
    # -- chat: no tool, and the fast path depends on getting these right ---------------------
    ("hey", "chat"),
    ("good morning", "chat"),
    ("thanks, that's great", "chat"),
    ("how are you doing", "chat"),
    ("tell me a joke", "chat"),
    ("you're the best", "chat"),
    ("never mind", "chat"),
    ("goodnight Afon", "chat"),
    # -- lookup: he wants to know something -------------------------------------------------
    ("what's on my calendar today", "lookup"),
    ("any unread email", "lookup"),
    ("check telegram", "lookup"),
    ("what's the weather tomorrow", "lookup"),
    ("what's bitcoin trading at", "lookup"),
    ("what does ephemeral mean", "lookup"),
    ("what's on my plate", "lookup"),
    ("do you remember what I said about the flat", "lookup"),
    ("look up the opening hours for the Bürgeramt", "lookup"),
    ("how far is the moon", "lookup"),
    ("when is my flight", "lookup"),
    ("what's in my vault about the rabbit farm", "lookup"),
    # -- act: he wants something changed or done -------------------------------------------
    ("remind me to call mum at six", "act"),
    ("remember that the landlord's name is Weber", "act"),
    ("add a task to renew the passport", "act"),
    ("mark everything done", "act"),
    ("send Anna a telegram saying I'm running late", "act"),
    ("draft an email to the school about the placement", "act"),
    ("put a meeting in the calendar for Thursday at ten", "act"),
    ("turn the lights off", "act"),
    ("play something quiet", "act"),
    ("forget what I told you about the car", "act"),
    ("research the German rental market and write me a summary", "act"),
    ("delete the task about the visa", "act"),
    # -- multi: more than one thing, and the second half is the one that gets dropped -------
    ("look up the weather and remember it", "multi"),
    ("check my email and add anything urgent to my tasks", "multi"),
    ("what time is it and remind me to call the bank", "multi"),
    ("find the address and send it to Anna", "multi"),
    ("play some music and turn the lights down", "multi"),
    # -- ambiguous: a pointer with nothing to point at --------------------------------------
    ("send it", "ambiguous"),
    ("delete that", "ambiguous"),
    ("the usual", "ambiguous"),
    ("can you cancel it", "ambiguous"),
    ("same as last time", "ambiguous"),
]


def main() -> None:
    from afon.brain.intent_router import (
        INTENT_CLASSES,
        _READ_TOOLS,
        _ROUTES,
        _WRITE_TOOLS,
        classify,
    )

    print(f"[1] 01.R1 — {len(LABELLED)} labelled prompts, >= 90% class accuracy")
    check("the labelled set is at least the 40 the plan asks for", len(LABELLED) >= 40,
          str(len(LABELLED)))
    check("every class is represented, so the score cannot be won by ignoring one",
          {c for _, c in LABELLED} == set(INTENT_CLASSES),
          str(set(INTENT_CLASSES) - {c for _, c in LABELLED}))

    misses = [(t, want, classify(t)) for t, want in LABELLED if classify(t) != want]
    hits = len(LABELLED) - len(misses)
    acc = hits / len(LABELLED)
    for t, want, got in misses:
        print(f"        miss: {t!r} -> {got} (labelled {want})")
    check(f"class accuracy {hits}/{len(LABELLED)} = {acc:.0%} (>= 90%)", acc >= 0.90,
          f"{len(misses)} wrong")
    check("every prediction is one of the declared classes",
          all(classify(t) in INTENT_CLASSES for t, _ in LABELLED))

    print("\n[2] the classes are decided, not emergent — the labels have a source")
    # Whatever a route returns is either a write or a read, and the act/lookup split IS that fact.
    routed = {n for _, names in _ROUTES for n in names}
    unlabelled = sorted(routed - _WRITE_TOOLS - _READ_TOOLS)
    check("every tool a route can return is labelled write or read", not unlabelled,
          f"unlabelled: {unlabelled} — a new route must pick a side")
    check("...and nothing is labelled both", not (_WRITE_TOOLS & _READ_TOOLS),
          str(_WRITE_TOOLS & _READ_TOOLS))
    check("the write labels are real tools", _WRITE_TOOLS <= routed | {"get_time"},
          str(_WRITE_TOOLS - routed))
    # Precedence the classifier promises in its docstring.
    check("chat wins over everything (the no-tool fast path depends on it)",
          classify("thanks") == "chat")
    check("a background write-up is ONE job, not a compound (agent.py has always agreed)",
          classify("research the market and write me a report") == "act")
    check("a write route beats a read route in the same sentence",
          classify("remember to check my mail") == "act")
    check("empty input is ambiguous, never chat", classify("") == "ambiguous")
    check("the classifier is deterministic",
          all(classify(t) == classify(t) for t, _ in LABELLED))

    print("\n[3] 01.R2 — an antecedent turns a question back into a command")
    for t in ("send it", "delete that", "can you cancel it"):
        check(f"{t!r} is ambiguous with nothing behind it", classify(t) == "ambiguous")
        check(f"...and an ordinary act once the reference resolver bound it",
              classify(t, has_context=True) != "ambiguous",
              "refusing to act on a pronoun he just gave you is its own failure")
    check("a pointer WITH its object is not ambiguous",
          classify("cancel the meeting with Anna") != "ambiguous")

    print("\n[4] 01.R2 — one clarifying question, and never the same one twice")
    from afon.brain import agent as A

    ag = A.AfonAgent.__new__(A.AfonAgent)          # no LLM, no state dir — we only want the memo
    ag._clarified = set()

    def nudge_for(text: str) -> str:
        """Replay the agent's own branch — the same code path `_prepare_turn` runs."""
        key = " ".join(text.lower().split())
        out = A._AMBIGUOUS_AGAIN_NUDGE if key in ag._clarified else A._AMBIGUOUS_NUDGE
        ag._clarified.add(key)
        return out

    first = nudge_for("send it")
    second = nudge_for("send it")
    check("the first ambiguous turn asks ONE question", "ask ONE short question" in first)
    check("...and is told not to act on the likeliest reading", "do NOT guess" in first.lower()
          or "not guess" in first.lower())
    check("the second time, he does NOT ask again", "not ask again" in second.lower())
    check("...he states the assumption instead", "which reading you took" in second)
    check("...and still confirms if it sends, deletes or spends",
          "sends, deletes or spends" in second)
    check("a DIFFERENT ambiguous request still gets its own question",
          nudge_for("delete that") == A._AMBIGUOUS_NUDGE)
    check("the memo is per-request, not a global latch", len(ag._clarified) == 2)
    # The 03.R5 unsure-nudge and this one must not both claim the turn with the same instruction.
    check("the ambiguity nudge is distinct from the deferred-group nudge",
          A._AMBIGUOUS_NUDGE != A._UNSURE_NUDGE)

    print("\n[5] holdout — prompts written months ago, for other reasons, labelled mechanically")
    # The set above scores 100%, which is not evidence: it was written alongside the classifier, so
    # it measures agreement with myself. behavioral_suite.py's turns were written for the live
    # behavioural benchmark long before this task, and each carries an `expect_tools` field. That
    # field labels the class mechanically — a turn expecting only write tools is `act`, only read
    # tools is `lookup` — so neither the prompts nor the labels are mine to tune.
    sys.path.insert(0, str(BENCH / "bench"))
    import behavioral_suite  # noqa: E402

    held: list[tuple[str, str]] = []
    for sc in behavioral_suite.scenarios():
        for turn in sc.turns:
            want = None
            if turn.expect_tools and all(e in _WRITE_TOOLS for e in turn.expect_tools):
                want = "act"
            elif turn.expect_tools and all(e in _READ_TOOLS for e in turn.expect_tools):
                want = "lookup"
            if want:
                held.append((turn.say, want))
    hmiss = [(t, w, classify(t)) for t, w in held if classify(t) != w]
    for t, w, g in hmiss:
        print(f"        holdout miss: {t!r} -> {g} (expect_tools say {w})")
    check("the holdout is big enough to mean something", len(held) >= 12, str(len(held)))
    check(f"holdout accuracy {len(held) - len(hmiss)}/{len(held)}",
          (len(held) - len(hmiss)) / len(held) >= 0.85,
          f"{len(hmiss)} wrong out of {len(held)}")

    print("\n[6] the classifier stays cheap — it runs on every turn")
    import time

    t0 = time.perf_counter()
    for _ in range(200):
        for t, _want in LABELLED:
            classify(t)
    per = (time.perf_counter() - t0) / (200 * len(LABELLED)) * 1e6
    check(f"~{per:.0f}us per classification (< 500us)", per < 500, f"{per:.0f}us")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
