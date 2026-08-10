"""Build a SANITISED clone of the fleet config for auditing, under ~/.openclaw-audit.

Keeps what the audit is meant to measure -- the agents, their models, the tool profile, the
governance shape -- and removes every path by which a hostile probe could reach the real world.
Prints only key names, never a secret value.
"""
import json, os, shutil
from pathlib import Path

SRC = Path.home() / ".openclaw" / "openclaw.json"
DST_DIR = Path.home() / ".openclaw-audit"
DST = DST_DIR / "openclaw.json"

d = json.loads(SRC.read_text())
removed = []

# 1. No credentials: agents cannot reach Notion, Telegram, Google, or any paid API.
for k in ("secrets", "auth", "env"):
    if d.pop(k, None) is not None:
        removed.append(k)

# 2. No channels and no routing bindings: nothing can be delivered to a human.
for k in ("channels", "bindings", "talk", "commitments"):
    if d.pop(k, None) is not None:
        removed.append(k)

# 3. No scheduled work: crons must not fire inside an audit profile.
d["cron"] = {"jobs": []}
removed.append("cron.jobs->[]")

tools = d.setdefault("tools", {})
# 4. Elevated is the one that bypasses the sandbox entirely -- host actions from a CLI probe.
tools["elevated"] = {"enabled": False, "allowFrom": {}}
# 5. Keep the agents from dispatching each other: one probe must not fan out to seven agents.
tools["agentToAgent"] = {"enabled": False, "allow": []}
removed += ["tools.elevated->off", "tools.agentToAgent->off"]

# 6. The vault bind is the expensive one: rw on the canonical memory, whose local copy is a
#    one-way replica and therefore cannot restore it. Point at an empty scratch dir instead.
scratch_vault = DST_DIR / "scratch-vault"
sb = ((d.get("agents") or {}).get("defaults") or {}).get("sandbox") or {}
binds = (sb.get("docker") or {}).get("binds") or []
newb = []
for b in binds:
    if "obsidian-vault" in b:
        newb.append(f"{scratch_vault}:/memory-vault:rw")
    else:
        src = b.split(":")[0]
        newb.append(b.replace(":rw", ":ro") if Path(src).exists() else b)
if binds:
    sb["docker"]["binds"] = newb
    removed.append("vault bind -> scratch, others -> ro")

DST_DIR.mkdir(parents=True, exist_ok=True)
scratch_vault.mkdir(parents=True, exist_ok=True)
DST.write_text(json.dumps(d, indent=2))
print("wrote", DST)
print("neutralised:", ", ".join(removed))
