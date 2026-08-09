"""Per-clause tool routing for compound requests (TODO: multi-intent completion hardening).

The behavioural suite's two Combination scenarios both fail the same way: the model
satisfies one half of a compound request and narrates the other. `forced_tools` could not
help, because it returns the FIRST matching route for the whole string — so in
"look up the capital of Japan and remember it", `web_search` won and the save half routed
to nothing at all.

Two defects, both fixed and pinned here:
  1. `remember it` / `save that` did not match the memory route. The natural phrasing of a
     chained request uses a pronoun for the thing just fetched, so every compound memory
     request lost its second half.
  2. There was no way to ask "what does EACH part of this need?" — `clause_tools` is that.

Run: python bench/test_clause_routing.py
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from afon.brain.intent_router import clause_tools, forced_tools  # noqa: E402

failures: list[str] = []
_total = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _total
    _total += 1
    print(("PASS  " if cond else "FAIL  ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        failures.append(label)


# ── the pronoun gap (defect 1) ─────────────────────────────────────────────
for phrase in ("remember it as my next travel destination",
               "remember that for later",
               "save that for me",
               "store it in my notes",
               "jot that down"):
    check(f"memory route matches {phrase!r}", forced_tools(phrase) == ["remember"],
          str(forced_tools(phrase)))

# ── the two failing benchmark scenarios (defect 2) ─────────────────────────
combo_web = "Look up the capital of Japan and remember it as my next travel destination."
check("combo_web_memory routes BOTH halves",
      clause_tools(combo_web) == ["web_search", "remember"],
      str(clause_tools(combo_web)))

# Deliberately does NOT pin *which* half wins. Route order decides that, and it shifted
# the moment the memory route learned about pronouns — the point being made is that
# whole-string routing can only ever see one half, whichever one it is.
whole = forced_tools(combo_web)
check("...while whole-string routing still sees only one half",
      len(whole) == 1 and whole[0] in clause_tools(combo_web),
      f"{whole} vs clauses {clause_tools(combo_web)}")

check("order follows the sentence, so the chain runs fetch-then-save",
      clause_tools(combo_web).index("web_search")
      < clause_tools(combo_web).index("remember"))

# ── other genuine compounds ────────────────────────────────────────────────
cases = {
    "check my email and remember that I owe Anna a reply": ["read_email", "remember"],
    "search for a good ramen place then add it to my task list":
        ["web_search", "notion_create_task"],
    "what's on my calendar today and also remember I prefer mornings":
        ["list_events", "remember"],
}
for text, want in cases.items():
    got = clause_tools(text)
    check(f"compound: {text[:44]!r}", got == want, f"got {got}")

# ── things that must NOT be treated as compound ────────────────────────────
for single in (
    "remember that I prefer tea over coffee",       # one action
    "check my email and let me know",               # trailing phrase, one routed clause
    "what do you think about this?",                # chat
    "fish and chips for dinner",                    # connector, no second action
    "tell me a joke",
):
    check(f"not compound: {single[:42]!r}", clause_tools(single) == [],
          str(clause_tools(single)))

# ── single-intent routing must be untouched (regression guard) ─────────────
unchanged = {
    "what's on my calendar today?": "list_events",
    "do I have any new emails?": "read_email",
    "remind me to stretch in 90 minutes": "set_reminder",
    "what's the weather in Yerevan today?": "weather",
}
for text, want in unchanged.items():
    got = forced_tools(text)
    check(f"unchanged: {text[:40]!r} -> {want}", bool(got) and got[0] == want, str(got))

for chat in ("what's two plus two", "how are you today", "thanks, that's great"):
    check(f"chat not narrowed: {chat!r}", forced_tools(chat) == [])


# ── B6.1 · the time clause of a COMPOUND request ──────────────────────────
# `get_time` is intentionally not in _ROUTES (forcing it on a bare "what time is it" was declined
# as scorer-gaming — Afon answers inline, faster). That left combo_time_memory at 51.5: the time
# half routed to nothing, clause_tools hit its >= 2 guard and the compound fell back to prose.
# _CLAUSE_ONLY_ROUTES makes the clause visible WITHOUT touching single-intent behaviour, and both
# halves of that claim are asserted here — the second is the one that could regress silently.
from afon.brain.intent_router import clause_tools  # noqa: E402

for text, want in {
    "what time is it and remember to call mum": ["get_time", "remember"],
    "what's the time and add milk to my task list": ["get_time", "notion_create_task"],
}.items():
    got = clause_tools(text)
    check(f"compound time: {text[:42]!r} -> {want}", got == want, str(got))

for solo in ("what time is it", "what's the time", "tell me the time"):
    check(f"single-intent time still NOT narrowed: {solo!r}", forced_tools(solo) == [],
          str(forced_tools(solo)))

print()
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print("  " + f)
    sys.exit(1)
# House marker: run_all_tests.py greps for this exact string. A suite printing its own
# wording is reported as failing even when every assertion passed.
print(f"=== {_total - len(failures)}/{_total} checks passed ===")
