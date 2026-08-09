"""Protocol smoke + overlap audit (TODO 6 P1) — the recovery protocols are never drilled.

You reach for phoenix/ragnarok exactly when things are on fire, so a script that only breaks THEN is
the worst kind of latent bug. This drills all 8 protocols WITHOUT firing the destructive action:

  * every protocol script parses (valid Python — no syntax rot that surfaces only at 3am);
  * the recovery scripts contain the behaviour they claim (phoenix relaunches the edge, ragnarok
    reboots, goodnight terminates) — a structural check, not an execution;
  * the password gate holds and, with the RIGHT password, run_protocol launches the RIGHT script —
    with subprocess.Popen STUBBED so nothing is actually killed/rebooted;
  * OVERLAP AUDIT: the three archive protocols (backup / checkpoint / auditpack) target DISTINCT
    roots, so none is redundant.

Fully hermetic — no process is ever really terminated.

    uv run python bench/test_protocol_smoke.py
"""

from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "src" / "afon" / "protocols"
passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f"  {detail}" if detail and not ok else ""))


def _src(name: str) -> str:
    return (SCRIPT_DIR / f"{name}.py").read_text(encoding="utf-8")


async def main() -> None:
    import afon.brain.protocols as P
    from afon.brain.tools import protocols as protocols_tool

    all_protos = P.protocol_names()

    print("[1] every protocol script parses (no latent syntax rot)")
    for name in all_protos:
        p = SCRIPT_DIR / f"{P._registry()[name]['script']}"
        check(f"{name}: script exists", p.is_file(), str(p))
        try:
            ast.parse(p.read_text(encoding="utf-8"))
            ok = True
        except SyntaxError as e:
            ok = False
            print(f"      syntax error: {e}")
        check(f"{name}: valid Python", ok)

    print("\n[2] recovery protocols carry their claimed behaviour — in the REGISTRY, not the scripts")
    # Until 2026-08-01 these three checks read the SCRIPTS, which is where the behaviour used to live.
    # The brain moved to the VPS and the scripts became landmines (taskkill against what is now the
    # brain's pid; `shutdown -r` aimed at the server), so the real commands moved into the registry as
    # `pc_command` and travel to the laptop. Asserting against the scripts would now pin the bug.
    from afon.brain.protocols import _registry as _reg

    _cmds = {n: (p.get("pc_command") or "") for n, p in _reg().items()}
    check("phoenix RELAUNCHES the edge (AfonEdgeRefresh on the laptop)",
          "AfonEdgeRefresh" in _cmds["phoenix"], _cmds["phoenix"][:70])
    check("ragnarok REBOOTS the machine (shutdown /r on the laptop)",
          "shutdown" in _cmds["ragnarok"].lower() and "/r" in _cmds["ragnarok"], _cmds["ragnarok"][:70])
    check("goodnight STOPS the edge and marks the silence as ordered",
          "edge_stopped_by_owner" in _cmds["goodnight"], _cmds["goodnight"][:70])

    print("\n[3] password gate holds; correct password launches the RIGHT script (Popen STUBBED)")
    # Stub subprocess.Popen inside the protocols module so a 'launch' records the argv but runs nothing.
    launched: dict = {}

    class _FakePopen:
        def __init__(self, argv, **kw):
            launched["argv"] = argv
            launched["kw"] = kw

    real_popen = P.subprocess.Popen
    P.subprocess.Popen = _FakePopen  # type: ignore[assignment]
    try:
        # Wrong password never launches.
        launched.clear()
        r = await protocols_tool.run_protocol({"name": "phoenix", "password": "definitely-wrong"})
        check("phoenix wrong password refuses", "incorrect" in r.lower(), r)
        check("phoenix wrong password launched NOTHING", "argv" not in launched)

        # Right password + a connected laptop -> the op travels to the LAPTOP, and NOTHING is launched
        # on the brain host. Before 2026-07-30 these ran wherever the brain was: on the VPS that meant
        # `shutdown` without sudo and `taskkill` on Linux — silent no-ops that still said "restarting".
        from afon.brain import pc_link

        forwarded: list = []

        class _FakeLink:
            active = True

            async def forward(self, op, args):
                forwarded.append((op, args))
                return "ok"

        real_link = pc_link.PC_LINK
        pc_link.PC_LINK = _FakeLink()
        try:
            for name, needle in (("phoenix", "AfonEdgeRefresh"), ("ragnarok", "shutdown /r")):
                launched.clear()
                forwarded.clear()
                pw = P._registry()[name]["password"]
                r = await protocols_tool.run_protocol({"name": name, "password": pw})
                check(f"{name} is sent to the laptop, not run on the brain host",
                      len(forwarded) == 1 and "argv" not in launched, f"{forwarded} {launched}")
                check(f"{name}'s laptop command actually does the job ({needle})",
                      needle in forwarded[0][1].get("command", ""), str(forwarded))

            # No laptop -> refuse out loud rather than report a success that never happened.
            pc_link.PC_LINK = type("Off", (), {"active": False})()
            launched.clear()
            r = await protocols_tool.run_protocol(
                {"name": "ragnarok", "password": P._registry()["ragnarok"]["password"]})
            check("with no laptop connected, ragnarok refuses", "laptop" in r.lower(), r)
            check("...and still launches nothing locally", "argv" not in launched)
        finally:
            pc_link.PC_LINK = real_link

        # Brain-side protocols are untouched: they still launch their own script here.
        launched.clear()
        r = await protocols_tool.run_protocol(
            {"name": "backup", "password": P._registry()["backup"]["password"]})
        argv = launched.get("argv", [])
        check("backup (brain-side) still launches backup.py", any("backup.py" in str(a) for a in argv),
              str(argv))
        check("backup passes pid+repo+python args", len(argv) >= 4, str(argv))
    finally:
        P.subprocess.Popen = real_popen  # type: ignore[assignment]

    print("\n[4] OVERLAP AUDIT — the three archive protocols target DISTINCT roots")
    backup_src, checkpoint_src, auditpack_src = _src("backup"), _src("checkpoint"), _src("auditpack")
    # backup = the LIVE memory (learned facts + journal).
    check("backup archives memory/ (the durable memory data)", '"memory"' in backup_src
          or "'memory'" in backup_src or "/ \"memory\"" in backup_src or "memory" in backup_src)
    # checkpoint = config/persona/docs, and it SKIPS the memory data (learned/journal) that backup owns.
    check("checkpoint archives personality + docs (config/context)",
          "personality" in checkpoint_src and "docs" in checkpoint_src)
    check("checkpoint SKIPS learned/journal (backup owns those — no data overlap)",
          "learned" in checkpoint_src and "journal" in checkpoint_src
          and "SKIP" in checkpoint_src.upper())
    # auditpack = the audit log dir only.
    check("auditpack archives the audit/ logs only", "audit" in auditpack_src
          and "make_archive" in auditpack_src)
    # Distinct output names prove three different artifacts, not one operation thrice.
    check("three DISTINCT archive names (memory / checkpoint / audit)",
          "afon-memory-" in backup_src and "afon-checkpoint-" in checkpoint_src
          and "afon-audit-" in auditpack_src)
    # phoenix is a RESTART, not an archive — no overlap with the archive trio (audit conclusion).
    check("phoenix is a restart, not an archive (no backup/checkpoint overlap)",
          "zipfile" not in _src("phoenix") and "make_archive" not in _src("phoenix"))

    print("\n[H1.5] routed protocols cannot execute on the brain host")
    from afon.brain.protocols import _registry, run_protocol

    reg = _registry()
    routed = [n for n, p in reg.items() if p.get("pc_command")]
    check("goodnight/phoenix/ragnarok are the routed set",
          set(routed) == {"goodnight", "phoenix", "ragnarok"}, str(sorted(routed)))
    for n in routed:
        # The sync launcher is public. Reaching it for a routed protocol on the VPS would run
        # laptop-era logic against the brain host — ragnarok's old POSIX branch was `shutdown -r +1`,
        # and goodnight/phoenix ran `taskkill` on a pid that is now the BRAIN's.
        r = run_protocol(n, reg[n]["password"], drill=False)
        check(f"sync run_protocol('{n}') refuses to execute here", r.ok is False, r.message[:70])
        check("...and says why, in his voice", "laptop" in r.message.lower(), r.message[:70])
    for n in ("backup", "ping", "diagnostics", "auditpack", "checkpoint"):
        check(f"brain-side '{n}' still passes its gate",
              run_protocol(n, reg[n]["password"], drill=True).ok)

    print("\n[H1.5b] the three scripts are inert stubs, and fail loudly if ever launched")
    for n in routed:
        src = _src(n)
        # Check for the ABILITY to act, not for the words: the docstrings deliberately name taskkill
        # and shutdown while explaining why they were removed. A stub that imports only `sys` can't
        # kill or reboot anything.
        check(f"{n}.py can no longer execute anything (no subprocess/os import)",
              "import subprocess" not in src and "import os" not in src, src[:70])
        check(f"{n}.py exits non-zero (a silent success would hide a misroute)",
              "return 2" in src and "SystemExit" in src)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
