"""C7 — skill runtime: invoke_skill runs a built-in manifest's steps in order (hermetic, no network).

Patches the tool handler registry with fakes that record call order, so we assert the composable
skill fires its steps in sequence without touching real Notion/Gmail/Telegram.
"""
import asyncio
import time

import afon.brain.tools as tools_pkg
from afon.brain.tools import skills
from afon.brain.tools.macros import run_steps

_ok = 0
_fail = 0


def check(cond, label):
    global _ok, _fail
    if cond:
        _ok += 1
    else:
        _fail += 1
        print(f"  FAIL: {label}")


# --- fake handler registry: every tool records that it ran, returns a canned line ------------
_CALLS: list[str] = []


def _install_fakes(names):
    handlers = {}
    for n in names:
        def make(n=n):
            async def _fn(args):
                _CALLS.append(n)
                return f"{n} ok, sir."
            return _fn
        handlers[n] = make()
    tools_pkg.tool_handlers = lambda: handlers  # monkeypatch the registry used by run_steps


_install_fakes(["notion_tasks", "list_events", "read_email", "check_telegram", "read_journal", "weather"])


async def main():
    # --- 1) invoke a real manifest -> steps fire IN ORDER ---------------------------------
    _CALLS.clear()
    out = await skills.invoke_skill({"name": "morning-briefing"})
    check(_CALLS == ["notion_tasks", "list_events", "read_email"],
          f"morning-briefing fired steps in order (got {_CALLS})")
    check("Running skill 'morning-briefing'" in out and "[3/3]" in out, "transcript labels the 3 steps")

    # --- 2) a manifest with a spoken step ------------------------------------------------
    _CALLS.clear()
    out2 = await skills.invoke_skill({"name": "evening-review"})
    check(_CALLS == ["notion_tasks", "read_journal"], f"evening-review fired its 2 tools (got {_CALLS})")
    check("Rest well" in out2, "evening-review speaks its closing line")

    # --- 3) fuzzy match on a partial name ------------------------------------------------
    _CALLS.clear()
    out3 = await skills.invoke_skill({"name": "comms"})
    check(_CALLS == ["check_telegram", "read_email"], f"'comms' fuzzy-matched comms-check (got {_CALLS})")

    # --- 4) unknown skill -> graceful, lists what's runnable -----------------------------
    out4 = await skills.invoke_skill({"name": "nonexistent-xyz"})
    check("don't have a runnable skill" in out4 and "morning-briefing" in out4,
          "unknown skill degrades and lists available")

    # --- 5) list_skills surfaces the runnable skills -------------------------------------
    lst = await skills.list_skills({})
    check("runnable skill" in lst and "morning-briefing" in lst, "list_skills advertises runnable skills")

    # --- 6) run_steps executes each step TYPE (tool / say / wait / unknown) ---------------
    _CALLS.clear()
    tr = await run_steps(
        [{"tool": "weather", "args": {}}, {"say": "hi sir"}, {"wait_seconds": 0},
         {"tool": "not_a_tool", "args": {}}],
        "Test run",
    )
    check(_CALLS == ["weather"], f"run_steps ran only the real tool (got {_CALLS})")
    check("said: hi sir" in tr, "run_steps renders a spoken step")
    check("waited 0" in tr, "run_steps handles a wait step")
    check("'not_a_tool': unknown tool" in tr, "run_steps reports an unknown tool without crashing")

    # --- 7) invoke_skill + list_skills are registered as tools ---------------------------
    check("invoke_skill" in skills.HANDLERS and "invoke_skill" in {s["function"]["name"] for s in skills.SCHEMAS},
          "invoke_skill is a registered tool (schema + handler)")

    # --- 8) 29.F4: every automation is listable with its last run, next run and outcome ----
    # Three kinds of automation each knew only about itself: loops had a tick registry, reminders
    # had a next fire time, and macros recorded nothing at all. So an automation that quietly
    # stopped looked exactly like one nobody had asked for lately.
    import pathlib
    import tempfile

    from afon.brain import automations as au
    from afon.brain.tools import macros as mac

    rows = au.automations()
    check(bool(rows), "something is listed — the registry is reachable outside a live brain")
    check(all(r.last_run and r.next_run and r.outcome for r in rows),
          "every row answers all three questions; none is left blank")
    check(all(r.kind in ("loop", "reminder", "macro") for r in rows),
          f"every row names what kind it is ({sorted({r.kind for r in rows})})")

    # Worst first. A failure below a fold is a failure nobody reads.
    mixed = [
        au.Automation("good", "loop", "x", "1 min ago", "in 30s", "ok", True),
        au.Automation("bad", "loop", "y", "2d ago", "in 30s", "failed: disk full", False),
    ]
    check(sorted(mixed, key=lambda a: (a.healthy, a.kind, a.name))[0].name == "bad",
          "a broken automation sorts above a healthy one")
    said = au.spoken(mixed)
    check("disk full" in said and "bad" in said, f"the spoken summary leads with the failure: {said!r}")
    check("behaving" in au.spoken([mixed[0]]), "an all-clear says so plainly")

    # The two things it must refuse to imply.
    never = au.Automation("fresh", "macro", "x", au.NEVER, au.ON_REQUEST, au.NEVER)
    check(never.last_run == "never run" and never.next_run == "when you ask",
          "a never-run macro says so, and claims no next run it cannot keep")
    check(au.UNKNOWN != "" and au.UNKNOWN != au.NEVER,
          "'not recorded' is a different answer from 'never ran'")

    # A macro records its own runs, so 'when did that last work' has an answer.
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    store_path = pathlib.Path(tmp.name) / "macros.json"
    real_path = mac._store_path
    mac._store_path = lambda: store_path
    try:
        mac._save({"morning": {"description": "lights and brief", "steps": [{"say": "hi"}]}})
        before = au._from_macros(time.time())
        check(before and before[0].last_run == au.NEVER,
              "a macro that has never run reports that, not an empty cell")

        mac._note_run("morning", "said: hi sir")
        after = au._from_macros(time.time())
        check(after and after[0].outcome == "ok", f"a clean run is recorded as ok ({after})")
        check(after and after[0].last_run.endswith("ago"), "...with when it happened")
        check(after and after[0].healthy, "...and it does not sort as broken")

        mac._note_run("morning", "That tool (lights) hit an error: RuntimeError.")
        failed = au._from_macros(time.time())
        check(failed and failed[0].outcome.startswith("failed:"),
              f"a step that failed is recorded as a failure ({failed})")
        check(failed and not failed[0].healthy, "...and the macro now reads as needing attention")

        # Recording must never be able to break the run it is recording.
        mac._note_run("no-such-macro", "anything")
        check(True, "recording a run of an unknown macro is a no-op, not a crash")
    finally:
        mac._store_path = real_path
        tmp.cleanup()

    check("list_automations" in mac.HANDLERS
          and "list_automations" in {x["function"]["name"] for x in mac.SCHEMAS},
          "list_automations is a registered tool (schema + handler)")

    print(f"=== {_ok}/{_ok + _fail} checks passed ===")
    import sys
    sys.exit(1 if _fail else 0)


asyncio.run(main())
