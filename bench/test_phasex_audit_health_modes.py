"""Phase X (cross-cutting) — audit log, self-health, behavioural modes & routines. Hermetic.

Verifies: the audit log writes a redacted trail (secrets never hit disk); self-health snapshots and
summarises, and emits a proactive signal only when something is actually wrong; the runtime modes
(focus/lockdown) gate unprompted speech; and the routine tool flips modes / backs up memory / rejects
unknown names. Audit goes to a temp dir; no network beyond an offline vault check.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f" — {detail}" if detail and not ok else ""))


def main() -> None:
    import afon.config as cfg
    from afon.brain import audit

    print("[1] audit log writes a redacted trail (secrets never on disk)")
    tmp = Path(tempfile.mkdtemp(prefix="afon-audit-"))
    old_dir = cfg.settings.audit_log_dir
    try:
        cfg.settings.audit_log_dir = str(tmp)
        audit.record("run_protocol", {"name": "goodnight", "password": "Vanadzor"}, "Goodnight, sir.")
        audit.record("web_search", {"query": "btc price"}, "Answer: ...")
        files = list(tmp.glob("*.jsonl"))
        content = files[0].read_text(encoding="utf-8") if files else ""
        check("an audit file was written", bool(files))
        check("the password is NOT on disk", "Vanadzor" not in content, "secret leaked!")
        check("the password field is redacted", "***redacted***" in content)
        entries = audit.recent(10)
        check("recent() reads back entries", len(entries) == 2, str(len(entries)))
        check("non-secret args are kept", entries[1]["args"].get("query") == "btc price")
    finally:
        cfg.settings.audit_log_dir = old_dir

    print("\n[2] self-health snapshot + summary")
    from afon.brain.health import check as health_check
    from afon.brain.health import health_signals, summarize

    snap = asyncio.run(health_check())
    check("snapshot has vault/cache/ticker", {"vault", "cache", "ticker"} <= set(snap))
    healthy = {"vault": {"ok": True, "detail": ""}, "cache": {"ok": True, "detail": ""},
               "ticker": {"ok": True, "detail": ""}}
    check("all-ok summarises as nominal", "nominal" in summarize(healthy).lower())
    degraded = {"vault": {"ok": False, "detail": "missing"}, "cache": {"ok": True, "detail": ""},
                "ticker": {"ok": True, "detail": ""}}
    check("a fault is named in the summary", "vault" in summarize(degraded).lower())

    print("\n[3] health_signals fires only when something is wrong")
    old_vault = cfg.settings.vault_path
    try:
        cfg.settings.vault_path = str(tmp / "nope-not-a-vault")
        sigs = asyncio.run(health_signals())
        keys = {s.key for s in sigs}
        check("a vault-down signal is emitted", "health-vault" in keys, str(keys))
    finally:
        cfg.settings.vault_path = old_vault
    # With the real (present) vault, no vault signal.
    sigs_ok = asyncio.run(health_signals())
    check("no vault signal when the vault is readable",
          "health-vault" not in {s.key for s in sigs_ok})

    print("\n[4] modes gate unprompted speech")
    from afon.brain.modes import MODES

    MODES.clear()
    check("default not suppressed", not MODES.proactivity_suppressed())
    MODES.set_focus(30)
    check("focus active suppresses", MODES.proactivity_suppressed())
    check("status reports focus", "focus" in MODES.status())
    MODES.clear()
    MODES.lockdown = True
    check("lockdown suppresses", MODES.proactivity_suppressed())
    MODES.clear()
    check("cleared -> normal", MODES.status() == "normal")

    print("\n[5] proactive engine honours lockdown")
    from afon.brain.proactive import ProactiveEngine, Signal

    sent = []

    async def emit(m, u, speak):
        sent.append(m)
        return "voice"

    eng = ProactiveEngine(emit=emit, sources=[lambda: [Signal("k", "urgent thing", 0.99)]],
                          is_listening=lambda: True, daily_budget=5, threshold=0.6)
    MODES.lockdown = True
    asyncio.run(eng.maybe_interject())
    check("locked down -> nothing said", sent == [], str(sent))
    MODES.clear()
    asyncio.run(eng.maybe_interject())
    check("after clearing -> it speaks", len(sent) == 1, str(sent))

    print("\n[6] routine tool flips modes, backs up, and rejects unknowns")
    import afon.brain.tools.routines as routines

    MODES.clear()
    out = asyncio.run(routines.routine({"name": "focus", "minutes": 15}))
    check("focus routine engages focus", MODES.focus_active() and "focus" in out.lower(), out)
    asyncio.run(routines.routine({"name": "normal"}))
    check("normal routine clears modes", MODES.status() == "normal")
    out = asyncio.run(routines.routine({"name": "lockdown"}))
    check("lockdown routine engages lockdown", MODES.lockdown)
    MODES.clear()
    unknown = asyncio.run(routines.routine({"name": "flibbertigibbet"}))
    check("unknown routine is rejected cleanly", "don't have" in unknown.lower(), unknown)
    check("home-location tool is registered", "set_home_location" in routines.HANDLERS)
    from afon.brain import prefs

    original_home = routines.settings.home_location
    original_home_pref = prefs.get("home_location")
    routines.settings.home_location = "Cologne, Germany"
    try:
        out = asyncio.run(routines.set_home_location({"location": "Yerevan, Armenia"}))
        check("home location can be changed at runtime", "Yerevan, Armenia" in out, out)
        out = asyncio.run(routines.set_home_location({}))
        check("home location reports runtime override", "Yerevan, Armenia" in out, out)
    finally:
        routines.settings.home_location = original_home
        prefs.set("home_location", original_home_pref)

    print("\n[7] backup routine archives memory")
    out = asyncio.run(routines.routine({"name": "backup"}))
    backups = Path(__file__).resolve().parents[1] / "backups"
    made = sorted(backups.glob("afon-memory-*.zip")) if backups.exists() else []
    check("a memory archive was created", "Backed up" in out and bool(made), out)
    for z in made:           # clean up the test artifact(s)
        z.unlink(missing_ok=True)

    print("\n[8] self_health tool speaks a status line")
    out = asyncio.run(routines.self_health({}))
    check("self_health returns a spoken status", isinstance(out, str) and len(out) > 10, out)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
