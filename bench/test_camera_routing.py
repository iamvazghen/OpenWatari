"""J3.9 — camera has "three detection paths and two capture paths", and nothing said which was which.

The finding came from the graph, which counts functions. Reading the bodies gives a different
answer, and the answer is worth pinning rather than refactoring:

  * There is **one** frontal detector, `_detect_boxes`. `_detect_faces` (count) and `_gray_faces`
    (100x100 identity crops) are both thin consumers of it — so a change to detection reaches
    counting and recognition together, which is the property you want and nothing currently checks.
  * `_person_evidence` is the third "path" and is deliberately NOT a detector of the same kind. It
    answers occupancy with profile/upper-body cascades that are useless for identity, and it must
    never contribute a recognition crop: a profile face matched against frontal references is noise
    that would show up as a false "I recognise you".
  * The two capture paths are also a real distinction. `_capture_burst` opens the camera once and
    takes N frames — required for presence and identity, because a single frame is a coin flip when
    the owner glances away. `_capture_jpeg` takes one well-exposed frame — right for `look_around`,
    where a burst would be waste and the VLM only ever sees one image anyway.

So the resolution of J3.9 is not a merge, it is an assertion. The I1 second factor picked one of
these paths and the choice was invisible; the failure mode is silent — presence quietly downgraded
to a single frame, or identity quietly fed from the occupancy cascade, both of which read as
"recognition got worse" months later with no bisect point.

Hermetic: the camera is never opened. Frames are synthesised with cv2 and every capture is stubbed.

    uv run python bench/test_camera_routing.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain.tools import camera  # noqa: E402
import afon.brain.llm as llm_mod  # noqa: E402

passed = failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def _frame(width: int = 240, height: int = 240) -> bytes:
    """A real JPEG, so the decode path in _detect_faces / _gray_faces runs for real."""
    import cv2
    import numpy as np

    img = np.full((height, width), 128, dtype="uint8")
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


class _Counter:
    def __init__(self, ret):
        self.n = 0
        self.args: tuple = ()
        self.ret = ret

    def __call__(self, *a, **k):
        self.n += 1
        self.args = a
        return self.ret


async def main() -> None:
    saved = (camera._detect_boxes, camera._capture_jpeg, camera._capture_burst,
             camera._person_evidence, camera._owner_refs, camera._recognise, llm_mod.LLMClient)
    jpeg = _frame()
    try:
        print("[1] one frontal detector, two consumers — counting and identity cannot drift apart")
        camera._detect_boxes = lambda _gray: [(10, 10, 60, 60), (100, 100, 50, 50)]
        check(camera._detect_faces(jpeg) == 2, "_detect_faces counts what _detect_boxes found",
              str(camera._detect_faces(jpeg)))
        crops = camera._gray_faces(jpeg)
        check(len(crops) == 2, "_gray_faces crops what _detect_boxes found", str(len(crops)))
        check(all(c.shape == (100, 100) for c in crops),
              "…normalised to the 100x100 identity crop", str([c.shape for c in crops]))
        camera._detect_boxes = lambda _gray: []
        check(camera._detect_faces(jpeg) == 0 and camera._gray_faces(jpeg) == [],
              "both consumers follow the detector to zero")

        print("\n[2] the occupancy cascade never feeds identity")
        # _person_evidence says "someone is there" while the frontal detector sees nothing. That must
        # produce zero recognition crops — a profile match against frontal refs is noise, not a match.
        camera._person_evidence = lambda _j: True
        check(camera._gray_faces(jpeg) == [],
              "evidence of a person yields no recognition crop", str(camera._gray_faces(jpeg)))
        camera._capture_burst = _Counter([jpeg, jpeg, jpeg])
        # Enrolled (else the verdict is correctly "not available"), and the identity engine itself is
        # stubbed — its accuracy is test_face_recognition.py's job; this is about what routes where.
        camera._owner_refs = lambda: object()
        camera._recognise = lambda _j, _refs: (0, False)
        v = await camera.verify_owner_present()
        check(v["faces"] == 0 and v["evidence"] is True and v["matched"] is False,
              "verify_owner_present: 0 faces + evidence, and NOT a match", str(v))
        check(v["available"] is True, "…and the sensor still reports itself available", str(v))

        print("\n[3] presence captures a burst; a single frame is a coin flip")
        burst = _Counter([jpeg, jpeg, jpeg])
        single = _Counter(jpeg)
        camera._capture_burst, camera._capture_jpeg = burst, single
        camera._person_evidence = lambda _j: False
        camera._detect_boxes = lambda _gray: [(10, 10, 60, 60)]
        camera._owner_refs = lambda: None      # unenrolled: the count path, so the routing is visible
        out = await camera._visual_presence_local({})
        check(burst.n == 1 and single.n == 0,
              "visual_presence goes through _capture_burst, never _capture_jpeg",
              f"burst={burst.n} single={single.n}")
        check(len(burst.args) > 1 and int(burst.args[1]) >= 2,
              "…and asks for more than one frame", str(burst.args))
        check("at your desk" in out, "…and still answers presence correctly", out)

        print("\n[4] look_around captures ONE frame; a burst there is pure waste")
        burst.n = single.n = 0

        class _LLM:
            async def see(self, _b64, _prompt, **_kw):
                return "a desk and two monitors"

        llm_mod.LLMClient = _LLM
        out = await camera.look_around({})
        check(single.n == 1 and burst.n == 0,
              "look_around goes through _capture_jpeg, never _capture_burst",
              f"burst={burst.n} single={single.n}")
        check("Through the camera, sir:" in out, "…and describes the frame", out)
    finally:
        (camera._detect_boxes, camera._capture_jpeg, camera._capture_burst,
         camera._person_evidence, camera._owner_refs, camera._recognise, llm_mod.LLMClient) = saved

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
