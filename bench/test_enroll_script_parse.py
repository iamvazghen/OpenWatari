"""`to-read-script.md` is DATA, not prose — editing it can silently weaken voice enrolment.

`enroll_voice.py::_script_segments()` parses that markdown for `## Segment` headers and pulls a
duration out of each `(≈ N s)` hint. Nothing asserted the two stay in step, so an ordinary edit to
a text file could break the enrolment run — the one thing the weak-voiceprint finding (G3) is
blocked on.

The dangerous failure is NOT a crash. If the non-ASCII `≈` is replaced (an editor, a paste through
a lossy encoding, someone "tidying" it to `~`), the regex still finds every `## Segment` header and
still returns five segments — each one silently falling back to SCRIPT_CLIP_S. Enrolment appears to
work, records the wrong durations, and produces a weaker voiceprint. So the checks below assert the
DURATIONS, not just the segment count.

Hermetic: parses the shipped file and synthetic strings. No audio, no torch, no mic.

    uv run python bench/test_enroll_script_parse.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def load_enroll():
    """Import enroll_voice WITHOUT its heavy optional deps (torch/speechbrain/pyaudio).

    The module imports those lazily inside main(), so a plain import is safe; if that ever changes
    this test should fail loudly rather than be quietly skipped.
    """
    spec = importlib.util.spec_from_file_location("_enroll", ROOT / "bench" / "enroll_voice.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)   # type: ignore[union-attr]
    return mod


def main() -> None:
    enroll = load_enroll()
    script = ROOT / "to-read-script.md"

    print("[1] the shipped script still parses")
    check("to-read-script.md exists at the repo root", script.is_file(), str(script))
    segs = enroll._script_segments(script)
    check("it yields the 5 segments the file documents", len(segs) == 5, f"got {len(segs)}")
    check("every segment has a non-empty prompt", all(p.strip() for p, _ in segs))
    check("every segment names itself in the prompt",
          all("Segment" in p for p, _ in segs), str([p[:40] for p, _ in segs]))

    print("\n[2] the DURATION hints are actually read (the silent-degradation case)")
    default = enroll.SCRIPT_CLIP_S
    secs = [s for _, s in segs]
    check("no segment fell back to the default clip length",
          all(s != default for s in secs), f"{secs} (default={default})")
    # Each header says (≈ N s); the parser adds a 3s tail buffer.
    expected = [30 + 3, 45 + 3, 40 + 3, 35 + 3, 30 + 3]
    check("durations match the file's own hints (+3s tail buffer)", secs == expected,
          f"got {secs}, want {expected}")
    total = sum(secs)
    check(f"total enrolment is ~3 minutes as the file claims ({total}s)",
          150 <= total <= 220, f"{total}s")

    print("\n[3] the duration hint survives a mangled '≈' (it used to collapse silently)")
    # Requiring the non-ASCII ≈ meant swapping it for '~' still matched every header and still ran
    # enrolment — just with default-length clips and a weaker voiceprint, with no error to notice.
    # The parser now accepts ≈ / ~ / approx / bare, so these variants must all give the SAME result.
    for repl, label in (("~", "tilde"), ("approx. ", "the word 'approx.'"), ("", "no marker at all")):
        text = script.read_text(encoding="utf-8").replace("≈ ", repl).replace("≈", repl.strip())
        tmp = Path(enroll.__file__).parent / f"_tmp_{label.split()[0]}.md"
        try:
            tmp.write_text(text, encoding="utf-8")
            got = [s for _, s in enroll._script_segments(tmp)]
        finally:
            tmp.unlink(missing_ok=True)
        check(f"durations survive {label}", got == expected, f"got {got}, want {expected}")

    print("\n[4] structural edits are caught")
    for text, want, label in (
        ("## Segment 1 — x (≈ 10 s)\nbody\n", 1, "one header -> one segment"),
        ("# Segment 1 — x (≈ 10 s)\nbody\n", 0, "an h1 is NOT a segment (## is the contract)"),
        ("## Warm-up — x (≈ 10 s)\nbody\n", 0, "a header not named 'Segment' is ignored"),
        ("", 0, "an empty file yields nothing rather than raising"),
    ):
        tmp = Path(enroll.__file__).parent / "_tmp_case.md"
        try:
            tmp.write_text(text, encoding="utf-8")
            got = len(enroll._script_segments(tmp))
        finally:
            tmp.unlink(missing_ok=True)
        check(label, got == want, f"got {got}, want {want}")

    print("\n[enrolment measures the SEPARATION, not just the owner]")
    # Everything the script measured before was false-REJECT: does the profile recognise the owner?
    # Nothing measured false-ACCEPT, which is exactly how a threshold that admits a television
    # survived — 742 live gate decisions on 2026-08-11 accepted 186, among them a film playing in
    # the room, in four languages. A gate is a separation and cannot be judged from one side.
    sv = enroll.separation_verdict
    good = sv(0.62, 0.20, 0.30)
    check("a clean separation passes", good.startswith("✓"), good)
    check("...and reports the gap", "0.42" in good, good)

    # The three failures are DIFFERENT problems with opposite fixes, so they must not share a verdict.
    tight = sv(0.36, 0.33, 0.30)
    check("owner and impostor on top of each other -> re-enrol", "NO USABLE SEPARATION" in tight, tight)
    check("...and it does NOT advise moving the threshold (nothing to move it to)",
          "RAISE" not in tight and "lower" not in tight, tight)

    low = sv(0.55, 0.40, 0.30)
    check("a real gap with the bar below the impostor -> RAISE the threshold",
          "RAISE" in low and "0.48" in low, low)   # midpoint of 0.55 and 0.40
    check("...and it does not tell him to re-enrol a profile that is fine",
          "Re-enrol" not in low, low)

    high = sv(0.25, 0.10, 0.30)
    check("a real gap with the bar above the owner -> LOWER the threshold",
          "lower" in high.lower() and "rather than re-enrolling" in high, high)

    # The distinction that matters most: same accept/reject outcome, opposite diagnosis.
    check("a small gap and a misplaced bar are told apart",
          sv(0.36, 0.33, 0.30) != sv(0.55, 0.40, 0.30),
          "one message for two problems sends him to fix the wrong thing")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
