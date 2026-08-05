"""C6 — recovery-protocol drills: every protocol is provably invocable + correctly gated, and a drill
NEVER launches the real (stop/restart/reboot) script. Patches subprocess.Popen to assert no launch.
"""
import asyncio

import jarvis.brain.protocols as proto
from jarvis.brain.protocols import protocol_names, run_protocol
from jarvis.config import settings

_ok = 0
_fail = 0


def check(cond, label):
    global _ok, _fail
    if cond:
        _ok += 1
    else:
        _fail += 1
        print(f"  FAIL: {label}")


# Guarantee protocols are enabled for the drill regardless of ambient config.
settings.protocols_enabled = True

# Record every Popen so we can prove drills DON'T launch and live runs DO reach the launch path.
# (Return a dummy so the engine's own try/except sees a normal launch, not an error.)
_launched = []


class _DummyProc:
    pid = 999


proto.subprocess.Popen = lambda *a, **k: (_launched.append(a), _DummyProc())[1]


def _password_for(name):
    return getattr(settings, f"protocol_{name}_password")


async def main():
    names = protocol_names()
    check(len(names) >= 4, f"there are recovery protocols to drill ({names})")

    # --- 1) every protocol drills OK with the right password, and launches NOTHING ----------
    for name in names:
        r = run_protocol(name, _password_for(name), drill=True)
        check(r.ok and "Drill OK" in r.message, f"drill '{name}' passes with the correct password")
    check(_launched == [], "no protocol drill ever launched a real script (Popen untouched)")

    # --- 2) the password gate still holds under drill (wrong password refused, nothing runs) --
    r_bad = run_protocol(names[0], "definitely-wrong-password", drill=True)
    check((not r_bad.ok) and "incorrect" in r_bad.message, "a wrong password is refused even in drill mode")

    # --- 3) an unknown protocol is reported, not launched -----------------------------------
    r_unk = run_protocol("no-such-protocol", "x", drill=True)
    check((not r_unk.ok) and "no protocol" in r_unk.message, "unknown protocol reported cleanly")

    # --- 4) the tool surface exposes drill + still gates on the password --------------------
    from jarvis.brain.tools.protocols import run_protocol as tool_run, SCHEMAS
    said = await tool_run({"name": names[0], "drill": True})  # no password
    check("password" in said.lower(), "tool asks for the password before drilling")
    props = SCHEMAS[0]["function"]["parameters"]["properties"]
    check("drill" in props, "run_protocol schema advertises the drill parameter")

    # --- 5) a real (non-drill) run with a good password DOES reach the launch path -----------
    _launched.clear()
    # Must be a BRAIN-SIDE protocol. Since 2026-08-01 the sync launcher refuses the PC_LINK-routed
    # three outright (they'd run laptop-era taskkill/shutdown against the VPS), so using names[0] —
    # goodnight — would now assert the very behaviour that was removed as a hazard.
    from jarvis.brain.protocols import _registry as _proto_reg

    live_name = next(n for n, p in _proto_reg().items() if not p.get("pc_command"))
    r_live = run_protocol(live_name, _password_for(live_name), drill=False)
    check(r_live.ok and len(_launched) == 1,
          f"a live (non-drill) run of brain-side '{live_name}' launches the script "
          "(drill is what suppresses it)")

    # --- 6) machine-level protocols act on the LAPTOP, not on whatever host the brain runs on ---
    # Regression guard for the 2026-07-30 finding: with the brain on the VPS these ran server-side and
    # reported success while doing nothing (shutdown without sudo / taskkill on Linux).
    from jarvis.brain import pc_link
    from jarvis.brain.protocols import run_protocol_async

    forwarded = []

    class _FakeLink:
        active = True

        async def forward(self, op, args):
            forwarded.append((op, args))
            return "ok"

    real_link = pc_link.PC_LINK
    pc_link.PC_LINK = _FakeLink()
    _launched.clear()
    try:
        r = await run_protocol_async("ragnarok", _password_for("ragnarok"))
        check(r.ok and len(forwarded) == 1 and forwarded[0][0] == "run_powershell",
              "ragnarok is dispatched to the laptop over PC_LINK")
        check("shutdown /r" in forwarded[0][1]["command"],
              "ragnarok's laptop command actually restarts Windows")
        check(_launched == [], "a laptop-targeted protocol does NOT run a brain-side script")

        forwarded.clear()
        r = await run_protocol_async("ragnarok", _password_for("ragnarok"), drill=True)
        check(r.ok and forwarded == [], "drilling a laptop protocol dispatches nothing")

        # Brain-side protocols are unaffected and still launch locally.
        forwarded.clear(); _launched.clear()
        r = await run_protocol_async("backup", _password_for("backup"))
        check(r.ok and len(_launched) == 1 and forwarded == [],
              "brain-side protocols (backup) still run on the brain")

        # Laptop offline -> refuse loudly instead of claiming success.
        pc_link.PC_LINK = type("Off", (), {"active": False})()
        r = await run_protocol_async("ragnarok", _password_for("ragnarok"))
        check((not r.ok) and "laptop" in r.message.lower(),
              "with the laptop offline, a machine protocol refuses instead of silently no-op'ing")
    finally:
        pc_link.PC_LINK = real_link

    print(f"=== {_ok}/{_ok + _fail} checks passed ===")
    import sys
    sys.exit(1 if _fail else 0)


asyncio.run(main())
