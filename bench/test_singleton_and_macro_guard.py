"""Two production guards: one live process per role, and no unconfirmed action inside a macro.

**Singleton.** Duplicates are a real failure here — two voice edges both hold the microphone and both
answer; two pc_agents race the same forwarded command; two brains double-send. The rule is newest
wins, so an overlapping restart heals itself instead of leaving a stale process serving old code.

**Macro bypass.** ``run_steps`` used to call handlers straight out of the registry, skipping the
confirm gate, the audit trail and error tracking. A saved macro was therefore an unguarded path to
every destructive tool (git_push, browser, send_email) while ``run_macro`` itself asked nothing.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

PASS = FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label} {detail}")


async def main() -> None:
    from jarvis.shared import singleton as S

    print("\n[1] one live process per role")
    S._RUN_DIR = Path(tempfile.mkdtemp())
    # Never scan real processes from a test: the first version of this killed the owner's live edge.
    _REAL_PATTERNS = dict(S._ROLE_PATTERNS)
    S._ROLE_PATTERNS = {}

    check("a lone process claims its role", S.claim("edge", watch=False) is True)
    c = json.loads((S._RUN_DIR / "edge.json").read_text(encoding="utf-8"))
    check("the claim records this pid", c["pid"] == os.getpid())
    check("the claim records a start time", isinstance(c.get("started"), (int, float)))

    (S._RUN_DIR / "edge.json").write_text(
        json.dumps({"role": "edge", "pid": 999_999_999, "started": 1.0}), encoding="utf-8")
    check("a dead pid in the claim is ignored, not fought", S.claim("edge", watch=False) is True)

    check("a process is never its own duplicate", S._alive(os.getpid()) is False)
    check("start time is readable (so 'older' is decidable)", S._proc_start_time(os.getpid()))

    # The edge is launched by a system-Python parent that re-execs into the venv, so BOTH command
    # lines contain 'jarvis.edge.assistant'. Treating a launcher parent (or a spawned child) as a
    # duplicate makes the process kill its own family — which took the live edge down once.
    rel = S._related_pids()
    check("ancestors/descendants are excluded from the duplicate scan", os.getpid() in rel)
    check("...and the parent process is among them", os.getppid() in rel or len(rel) > 1,
          str(sorted(rel)[:5]))
    (S._RUN_DIR / "pc_agent.json").write_text(
        json.dumps({"role": "pc_agent", "pid": os.getppid(), "started": 1.0}), encoding="utf-8")
    killed_family: list[int] = []
    _real_kill = S._kill
    S._kill = lambda pid, why: (killed_family.append(pid), True)[1]
    try:
        S.enforce("pc_agent")
        check("a claim held by our own PARENT is never killed", killed_family == [],
              str(killed_family))
    finally:
        S._kill = _real_kill

    # Newest wins: presented with a peer that started LATER, this process stands down rather than
    # killing it — otherwise two restarts ping-pong kills at each other forever.
    killed: list[int] = []
    S._kill = lambda pid, why: (killed.append(pid), True)[1]
    S._alive = lambda pid: pid in (4242, 4243)
    S._proc_start_time = lambda pid: 5000.0 if pid == os.getpid() else 9999.0
    (S._RUN_DIR / "brain.json").write_text(
        json.dumps({"role": "brain", "pid": 4242, "started": 9999.0}), encoding="utf-8")
    check("a NEWER peer makes this process stand down", S.enforce("brain") == -1)
    check("...and it kills nothing on the way out", killed == [])

    # Older peer: kill it and take over.
    S._proc_start_time = lambda pid: 9999.0 if pid == os.getpid() else 1000.0
    (S._RUN_DIR / "brain.json").write_text(
        json.dumps({"role": "brain", "pid": 4243, "started": 1000.0}), encoding="utf-8")
    n = S.enforce("brain")
    check("an OLDER duplicate is killed", n == 1 and killed == [4243], f"{n} {killed}")
    check("...and the claim is taken over",
          json.loads((S._RUN_DIR / "brain.json").read_text(encoding="utf-8"))["pid"] == os.getpid())

    print("\n[1b] the scan must not mistake bystanders for the daemon")
    # Observed live: a bare module-name scan matched three Git-Bash shells and a `python -c`
    # diagnostic whose command lines merely CONTAINED the module name. Killing those would have hit
    # unrelated user processes, so the pattern is the full '-m <module>' invocation AND the process
    # must be a Python interpreter.
    S._ROLE_PATTERNS = _REAL_PATTERNS
    for role, pats in {"edge": ("-m jarvis.edge.assistant",),
                       "pc_agent": ("-m jarvis.edge.pc_agent",),
                       "brain": ("-m jarvis.brain.server",)}.items():
        check(f"'{role}' matches the -m invocation, not the bare module name",
              S._ROLE_PATTERNS.get(role) == pats, str(S._ROLE_PATTERNS.get(role)))
    check("only python interpreters are considered",
          all(p in S._PY_EXE_PREFIXES for p in ("python", "pythonw")))

    fake = [
        ("bash.exe", "bash -c 'tail logs | grep jarvis.edge.assistant'", False),
        ("python.exe", "python -c \"print('jarvis.edge.assistant')\"", False),
        ("pythonw.exe", r"C:\Jarvis\.venv\Scripts\pythonw.exe -m jarvis.edge.assistant", True),
        ("code.exe", "code src/jarvis/edge/assistant.py", False),
    ]
    for name, cmd, want in fake:
        is_py = any(name.lower().startswith(p) for p in S._PY_EXE_PREFIXES)
        got = is_py and any(pat in cmd for pat in S._ROLE_PATTERNS["edge"])
        check(f"{'DAEMON' if want else 'bystander'}: {name} {'matches' if want else 'ignored'}",
              got is want, f"{cmd[:50]} -> {got}")

    print("\n[2] macros cannot perform unconfirmed actions")
    import jarvis.brain.tools.macros as M
    from jarvis.brain.proactive import confirm_required

    store = Path(tempfile.mkdtemp()) / "macros.json"
    store.write_text(json.dumps({
        "danger": {"description": "pushes code", "steps": [{"tool": "git_push", "args": {}}]},
        "safe": {"description": "reads the time", "steps": [{"tool": "get_time", "args": {}}]},
    }), encoding="utf-8")
    M._store_path = lambda: store

    check("a macro containing a gated tool REQUIRES confirmation",
          confirm_required("run_macro", {"name": "danger"}) is True)
    check("a read-only macro stays frictionless",
          confirm_required("run_macro", {"name": "safe"}) is False)
    check("a macro with no name given fails CLOSED",
          confirm_required("run_macro", {}) is True)

    ran: list[str] = []

    async def _spy(_args):
        ran.append("git_push")
        return "pushed"

    import jarvis.brain.tools as T
    real_handlers = T.tool_handlers
    T.tool_handlers = lambda: {**real_handlers(), "git_push": _spy}
    try:
        out = await M.run_steps([{"tool": "git_push", "args": {}}], "t", authorized=False)
        check("an UNauthorised macro refuses a gated step", "confirmation" in out.lower(), out)
        check("...and the tool never ran", ran == [], str(ran))

        out = await M.run_steps([{"tool": "git_push", "args": {}}], "t", authorized=True)
        check("an authorised macro (owner already said yes) runs it", ran == ["git_push"], str(ran))
    finally:
        T.tool_handlers = real_handlers

    check("run_steps defaults to UNauthorised, so any future caller is safe by default",
          "authorized" in M.run_steps.__kwdefaults__ if M.run_steps.__kwdefaults__ else False)
    check("...and that default is False", (M.run_steps.__kwdefaults__ or {}).get("authorized") is False)

    print("\n[3] macro steps are tracked and audited like any other tool call")
    from jarvis.shared import errors as err

    err._DIR = Path(tempfile.mkdtemp())
    err.JOURNAL = err._DIR / "errors.jsonl"

    async def _boom(_args):
        raise RuntimeError("step blew up")

    T.tool_handlers = lambda: {**real_handlers(), "_boom": _boom}
    try:
        await M.run_steps([{"tool": "_boom", "args": {"a": 1}}], "t", authorized=True)
    finally:
        T.tool_handlers = real_handlers
    entries = err.read(limit=5)
    check("a failing macro step is journalled",
          any(e["subsystem"] == "macro-step/_boom" for e in entries), str(entries))
    await M.run_steps([{"tool": "_nosuch", "args": {}}], "t", authorized=True)
    check("an unknown macro step is journalled",
          any(e["subsystem"] == "macro-step/_nosuch" for e in err.read(limit=4)))

    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
