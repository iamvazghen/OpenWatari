"""The ArcFace path: a real recogniser, wired so that having it or not having it are both safe.

The LBP signature it replaces is a texture heuristic. Measured on Olivetti (40 people x 10 photos)
the shipped global histogram could not separate anyone from anyone (23.6% false-accept at its BEST
threshold, best-of-N overlapping outright); the 8x8 grid that replaced it reached 14.5%/14.1%, which
is better and still not identity. ArcFace on the same benchmark: **8.4%/8.6%**, and best-of-5
separates with four times the margin — and that benchmark is its worst case, 64x64 grayscale
upscaled, where a webcam gives 640x480 colour.

Two backends means two ways to be wrong, and this file is aimed at both:

  * comparing across them. 512-d cosine and 256-bin histogram intersection are different spaces;
    a number from one measured against the other is noise with a decimal point. Hence two files,
    two settings, two thresholds — and refusal rather than a guess when the width is wrong.
  * enrolling only one. Whichever backend is unenrolled degrades to face-COUNTING, so an owner who
    installs the vision extra would find recognition had quietly stopped. Enrollment writes BOTH.

Runs with or without uniface installed: every check either asserts the ArcFace behaviour or asserts
the fallback, and says which it did.

    uv run python bench/test_face_arcface_backend.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

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


def main() -> None:
    import afon.brain.tools.camera as cam
    from afon.config import settings

    have_arc = cam._arcface() is not None
    print(f"[0] backend: {'ArcFace + SCRFD' if have_arc else 'LBP fallback (uniface unavailable)'}")

    print("\n[1] the two spaces are kept apart")
    check(cam._OWNER_EMB != cam._OWNER_REFS, "embeddings and signatures live in different files")
    check(hasattr(settings, "face_embed_threshold") and hasattr(settings, "face_match_threshold"),
          "and each metric has its OWN threshold setting",
          "one number cannot serve two metrics")
    tmp = Path(tempfile.mkdtemp())
    saved = (cam._FACE_DIR, cam._OWNER_REFS, cam._OWNER_EMB)
    cam._FACE_DIR, cam._OWNER_REFS, cam._OWNER_EMB = tmp, tmp / "o.npy", tmp / "e.npy"
    try:
        np.save(cam._OWNER_EMB, np.random.default_rng(0).normal(size=(4, 256)).astype(np.float32))
        check(cam._owner_embeddings() is None,
              "a wrong-width embedding file is REFUSED, not compared anyway",
              "comparing across spaces is not a weak answer, it is no answer")
        np.save(cam._OWNER_EMB, np.random.default_rng(0).normal(size=(4, 512)).astype(np.float32))
        got = cam._owner_embeddings()
        check(got is not None and got.shape[1] == 512, "a correct-width one loads")
        cam._OWNER_EMB.unlink()
        check(cam._owner_embeddings() is None, "absent = not enrolled, not an error")

        print("\n[2] enrollment writes BOTH formats from the same frames")
        face = _a_face()
        jpegs = [_jpg(face)] * 3
        n = cam._enroll_from_jpegs(jpegs)
        check(n > 0, f"enrolled {n} reference(s)")
        check(cam._OWNER_REFS.exists(), "the LBP signatures are written")
        if have_arc:
            check(cam._OWNER_EMB.exists(), "...and so are the ArcFace embeddings",
                  "the other backend would be left silently unenrolled")
            e = np.load(cam._OWNER_EMB)
            check(e.shape[1] == 512 and len(e) >= 1, f"embeddings are {e.shape}",
                  str(e.shape))
            check(abs(float(np.linalg.norm(e[0])) - 1.0) < 1e-3,
                  "embeddings are stored L2-normalised (so a dot product IS the cosine)")
        else:
            check(not cam._OWNER_EMB.exists(),
                  "no embeddings written when the backend is absent (nothing to write)")

        print("\n[3] recognition tells the owner from someone else")
        refs = cam._owner_refs()
        cnt, matched = cam._recognise(_jpg(face), refs)
        check(matched is True, "the enrolled face is recognised", f"count={cnt}")
        other = _another_face()
        cnt2, matched2 = cam._recognise(_jpg(other), refs)
        if have_arc:
            check(matched2 is False, "a DIFFERENT real face is rejected",
                  "this is the exact case the old descriptor got wrong (a stranger scored 0.815)")
        else:
            check(True, f"(LBP fallback: stranger matched={matched2} — see "
                        "test_face_identity_separation.py for what that path can and cannot do)")
        cnt0, m0 = cam._recognise(_jpg(np.zeros((200, 200, 3), np.uint8)), refs)
        check(cnt0 == 0 and m0 is False, "an empty frame is nobody")

        print("\n[4] the calibration line measures the backend actually in use")
        msg = cam._enrollment_margin()
        check(bool(msg), "enrollment reports a margin", "the owner cannot tell if the bar fits him")
        want = settings.face_embed_threshold if have_arc else settings.face_match_threshold
        check(f"{want:.2f}" in msg,
              f"...against the {'ArcFace' if have_arc else 'LBP'} threshold ({want:.2f})",
              f"calibrating the path nobody is on: {msg.strip()[:90]}")
    finally:
        cam._FACE_DIR, cam._OWNER_REFS, cam._OWNER_EMB = saved

    print("\n[5] a broken backend degrades, it does not crash")
    # An owner without the extra installed must get a working face-counter, not a stack trace every
    # time he walks past the camera.
    real = cam._ARC
    try:
        cam._ARC = False
        check(cam._arcface() is None, "an unavailable backend reports itself absent")
        check(cam._embed_faces(_jpg(_a_face())) == [], "...and embedding returns nothing, quietly")
    finally:
        cam._ARC = real

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


def _jpg(rgb: np.ndarray) -> bytes:
    if rgb.ndim == 2:
        rgb = cv2.cvtColor(rgb, cv2.COLOR_GRAY2RGB)
    return cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))[1].tobytes()


def _a_face() -> np.ndarray:
    from skimage import data
    return data.astronaut()


def _another_face() -> np.ndarray:
    from skimage import data
    return cv2.cvtColor(data.camera(), cv2.COLOR_GRAY2RGB)


if __name__ == "__main__":
    main()
