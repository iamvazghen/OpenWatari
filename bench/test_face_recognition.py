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
    if kind == "noise":   # a stranger: unstructured, so it shares no layout with the striped 'faces'
        return np.random.default_rng(4).integers(0, 256, (100, 100), dtype=np.uint8)
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

        # A stranger, and deliberately NOT _face('h'): every pair of these two-tone stripe patterns
        # scores exactly 0.500 under both descriptors, so that check only ever asserted "the
        # threshold constant is above 0.5" — it would have gone green for a recogniser that
        # recognised nobody. Real identity separation is measured in test_face_identity_separation.py;
        # this one only has to prove the roundtrip wires up.
        camera._gray_faces = lambda jpeg: [_face('noise')]
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
        camera._capture_burst = lambda *a, **k: [b"\xff\xd8jpeg"] * 6  # frames present; result mocked
        # 09.F2 — the burst is now judged before anything is written, so a fake JPEG has to come with
        # fake measurements: cv2 cannot decode `b"\xff\xd8jpeg"`, and an undecodable frame is
        # (correctly) counted as dark-and-faceless.
        camera._frame_measurements = lambda jpegs: [(120.0, 1)] * len(jpegs)
        camera._enroll_from_jpegs = lambda jpegs: 5
        r = await camera.enroll_owner_face({"frames": 3})
        check("success line reports refs", "Learned your face" in r and "5 reference" in r, r)
        camera._enroll_from_jpegs = lambda jpegs: 0
        r = await camera.enroll_owner_face({"frames": 3})
        check("no face -> guidance, not a crash", "couldn't spot a face" in r, r)
        camera._capture_burst = lambda *a, **k: []  # enroll_owner_face captures via a burst
        r = await camera.enroll_owner_face({})
        check("no camera -> calm line", "couldn't reach the camera" in r, r)

        await capture_rejection()
    finally:
        camera._FACE_DIR, camera._OWNER_REFS, camera._gray_faces = saved_dir, saved_refs_path, saved_gray

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


# -- 09.F2: an unusable capture is rejected AT CAPTURE TIME, and says which problem it was --------
# Enrolment answered every failed capture with one sentence covering three unrelated problems: "I
# couldn't spot a face — sit facing the camera in good light. If someone else is in shot, those
# frames are skipped." No face, too dark, and someone-standing-behind-you have different fixes, and
# the third matters most: `_enroll_from_jpegs` skips multi-face frames precisely because enrolling a
# second face makes that person a permanent owner match. A burst where every frame held two faces was
# reported as "I couldn't spot a face" — while faces were all it saw.
async def capture_rejection() -> None:
    from afon.brain.tools import camera as cam

    print("\n[09.F2] the burst is judged, and each cause is named")
    good = cam.capture_verdict([(120.0, 1)] * 8)
    # The positive case first: a verdict that rejects everything passes a gate made only of
    # negatives, and then the owner can never enrol at all.
    check(f"a clean burst is accepted ({good.summary()})", not good.reject, good.reason)

    cases = (
        ("two faces throughout", [(120.0, 2)] * 8, ("someone else was in shot", "permanent match")),
        ("a dark room", [(4.0, 1)] * 8, ("too dark", "exposure floor")),
        ("camera pointed at a wall", [(120.0, 0)] * 8, ("no face", "facing the lens")),
    )
    seen = {}
    for name, frames, expect in cases:
        v = cam.capture_verdict(frames)
        seen[name] = v.reason
        check(f"{name} is rejected", v.reject, v.summary())
        for phrase in expect:
            check(f"...and says why ({phrase!r})", phrase in v.reason, v.reason)
    check("the three causes do not share a message", len(set(seen.values())) == 3, str(seen))

    print("\n[09.F2] the counts, and the dominant cause")
    # The `(4.0, 0)` frame is the one that matters: dark AND faceless. A dark frame is normally
    # faceless *because* it is dark, so counting it under both causes double-counts every dark burst
    # and can make "no face" the dominant diagnosis for a lighting problem. Without this frame in the
    # fixture the mistake is invisible — the first version of this check used `(4.0, 1)`, which no
    # amount of breaking the brightness condition could distinguish.
    v = cam.capture_verdict([(120.0, 1), (120.0, 1), (4.0, 1), (4.0, 0), (120.0, 0), (120.0, 3)])
    check("every frame is accounted for exactly once",
          v.dark + v.empty + v.crowded + v.usable == v.frames, v.summary())
    check("a dark frame is not also counted as faceless", v.dark == 2 and v.empty == 1, v.summary())
    # A burst that is mostly fine with one bystander frame must still ENROL — that frame is skipped
    # downstream. Rejecting a whole capture over one passer-by is how a working feature gets
    # abandoned.
    mostly = cam.capture_verdict([(120.0, 1)] * 7 + [(120.0, 2)])
    check("one bystander frame does not sink an otherwise good burst", not mostly.reject,
          mostly.summary())
    check("...but too few usable frames does", cam.capture_verdict([(120.0, 1)] * 2).reject,
          "two frames seconds apart is one angle in one light")
    check("an empty burst is rejected rather than enrolled from nothing",
          cam.capture_verdict([]).reject)

    print("\n[09.F2] the tool refuses to WRITE on a bad capture")
    # The load-bearing half: the verdict has to stop the write, not just print. Enrolment APPENDS to
    # the owner refs, so a bad capture is not a wasted minute — it is a permanent contribution.
    saved_burst, saved_meas, saved_enroll = (cam._capture_burst, cam._frame_measurements,
                                             cam._enroll_from_jpegs)
    wrote: list[int] = []
    cam._enroll_from_jpegs = lambda jpegs: (wrote.append(len(jpegs)), 5)[1]
    try:
        cam._capture_burst = lambda *a, **k: [b"jpeg"] * 8
        cam._frame_measurements = lambda jpegs: [(120.0, 2)] * len(jpegs)
        r = await cam.enroll_owner_face({})
        check("a two-face burst is refused with the reason spoken",
              "didn't learn your face" in r and "someone else was in shot" in r, r)
        check("...and nothing was written", not wrote, f"enrolled from {wrote} frames anyway")
        cam._frame_measurements = lambda jpegs: [(120.0, 1)] * len(jpegs)
        r = await cam.enroll_owner_face({})
        check("a good burst still enrols", "Learned your face" in r, r)
        check("...and did write", bool(wrote), "the good path stopped working")

        # A measurement failure must not become a refusal to enrol: this is a guard on quality, not a
        # new dependency in the middle of the only path to a face profile.
        def _boom(_jpegs):
            raise RuntimeError("cv2 exploded")

        cam._frame_measurements = _boom
        r = await cam.enroll_owner_face({})
        check("a broken quality check degrades to enrolling, not to failing",
              "Learned your face" in r, r)
    finally:
        cam._capture_burst, cam._frame_measurements = saved_burst, saved_meas
        cam._enroll_from_jpegs = saved_enroll


if __name__ == "__main__":
    asyncio.run(main())
