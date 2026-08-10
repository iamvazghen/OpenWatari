"""Neutralise every secret reference in the audit clone -- BOTH shapes.

A reference is not always a string. The one that broke gateway startup is an OBJECT:

    "apiKey": {"source": "exec", "provider": "pass", "id": "apis/freellmapi/key"}

A regex over string values finds none of those, which is why the first attempt reported "0
neutralised" and the gateway kept failing on a ref that was plainly in the file.

Re-adding the `pass` provider would also fix startup -- by handing the audit profile the owner's
real Notion / Telegram / Google credentials, i.e. by undoing the entire point of the clone. So the
references are replaced with an inert literal: startup succeeds and every credential-backed tool
is genuinely dead. Model keys are literals under models.providers and are left alone.
"""
import json, re
from pathlib import Path

p = Path.home() / ".openclaw-audit" / "openclaw.json"
a = json.loads(p.read_text())
STR_REF = re.compile(r"^(exec:|file:|pass:|secret:|env:)", re.I)
REF_KEYS = {"source", "provider", "id"}
hits = []

def is_ref_obj(v):
    return isinstance(v, dict) and REF_KEYS.issubset(v.keys()) and len(v) <= 4

def walk(node, path=""):
    if isinstance(node, dict):
        for k, v in list(node.items()):
            here = f"{path}.{k}"
            if is_ref_obj(v):
                node[k] = "audit-profile-no-secret"
                hits.append(f"{here}  ({v.get('source')}:{v.get('provider')}:{v.get('id')})")
            elif isinstance(v, str) and STR_REF.match(v):
                node[k] = "audit-profile-no-secret"
                hits.append(here)
            else:
                walk(v, here)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            here = f"{path}[{i}]"
            if is_ref_obj(v):
                node[i] = "audit-profile-no-secret"
                hits.append(here)
            else:
                walk(v, here)

walk(a)
p.write_text(json.dumps(a, indent=2))
print(f"neutralised {len(hits)} secret reference(s)")
for h in hits[:20]:
    print("   ", h)
