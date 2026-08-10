"""Give the audit clone LLM access and nothing else.

The clone needs a working model provider or no agent turn can run at all — but re-attaching the
real password store would hand it Notion, Telegram, Google and every other credential, which is
exactly what the clone exists to prevent.

So: a FILTERED pass store containing only the two model keys, and the model refs restored to point
at it. Every other secret reference stays neutralised, so those tools are dead rather than merely
unused. The copied entries are still GPG-encrypted with the same key; nothing is decrypted here.
"""
import json, shutil
from pathlib import Path

HOME = Path.home()
SRC_STORE = HOME / ".password-store"
DST_STORE = HOME / ".openclaw-audit" / "password-store"

KEEP = ["apis/minimax/key", "apis/freellmapi/key"]

if DST_STORE.exists():
    shutil.rmtree(DST_STORE)
DST_STORE.mkdir(parents=True)
shutil.copy2(SRC_STORE / ".gpg-id", DST_STORE / ".gpg-id")

copied = []
for entry in KEEP:
    src = SRC_STORE / (entry + ".gpg")
    if not src.exists():
        print("   MISSING", entry)
        continue
    dst = DST_STORE / (entry + ".gpg")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    copied.append(entry)

total = sum(1 for _ in SRC_STORE.rglob("*.gpg"))
print(f"filtered store: {len(copied)} of {total} entries copied -> {DST_STORE}")
for c in copied:
    print("   kept:", c)

# Restore ONLY the model refs; everything else stays inert.
p = HOME / ".openclaw-audit" / "openclaw.json"
a = json.loads(p.read_text())
prov = a["models"]["providers"]
prov["minimax"]["apiKey"] = {"source": "exec", "provider": "pass", "id": "apis/minimax/key"}
prov["litellm"]["apiKey"] = {"source": "exec", "provider": "pass", "id": "apis/freellmapi/key"}
# Re-declare the pass backend, pointed at the FILTERED store.
a["secrets"] = {
    "providers": {"pass": {"type": "exec", "command": ["pass", "show", "{id}"],
                           "env": {"PASSWORD_STORE_DIR": str(DST_STORE)}}},
    "defaults": {"exec": "pass"},
}
p.write_text(json.dumps(a, indent=2))
print("restored model refs; pass backend -> filtered store only")
