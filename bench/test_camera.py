"""Phase 3.2 / 3.3 — camera perception (look_around + visual_presence), hermetic.

Locks the logic without turning the webcam on: capture, vision description + degradation, and local
face-count → spoken presence. Live camera capture is exercised separately (bench/camera_presence_live.py).

    uv run python bench/test_camera.py
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


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


async def main() -> None:
    saved_cap, saved_faces, saved_llm = camera._capture_jpeg, camera._detect_faces, llm_mod.LLMClient
    saved_refs, saved_burst = camera._owner_refs, camera._capture_burst
    saved_evidence = camera._person_evidence
    camera._owner_refs = lambda: None   # count-based path (owner not enrolled) — deterministic
    try:
        print("[1] look_around: captures a frame + describes it via vision")
        camera._capture_jpeg = lambda index=0, warmup=3: b"\xff\xd8jpegbytes"

        class SeeingLLM:
            async def see(self, b64, prompt, **kw):
                return "a desk with two monitors and a coffee mug, sir"

        llm_mod.LLMClient = lambda: SeeingLLM()
        r = await camera.look_around({"prompt": "what's in front of me?"})
        check("returns a spoken camera description", "Through the camera, sir:" in r and "monitors" in r, r)

        print("\n[2] look_around: no vision model -> honest degradation, not a crash")
        class BlindLLM:
            async def see(self, *a, **k):
                raise RuntimeError("no vision reachable")

        llm_mod.LLMClient = lambda: BlindLLM()
        r = await camera.look_around({})
        check("degrades when vision is unreachable", "no vision model is reachable" in r, r)

        print("\n[3] look_around: no camera frame -> calm line")
        camera._capture_jpeg = lambda index=0, warmup=3: None
        r = await camera.look_around({})
        check("handles a missing webcam", "couldn't get a camera frame" in r, r)

        print("\n[4] visual_presence: local face count -> spoken presence")
        camera._capture_burst = lambda *a, **k: [b"\xff\xd8jpeg"]  # one frame; count comes from the mock
        camera._detect_faces = lambda jpeg: 0
        camera._person_evidence = lambda jpeg: False
        check("no face + no evidence -> 'no one in view'",
              "No one's in view" in await camera.visual_presence({}))

        # L2529: the frontal cascade is frontal-only at minSize 50x50, so "no box" also means
        # leaning back, turned to the other monitor, or a dim room. Claiming an empty room in those
        # cases is a confident wrong answer — and presence is what decides whether nudges fire.
        camera._person_evidence = lambda jpeg: True
        r = await camera.visual_presence({})
        check("no face BUT a person in frame -> can't tell, not 'no one'",
              "not facing the camera" in r and "No one's in view" not in r, r)
        camera._detect_faces = lambda jpeg: 1
        check("one face -> 'you're at your desk'", "at your desk" in await camera.visual_presence({}))
        camera._detect_faces = lambda jpeg: 3
        check("many faces -> counts them", "3 people" in await camera.visual_presence({}))

        print("\n[4b] the evidence cascades are really there, and do not invent people")
        # The whole L2529 fix degrades to False if a cascade file is missing — silently, and it
        # would look exactly like "the room really was empty". OpenCV has dropped shipped XMLs
        # between versions before, so name them.
        camera._person_evidence = saved_evidence
        import cv2  # noqa: E402
        import numpy as np  # noqa: E402
        for xml in ("haarcascade_profileface.xml", "haarcascade_upperbody.xml"):
            check(f"{xml} ships with this OpenCV",
                  not cv2.CascadeClassifier(cv2.data.haarcascades + xml).empty())
        noise = np.random.default_rng(7).integers(0, 256, (480, 640), dtype=np.uint8)
        ok, buf = cv2.imencode(".jpg", noise)
        check("random noise is not a person (no false 'someone is there')",
              ok and not camera._person_evidence(buf.tobytes()))
        check("garbage bytes degrade to 'no evidence', never a crash",
              camera._person_evidence(b"not a jpeg") is False)

        print("\n[5] visual_presence: no camera -> calm line, no crash")
        camera._capture_burst = lambda *a, **k: []
        r = await camera.visual_presence({})
        check("handles a missing webcam", "couldn't check the camera" in r, r)
    finally:
        camera._capture_jpeg, camera._detect_faces, llm_mod.LLMClient = saved_cap, saved_faces, saved_llm
        camera._owner_refs, camera._capture_burst = saved_refs, saved_burst
        camera._person_evidence = saved_evidence

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
