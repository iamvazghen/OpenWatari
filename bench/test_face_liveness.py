"""11.F3 — a photograph of the owner is not the owner.

The camera is a SECOND FACTOR whose only power is to block: `matched` never authorises anything, it
declines to refuse. So the attack that mattered was never "spoof your way past the gate" — it was a
printed photo of the owner propped in front of the webcam, which grants `matched` on every check
from then on and silently retires the second factor. Nothing reported it, and nothing would have.

What is checked here:

  [1] the measurement — a still image reads as still even when the "camera" jitters, and a changing
      face reads as live; the shift-correction that makes that true is pinned in both directions
  [2] the verdicts — three of them, and "too few frames" is can't-tell rather than an accusation
  [3] the wiring — a still burst WITHDRAWS the verdict instead of inverting it, because inverting it
      blocks a very still owner, and that is the absence claim 11.F1 exists to forbid
  [4] the degradation — a liveness check that throws leaves behaviour exactly as it was

Hermetic: synthetic crops and injected frames. No webcam.
Scope, stated because a gate believed to do more than it does stops being watched: this catches a
burst that is ONE IMAGE (print, paused screen, photo on a phone). A video replay has real
micro-motion and is not covered — that is 11.R's spoof corpus and the tier the plan declines until
the cheap check has been measured and found wanting.

    uv run python bench/test_face_liveness.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain.tools import camera  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def _face(seed: int = 0) -> np.ndarray:
    """A textured 100x100 'face'. Texture everywhere matters: a flat patch has no shift to correct
    and no motion to measure, so it would pass every check below for the wrong reason."""
    rng = np.random.default_rng(seed)
    x, y = np.meshgrid(np.arange(100), np.arange(100))
    base = 128 + 60 * np.sin(x / 7.0) * np.cos(y / 9.0)
    return np.clip(base + rng.normal(0, 12, (100, 100)), 0, 255).astype(np.uint8)


def _sensor_noise(img: np.ndarray, sigma: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.clip(img.astype(np.float64) + rng.normal(0, sigma, img.shape), 0, 255).astype(np.uint8)


def _shifted(img: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Whole-crop translation — what the haar box does between frames, to a photo and a face alike."""
    return np.roll(np.roll(img, dy, axis=0), dx, axis=1)


def _subpixel_shifted(img: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """Translation that does NOT land on the pixel grid — real box jitter, and the harder case.

    An integer roll is solved exactly by phase correlation, so a fixture built only from integer
    shifts proves less than it appears to: it leaves a residue of exactly zero. Sub-pixel shifts
    resample, which blurs, and the blur survives alignment.
    """
    import cv2

    m = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, m, (img.shape[1], img.shape[0]), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


def _expressive(img: np.ndarray, amount: float) -> np.ndarray:
    """Change part of the face, as a blink or a word does — localised, not a global shift."""
    out = img.astype(np.float64).copy()
    out[30:45, 20:80] = np.clip(out[30:45, 20:80] + amount, 0, 255)   # the eye band
    return out.astype(np.uint8)


def measurement() -> None:
    print("[1] the measurement: translation is removed, real change is not")
    base = _face(1)

    # The positive control, first. A measure that reports "no motion" for everything makes every
    # check below pass and every owner a photograph.
    moving = [_expressive(base, 40 * (i % 2)) for i in range(6)]
    live_motion = camera._aligned_diff(moving[0], moving[1])
    check(f"a changing face measures real motion ({live_motion:.1f})",
          live_motion > camera.MIN_LIVENESS_MOTION, f"{live_motion}")

    # The confound this exists to remove: a photograph whose crop is shifted by box jitter. Without
    # alignment the shift alone measures more motion than a blink does, and then a print passes.
    shifted = _shifted(base, 3, 2)
    raw = float(np.mean(np.abs(base[8:-8, 8:-8].astype(float) - shifted[8:-8, 8:-8].astype(float))))
    aligned = camera._aligned_diff(base, shifted)
    check(f"an unaligned 3px shift looks like motion ({raw:.1f}) — the confound is real",
          raw > camera.MIN_LIVENESS_MOTION, f"{raw}")
    check(f"...and alignment removes most of it ({aligned:.1f} vs {raw:.1f})", aligned < raw / 3.0,
          "phase correlation is not correcting, or is correcting the wrong way")
    # Sign check, both directions. A sign error DOUBLES the shift instead of removing it, and the
    # single-direction case above cannot tell the two apart when the image is nearly symmetric.
    check("...in both directions", camera._aligned_diff(base, _shifted(base, -3, -2)) < raw / 3.0,
          "the correction has the wrong sign for negative shifts")

    check("an image against itself measures zero motion",
          camera._aligned_diff(base, base) == 0.0, str(camera._aligned_diff(base, base)))
    noisy = camera._aligned_diff(base, _sensor_noise(base, 1.5, 7))
    check(f"sensor noise alone stays under the floor ({noisy:.2f})",
          noisy < camera.MIN_LIVENESS_MOTION, "a photograph would be called live on noise alone")


def verdicts() -> None:
    print("\n[2] three verdicts, and 'too few frames' is not an accusation")
    base = _face(2)

    live = camera.liveness_verdict([_expressive(base, 35 * (i % 2)) for i in range(6)])
    check(f"a moving face is live ({live.summary()})", live.live, live.reason)

    # The attack, in its three shapes. A print held still, a print with a shaky hand (the case a
    # naive frame difference gets wrong), and a paused screen with sensor noise on top.
    still = camera.liveness_verdict([base.copy() for _ in range(6)])
    check(f"a still photograph is rejected ({still.summary()})", still.verdict == "still",
          still.reason)
    shaky = camera.liveness_verdict([_shifted(base, (i % 3) - 1, (i % 2)) for i in range(6)])
    check(f"a SHAKY photograph is still rejected ({shaky.summary()})", shaky.verdict == "still",
          "hand shake is a rigid shift; if that reads as life, a print in a hand defeats the gate")
    print("\n[2b] the KNOWN GAP, asserted so that nobody later assumes it closed")
    # A hand-held print jitters by fractions of a pixel. That resamples, and resampling blurs, and
    # blur survives alignment — so it arrives as motion. Measured here: sub-pixel jitter reads ~3.5
    # against a moving face's ~4.5. The floor sits at 2.0, so a hand-held print reads as LIVE.
    #
    # This is asserted rather than papered over. Tuning the floor up to 4 on these fixtures would be
    # fitting a threshold to synthetic faces I invented, and the number would mean nothing on a real
    # print; the fixtures can show a gap exists but cannot size it. Closing it needs the spoof corpus
    # (11.R1) — print, phone screen, video replay, shot on this camera in this room.
    #
    # What the gap costs is bounded, and that is why shipping the narrow version is right: a print
    # that reads as live leaves the second factor exactly where it was BEFORE 11.F3. The attack this
    # does close is the one that was silently permanent — a photo propped against the monitor, which
    # grants `matched` on every check forever.
    jitter = camera.liveness_verdict(
        [_subpixel_shifted(base, 0.37 * ((i % 3) - 1), 0.29 * ((i % 2) * 2 - 1)) for i in range(6)])
    live_motion = camera.liveness_verdict([_expressive(base, 35 * (i % 2)) for i in range(6)]).motion
    check(f"sub-pixel jitter is NOT yet distinguished from a face ({jitter.motion:.2f} vs "
          f"{live_motion:.2f} live) — 11.R1 corpus", jitter.verdict == "live",
          "if this now passes as 'still', the measure improved: re-measure the margin and tighten "
          "this check rather than deleting it")
    check("...and the gap is a margin, not an inversion — a face still moves more than a print does",
          live_motion > jitter.motion,
          "a print measuring MORE motion than a face means the measure is backwards, not merely blunt")
    screen = camera.liveness_verdict([_sensor_noise(base, 1.5, i) for i in range(6)])
    check(f"a paused screen with sensor noise is rejected ({screen.summary()})",
          screen.verdict == "still", screen.reason)

    check("the reason names the thing to look for",
          "photograph" in still.reason and "screen" in still.reason, still.reason)
    check("...and a live verdict does not borrow that wording",
          "photograph" not in live.reason, live.reason)

    thin = camera.liveness_verdict([base.copy() for _ in range(camera.MIN_LIVENESS_FRAMES - 1)])
    check("too few frames is UNKNOWN, never 'still'", thin.verdict == "unknown", thin.reason)
    check("...and says so rather than accusing him of being a picture",
          "too few" in thin.reason and "photograph" not in thin.reason.split("—")[0], thin.reason)
    check("an empty burst is unknown, not still", camera.liveness_verdict([]).verdict == "unknown")
    check("...and a still verdict is not reachable from no evidence at all",
          camera.liveness_verdict([]).live is False and camera.liveness_verdict([]).motion == 0.0)


async def wiring() -> None:
    print("\n[3] the wiring: a photograph WITHDRAWS the verdict, it does not invert it")
    saved = (camera._capture_burst, camera._owner_refs, camera._recognise, camera._gray_faces,
             camera._person_evidence)
    try:
        camera._capture_burst = lambda *a, **k: [b"jpeg"] * 8
        camera._owner_refs = lambda: object()
        camera._recognise = lambda _j, _r: (1, True)          # every frame says "the owner"
        camera._person_evidence = lambda _j: False

        base = _face(3)
        camera._gray_faces = lambda _j: [_expressive(base, 35)]   # identical crop every frame
        v = json.loads(await camera._verify_owner_present_local({}))
        check("a still burst reports live=False", v["live"] is False, str(v))
        check("...and WITHDRAWS the verdict (available False)", v["available"] is False, str(v))
        # The load-bearing half. matched=True + faces=1 proceeds; matched=False + faces=1 is the
        # "someone is there and it is not him" branch, which BLOCKS. A very still owner must not
        # land there — that is an absence claim the detector cannot support (11.F1).
        check("...and does NOT report matched=False with a face, which would block a still owner",
              not (v["matched"] and v["available"]) and v["faces"] >= 1, str(v))

        # A moving face: the verdict stands, exactly as before 11.F3 existed.
        seq = iter([_expressive(base, 40 * (i % 2)) for i in range(8)])
        camera._gray_faces = lambda _j: [next(seq)]
        v2 = json.loads(await camera._verify_owner_present_local({}))
        check("a live burst still verifies him", v2["available"] and v2["matched"], str(v2))
        check("...and records that liveness was checked", v2["live"] is True, str(v2))

        # Liveness is only consulted when he matched — it is the only verdict it can change, and a
        # second detector pass is not free on a confirm-gate's critical path.
        camera._recognise = lambda _j, _r: (1, False)
        camera._gray_faces = lambda _j: [base.copy()]
        v3 = json.loads(await camera._verify_owner_present_local({}))
        check("a non-match is not put through the liveness check at all", v3["live"] is None, str(v3))
        check("...and is still reported as a face that is not him",
              v3["available"] and not v3["matched"] and v3["faces"] == 1, str(v3))

        print("\n[4] a broken check leaves behaviour exactly as it was")

        def _boom(_crops):
            raise RuntimeError("cv2 exploded")

        saved_verdict = camera.liveness_verdict
        camera.liveness_verdict = _boom
        camera._recognise = lambda _j, _r: (1, True)
        try:
            v4 = json.loads(await camera._verify_owner_present_local({}))
        finally:
            camera.liveness_verdict = saved_verdict
        check("a liveness check that throws does not retire the second factor",
              v4["available"] and v4["matched"], str(v4))
        check("...and records that it could not tell (live=None), rather than claiming live",
              v4["live"] is None, str(v4))

        print("\n[5] the public verdict carries the field")
        camera._recognise = lambda _j, _r: (1, True)
        camera._gray_faces = lambda _j: [base.copy()]
        pub = await camera.verify_owner_present()
        check("verify_owner_present passes 'live' through", pub["live"] is False, str(pub))
        check("...and a dropped PC_LINK is can't-tell with live=None",
              set(pub) == {"available", "matched", "faces", "evidence", "live"}, str(sorted(pub)))
    finally:
        (camera._capture_burst, camera._owner_refs, camera._recognise, camera._gray_faces,
         camera._person_evidence) = saved


async def main() -> None:
    measurement()
    verdicts()
    await wiring()
    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
