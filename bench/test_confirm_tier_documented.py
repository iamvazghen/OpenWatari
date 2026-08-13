"""SECURITY.md's confirmation tier must match `CONFIRM_TIER` in code — in both directions.

Security prose that drifts from the enforcing code is the worst kind of stale note: it is what an
auditor (or the owner) reads *instead of* the code, so it is trusted precisely where being wrong
costs most. There was no edge between the two, and they had drifted badly — SECURITY.md named 17
tools while `CONFIRM_TIER` gated 32, so `place_call` (Twilio: outward and unrecallable),
`write_vault`, `forget` and 12 others read as ungated.

Both directions matter, for different reasons:

  * doc -> code: the doc must not PROMISE a gate that does not exist. This is the dangerous
    direction — it invents protection.
  * code -> doc: every gate must be written down. Undocumented protection is the safe direction,
    but it is how the doc rots until nobody trusts it.

Hermetic: reads the file and the module. No network.

    uv run python bench/test_confirm_tier_documented.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

passed = failed = 0

# Named in SECURITY.md as explicitly NOT gated (the "reads need no confirmation" sentence). Listing
# them here is deliberate: it keeps the doc free to mention a read tool by name without this test
# reading that mention as a promise of a gate.
DOCUMENTED_AS_UNGATED = {"recall", "read_email", "read_chat", "git_status", "search_vault"}


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> None:
    from afon.brain.proactive import CONFIRM_TIER

    doc_path = ROOT / "SECURITY.md"
    doc = doc_path.read_text(encoding="utf-8")
    # Only the confirmation-tier section makes gating claims; the rest of the file mentions tools
    # in passing (the coding-tools section, the reporting section) and must not be read as a list.
    start = doc.index("## 2. Confirmation tier")
    end = doc.index("##", start + 10)
    section = doc[start:end]
    named = set(re.findall(r"`([a-z_][a-z0-9_]*)`", section))

    print(f"[1] the two sets ({len(CONFIRM_TIER)} gated in code)")
    check("SECURITY.md still has a confirmation-tier section", bool(section.strip()))
    check("the section names a plausible number of tools", len(named) >= 20, str(len(named)))

    print("\n[2] code -> doc: every gate is written down")
    undocumented = sorted(t for t in CONFIRM_TIER if t not in named)
    check("no confirm-gated tool is missing from SECURITY.md", not undocumented, str(undocumented))

    print("\n[3] doc -> code: the doc promises no gate that does not exist")
    # The dangerous direction: a reader trusting a gate that was renamed or removed.
    #
    # The section also names FUNCTIONS in prose (`confirm_required`, `needs_clarification`,
    # `_is_affirmation`), which a backtick regex cannot tell from tool names. Discriminate against
    # the real tool registry rather than an exclusion list — a list would need editing every time
    # the prose does, which is the same rot this test exists to prevent.
    from afon.brain.tools import tool_schemas

    # Afon has TWO tool registries, and using only the module one here was a hole: `tool_schemas()`
    # enumerates brain/tools/*.py (136 tools), while `get_time`, `delegate_to_fleet`, `work_on_task`
    # and friends are AGENT BUILT-INS defined in agent.py and merged into the handler map alongside
    # `**tool_handlers()`. They are advertised to the model exactly like any other tool, so
    # SECURITY.md naming one must be checked, not ignored.
    #
    # Taken from the LIVE handler map rather than a hard-coded list: a list of built-ins would need
    # editing every time one is added, and would then silently stop covering the new one — the same
    # rot this file exists to catch.
    from afon.brain.agent import AfonAgent

    real_tools = {s["function"]["name"] for s in tool_schemas()} | set(AfonAgent()._registry)
    phantom = sorted(n for n in named
                     if n in real_tools and n not in CONFIRM_TIER and n not in DOCUMENTED_AS_UNGATED)
    check("SECURITY.md names no tool it cannot back with a real gate", not phantom, str(phantom))
    # ...and the registry lookup must not be quietly empty, or [3] passes for the wrong reason.
    check("the tool registry actually loaded", len(real_tools) > 50, f"{len(real_tools)} tools")

    print("\n[4] the tools it calls out as UNGATED really are")
    wrongly_gated = sorted(t for t in DOCUMENTED_AS_UNGATED if t in CONFIRM_TIER)
    check("reads/lookups documented as free are not secretly gated", not wrongly_gated,
          str(wrongly_gated))

    print("\n[5] the highest-consequence gates are present by name")
    # A guard that only compares two sets passes if BOTH lose an entry together. These are asserted
    # against the code directly, so deleting a gate fails here even if the doc is edited to match.
    for t in ("send_email", "place_call", "git_push", "run_powershell", "file_op", "write_vault"):
        check(f"{t} is confirm-gated", t in CONFIRM_TIER)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
