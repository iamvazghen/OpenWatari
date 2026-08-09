"""C7 — skill runtime: invoke_skill runs a built-in manifest's steps in order (hermetic, no network).

Patches the tool handler registry with fakes that record call order, so we assert the composable
skill fires its steps in sequence without touching real Notion/Gmail/Telegram.
"""
import asyncio

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

    print(f"=== {_ok}/{_ok + _fail} checks passed ===")
    import sys
    sys.exit(1 if _fail else 0)


asyncio.run(main())
