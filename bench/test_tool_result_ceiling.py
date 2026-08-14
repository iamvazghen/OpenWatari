"""J1.4 — every tool is trusted to bound its own output, and one that forgets costs the whole turn.

The finding called `clip()` the fourth-largest hub and read it as "spoken-output length policy
applied at 50 scattered call sites instead of once at the speech boundary". Reading those sites
gives a different answer: the limits run from 80 to 4000 characters, so `clip()` is not one policy
applied fifty times — it is a general utility sizing a label, an excerpt and a document. Centralising
it would flatten distinctions that are deliberate.

The other half of the finding is exactly right and was unguarded: **nothing catches a tool that
forgets.** Both places a tool result enters a message list appended `str(result)` raw:

  * `agent.py` — the live turn. One oversized scrape or document read enters the next request's
    prefill whole, on the path that is already the dominant per-turn cost (K2).
  * `worker.py` — the autonomous path, and the worse of the two. It loops for many steps with the
    message list growing, so a single unbounded result is paid again on every subsequent step, with
    nobody watching.

So: a backstop at the boundary rather than a policy at the call sites. It sits well above every
per-tool `clip()` limit, which is the point — it must never fight a tool's own sizing decision, only
catch the pathological case. Check [4] asserts that relationship directly, so a future
`clip(x, 20000)` fails here instead of quietly making the backstop the active policy.

Truncation is announced in the content, not silent: a model that can see its input was cut can
narrow the query or ask for the rest; one that cannot will answer confidently from half a document.

Hermetic — string in, string out, plus a source read.

    uv run python bench/test_tool_result_ceiling.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from afon.brain.tools import base  # noqa: E402
from afon.config import settings  # noqa: E402

passed = failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> None:
    print("[1] the boundary has a ceiling, and it is tunable")
    ceiling = base.TOOL_RESULT_CEILING
    check(isinstance(ceiling, int) and ceiling > 0, "TOOL_RESULT_CEILING is a positive int",
          str(ceiling))
    check(getattr(settings, "tool_result_ceiling", None) == ceiling,
          "…and it comes from settings, so it can be raised without a code change",
          str(getattr(settings, "tool_result_ceiling", None)))

    print("\n[2] a pathological result is bounded, and says so")
    huge = "x" * 200_000
    out = base.bound_tool_result(huge, "scrape_url")
    check(len(out) <= ceiling + 300, f"200k chars bounded to ≈ceiling (got {len(out)})", str(len(out)))
    check("truncated" in out.lower(), "…and the model can see it was cut", out[-160:])
    check("scrape_url" in out, "…and which tool produced it", out[-160:])
    check("200000" in out.replace(",", ""), "…and how much there was, so it can narrow the query",
          out[-160:])

    print("\n[3] an ordinary result is passed through untouched")
    small = "It's 14 degrees and overcast in Cologne, sir."
    check(base.bound_tool_result(small, "weather") == small, "a normal answer is byte-identical")
    edge = "y" * ceiling
    check(base.bound_tool_result(edge, "read_document") == edge,
          "exactly at the ceiling is not truncated")
    check(base.bound_tool_result("", "noop") == "", "an empty result stays empty")

    print("\n[4] the backstop never fights a tool's own sizing decision")
    limits: list[tuple[str, int]] = []
    for path in (ROOT / "src" / "afon").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"\bclip\(\s*[^,()]+,\s*(\d{2,6})\s*\)", text):
            limits.append((path.name, int(m.group(1))))
    check(len(limits) >= 10, f"per-tool clip limits found ({len(limits)})", str(limits[:4]))
    over = [f"{n}:{v}" for n, v in limits if v >= ceiling]
    check(not over, f"every per-tool limit is below the ceiling ({ceiling})", str(over))

    print("\n[5] BOTH message paths go through it — the live turn and the autonomous one")
    for name in ("agent.py", "worker.py"):
        src = (ROOT / "src" / "afon" / "brain" / name).read_text(encoding="utf-8")
        appended = re.search(r'messages\.append\(\{"role": "tool".*?\}\)', src, re.DOTALL)
        check(appended is not None, f"{name}: found the tool-result append site")
        if appended:
            check("bound_tool_result" in appended.group(0),
                  f"{name}: the content goes through the ceiling", appended.group(0)[:150])

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
