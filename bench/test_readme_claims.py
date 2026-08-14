"""J5.2 — the README is a feature tour, and nothing stopped it drifting from what Afon can do.

The graph put README in its own community with almost no edges to code (cohesion 0.09), which is
the structural way of saying: this file makes claims and nothing checks them. That is not
hypothetical here. README advertised a `glasses/` TypeScript bridge as a shipped, "on" integration
for weeks after the scaffold was deleted — a reader cloning the repo went looking for a capability
that was not there. `test_doc_paths.py` catches the *path* version of that mistake; this catches the
*capability* version, which is the one a README makes most.

Three claims are checked, chosen because each has already been wrong once somewhere in this repo:

  * **Tool claims.** A backticked `snake_case` name in README that is one edit away from a real tool
    is almost always a rename that the README missed. The tour is anchored: ~47 of its backticked
    identifiers are live tool names, so it goes stale by drifting, not by being unrelated.
  * **Gated capabilities are still real.** `delegate_to_fleet` is NOT in the tool registry — it is
    an agent built-in whose schema is added in `agent.py`, and README correctly says it is gated off
    by default. A naive "is it in the registry" check would call that a phantom and be wrong, so the
    callable surface here is registry + agent built-ins, exactly as `test_skill_docs_resolve.py`
    resolves it.
  * **Install claims.** `pip install .[edge]` is a promise about `pyproject.toml`. An extras group
    that gets renamed leaves a README instruction that fails on the reader's first command.

What this does NOT do is require every backticked word in README to resolve — README talks about
shells, third-party packages and prose. Requiring that would produce a wall of false positives and
the check would be turned off within a week.

    uv run python bench/test_readme_claims.py
"""

from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

passed = failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def _extras() -> set[str]:
    """Extras group names from pyproject's [project.optional-dependencies] block."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = text.split("[project.optional-dependencies]", 1)
    if len(block) < 2:
        return set()
    body = re.split(r"\n\[", block[1], 1)[0]
    return set(re.findall(r"^([a-z][a-z0-9_-]*)\s*=\s*\[", body, re.MULTILINE))


def main() -> None:
    from afon.brain.tools import tool_names

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    tools = set(tool_names())
    agent_src = (ROOT / "src" / "afon" / "brain" / "agent.py").read_text(encoding="utf-8")
    builtins_in_agent = set(re.findall(r'"name":\s*"([a-z_]+)"', agent_src))
    callable_names = tools | builtins_in_agent
    extras = _extras()
    tokens = set(re.findall(r"`([a-z][a-z0-9_]{2,40})`", readme))

    print("[1] the tour is anchored in the real registry")
    check(len(tools) > 100, f"tool registry loaded ({len(tools)} tools)")
    named = tokens & callable_names
    check(len(named) >= 25,
          f"README names {len(named)} real capabilities — it describes THIS assistant",
          "a tour that names almost no real tools cannot go stale in a detectable way")
    check(bool(extras) and len(extras) >= 4, f"extras groups parsed from pyproject ({len(extras)})",
          str(sorted(extras)))

    print("\n[2] no capability claim is a near-miss for a real tool (the rename case)")
    suspects = []
    for token in sorted(tokens - callable_names):
        if token in extras:
            continue                      # `browse` is an install extra, not a tool
        close = difflib.get_close_matches(token, sorted(callable_names), 1, 0.85)
        if close:
            suspects.append(f"`{token}` -> did you mean `{close[0]}`?")
    check(not suspects, "no README identifier is one edit from a tool name", "; ".join(suspects))

    print("\n[3] a capability presented as a bullet resolves to something callable")
    # The shape README uses for its feature tour: "- **Name** — `tool_a`, `tool_b` …". Anything
    # backticked in one of those lines is being SOLD as a capability, so it must be callable.
    phantom = []
    for line in readme.splitlines():
        if not re.match(r"\s*[-*]\s+\*\*", line):
            continue
        for token in re.findall(r"`([a-z][a-z0-9_]{2,40})`", line):
            if token in callable_names or token in extras:
                continue
            if re.search(rf"\b{re.escape(token)}\b", agent_src):
                continue                  # named in the agent (a mode, an arg, a built-in)
            phantom.append(f"{token}: {line.strip()[:80]}")
    check(not phantom, "every capability bullet names a callable tool", "\n        ".join(phantom[:8]))

    print("\n[4] every install instruction points at a real extras group")
    bad = []
    for grp in re.findall(r"pip install[^\n`]*\.\[([a-z0-9_,\- ]+)\]", readme):
        for one in (g.strip() for g in grp.split(",")):
            if one and one not in extras:
                bad.append(one)
    for grp in re.findall(r"`([a-z][a-z0-9_-]*)`(?=[^\n]*extra)", readme):
        if grp not in extras and grp in tokens and grp not in callable_names:
            bad.append(grp)
    check(not bad, "README's install extras all exist in pyproject", str(sorted(set(bad))))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
