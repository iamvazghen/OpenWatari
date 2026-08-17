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

    capture_quality()

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


# ── 08.F2: a bad clip is rejected AT THE MIC, not discovered weeks later ──────────────────────
# The owner's voiceprint scores 0.47 against a 0.60 target and nothing ever said why. Enrolment
# embedded whatever the mic produced and printed "captured ✓", so a segment read next to a running
# fan — or with the wrong input device selected — went into the profile looking exactly like a good
# one. The cost surfaced as a threshold that could not be placed (`separation_verdict` above),
# weeks after the five minutes when the owner was sitting at the mic and could have re-read it.
def capture_quality() -> None:
    from afon.edge.enroll_quality import (MAX_CLIPPING, MIN_SECONDS, MIN_SNR_DB, MIN_VARIETY,
                                          score_pcm, _synth)

    print("\n[08.F2] a usable clip passes")
    good = score_pcm(_synth("speech"))
    # The check that keeps this from becoming a scorer that rejects everything. A gate with only
    # negative cases is passed by `return reject`, and then enrolment can never complete.
    check(f"a clip with speech in it is accepted ({good.summary()})", not good.reject, good.reason)
    check("...and its numbers are clear of the thresholds, not scraping them",
          good.snr_db > MIN_SNR_DB and good.variety > MIN_VARIETY, good.summary())

    print("\n[08.F2] each way a clip can be unusable is named separately")
    # One "check your setup" message for six different problems sends the owner to fix the wrong
    # thing — the same reasoning as `separation_verdict` above, at capture time instead of after.
    cases = (("silence", "no speech", "a silent clip"),
             ("tone", "no speech", "a steady tone — loud, clean, long, and no speech in it"),
             ("noise", "noisy", "broadband noise"),
             ("clipped", "clipping", "a clipped recording"),
             ("quiet_speech", "quiet", "speech too far from the mic"),
             ("noisy_speech", "noisy", "speech under a fan"))
    reasons = {}
    for kind, expect, desc in cases:
        q = score_pcm(_synth(kind))
        reasons[kind] = q.reason
        check(f"{desc} is rejected, and told apart ({expect})",
              q.reject and expect in q.reason, f"{q.reason!r} [{q.summary()}]")
    check("no two failures share a message",
          len(set(reasons.values())) == len(reasons),
          "one message for several problems is how a diagnostic stops helping")

    print("\n[08.F2] the measurements themselves")
    short = score_pcm(_synth("speech", seconds=1.0))
    check(f"a clip under {MIN_SECONDS}s is rejected on duration",
          short.reject and "at least" in short.reason, short.reason)
    check("a clip shorter than one frame does not crash the scorer",
          score_pcm(b"\x00\x00" * 10).reject)
    check("an empty recording is rejected, not treated as silence-that-passes",
          score_pcm(b"").reject)
    # The bug this measurement had, and the reason it is checked rather than assumed: variety used to
    # be computed over frames selected by `noise_rms * 2`, which on pure noise selected NOTHING —
    # every frame has the same energy — and then reported variety 0.0, i.e. "no speech detected", for
    # a recording that is nothing but noise. It diagnosed the wrong problem, confidently.
    noise = score_pcm(_synth("noise"))
    check("noise is diagnosed as noise, not as 'no speech'",
          noise.variety > MIN_VARIETY and "noisy" in noise.reason,
          f"variety={noise.variety:.3f} reason={noise.reason!r}")
    tone = score_pcm(_synth("tone"))
    check("...while a tone really does read as no speech",
          tone.variety < MIN_VARIETY, f"variety={tone.variety:.3f}")
    clipped = score_pcm(_synth("clipped"))
    check("clipping is measured as a fraction of samples at full scale",
          clipped.clipping > MAX_CLIPPING, f"{clipped.clipping:.3f}")

    print("\n[08.F2] the enrolment script actually consults it")
    # A scorer nothing calls is a scorer that changes nothing. Checked structurally because the call
    # site is inside a mic loop that cannot run in a test.
    src = (Path(__file__).resolve().parents[1] / "bench" / "enroll_voice.py").read_text(
        encoding="utf-8")
    check("enroll_voice imports the scorer", "from afon.edge.enroll_quality import score_pcm" in src)
    # Counting >= 1 was not enough: deleting the FIRST call left the retry-path call in place, so the
    # check passed while every clip went in unscored. Both the capture and the retry must score, and
    # the scoring must happen BEFORE the embedding — after it, the clip is already in the profile.
    check("...scores both the capture and the re-record", src.count("score_pcm(pcm") == 2,
          f"{src.count('score_pcm(pcm')} call(s)")
    check("...before embedding it, not after",
          src.index("score_pcm(pcm") < src.index("verifier.embed(pcm"),
          "a quality verdict after the vector is in the profile changes nothing")
    check("...re-records a rejected segment instead of accepting it",
          "re-recording this segment" in src)
    check("...and repeats the warning at the end, where it will be read",
          "went in below quality" in src,
          "a warning four screens up during a five-minute read is a warning nobody acts on")


if __name__ == "__main__":
    main()
