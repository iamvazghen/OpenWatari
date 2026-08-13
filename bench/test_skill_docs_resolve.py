"""J4.6/J4.7 — a skill doc that names a tool Afon cannot call fails SILENTLY, mid-conversation.

`skills/*.md` and `personality/*.md` are instructions to the model. They are prose, so nothing
compiles them and no test read them: a tool renamed in `brain/tools/` left the doc still telling
Afon to call the old name, and the only symptom is Afon trying, failing, and improvising — during a
conversation, with no error anyone sees afterwards.

Writing this found one: `skills/research-method.md` rung 3 offered "**`scrape_url` / `read_page`**"
and `read_page` has never been a tool. The real page-readers are `scrape_url` and `browse_web`
(`notion_read_page` is the Notion one), so a third of that rung pointed at nothing.

Two checks, because one is not enough:

  * every backticked identifier must NAME SOMETHING — a tool, a tool argument or enum value, or an
    identifier that exists in `src/afon`. That is what caught `read_page`.
  * anything close to a tool name but not a tool is flagged even when it resolves somewhere else,
    because a renamed tool usually lingers in a comment and would otherwise resolve there.

    uv run python bench/test_skill_docs_resolve.py
"""

from __future__ import annotations

import builtins
import difflib
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

#: Names that legitimately resolve OUTSIDE this codebase. Deliberately tiny and annotated: the
#: value of this test is that adding to this list is a visible act, not a silent one.
EXTERNAL = {
    "winget": "Windows package manager, named as prose in pc-control.md",
    "choco": "Chocolatey package manager, likewise",
    # MCP tools are discovered at runtime from mcp_servers, so they are not in the static registry.
    # The doc must say so — the check below enforces that, rather than trusting this entry.
    "get_news": "MyNews MCP tool; daily-briefing.md must label it as MCP",
}

passed = failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def docs() -> list[Path]:
    return sorted(list((ROOT / "skills").glob("*.md")) + list((ROOT / "personality").glob("*.md")))


def main() -> None:
    from afon.brain.tools import tool_schemas

    schemas = tool_schemas()
    tools = {s["function"]["name"] for s in schemas}
    agent_src = (ROOT / "src" / "afon" / "brain" / "agent.py").read_text(encoding="utf-8")
    builtins_in_agent = set(re.findall(r'"name":\s*"([a-z_]+)"', agent_src))
    callable_names = tools | builtins_in_agent

    print(f"[1] the surfaces this checks against")
    check(len(tools) > 100, f"tool registry loaded ({len(tools)} tools)", str(len(tools)))
    check(len(builtins_in_agent) >= 4,
          f"agent built-ins found ({len(builtins_in_agent)}) — they are NOT in the registry",
          str(sorted(builtins_in_agent)))
    files = docs()
    check(len(files) >= 15, f"skill + personality docs found ({len(files)})", str(len(files)))

    # Everything a doc may legitimately name: tool names, argument names, enum values, and any
    # identifier that appears in the source at all.
    known = set(callable_names)
    known |= set(re.findall(r'"([a-z][a-z0-9_]{2,40})"', json.dumps(schemas)))
    for p in (ROOT / "src" / "afon").rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        known |= set(re.findall(r"\b([a-z_][a-z0-9_]{2,40})\b", text))
        known.add(p.stem)
    known |= set(dir(builtins)) | set(EXTERNAL)

    print("\n[2] every backticked identifier names something real")
    unresolved: dict[str, set] = {}
    mentions: dict[str, set] = {}
    for p in files:
        for m in re.finditer(r"`([a-z][a-z0-9_]{2,40})`", p.read_text(encoding="utf-8")):
            mentions.setdefault(m.group(1), set()).add(p.name)
            if m.group(1) not in known:
                unresolved.setdefault(m.group(1), set()).add(p.name)
    check(not unresolved,
          f"all {len(mentions)} distinct identifiers across {len(files)} docs resolve",
          "; ".join(f"`{t}` in {sorted(f)}" for t, f in sorted(unresolved.items())))

    print("\n[3] nothing is a near-miss for a real tool (the rename case)")
    # A renamed tool usually survives in a comment somewhere, so check [2] would resolve it. This
    # asks the sharper question: does this look like a tool name that is ALMOST, but not, a tool?
    suspects = []
    for token, where in sorted(mentions.items()):
        if token in callable_names or token in EXTERNAL:
            continue
        close = difflib.get_close_matches(token, callable_names, 1, 0.85)
        if close:
            suspects.append(f"`{token}` in {sorted(where)} -> did you mean `{close[0]}`?")
    check(not suspects, "no doc identifier is a near-miss for a tool name", "; ".join(suspects))

    print("\n[4] a tool named as MCP is LABELLED as MCP")
    # MCP tools are discovered at runtime, so they cannot be verified here. The doc must at least
    # say so, or a reader cannot tell "runtime-provided" from "stale".
    for token, note in EXTERNAL.items():
        if "MCP" not in note:
            continue
        for name in mentions.get(token, set()):
            body = (ROOT / ("skills" if (ROOT / "skills" / name).exists() else "personality")
                    / name).read_text(encoding="utf-8")
            line = next((ln for ln in body.splitlines() if f"`{token}`" in ln), "")
            check("MCP" in line, f"`{token}` in {name} is labelled as an MCP tool",
                  f"line does not say MCP: {line.strip()[:90]}")

    print("\n[5] the allow-list stays honest")
    # An entry that no doc uses any more is a licence nobody asked for; delete it.
    for token in sorted(EXTERNAL):
        check(token in mentions, f"allow-listed `{token}` is still actually used by a doc",
              "stale exemption — remove it")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
