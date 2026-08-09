"""H2.12 — `if_then` must honour the comparison operator it parses.

The operator was parsed and then discarded (`macros.py` conceded "op is currently advisory"), so
`has_unread_email == 0` and `has_unread_email != 0` did the same thing — and both ran the THEN
branch when unread mail existed. `== 0` is the example in the tool's own error message, so the
documented usage was the broken one and every macro built on a negative condition fired backwards.

The first two checks are the regression: same subject, opposite operators, opposite branches.

    uv run python bench/test_if_then_operator.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from afon.brain.tools import macros  # noqa: E402

_ok = 0
_fail = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"PASS  {label}" + (f"  [{detail}]" if detail else ""))
    else:
        _fail += 1
        print(f"FAIL  {label}" + (f"  [{detail}]" if detail else ""))


async def _branch(condition: str) -> str:
    """Which branch ran — 'then', 'else', 'neither', or the refusal text."""
    out = await macros.if_then({"condition": condition,
                                "then_steps": [{"say": "THEN"}],
                                "else_steps": [{"say": "ELSE"}]})
    if "THEN" in out:
        return "then"
    if "ELSE" in out:
        return "else"
    return out


async def main() -> None:
    # Pin the subject: pretend there IS unread mail, so has_unread_email resolves to 1.
    async def _has_mail(_args):
        return "You have 2 new emails, sir: one from the bank."

    import afon.brain.tools.gmail as gmail
    saved = gmail.read_email
    gmail.read_email = _has_mail
    try:
        # THE REGRESSION. With mail present: "== 0" is FALSE, "!= 0" is TRUE. Before the fix both
        # ran THEN, so the owner's "if I have no unread email, then…" fired exactly backwards.
        check(await _branch("has_unread_email == 0") == "else",
              "'== 0' takes the ELSE branch when mail EXISTS")
        check(await _branch("has_unread_email != 0") == "then",
              "'!= 0' takes the THEN branch when mail exists")
        check(await _branch("has_unread_email > 0") == "then", "'> 0' works")
        check(await _branch("has_unread_email == false") == "else",
              "a spoken boolean ('false') compares like 0")

        # And the mirror: no mail, so the operators must swap branches.
        async def _no_mail(_args):
            return "No unread email, sir."

        gmail.read_email = _no_mail
        check(await _branch("has_unread_email == 0") == "then",
              "'== 0' takes the THEN branch when the inbox is EMPTY")
        check(await _branch("has_unread_email != 0") == "else",
              "'!= 0' takes the ELSE branch when the inbox is empty")
    finally:
        gmail.read_email = saved

    # Non-boolean subject, to prove the comparison is not bool-only.
    import datetime
    today = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][datetime.datetime.now().weekday()]
    check(await _branch(f"weekday == {today}") == "then", f"'weekday == {today}' matches today")
    check(await _branch("weekday == xxx") == "else", "a non-matching weekday takes ELSE")
    check(await _branch(f"weekday contains {today}") == "then", "'contains' still works")

    # An unevaluable subject must REFUSE, not quietly answer "false" — silently running the else
    # branch of a condition nobody evaluated is the failure mode this whole item is about.
    got = await _branch("phase_of_the_moon == full")
    check(got not in ("then", "else"), "an unknown subject refuses instead of guessing a branch",
          str(got)[:60])


asyncio.run(main())

print(f"=== {_ok}/{_ok + _fail} checks passed ===")
sys.exit(1 if _fail else 0)
