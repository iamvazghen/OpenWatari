"""Point an audit-only copy of the pass resolver at the FILTERED store.

The live resolver hard-codes STORE = ~/.password-store and ignores PASSWORD_STORE_DIR, so the
env-var approach silently resolved nothing. A copy with the constant rewritten is the honest fix:
the audit gateway can decrypt exactly the two model keys that were copied, and every other
credential is not merely unauthorised but absent from the store it reads.
"""
import json, re, shutil
from pathlib import Path

HOME = Path.home()
LIVE = HOME / ".openclaw" / "secrets" / "pass-resolver.py"
DST_DIR = HOME / ".openclaw-audit" / "secrets"
DST = DST_DIR / "pass-resolver.py"
STORE = HOME / ".openclaw-audit" / "password-store"

DST_DIR.mkdir(parents=True, exist_ok=True)
src = LIVE.read_text()
patched, n = re.subn(r'STORE\s*=\s*os\.path\.expanduser\([^)]*\)',
                     f'STORE = {str(STORE)!r}', src, count=1)
if n != 1:
    raise SystemExit(f"refusing to write: expected 1 STORE assignment, patched {n}")
DST.write_text(patched)
DST.chmod(0o755)
print("audit resolver ->", DST)
print("   STORE now:", [l for l in patched.splitlines() if l.startswith("STORE")][0])

cfg = HOME / ".openclaw-audit" / "openclaw.json"
a = json.loads(cfg.read_text())
a["secrets"] = {
    "providers": {
        "pass": {
            "source": "exec",
            "command": str(DST),
            "env": {"GNUPGHOME": str(HOME / ".gnupg"), "PATH": "/usr/bin:/bin"},
            "timeoutMs": 15000,
            "jsonOnly": True,
        }
    },
    "defaults": {"exec": "pass"},
}
cfg.write_text(json.dumps(a, indent=2))
print("secrets block rewritten to the live shape, pointed at the audit resolver")
