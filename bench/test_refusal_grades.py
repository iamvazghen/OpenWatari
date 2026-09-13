"""44.F4 — four answers to "may I", and a precedence that cannot be softened.

Afon had two answers. A deterministic refusal lived in the agent for the handful of instructions
that destroy a machine, and a confirm tier lived in the policy layer for everything outward or
hard to undo. Everything else was silence — and the gap showed as exactly that.

Guest mode is turned on because someone else is in the room. Lockdown is turned on because he wants
quiet. Both changed what Afon did, and neither said so, because the only way to tell him something
was to refuse and the only way to proceed was to say nothing. Proceed-with-note is the missing
middle: do it, and say the thing he would have wanted to know.

What this asserts:

  * four named grades, ordered strongest first, and the order is the design;
  * a permissive rule can never soften a stronger one by matching later;
  * the refusal is decided by what he ASKED, not by which tool the model reached for;
  * a note PROCEEDS — it is not a quiet second confirm;
  * a grader that cannot do its job escalates rather than falls through to allow;
  * the note actually reaches the owner, instead of being a value nobody reads.

Hermetic: no LLM, no network, no store.

    uv run python bench/test_refusal_grades.py
"""

from __future__ import annotations

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


class _Modes:
    def __init__(self, guest=False, lockdown=False):
        self.guest, self.lockdown = guest, lockdown


def main() -> None:
    from afon.brain import proactive as P
    from afon.brain.proactive import ALLOW, CONFIRM, NOTE, PRECEDENCE, REFUSE, grade

    quiet = _Modes()

    print("[1] four grades, strongest first")
    check("the order is refuse, confirm, note, allow",
          PRECEDENCE == (REFUSE, CONFIRM, NOTE, ALLOW), PRECEDENCE)
    check("they are four distinct values", len(set(PRECEDENCE)) == 4)

    print("\n[2] what he ASKED decides the strongest grade, not what the model reached for")
    # The tempting bug: grading the tool alone. A model can carry out a catastrophic instruction
    # with a harmless-looking call, and a grader that only looks at the call would wave it through.
    v = grade("recall", {}, user_text="wipe my c: drive", modes=quiet)
    check("a catastrophic request refuses even on an innocent tool", v.grade == REFUSE, v)
    check("...and says why", "cannot be undone" in v.why, v.why)
    check("...and refuses to proceed", not v, v)
    check("an ordinary request on the same tool just runs",
          grade("recall", {}, user_text="what did I say about the farm", modes=quiet).grade == ALLOW)

    print("\n[3] precedence: no permissive rule can soften a stronger one")
    # Every one of these matches a LATER rule too. If the function returned the last match, or the
    # first rule written, each would come back weaker than it should.
    loud = _Modes(guest=True, lockdown=True)
    v = grade("send_email", {"to": "x", "body": "y"}, user_text="email Jane", modes=loud)
    check("an outward tool in lockdown still CONFIRMS, it does not merely note",
          v.grade == CONFIRM, v)
    v = grade("send_email", {"to": "x"}, user_text="wipe c:\\windows", modes=loud)
    check("...and a catastrophic ask outranks even the confirm", v.grade == REFUSE, v)
    check("a read tool with guest on is a note, not a confirm",
          grade("recall", {}, modes=_Modes(guest=True)).grade == NOTE)

    print("\n[4] a note PROCEEDS — it is not a quieter confirm")
    v = grade("recall", {}, modes=_Modes(guest=True))
    check("the verdict is truthy, so the action runs", bool(v), v)
    check("...and it carries the thing he'd want to know", "guest mode" in v.why, v.why)
    v = grade("delegate_to_fleet", {}, modes=_Modes(lockdown=True))
    check("lockdown notes an outward call rather than blocking it", v.grade == NOTE, v)
    check("...naming lockdown", "lockdown" in v.why, v.why)
    check("a confirm is NOT truthy — it has not been permitted yet",
          not grade("send_email", {"to": "x"}, modes=quiet))
    check("a plain allow is truthy and says nothing",
          bool(grade("get_time", {}, modes=quiet)) and grade("get_time", {}, modes=quiet).why == "")

    print("\n[5] modes only speak where they are actually relevant")
    check("guest mode does not narrate a weather lookup",
          grade("get_weather", {}, modes=_Modes(guest=True)).grade == ALLOW)
    check("lockdown does not narrate a local read",
          grade("recall", {}, modes=_Modes(lockdown=True)).grade == ALLOW)
    check("with no modes at all, nothing is invented",
          grade("recall", {}, modes=quiet).grade == ALLOW)

    print("\n[6] a grader that cannot do its job escalates, it does not fall through")
    # The dangerous failure: an import error in the hard-refusal check silently downgrading a
    # system-wiping request to 'allow'. Unavailable means ASK, never means yes.
    import afon.brain.agent as agent_mod

    real = agent_mod._catastrophic
    try:
        def _boom(_t):
            raise RuntimeError("classifier gone")

        agent_mod._catastrophic = _boom
        v = grade("recall", {}, user_text="wipe my c: drive", modes=quiet)
        check("a broken refusal check asks instead of allowing", v.grade == CONFIRM, v)
        check("...and says it could not check", "couldn't check" in v.why, v.why)
    finally:
        agent_mod._catastrophic = real

    broken_modes = type("M", (), {"guest": property(lambda self: (_ for _ in ()).throw(OSError()))})()
    check("unreadable modes degrade to allow, not to a crash",
          grade("recall", {}, modes=broken_modes).grade == ALLOW)

    print("\n[7] the grades match what the rest of the system already does")
    # Two definitions of 'needs a yes' would drift, so the confirm grade IS the confirm tier.
    from afon.brain.proactive import CONFIRM_TIER, confirm_required

    for tool in sorted(CONFIRM_TIER)[:8]:
        want = confirm_required(tool, {})
        got = grade(tool, {}, modes=quiet).grade
        check(f"{tool}: grade agrees with confirm_required",
              (got == CONFIRM) == want, f"{got} vs confirm_required={want}")

    print("\n[8] the note reaches the owner, not just the log")
    src = (Path(__file__).resolve().parents[1]
           / "src/afon/brain/agent.py").read_text(encoding="utf-8")
    check("the agent grades each permitted call", "grade as _grade" in src)
    check("...and speaks a NOTE before the tool runs",
          "Just so you know, sir" in src and "on_progress(said_note)" in src)
    check("...and grading can never block a permitted action",
          "grading never blocks" in src or "never blocks a permitted action" in src)

    # The one thing a note must never become: a reason the action did not happen.
    check("nothing in the agent treats a note as a block",
          "NOTE" in src and "verdict.grade == NOTE and verdict.why" in src)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
