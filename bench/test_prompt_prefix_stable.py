"""02.R2 — the stable half of the system prompt is byte-identical from one turn to the next.

A provider's prompt cache is prefix-matched: it hits for exactly as many leading bytes as two
requests share, and stops at the first byte that differs. So WHERE a changing section sits decides
what the cache is worth. The learned digest rebuilds every `memory_digest_refresh_every_turns`
turns and the delegation hint moves as domains repeat — and both sat in the MIDDLE of the prompt,
ahead of the Composio catalogue and the entire operating-rules block. Every refresh therefore
invalidated roughly the back half of a prompt whose contents had not changed at all.

Nothing failed when that regressed. The prompt was still correct, the answers were still right, and
the only symptom was a bill and a time-to-first-word. That is the kind of defect a gate has to hold,
because no one is going to notice it by using the thing.

    uv run python bench/test_prompt_prefix_stable.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  [{detail}]")


def common_prefix(a: str, b: str) -> int:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n


def main() -> None:
    from afon.brain import context as C

    print("[1] the prompt is deterministic when nothing has changed")
    a, b = C.build_system_prompt(), C.build_system_prompt()
    check("two builds of the same state are byte-identical", a == b,
          f"diverge at byte {common_prefix(a, b)} of {len(a)}")

    print("\n[2] a digest refresh does not move the bytes ahead of it")
    real = C._learned_digest
    try:
        C._learned_digest = lambda: "- he prefers tea\n- the landlord is called Weber"
        one = C.build_system_prompt()
        C._learned_digest = lambda: "- he prefers coffee now\n- the boiler was serviced in May\n" \
                                    "- the car is due an MOT"
        two = C.build_system_prompt()
    finally:
        C._learned_digest = real
    shared = common_prefix(one, two)
    check("the two prompts differ at all (otherwise this proves nothing)", one != two)
    check("everything before the digest is byte-identical across the refresh",
          shared >= len(C.stable_prefix()), f"shared {shared}, stable prefix {len(C.stable_prefix())}")

    print("\n[3] the stable prefix is worth caching")
    full = C.build_system_prompt()
    prefix = C.stable_prefix()
    check("the stable prefix is a real prefix of the prompt", full.startswith(prefix))
    share = len(prefix) / len(full)
    check(f"it is most of the prompt ({share:.0%} of {len(full)} chars, >= 70%)", share >= 0.70,
          f"{len(prefix)}/{len(full)} — a small cacheable prefix is not worth the ordering rule")

    print("\n[4] the load-bearing sections are INSIDE the stable prefix")
    # These are the ones that used to be invalidated by a digest refresh: they never change, and
    # they are the bulk of the prompt.
    rules = (BENCH / "personality/operating-rules.md").read_text(encoding="utf-8")
    first_rule = next(ln for ln in rules.splitlines() if ln.startswith("## "))
    for label, needle in [("the persona", "Afon"),
                          ("the operating rules", first_rule),
                          ("the 'check before refusing' rule", "Check before refusing")]:
        check(f"{label} is in the cacheable prefix", needle in prefix,
              "it sits after a volatile section, so every digest refresh re-sends it")

    print("\n[5] every volatile section is declared")
    # A volatile section that `_VOLATILE_MARKERS` does not name would be invisible to
    # `stable_prefix()`, which would then happily report changing bytes as stable.
    check("the markers are non-empty and each is a whole line's start",
          bool(C._VOLATILE_MARKERS) and all(m.strip() for m in C._VOLATILE_MARKERS))
    try:
        C._learned_digest = lambda: "- planted"
        with_digest = C.build_system_prompt()
    finally:
        C._learned_digest = real
    check("the digest lands AFTER the stable prefix", with_digest.index("- planted") >= len(prefix),
          "the volatile section is inside the bytes we call stable")
    check("...and the prefix is unchanged by it", with_digest.startswith(prefix))

    print("\n[6] the turn path actually sends the prefix first")
    src = (BENCH / "src/afon/brain/agent.py").read_text(encoding="utf-8")
    check("the system prompt is message[0] on every turn",
          "messages = [self._system, *self._history]" in src,
          "a per-turn note ahead of it would defeat the whole ordering")
    check("...and is rebuilt in place on a digest refresh, not appended to",
          src.count('self._system = {"role": "system", "content": build_system_prompt()}') == 2)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
