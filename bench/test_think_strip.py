"""MiniMax <think>...</think> stripping — the reasoning-primary must never speak its chain-of-thought.

Covers the whole-string path (complete()) and the streaming state machine (stream/stream_with_tools),
including a think block split across arbitrary chunk boundaries.

    uv run python bench/test_think_strip.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain.llm import _strip_think, _ThinkStripper  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def stream_strip(text: str, size: int) -> str:
    """Feed `text` through _ThinkStripper in fixed-size chunks; return the concatenated output."""
    s = _ThinkStripper()
    out = [s.feed(text[i:i + size]) for i in range(0, len(text), size)]
    out.append(s.flush())
    return "".join(out)


print("[1] whole-string strip (complete path)")
check("leading think block removed",
      _strip_think("<think>reasoning here</think>\n\nHello, sir.") == "Hello, sir.",
      _strip_think("<think>x</think>\n\nHello, sir."))
check("multiline think removed",
      _strip_think("<think>\nline1\nline2\n</think>\n\nThe answer.") == "The answer.")
check("no think block -> unchanged", _strip_think("Just a normal answer.") == "Just a normal answer.")
check("empty/None safe", _strip_think("") == "" and _strip_think(None) is None)
check("think with tags inside preserved answer",
      _strip_think("<think>use <function>x</function></think>\n\nDone.") == "Done.")

print("\n[2] streaming strip — many chunk sizes")
THINK = "<think>\nThe user greets me. Respond warmly.\n</think>\n\nHello! How can I help, sir?"
EXPECT = "Hello! How can I help, sir?"
for size in (1, 2, 3, 5, 7, 13, 100):
    got = stream_strip(THINK, size)
    check(f"chunk size {size} -> clean answer", got == EXPECT, repr(got))

print("\n[3] streaming: no think block passes straight through")
plain = "Yes, sir. Right away."
check("plain text unchanged (size 1)", stream_strip(plain, 1) == plain, repr(stream_strip(plain, 1)))
check("plain text unchanged (size 4)", stream_strip(plain, 4) == plain)

print("\n[4] streaming edge cases")
check("only a think block -> empty", stream_strip("<think>just thinking</think>", 3) == "")
check("think then whitespace only -> empty", stream_strip("<think>x</think>\n\n  ", 2).strip() == "")
# a stray token that merely starts like the tag but isn't
check("'<' non-think passes", stream_strip("<3 sir", 1) == "<3 sir", repr(stream_strip("<3 sir", 1)))

print(f"\n=== {passed}/{passed + failed} checks passed ===")
if failed:
    sys.exit(1)
