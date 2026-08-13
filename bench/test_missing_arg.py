"""A tool must not answer a mis-keyed call with a fluent QUESTION.

L3c found the failure: the model called `recall({'entity': …, 'key': …})` where the schema says
`query`, the tool found nothing, returned "What should I recall, sir?", and the model SPOKE that
sentence as its reply. The tool never ran, and the audit scored the echo as a governance answer.

The tempting fix — make every tool return an error instead of asking — trades one failure for
another, because asking is CORRECT when the owner genuinely left the detail out ("remind me",
"play something"). So `base.missing_arg` discriminates on which KEYS arrived, and this file
asserts BOTH halves: the mis-keyed call must error, and the underspecified one must still ask.

Hermetic: handlers are called directly with dicts; no network, no model, no store writes (every
case returns before any I/O).

    uv run python bench/test_missing_arg.py
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


def is_error(s: str) -> bool:
    from afon.brain.tools.base import tool_failed
    return tool_failed(s)


def is_question(s: str) -> bool:
    return s.strip().endswith("?") and not is_error(s)


async def main() -> None:
    from afon.brain.tools import contacts, graphmem, maps, memory, notify, objectives, reminders
    from afon.brain.tools.base import missing_arg
    from afon.config import settings

    settings.memory_enabled = True

    # ---- the unit itself: the three cases, spelled out -----------------------------
    check("no args at all -> ASK (owner left the detail out)",
          missing_arg("t", {}, "query", ask="Which one, sir?") == "Which one, sir?")
    check("right key, empty value -> ASK (still underspecified)",
          missing_arg("t", {"query": ""}, "query", ask="Which one, sir?") == "Which one, sir?")
    check("unknown keys -> ERROR (the model invented the key)",
          is_error(missing_arg("t", {"entity": "x", "key": "y"}, "query", ask="Which one, sir?")))
    check("a synonym counts as known -> ASK, not error",
          missing_arg("t", {"text": ""}, "query", "text", ask="Which one, sir?") == "Which one, sir?")
    # The error must be logged with the offending keys, or a wrong-key bug is invisible in the
    # logs — but the SPOKEN string must not leak them. tool_error() prints the type only.
    spoken = missing_arg("t", {"entity": 1}, "query", ask="Which one, sir?")
    check("the spoken error stays generic (no raw key names read aloud)",
          "entity" not in spoken and "ValueError" in spoken, repr(spoken))

    # ---- the sweep, both halves, on real handlers ----------------------------------
    # (handler, mis-keyed args -> must ERROR, empty args -> must still ASK)
    cases = [
        ("remember",         memory.remember,            {"entity": "flight", "key": "3 July"}),
        ("resolve_contact",  contacts.resolve_contact,   {"query": "mum"}),
        ("save_contact",     contacts.save_contact,      {"query": "mum"}),
        ("travel_time",      maps.travel_time,           {"query": "Berlin"}),
        ("find_place",       maps.find_place,            {"entity": "sushi"}),
        ("set_reminder",     reminders.set_reminder,     {"subject": "call mum"}),
        ("recall_related",   graphmem.recall_related,    {"query": "Alex"}),
        ("assign_objective", objectives.assign_objective, {"content": "ship it"}),
        ("send_push",        notify.send_push,           {"body_text": "hi"}),
    ]

    for name, fn, bad in cases:
        got_bad = await fn(dict(bad))
        check(f"{name}: mis-keyed {list(bad)} -> error, not a question",
              is_error(got_bad), repr(got_bad[:70]))

        got_empty = await fn({})
        # not_configured is an acceptable ASK-side outcome: a tool without credentials declines
        # before it ever inspects arguments, which is the correct order.
        from afon.brain.tools.base import is_not_configured
        ok = is_question(got_empty) or is_not_configured(got_empty)
        check(f"{name}: no args -> still asks the owner", ok, repr(got_empty[:70]))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
