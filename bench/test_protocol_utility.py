"""Each of the eight protocols does the thing it claims to do.

`test_protocol_drills.py` proves the authorization gate: the right password runs, a wrong one
refuses, an unset one fails closed. That is the half that was tested, and it is the half that was
never broken. This file tests the other half — whether the protocol's *utility* actually happens —
and writing it found that one of the eight had never worked at all:

    auditpack archived `<repo>/audit`, which does not exist on this deployment. `brain/audit.py`
    writes to `settings.audit_log_dir`, a folder in the Obsidian vault. `main()` hit
    `if not audit.is_dir(): return`, wrote nothing, exited 0, and the runner told the owner
    "Audit archive started, sir." It had been reporting success and producing nothing for months.

That is why the checks below run the scripts and look at the artifacts, rather than asserting the
scripts exist. The three machine-level protocols (goodnight, phoenix, ragnarok) are never executed
— restarting the owner's laptop is not a test — so for those the assertion is that they are inert
by construction and that the real work lives in a PC_LINK command aimed at the right machine.

    uv run python bench/test_protocol_utility.py
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


#: What each protocol is FOR, and how you can tell it happened. `artifact` None means the utility
#: is a machine action rather than a file, and is verified structurally instead.
UTILITY = {
    "backup":      ("archive Afon's memory",           "afon-memory-*.zip"),
    "diagnostics": ("write a config health report",    "afon-diagnostics-*.txt"),
    "auditpack":   ("archive the tool-call audit log", "afon-audit-*.zip"),
    "checkpoint":  ("archive non-secret context",      "afon-checkpoint-*.zip"),
    "ping":        ("prove the phone push works",      "afon-ping-*.txt"),
    "goodnight":   ("stop the edge on the laptop",     None),
    "phoenix":     ("restart the edge on the laptop",  None),
    "ragnarok":    ("restart the laptop",              None),
}

MACHINE = ("goodnight", "phoenix", "ragnarok")


def run_script(name: str, tmp: Path) -> subprocess.CompletedProcess:
    """Run a protocol exactly as the runner launches it: <parent_pid> <repo_root> <python_exe>."""
    return subprocess.run(
        [sys.executable, "-m", f"afon.protocols.{name}", "99999", str(tmp), sys.executable],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")})


def main() -> int:
    import shutil
    import tempfile

    from afon.brain.protocols import _REPORTS, _registry

    reg = _registry()

    print(f"[1] every protocol declares a utility, and every declared one exists ({len(reg)})")
    check(f"the registry holds exactly the {len(UTILITY)} declared protocols",
          set(reg) == set(UTILITY), f"registry={sorted(reg)} declared={sorted(UTILITY)}")
    for name, entry in sorted(reg.items()):
        script = ROOT / "src" / "afon" / "protocols" / entry["script"]
        check(f"{name}: its script is present ({entry['script']})", script.is_file())
    for name, entry in sorted(reg.items()):
        check(f"{name}: describes itself as '{entry['description']}'", bool(entry["description"]))

    print("\n[2] the machine-level three are inert scripts routed to the laptop")
    # They must NOT act on the brain host. Before 2026-08-01 ragnarok's fallback aimed `shutdown`
    # at the VPS and goodnight killed the brain's own parent pid, which systemd revived seconds
    # later — "goodnight" therefore meant "Afon dies and immediately returns".
    import ast

    import tempfile as _tf
    for name in MACHINE:
        entry = reg[name]
        check(f"{name}: carries a pc_command aimed at the laptop", bool(entry.get("pc_command")))
        # Parsed, not grepped. Each of these files documents the destructive call it USED to make
        # ("taskkill", "shutdown"), so a substring scan flags the explanation as the offence — the
        # same mistake the exit-code scanner made against its own comment.
        tree = ast.parse((ROOT / "src" / "afon" / "protocols" / entry["script"]).read_text(encoding="utf-8"))
        # An inert script needs `sys` and nothing else, so that is the invariant — not a list of
        # forbidden calls. Chasing calls was the first version and `import subprocess as _s`
        # walked straight past it; an allowlist of imports cannot be aliased around, and it makes
        # adding any capability here a decision somebody has to make on purpose.
        ALLOWED_IMPORTS = {"sys", "__future__"}
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        check(f"{name}: the script imports nothing that could act ({sorted(imported)})",
              imported <= ALLOWED_IMPORTS,
              f"{sorted(imported - ALLOWED_IMPORTS)} — a second implementation of a destructive "
              "action is one that can fire on the wrong host")
        # Behaviour, not spelling: launch it and read the exit code. `raise SystemExit(main())`
        # returning 2 is just as correct as `sys.exit(1)`, and a literal scan missed that.
        with _tf.TemporaryDirectory() as td:
            proc = run_script(name, Path(td))
        check(f"{name}: exits NON-zero if something launches it anyway (got {proc.returncode})",
              proc.returncode != 0,
              "exiting 0 would make a misrouted call look like it worked")
    check("only the machine-level protocols carry a pc_command",
          {n for n, e in reg.items() if e.get("pc_command")} == set(MACHINE),
          str(sorted(n for n, e in reg.items() if e.get("pc_command"))))

    print("\n[3] the brain-side five actually produce their artifact")
    tmp = Path(tempfile.mkdtemp(prefix="afon-protocol-utility-"))
    try:
        # A repo_root of its own, so a real run cannot be mistaken for this one and vice versa.
        (tmp / "backups").mkdir()
        shutil.copy(ROOT / ".env", tmp / ".env") if (ROOT / ".env").is_file() else None
        (tmp / "memory").mkdir(exist_ok=True)
        (tmp / "memory" / "seed.md").write_text("a fact worth backing up", encoding="utf-8")

        for name, (utility, pattern) in sorted(UTILITY.items()):
            if pattern is None:
                continue
            proc = run_script(name, tmp)
            made = sorted((tmp / "backups").glob(pattern))
            check(f"{name}: exits cleanly", proc.returncode == 0,
                  (proc.stderr or proc.stdout)[-200:])
            check(f"{name}: produces the artifact that proves it {utility} ({pattern})",
                  bool(made), f"stdout={proc.stdout[-160:]!r} dir={[p.name for p in (tmp/'backups').iterdir()]}")
            if made:
                check(f"{name}: and the artifact is not empty",
                      made[-1].stat().st_size > 0, str(made[-1].stat().st_size))

        print("\n[4] auditpack archives the audit log that is ACTUALLY being written")
        # The defect this file was written for. A protocol that keeps its own copy of a path is a
        # protocol that quietly archives the wrong (or no) directory — J3.6, again.
        from afon.brain.audit import _audit_dir
        from afon.shared.paths import audit_dir
        check("brain and protocols resolve the audit directory through one function",
              _audit_dir() == audit_dir(), f"{_audit_dir()} != {audit_dir()}")
        src = (ROOT / "src" / "afon" / "protocols" / "auditpack.py").read_text(encoding="utf-8")
        check("auditpack does not rebuild the path itself",
              'repo_root / "audit"' not in src,
              "its own spelling of the location is what made it archive nothing for months")
        check("it asks shared.paths instead", "audit_dir" in src)

        made = sorted((tmp / "backups").glob("afon-audit-*.zip"))
        real = [p.name for p in audit_dir().glob("*.jsonl")] if audit_dir().is_dir() else []
        if made and real:
            names = zipfile.ZipFile(made[-1]).namelist()
            check(f"the archive contains the real logs ({len(names)} members)",
                  any(n in real for n in names), f"{names[:3]} vs {real[:3]}")

        # The empty case, forced rather than hoped for. This machine HAS audit logs, so the
        # branch that matters — "there was nothing to archive" — never ran, and a plant that
        # deleted it passed. A check whose interesting path depends on the developer's own
        # machine state is not a check.
        import os as _os
        empty_root = tmp / "empty-audit"
        (empty_root / "backups").mkdir(parents=True)
        proc = subprocess.run(
            [sys.executable, "-m", "afon.protocols.auditpack", "99999", str(empty_root), sys.executable],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180,
            env={**_os.environ, "PYTHONPATH": str(ROOT / "src"),
                 "AFON_AUDIT_LOG_DIR": str(tmp / "no-such-audit-dir")})
        empties = sorted((empty_root / "backups").glob("afon-audit-*.zip"))
        check("with NO logs to archive it still produces an artifact",
              bool(empties),
              f"nothing written; the owner hears 'archive started' and then nothing. {proc.stdout[-120:]}")
        if empties:
            names = zipfile.ZipFile(empties[-1]).namelist()
            body = zipfile.ZipFile(empties[-1]).read(names[0]).decode("utf-8", "replace")
            check("...which explains that there was nothing, and where it looked",
                  any("EMPTY" in n for n in names) and "Looked in" in body, str(names))

        print("\n[5] ping REPORTS its verdict, including when the push fails")
        # The failure mode that mattered: `except Exception: return`. The one tool whose entire
        # purpose is detecting a broken push channel reported success when the push failed.
        src = (ROOT / "src" / "afon" / "protocols" / "ping.py").read_text(encoding="utf-8")
        check("a failed push is not swallowed",
              "except Exception:\n        return" not in src and "_report(" in src,
              "a test that always says yes is worse than no test")
        pings = sorted((tmp / "backups").glob("afon-ping-*.txt"))
        check("it writes a verdict either way", bool(pings), "no report written")
        if pings:
            body = pings[-1].read_text(encoding="utf-8")
            check("the verdict says plainly whether the phone was reached",
                  ("REACHED your phone" in body) or ("DID NOT reach your phone" in body), body[:120])
            check("...and says why, so it is actionable",
                  len(body.strip().splitlines()) >= 3, body[:120])

        # ...including the unconfigured case, forced. The tmp root copies the real .env, which HAS
        # a topic — so "no topic configured" never ran here either, and deleting its report passed.
        no_topic = tmp / "no-topic"
        (no_topic / "backups").mkdir(parents=True)
        (no_topic / ".env").write_text("AFON_ASSISTANT_NAME=Afon\n", encoding="utf-8")
        run_script("ping", no_topic)
        unconfigured = sorted((no_topic / "backups").glob("afon-ping-*.txt"))
        check("an UNCONFIGURED push channel is reported, not passed over in silence",
              bool(unconfigured),
              "'no topic set' is the most likely reason a push never arrives, and the most "
              "silently returned")
        if unconfigured:
            check("...and the report names the setting to fix",
                  "AFON_NTFY_TOPIC" in unconfigured[-1].read_text(encoding="utf-8"))

        print("\n[6] every protocol that produces a report has one delivered")
        producing = {n for n, (_u, pattern) in UTILITY.items() if pattern}
        check(f"all {len(producing)} artifact-producing protocols are registered for delivery",
              producing - set(_REPORTS) <= {"backup"},
              f"produce an artifact nobody sends: {sorted(producing - set(_REPORTS) - {'backup'})}")
        check("backup is deliberately not delivered (an archive of his memory is not a chat file)",
              "backup" not in _REPORTS)
        # Behaviourally. Reading the source for "send_telegram" passed with the call replaced by
        # a no-op, because the `from ... import send_telegram` line above it still matched. Drive
        # the function instead, with a pattern nothing will ever produce.
        import asyncio
        import time as _time

        import afon.brain.protocols as P
        import afon.brain.tools.telegram as TG

        sent: list[dict] = []

        async def _fake_send(args):
            sent.append(args)
            return "ok"

        saved_send, saved_reports, saved_wait = TG.send_telegram, dict(P._REPORTS), P._REPORT_WAIT_S
        try:
            TG.send_telegram = _fake_send
            P._REPORTS["nevermade"] = "afon-nevermade-*.zip"
            P._REPORT_WAIT_S = 0.1          # don't spend 90s proving a negative
            asyncio.run(P._deliver_report("nevermade", _time.time()))
            check("a protocol that produces NOTHING tells the owner so", bool(sent),
                  "he was told it started; silence afterwards is the other half of a sentence "
                  "that never arrives")
            if sent:
                msg = str(sent[-1].get("message", ""))
                check("...naming the protocol and what was expected",
                      "nevermade" in msg and "no report" in msg.lower(), msg[:140])
        finally:
            TG.send_telegram, P._REPORT_WAIT_S = saved_send, saved_wait
            P._REPORTS.clear()
            P._REPORTS.update(saved_reports)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
