"""Phase 3.3+ — local owner face recognition (LBP histograms), hermetic.

Locks the recognition math (self-match = 1.0, lighting-shift tolerance, different face rejected), the
enroll->recognise roundtrip through real file I/O, and the visual_presence / enroll_owner_face
messaging. No webcam and no cloud — face crops are injected. cv2's haar detector is not exercised here
(that's the live smoke, bench/camera_presence_live.py); this pins the logic on top of it.

    uv run python bench/test_face_recognition.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
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


def _face(kind: str) -> np.ndarray:
    """A structured synthetic 'face'. Real faces are textured EVERYWHERE (no big flat regions), which
    is what makes their LBP histograms person-specific; fine (period-1) patterns model that, so two
    different 'faces' genuinely separate under LBP instead of both collapsing to the flat-region code."""
    x, y = np.meshgrid(np.arange(100), np.arange(100))
    m = {"v": x % 2, "h": y % 2, "checker": (x + y) % 2}[kind]
    return (np.asarray(m).astype(np.uint8) * 255)


async def main() -> None:
    print("[1] LBP histogram similarity")
    a = _face('v')
    b = _face('checker')
    ha, hb = camera._lbp_hist(a), camera._lbp_hist(b)
    check("self-similarity is 1.0", abs(camera._similarity(ha, ha) - 1.0) < 1e-9)
    check("a different face scores lower", camera._similarity(ha, hb) < 0.95,
          f"{camera._similarity(ha, hb):.3f}")
    # LBP compares neighbours to the centre — a uniform brightness shift preserves every comparison.
    bright = np.clip(a.astype(np.int16) + 40, 0, 255).astype(np.uint8)
    check("tolerates a uniform lighting shift", camera._similarity(ha, camera._lbp_hist(bright)) > 0.9,
          f"{camera._similarity(ha, camera._lbp_hist(bright)):.3f}")

    print("\n[2] enroll -> recognise roundtrip (real save/load)")
    saved_dir, saved_refs_path, saved_gray = camera._FACE_DIR, camera._OWNER_REFS, camera._gray_faces
    tmp = Path(tempfile.mkdtemp())
    camera._FACE_DIR = tmp
    camera._OWNER_REFS = tmp / "owner.npy"
    try:
        owner = _face('v')
        camera._gray_faces = lambda jpeg: [owner]           # every frame "shows" the owner
        n = camera._enroll_from_jpegs([b"j1", b"j2", b"j3"])
        check("enrollment stores refs", n == 3 and camera._OWNER_REFS.exists())
        refs = camera._owner_refs()
        check("refs load back", refs is not None and len(refs) == 3)

        camera._gray_faces = lambda jpeg: [owner]
        cnt, matched = camera._recognise(b"frame", refs)
        check("owner is recognised", cnt == 1 and matched is True)

        camera._gray_faces = lambda jpeg: [_face('h')]         # a stranger (orthogonal structure)
        _, matched2 = camera._recognise(b"frame", refs)
        check("a stranger is rejected", matched2 is False)

        camera._gray_faces = lambda jpeg: []                 # empty frame
        cnt0, matched0 = camera._recognise(b"frame", refs)
        check("empty frame -> no face, no match", cnt0 == 0 and matched0 is False)

        print("\n[3] visual_presence recognition branch")
        camera._capture_burst = lambda *a, **k: [b"\xff\xd8jpeg"]  # visual_presence samples a burst
        camera._owner_refs = lambda: refs
        camera._recognise = lambda jpeg, r: (1, True)
        r = await camera.visual_presence({})
        check("recognised -> 'I recognise you'", "recognise you" in r, r)
        camera._recognise = lambda jpeg, r: (1, False)
        r = await camera.visual_presence({})
        check("stranger -> 'don't recognise them'", "don't recognise" in r, r)
        camera._recognise = lambda jpeg, r: (0, False)
        r = await camera.visual_presence({})
        check("empty -> 'no one in view'", "No one's in view" in r, r)

        print("\n[4] enroll_owner_face tool messaging")
        camera._capture_burst = lambda *a, **k: [b"\xff\xd8jpeg"]  # frames present; result from the mock
        camera._enroll_from_jpegs = lambda jpegs: 5
        r = await camera.enroll_owner_face({"frames": 3})
        check("success line reports refs", "Learned your face" in r and "5 reference" in r, r)
        camera._enroll_from_jpegs = lambda jpegs: 0
        r = await camera.enroll_owner_face({"frames": 3})
        check("no face -> guidance, not a crash", "couldn't spot a face" in r, r)
        camera._capture_burst = lambda *a, **k: []  # enroll_owner_face captures via a burst
        r = await camera.enroll_owner_face({})
        check("no camera -> calm line", "couldn't reach the camera" in r, r)
    finally:
        camera._FACE_DIR, camera._OWNER_REFS, camera._gray_faces = saved_dir, saved_refs_path, saved_gray

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
