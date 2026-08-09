"""Phase 3.2 / 3.3 — Perception: the laptop camera as Afon's eye.

* ``look_around`` (3.2): grab a webcam frame and have a vision model answer about it — "what am I
  looking at", "is the whiteboard readable", "what's in front of me". Reuses ``LLMClient.see``.
* ``visual_presence`` (3.3): grab a frame and count faces LOCALLY with OpenCV's haar cascade — no
  cloud, and the image never leaves the machine (only a yes/no + count) — so Afon can be
  presence-aware (greet when the owner sits down, hold non-urgent nudges when the seat is empty).

Both degrade cleanly: no webcam / camera busy / access blocked → a calm spoken line, never a crash.
The heavy, blocking OpenCV calls run in a thread so the event loop keeps breathing.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

from loguru import logger

from afon.config import settings
from afon.brain.tools.base import tool_error
from afon.brain.tools.system import _dispatch  # forward-to-laptop-if-connected, like screenshot

# Owner face refs live locally (never uploaded). One .npy of LBP histograms captured at enrollment.
_FACE_DIR = Path.home() / ".afon" / "faces"
_OWNER_REFS = _FACE_DIR / "owner.npy"
_MAX_REFS = 80   # newest refs kept across enrollment sessions (append, don't clobber)


def _capture_jpeg(index: int = 0, warmup: int = 20, min_brightness: float = 12.0) -> bytes | None:
    """Grab ONE well-exposed JPEG frame from the webcam. Returns bytes, or None if no frame at all.

    Blocking (opens the device) — call via ``asyncio.to_thread``. Reads warmup frames so the camera's
    auto-exposure can ramp (the first frame or two are typically near-black), keeping the BRIGHTEST
    frame seen and stopping early once one clears ``min_brightness``. Uses OpenCV's default backend —
    the DirectShow backend on this hardware hands back a black first frame then locks dim, whereas the
    default (MSMF) is well-exposed straight away.

    ponytail: min_brightness is the exposure floor — the calibration knob. A genuinely dark room still
    returns the brightest frame captured (the floor only gates the early-exit, never the return).
    """
    try:
        import cv2
    except ImportError:
        logger.warning("camera: opencv (cv2) not installed")
        return None
    cap = cv2.VideoCapture(index)  # default backend; forced CAP_DSHOW returns dark, slow-ramping frames
    try:
        if not cap.isOpened():
            return None
        best = None
        best_b = -1.0
        for i in range(max(1, warmup)):
            ok, f = cap.read()
            if not ok or f is None:
                continue
            b = float(f.mean())
            if b > best_b:
                best, best_b = f, b
            if i >= 2 and b >= min_brightness:  # exposure has ramped enough — good frame in hand
                break
        if best is None:
            return None
        ok, buf = cv2.imencode(".jpg", best)
        return buf.tobytes() if ok else None
    finally:
        cap.release()


def _capture_burst(index: int = 0, n: int = 12, warmup: int = 20,
                   min_brightness: float = 12.0, gap: float = 0.12) -> list:
    """Open the camera ONCE, ramp exposure once, then grab ``n`` JPEG frames rapidly. Returns a list of
    JPEG bytes (possibly empty). For enrollment: the owner holds a pose for ~n*gap seconds, not the
    ~n*(reopen+warmup) of calling ``_capture_jpeg`` per frame. Blocking — call via ``to_thread``."""
    import time

    try:
        import cv2
    except ImportError:
        logger.warning("camera: opencv (cv2) not installed")
        return []
    cap = cv2.VideoCapture(index)
    try:
        if not cap.isOpened():
            return []
        for i in range(max(1, warmup)):  # ramp auto-exposure once for the whole burst
            ok, f = cap.read()
            if ok and f is not None and i >= 2 and float(f.mean()) >= min_brightness:
                break
        out = []
        for _ in range(max(1, n)):
            ok, f = cap.read()
            if ok and f is not None:
                ok2, buf = cv2.imencode(".jpg", f)
                if ok2:
                    out.append(buf.tobytes())
            time.sleep(gap)
        return out
    finally:
        cap.release()


def _detect_boxes(gray) -> list:
    """Face bounding boxes in a grayscale image. Histogram-equalise first (rescues backlit/dim faces),
    then try OpenCV's default haar cascade and fall back to the 'alt' cascade — measured here to be
    markedly more tolerant of glasses, slight head angle, and window backlight. Returns [(x,y,w,h)]."""
    import cv2

    eq = cv2.equalizeHist(gray)
    for xml in ("haarcascade_frontalface_default.xml", "haarcascade_frontalface_alt.xml"):
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + xml)
        if cascade.empty():
            continue
        boxes = cascade.detectMultiScale(eq, scaleFactor=1.1, minNeighbors=4, minSize=(50, 50))
        if len(boxes):
            return list(boxes)
    return []


def _detect_faces(jpeg: bytes) -> int:
    """Count frontal faces in a JPEG (fully local)."""
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0
    return len(_detect_boxes(img))


# ---- owner recognition (Phase 3.3+, local LBP histograms) --------------------------------------

def _gray_faces(jpeg: bytes) -> list:
    """Detect faces and return each as a 100x100 histogram-equalised grayscale crop (lighting-norm)."""
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return []
    out = []
    for (x, y, w, h) in _detect_boxes(img):
        crop = cv2.equalizeHist(cv2.resize(img[y:y + h, x:x + w], (100, 100)))
        out.append(crop)
    return out


def _lbp_hist(gray) -> "list":
    """Normalised 256-bin Local Binary Pattern histogram of a grayscale face crop.

    LBP encodes each pixel by the sign of its 8 neighbours vs the centre — a texture signature that's
    largely invariant to monotonic lighting change, which raw pixels aren't. This is the same feature
    OpenCV's (contrib-only) LBPHFaceRecognizer uses; we compute it in numpy so no extra dep is needed.
    """
    import numpy as np

    g = gray.astype(np.int16)
    c = g[1:-1, 1:-1]
    code = (((g[:-2, :-2] >= c).astype(np.uint8) << 7) | ((g[:-2, 1:-1] >= c).astype(np.uint8) << 6) |
            ((g[:-2, 2:] >= c).astype(np.uint8) << 5) | ((g[1:-1, 2:] >= c).astype(np.uint8) << 4) |
            ((g[2:, 2:] >= c).astype(np.uint8) << 3) | ((g[2:, 1:-1] >= c).astype(np.uint8) << 2) |
            ((g[2:, :-2] >= c).astype(np.uint8) << 1) | (g[1:-1, :-2] >= c).astype(np.uint8))
    hist = np.histogram(code, bins=256, range=(0, 256))[0].astype(np.float64)
    s = hist.sum()
    return hist / s if s else hist


def _similarity(h1, h2) -> float:
    """Histogram intersection of two normalised histograms: 1.0 identical, 0.0 disjoint."""
    import numpy as np

    return float(np.minimum(h1, h2).sum())


def _owner_refs():
    """Load enrolled owner histograms, or None if the owner's face hasn't been learned yet."""
    import numpy as np

    if not _OWNER_REFS.exists():
        return None
    try:
        refs = np.load(_OWNER_REFS)
        return refs if len(refs) else None
    except Exception:  # noqa: BLE001 — a corrupt ref file just means "not enrolled"
        return None


def _enroll_from_jpegs(jpegs: list) -> int:
    """Enrol the owner from frames: store the LBP histogram of every detected face. APPENDS to any
    existing refs (each session adds lighting/angle diversity instead of discarding it), keeping the
    newest ``_MAX_REFS``. Returns the number of refs now stored."""
    import numpy as np

    hists = [_lbp_hist(f) for j in jpegs for f in _gray_faces(j)]
    if not hists:
        return 0
    old = _owner_refs()
    if old is not None:
        hists = list(old) + hists
    hists = hists[-_MAX_REFS:]
    _FACE_DIR.mkdir(parents=True, exist_ok=True)
    np.save(_OWNER_REFS, np.array(hists))
    return len(hists)


def _recognise(jpeg: bytes, refs) -> tuple[int, bool]:
    """(face_count, owner_matched) for a frame given enrolled refs. Matched only if a face clears the
    similarity threshold against the best-matching enrolled sample."""
    faces = _gray_faces(jpeg)
    if not faces:
        return 0, False
    thr = settings.face_match_threshold
    matched = any(max(_similarity(_lbp_hist(f), r) for r in refs) >= thr for f in faces)
    return len(faces), matched


async def _camera_capture_local(args: dict) -> str:
    """Grab one webcam frame HERE and return it as JSON {"b64": ...} (or {"b64": null}). Runs on the
    machine with the camera — locally on the laptop, or forwarded there by ``look_around``."""
    idx = int(args.get("camera_index", 0) or 0)
    jpeg = await asyncio.to_thread(_capture_jpeg, idx)
    return json.dumps({"b64": base64.b64encode(jpeg).decode() if jpeg else None})


async def look_around(args: dict) -> str:
    """Phase 3.2 — capture a webcam frame and describe/answer about it via a vision model. The CAPTURE
    forwards to the laptop when the brain runs cameraless on the VPS (like ``screenshot``); the vision
    model then runs on the brain (which holds the vision key)."""
    prompt = (args.get("prompt") or "").strip() or (
        "Describe what you can see through the camera, concisely, for the owner. One or two sentences."
    )
    raw = await _dispatch("camera_capture", {"camera_index": int(args.get("camera_index", 0) or 0)},
                          _camera_capture_local)
    try:
        b64 = json.loads(raw).get("b64")
    except (json.JSONDecodeError, TypeError, AttributeError):
        return raw or "I couldn't reach the camera, sir."  # _dispatch returned a laptop-offline note
    if not b64:
        return ("I couldn't get a camera frame, sir — there may be no webcam, it's in use, or camera "
                "access is blocked.")
    from afon.brain.llm import LLMClient

    try:
        desc = await LLMClient().see(b64, prompt)
        return f"Through the camera, sir: {desc}"
    except Exception as e:  # noqa: BLE001 — vision unreachable -> honest degradation, not a crash
        logger.warning(f"look_around: vision unavailable ({type(e).__name__})")
        return "I took a look, sir, but no vision model is reachable right now to interpret it."


async def visual_presence(args: dict) -> str:
    """Phase 3.3 — is the owner at the desk? Recognises the enrolled owner. Runs WHOLE on the machine
    with the camera + face refs: forwarded to the laptop when the brain is the cameraless VPS, else
    local. The owner's biometric refs (owner.npy) never leave the laptop — only the yes/no verdict."""
    return await _dispatch("camera_presence", args, _visual_presence_local)


async def _visual_presence_local(args: dict) -> str:
    idx = int(args.get("camera_index", 0) or 0)
    # A short burst, not one frame: a single frame is a coin-flip when the owner glances away. Take the
    # most face-ful frame (and a match from ANY frame) so a downward glance doesn't read as "empty".
    jpegs = await asyncio.to_thread(_capture_burst, idx, 12, 20, 12.0, 0.2)
    if not jpegs:
        return "I couldn't check the camera, sir — no webcam, it's in use, or access is blocked."
    refs = _owner_refs()
    try:
        if refs is not None:
            results = [await asyncio.to_thread(_recognise, j, refs) for j in jpegs]
            n = max((c for c, _ in results), default=0)
            matched = any(m for _, m in results)
            if matched:
                return "You're at your desk, sir — I recognise you."
            if n <= 0:
                return "No one's in view of the camera, sir."
            if n == 1:
                return "Someone's at the desk, sir, but I don't recognise them."
            return f"I can see {n} people, sir, but none I recognise as you."
        counts = [await asyncio.to_thread(_detect_faces, j) for j in jpegs]
        n = max(counts, default=0)
    except Exception as e:  # noqa: BLE001
        return tool_error("visual_presence", e)
    if n <= 0:
        return "No one's in view of the camera, sir."
    if n == 1:
        return "You're at your desk, sir — I can see you."
    return f"I can see {n} people in front of the camera, sir."


async def verify_owner_present() -> dict:
    """I1 — a STRUCTURED owner verdict for use as a second authorisation factor.

    ``visual_presence`` answers the owner in prose, which is right for him and useless to a gate.
    This returns ``{"available": bool, "matched": bool, "faces": int}``:

      * ``available`` False — no camera, no enrolment, or the laptop is offline. The caller must then
        fall back to whatever it did before. A second factor that LOCKS THE OWNER OUT when a webcam is
        busy is worse than no second factor at all.
      * ``available`` True, ``matched`` False, ``faces`` 0 — the room is empty. Nobody is there to
        authorise anything, which is exactly the case a television talking at the microphone produces.
      * ``available`` True, ``matched`` False, ``faces`` >0 — someone is there and it is not him.

    Not a tool: nothing should let the MODEL decide whether the owner is present.
    """
    raw = await _dispatch("camera_verify", {}, _verify_owner_present_local)
    try:
        d = json.loads(raw)
        return {"available": bool(d.get("available")), "matched": bool(d.get("matched")),
                "faces": int(d.get("faces") or 0)}
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        # _dispatch turns a dropped PC_LINK into a spoken sentence rather than JSON — that is the
        # "can't tell" case, not a negative verdict.
        return {"available": False, "matched": False, "faces": 0}


async def _verify_owner_present_local(_args: dict) -> str:
    refs = _owner_refs()
    if refs is None:
        return json.dumps({"available": False, "matched": False, "faces": 0})
    try:
        # Same burst as visual_presence: one frame is a coin-flip when he glances away.
        jpegs = await asyncio.to_thread(_capture_burst, 0, 12, 20, 12.0, 0.2)
        if not jpegs:
            return json.dumps({"available": False, "matched": False, "faces": 0})
        results = [await asyncio.to_thread(_recognise, j, refs) for j in jpegs]
        return json.dumps({"available": True,
                           "matched": any(m for _, m in results),
                           "faces": max((c for c, _ in results), default=0)})
    except Exception as e:  # noqa: BLE001 — an unreadable camera is "can't tell", never "not him"
        logger.warning(f"face second factor: camera check failed ({type(e).__name__}: {e})")
        return json.dumps({"available": False, "matched": False, "faces": 0})


async def enroll_owner_face(args: dict) -> str:
    """Phase 3.3 — learn the owner's face locally so ``visual_presence`` can recognise them. Forwards
    to the laptop (camera + owner.npy live there) when the brain is the cameraless VPS, else local."""
    return await _dispatch("camera_enroll", args, _enroll_owner_face_local)


async def _enroll_owner_face_local(args: dict) -> str:
    """Grabs several frames from the webcam over a couple of seconds and stores the LBP histogram of
    each detected face. Everything stays on disk locally (``~/.afon/faces/owner.npy``); nothing
    uploads. Say "learn my face" / "remember what I look like" while sitting in front of the camera."""
    idx = int(args.get("camera_index", 0) or 0)
    shots = max(6, min(30, int(args.get("frames", 24) or 24)))
    # Single-open burst over a few seconds — a wider window so the owner can glance up at the lens.
    jpegs = await asyncio.to_thread(_capture_burst, idx, shots, 20, 12.0, 0.2)
    if not jpegs:
        return "I couldn't reach the camera to learn your face, sir — check it's connected and not in use."
    try:
        n = await asyncio.to_thread(_enroll_from_jpegs, jpegs)
    except Exception as e:  # noqa: BLE001
        return tool_error("enroll_owner_face", e)
    if n <= 0:
        return ("I couldn't spot a face to learn, sir — sit facing the camera in good light and try "
                "'learn my face' again.")
    return f"Learned your face, sir — {n} reference{'s' if n != 1 else ''} captured. I'll recognise you now."


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "look_around",
            "description": (
                "SEE through the laptop camera and describe/answer about what's physically in front of "
                "the owner. Use for 'what am I looking at', 'what's in front of me', 'look at this', "
                "'can you see this'. Optional 'prompt' for a specific question."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Optional specific question about the view."},
                    "camera_index": {"type": "integer", "description": "Webcam index (default 0)."},
                },
                "required": [],   # every argument genuinely optional — stated, not left implied
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "visual_presence",
            "description": (
                "Check whether the owner is physically at the desk using the camera (local face "
                "detection/recognition only — no image is sent anywhere). Use for 'am I visible', "
                "'are you watching', 'do you recognise me', or internally to be presence-aware. If the "
                "owner's face is enrolled it says whether it's them; otherwise it reports faces in view."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "camera_index": {"type": "integer", "description": "Webcam index (default 0)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "enroll_owner_face",
            "description": (
                "Learn the owner's face locally (stored on disk, never uploaded) so future "
                "'visual_presence' checks can recognise them. Use for 'learn my face', 'remember what I "
                "look like', 'enroll my face'. The owner should be sitting in front of the camera."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "camera_index": {"type": "integer", "description": "Webcam index (default 0)."},
                    "frames": {"type": "integer", "description": "How many frames to sample (default 6)."},
                },
                "required": [],
            },
        },
    },
]

HANDLERS = {"look_around": look_around, "visual_presence": visual_presence,
            "enroll_owner_face": enroll_owner_face}

# What the laptop executor (edge/pc_agent.py) runs LOCALLY for each forwarded camera op — the camera
# and the owner's face refs live on the laptop, so recognition/enrollment run THERE and only the
# verdict (or, for look_around, one frame) crosses the wire. No re-dispatch (these are the _local fns).
LOCAL_HANDLERS = {
    "camera_capture": _camera_capture_local,
    "camera_presence": _visual_presence_local,
    "camera_enroll": _enroll_owner_face_local,
    # Runs on the laptop, where the camera and owner.npy live. Only the verdict crosses the link —
    # the biometric refs never leave the machine.
    "camera_verify": _verify_owner_present_local,
}
