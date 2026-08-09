"""P1 #7 — security hardening of the high-power tools.

Verifies the fixes from the security pass:
  * system file ops refuse Afon's OWN secrets/state (.env, *.session, voiceprint.json, audit/),
    in addition to the existing system-path / drive-root refusal — so "delete the .env" can't land;
  * the audit log scrubs secret VALUES (not just secret-shaped keys) from args AND results, so a
    secret echoed in a tool result never reaches disk;
  * elevated-PowerShell argument quoting escapes single quotes.

Hermetic & SAFE: never deletes a real file (uses a throwaway scratch file and asserts refusal +
survival); audit writes go to a temp dir.

    uv run python bench/test_security_hardening.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
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


async def main() -> None:
    import afon.brain.tools.system as system
    from afon.brain import audit
    from afon.config import settings

    repo = Path(__file__).resolve().parents[1]

    print("[1] system file ops refuse Afon's own secrets/state")
    check(".env flagged sensitive", system._sensitive(repo / ".env"))
    check("a *.session file flagged sensitive", system._sensitive(repo / "afon.session"))
    check("voiceprint.json flagged sensitive", system._sensitive(repo / "voiceprint.json"))
    check("audit/ contents flagged sensitive", system._sensitive(repo / "audit" / "2026-06-12.jsonl"))
    check("a normal doc is NOT sensitive", not system._sensitive(repo / "README.md"))

    # A real, existing sensitive-named file — deletion must be REFUSED and the file must survive.
    scratch = repo / "bench" / "_sec_scratch.session"
    scratch.write_text("not a real session", encoding="utf-8")
    try:
        out = await system.file_op({"action": "delete_file", "path": str(scratch)})
        check("delete of a .session file is refused", "won't delete" in out.lower(), out)
        check("the file still exists after the refusal", scratch.exists())
        over = await system.file_op({"action": "create_file", "path": str(scratch), "content": "x"})
        check("overwriting a sensitive file is refused", "won't overwrite" in over.lower(), over)
    finally:
        scratch.unlink(missing_ok=True)

    print("\n[2] system file ops still refuse system/drive paths")
    check("a drive root is protected", system._protected(Path("C:/")))
    check("C:/Windows is protected", system._protected(Path("C:/Windows")))

    print("\n[3] audit scrubs secret VALUES from args and results")
    with tempfile.TemporaryDirectory() as d:
        saved = settings.audit_log_dir
        settings.audit_log_dir = d
        try:
            # A genuine secret from settings, embedded where a leak would happen: the result text.
            secret = settings.protocol_goodnight_password   # e.g. a real protocol password
            # Protocol passwords may be short numeric codes (e.g. 4-digit pins); a non-empty string
            # is enough to test redaction — length is no longer a reliable proxy.
            check("there is a secret value to test with", isinstance(secret, str) and bool(secret),
                  repr(secret))
            audit.record(
                "run_powershell",
                {"command": "echo $env:SECRET", "password": secret},
                result=f"the value is {secret} and also a token {secret}",
                ok=True,
            )
            written = Path(d)
            files = list(written.glob("*.jsonl"))
            text = files[0].read_text(encoding="utf-8") if files else ""
            check("an audit line was written", bool(text))
            check("the secret value is NOT on disk (args + result scrubbed)", secret not in text,
                  "secret leaked into audit!")
            check("the redaction marker is present", "***redacted***" in text)
        finally:
            settings.audit_log_dir = saved

    print("\n[4] elevated-PowerShell quoting escapes single quotes")
    check("single quotes are doubled", system._ps_quote("a'b") == "'a''b'", system._ps_quote("a'b"))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
