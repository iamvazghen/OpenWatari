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

    print("\n[5] the persona TEMPLATE carries every section the shipped persona has (J3.4)")
    # The graph showed communities 211 and 228 both titled "{assistant_name} — Persona" with
    # different section sets, which on disk is personality/afon.md against persona.example.md. The
    # template was missing "Proactive companion" and "Protocols" — so a stranger who copies it, as
    # its own header tells them to, gets a strictly reactive assistant with no protocol handling and
    # nothing anywhere saying that is what they chose. Extra sections in the template are fine (it
    # explains itself); missing ones are the drift.
    def _sections(path: Path) -> set[str]:
        # Strip HTML comments first: afon.md's header COMMENT lists section names as guidance, and
        # counting those would make the file pass by talking about sections it does not have.
        body = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S)
        return {ln.lstrip("# ").split("(")[0].strip().lower()
                for ln in body.splitlines() if ln.startswith("## ")}

    shipped = _sections(ROOT / "personality" / "afon.md")
    template = _sections(ROOT / "personality" / "persona.example.md")
    missing = sorted(shipped - template)
    check(not missing, f"persona.example.md covers all {len(shipped)} shipped sections",
          f"missing from the template: {missing}")

    print("\n[single source] 25.F3 — one definition of who he is, and one of how he sounds")
    # The fleet's lesson, and it cost a week there: retired names kept coming back because only
    # SOUL.md was ever edited and IDENTITY.md quietly said something else. Here the same shape had
    # already appeared in miniature — context.py carried a hardcoded copy of the operating rules
    # that had DRIFTED from the file it was a fallback for, so a brain that fell back was governed
    # by a rulebook the owner had never seen. Two copies do not stay equal; they stay unequal
    # quietly, which is worse than one copy being wrong.
    persona_body = re.sub(r"<!--.*?-->", "",
                          (ROOT / "personality" / "afon.md").read_text(encoding="utf-8"), flags=re.S)
    rules_body = re.sub(r"<!--.*?-->", "",
                        (ROOT / "personality" / "operating-rules.md").read_text(encoding="utf-8"),
                        flags=re.S)
    context_src = (ROOT / "src" / "afon" / "brain" / "context.py").read_text(encoding="utf-8")
    voice_skill = (ROOT / "skills" / "voice-style.md").read_text(encoding="utf-8")

    check(persona_body.count("\u2014 Persona") == 1,
          "the persona names itself exactly once",
          "two headers in one file is already two definitions")

    # No Python file may carry persona or rule PROSE. A fallback that quotes the rules is a second
    # rulebook with no owner; a fallback that says the rules are missing is a status message.
    check("_DEFAULT_OPERATING_RULES" not in context_src,
          "no Python module embeds a copy of the operating rules",
          "the hardcoded copy is back — it drifted from the file last time")
    check("_RULES_UNREADABLE" in context_src and "could not be read" in context_src,
          "...and the fallback SAYS the rules are missing instead of inventing some")
    fallback = context_src.split("_RULES_UNREADABLE = (", 1)[-1].split(chr(10) + ")", 1)[0]
    check(len(fallback) < 700 and "composio_find_tools" not in fallback,
          "...and the fallback is a status message, not a rulebook of its own",
          f"the fallback grew back into rules ({len(fallback)} chars)")

    # The one rule that had three copies. Each of these belongs to exactly one file now.
    def _owns(text, *needles):
        low = text.lower()
        return any(n in low for n in needles)

    check(_owns(persona_body, "short sentences", "one breath"),
          "the persona owns how long a reply is")
    check(not _owns(rules_body, "one or two sentences", "no markdown or emoji"),
          "...and the operating rules no longer restate it",
          "the spoken-output rule is back in the rules file")
    check(not _owns(voice_skill, "one or two sentences by default", "no markdown, no emoji"),
          "...and the voice skill no longer restates it either",
          "the skill is defining the persona again")
    check(all(w in voice_skill.lower() for w in ("youtube dot com", "round unless precision")),
          "the voice skill still carries the technique that is only its own",
          "trimming the duplication took the useful half with it")

    # Whatever is actually assembled must contain one persona and one set of rules.
    from afon.brain.context import build_system_prompt

    prompt = build_system_prompt()
    check(prompt.count("\u2014 Persona") == 1,
          "the assembled prompt carries exactly one persona header",
          f"{prompt.count(chr(8212) + ' Persona')} persona headers in one prompt")
    check(prompt.count("Check before refusing") == 1,
          "...and one 'check before refusing' rule, not two",
          f"{prompt.count('Check before refusing')} copies — the fallback is appended as well")

    # persona.example.md is a COPY on purpose (it is the fork-me template) and is never loaded.
    # That is the one duplicate allowed, so it is pinned rather than merely tolerated.
    check("persona.example" not in context_src,
          "the template is not loaded into the prompt",
          "the example persona is only a template; loading it would make it a second definition")

    print("\n[6] the allow-list stays honest")
    # An entry that no doc uses any more is a licence nobody asked for; delete it.
    for token in sorted(EXTERNAL):
        check(token in mentions, f"allow-listed `{token}` is still actually used by a doc",
              "stale exemption — remove it")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
