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
from afon.shared.paths import state_dir

# Owner face refs live locally (never uploaded). One .npy of LBP histograms captured at enrollment.
_FACE_DIR = state_dir() / "faces"
_OWNER_REFS = _FACE_DIR / "owner.npy"
_MAX_REFS = 80   # newest refs kept across enrollment sessions (append, don't clobber)

# ArcFace embeddings — the real recogniser. SEPARATE file, separate threshold, separate metric:
# 512-dim cosine has nothing to do with 256-bin histogram intersection, and a number from one
# compared against the other is noise wearing a decimal point. Enrollment writes BOTH, so whichever
# backend is available at recognition time finds itself enrolled.
_OWNER_EMB = _FACE_DIR / "owner_emb.npy"
_EMB_DIM = 512


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


def _person_evidence(jpeg: bytes) -> bool:
    """Is there a person in frame that the FRONTAL detector cannot see? (turned away, leaning back)

    `_detect_boxes` is frontal-only at minSize 50x50 — deliberately, because its boxes feed identity
    (`_gray_faces` -> LBP refs / ArcFace crops), and a profile crop matched against frontal refs is
    noise. The cost of that correct choice is that "no box" has been reported as "no one is in view",
    which is an absence claim the detector cannot support: sitting back from the desk, turning to a
    second monitor, or a dim room all produce zero boxes with the owner sitting right there.

    So this answers a DIFFERENT question with cascades that are useless for identity and fine for
    occupancy: a profile face (both ways — OpenCV's profileface only detects one side, so the frame
    is mirrored and retried) or an upper body. It never contributes a recognition crop.

    Returns False on any failure. A missing cascade file must degrade to today's behaviour, not
    invent a person.
    """
    try:
        import cv2
        import numpy as np

        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return False
        eq = cv2.equalizeHist(img)
        for xml, scale, neighbours, min_size in (
                ("haarcascade_profileface.xml", 1.1, 4, (50, 50)),
                # Upper body is coarser and needs a bigger minimum, or curtains and chair backs
                # start reading as people — which would turn one wrong answer into a louder one.
                ("haarcascade_upperbody.xml", 1.1, 5, (90, 90))):
            cascade = cv2.CascadeClassifier(cv2.data.haarcascades + xml)
            if cascade.empty():
                continue
            for frame in (eq, cv2.flip(eq, 1)) if "profile" in xml else (eq,):
                if len(cascade.detectMultiScale(frame, scaleFactor=scale,
                                                minNeighbors=neighbours, minSize=min_size)):
                    return True
        return False
    except Exception as e:  # noqa: BLE001 — occupancy evidence is a nicety; identity is not
        logger.debug(f"person-evidence check skipped: {type(e).__name__}: {e}")
        return False


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


#: Cells per side for the face signature. A face is identified by WHERE its textures sit, not by
#: which textures it has: one global histogram over the whole crop is blind to layout, and measured
#: against the live refs on 2026-08-11 that blindness was total — a photo of a different person
#: scored 0.815 against the owner's 75 refs, higher than the owner's own median of 0.712, clearing
#: the 0.62 bar against 48 of them. Mean-to-refs was 0.700 for the stranger vs 0.709 for the owner:
#: no threshold could separate them because the descriptor carried no identity at all. Splitting the
#: crop into an 8x8 grid and concatenating the per-cell histograms (what OpenCV's LBPHFaceRecognizer
#: actually does) restores it: same-person 0.446-0.656 vs different-person 0.263-0.351, separable
#: with margin. See bench/test_face_identity_separation.py.
_GRID = 8
_SIG_LEN = _GRID * _GRID * 256


def _face_signature(gray) -> "list":
    """Spatially-blocked LBP signature of a 100x100 face crop — the identity descriptor."""
    import numpy as np

    step = gray.shape[0] // _GRID
    cells = [_lbp_hist(gray[gy * step:(gy + 1) * step + 2, gx * step:(gx + 1) * step + 2])
             for gy in range(_GRID) for gx in range(_GRID)]
    v = np.concatenate(cells)
    return v / (v.sum() or 1.0)


def _similarity(h1, h2) -> float:
    """Histogram intersection of two normalised histograms: 1.0 identical, 0.0 disjoint."""
    import numpy as np

    return float(np.minimum(h1, h2).sum())


def _best_similarity(sig, refs) -> float:
    """Best intersection against any ref, in one vectorised pass (refs is (n, _SIG_LEN))."""
    import numpy as np

    return float(np.minimum(refs, sig).sum(axis=1).max())


# ---- ArcFace backend (optional: `uv sync --extra vision`) --------------------------------------

_ARC: "tuple | None | bool" = None    # None = untried, False = unavailable, tuple = (detector, recogniser)


def _arcface():
    """(detector, recogniser) or None. Loaded once, lazily — importing onnxruntime and reading two
    ONNX files costs ~1s, and the LBP path must keep working on a machine that has neither.

    Deliberately silent about WHY beyond one warning: an owner without the vision extra installed
    should get a working face-counter, not a stack trace every time he walks past the camera.
    """
    global _ARC
    if _ARC is not None:
        return _ARC or None
    try:
        from uniface.detection import SCRFD
        from uniface.recognition import ArcFace

        _ARC = (SCRFD(), ArcFace())
        logger.info("face recognition: ArcFace + SCRFD loaded")
    except Exception as e:  # noqa: BLE001 — no uniface, no models, no network on first run
        _ARC = False
        logger.warning(f"face recognition: ArcFace unavailable ({type(e).__name__}); "
                       "falling back to the local LBP signature. Install: uv sync --extra vision")
    return _ARC or None


def _embed_faces(jpeg: bytes) -> list:
    """Every face in a frame as a normalised 512-d ArcFace embedding, or [] if the backend is off.

    The landmarks matter more than anything else here: ArcFace is trained on 5-point-aligned crops,
    and feeding it an unaligned box costs most of its accuracy. SCRFD returns the landmarks with the
    box, so alignment is free — skipping it would be the classic way to wire this up and get a
    fraction of the model people quote.
    """
    import numpy as np

    arc = _arcface()
    if arc is None:
        return []
    det, rec = arc
    import cv2

    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return []
    out = []
    try:
        for f in det.detect(img):
            emb = np.asarray(rec.get_embedding(img, np.asarray(f.landmarks))).ravel()
            n = float(np.linalg.norm(emb))
            if n:
                out.append(emb / n)
    except Exception as e:  # noqa: BLE001 — a bad frame must not take the camera down
        logger.warning(f"face embedding failed ({type(e).__name__}: {e})")
        return []
    return out


def _owner_embeddings():
    """Enrolled ArcFace embeddings, or None. Wrong-width files are refused for the same reason the
    LBP ones are: a comparison across two incompatible spaces is not a weak answer, it is no answer."""
    import numpy as np

    if not _OWNER_EMB.exists():
        return None
    try:
        e = np.load(_OWNER_EMB)
        if not len(e):
            return None
        if e.shape[1] != _EMB_DIM:
            logger.warning(f"face embeddings are {e.shape[1]}-wide, expected {_EMB_DIM} — ignoring")
            return None
        return e
    except Exception:  # noqa: BLE001
        return None


def _owner_refs():
    """Load enrolled owner signatures, or None if the owner's face hasn't been learned yet.

    Refs saved by the pre-2026-08-11 global-histogram descriptor are 256-wide and are REJECTED here:
    they cannot be compared against the current signature, and scoring them anyway is how a stranger
    got read as the owner. Rejecting them degrades to face-COUNTING and asks for a re-enrollment,
    which is honest; silently mis-scoring them is not.
    """
    import numpy as np

    if not _OWNER_REFS.exists():
        return None
    try:
        refs = np.load(_OWNER_REFS)
        if not len(refs):
            return None
        if refs.shape[1] != _SIG_LEN:
            logger.warning(
                f"face refs are the old {refs.shape[1]}-wide format (no spatial layout — it could "
                "not tell the owner from a stranger). Ignoring them; say 'learn my face' to re-enrol."
            )
            return None
        return refs
    except Exception:  # noqa: BLE001 — a corrupt ref file just means "not enrolled"
        return None


# ---- 09.F2: judge the capture before it becomes a permanent reference --------------------------

#: Mean frame brightness (0-255) below which a frame is too dark to enrol from. Same number the
#: burst uses for its exposure early-exit, named once so the two cannot drift apart.
DARK_FLOOR = 12.0

#: How many frames of the burst must be usable. A burst is 6-30 frames and the owner is allowed to
#: blink, glance away, or be walked past. Three is a floor on "did the capture work at all" rather
#: than on diversity — frames from one burst are seconds apart and highly correlated, so the real
#: angle and lighting variety comes from enrolling more than once (the refs append).
MIN_USABLE_FRAMES = 3


class CaptureVerdict:
    """What the burst actually contained, and what to tell the owner.

    Enrolment used to answer with one sentence for three different problems: "I couldn't spot a face
    — sit facing the camera in good light. If someone else is in shot, those frames are skipped."
    That covers no-face, too-dark and someone-standing-behind-you at once, and the owner cannot tell
    which of the three to fix. Worse, a burst where every frame held two faces looked identical to a
    burst where the camera was pointed at a wall — and one of those is a security-relevant near-miss
    (see `_enroll_from_jpegs`: enrolling a second face makes that person a permanent owner).
    """

    __slots__ = ("frames", "dark", "empty", "crowded", "usable", "reason")

    def __init__(self, frames: int, dark: int, empty: int, crowded: int, usable: int,
                 reason: str = "") -> None:
        self.frames, self.dark, self.empty = frames, dark, empty
        self.crowded, self.usable, self.reason = crowded, usable, reason

    @property
    def reject(self) -> bool:
        return bool(self.reason)

    def summary(self) -> str:
        return (f"{self.frames} frames: {self.usable} usable, {self.dark} too dark, "
                f"{self.empty} with no face, {self.crowded} with more than one")


def capture_verdict(frames: list) -> CaptureVerdict:
    """Judge a burst from `(brightness, face_count)` per frame. Pure — no camera, no cv2.

    Deliberately takes measurements rather than JPEGs so the decision can be tested without a webcam
    and without OpenCV's haar detector, which is the part that cannot be pinned in a hermetic test.
    """
    total = len(frames)
    dark = sum(1 for b, _n in frames if b < DARK_FLOOR)
    empty = sum(1 for b, n in frames if b >= DARK_FLOOR and n == 0)
    crowded = sum(1 for b, n in frames if b >= DARK_FLOOR and n > 1)
    usable = sum(1 for b, n in frames if b >= DARK_FLOOR and n == 1)
    v = CaptureVerdict(total, dark, empty, crowded, usable)
    if usable >= MIN_USABLE_FRAMES:
        return v
    # Report the DOMINANT cause, in the order that makes the owner's next action unambiguous. Two
    # faces first: it is the only one of the three with a security consequence, and it is the one
    # that would otherwise be reported as "I couldn't spot a face" while faces were all it saw.
    if crowded and crowded >= max(dark, empty):
        v.reason = (f"someone else was in shot for {crowded} of {total} frames, so I skipped them — "
                    f"enrolling a second face would make that person a permanent match for you. "
                    f"Try again alone, sir.")
    elif dark and dark >= max(empty, crowded):
        v.reason = (f"too dark — {dark} of {total} frames were under the exposure floor. Turn a light "
                    f"on or face a window, then try again.")
    elif empty:
        v.reason = (f"no face in {empty} of {total} frames — sit facing the lens and look up at it "
                    f"while I capture.")
    else:
        v.reason = (f"only {usable} usable frame{'s' if usable != 1 else ''} of {total}, and I need "
                    f"{MIN_USABLE_FRAMES} to cover more than one angle.")
    return v


def _frame_measurements(jpegs: list) -> list:
    """`(brightness, face_count)` per JPEG. The cv2 half of `capture_verdict`."""
    import cv2
    import numpy as np

    out = []
    for j in jpegs:
        img = cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            out.append((0.0, 0))
            continue
        out.append((float(img.mean()), len(_detect_boxes(img))))
    return out


# ---- liveness: is that a face, or a picture of one? (SYSTEMS.md 11.F3) -------------------------
# The camera is a SECOND FACTOR whose only power is to block: `matched` does not authorise anything,
# it just declines to refuse. So the attack that mattered was never "spoof your way in" — it was a
# printed photo of the owner propped in front of the webcam, which grants `matched` on every check
# forever and silently retires the second factor. Nothing would have reported that.
#
# What this covers, precisely: a burst that is the SAME IMAGE, held still relative to the camera — a
# print propped against the monitor, a photo taped up, a paused screen. That is the attack worth
# closing, because it is the one that is silently PERMANENT.
#
# What it does NOT cover, measured rather than assumed (bench/test_face_liveness.py [2b]):
#   * a print held in a HAND. Sub-pixel jitter resamples, resampling blurs, and blur survives
#     alignment, so it arrives as motion: ~3.5 against a moving face's ~4.5, with the floor at 2.0.
#     There is a margin but not a separation, and sizing it needs real prints on this camera, not
#     synthetic fixtures (11.R1's corpus).
#   * a VIDEO replay, which has genuine micro-motion. That is the texture-CNN / depth-camera tier the
#     plan declines until the cheap check has been measured and found wanting.
# Both are stated here and asserted in the gate, because a check believed to stop more than it does
# stops being watched — and the cost of each gap is bounded: an uncaught spoof leaves the second
# factor exactly where it was before this existed.
#
# The measurement is a frame difference, which is what the plan specifies — done properly. A raw
# difference is dominated by haar box jitter: the detector's box wanders a few pixels between frames,
# which moves the whole crop and swamps the signal for a live face and a photograph alike. So each
# pair is aligned by phase correlation first, and what is left is the NON-RIGID change: expression,
# blink, the parallax of a face that has depth. A flat picture has none of it, however much the hand
# holding it shakes.
#
# ponytail: the thresholds are defaults from the geometry (100x100 equalised crops, 8-bit grey), not
# measurements — there is no spoof corpus in this repo. MIN_LIVENESS_MOTION is deliberately LOW: it
# is sized to catch the unambiguous still image and to let anything arguable through, because a false
# "still" verdict only removes the second factor (see _verify_owner_present_local) while a false
# "live" verdict leaves things exactly as they were before this existed.

#: Mean absolute grey-level change between shift-corrected consecutive face crops, below which the
#: burst is one image rather than a face.
MIN_LIVENESS_MOTION = 2.0
#: Face crops needed before liveness can be judged at all. Fewer is "can't tell", never "still".
MIN_LIVENESS_FRAMES = 4
#: Border to drop before differencing. Warping and box jitter both corrupt the edges of a crop, and
#: those edges are where a rigid shift leaves its largest residue — exactly the confound being removed.
_LIVENESS_MARGIN = 8


class Liveness:
    """One burst, judged. ``verdict`` is "live" | "still" | "unknown"; ``reason`` is what to log."""

    __slots__ = ("verdict", "motion", "frames", "reason")

    def __init__(self, verdict: str, motion: float, frames: int, reason: str) -> None:
        self.verdict, self.motion, self.frames, self.reason = verdict, motion, frames, reason

    @property
    def live(self) -> bool:
        return self.verdict == "live"

    def summary(self) -> str:
        return f"{self.verdict} ({self.frames} frames, motion {self.motion:.2f})"


def _aligned_diff(a, b) -> float:
    """Mean |a - b| over two face crops, after removing whole-crop translation.

    Translation is the confound: the haar box wanders between frames, so an unaligned difference
    measures the detector's jitter rather than the face. Phase correlation finds that shift in one
    FFT pair and it is subtracted, leaving only what changed WITHIN the face.
    """
    import cv2
    import numpy as np

    fa, fb = a.astype(np.float32), b.astype(np.float32)
    try:
        (dx, dy), _ = cv2.phaseCorrelate(fa, fb)
    except Exception:  # noqa: BLE001 — a failed alignment measures more motion, never less
        dx = dy = 0.0
    if abs(dx) > 0.05 or abs(dy) > 0.05:
        shift = np.float32([[1, 0, -dx], [0, 1, -dy]])
        fb = cv2.warpAffine(fb, shift, (fb.shape[1], fb.shape[0]),
                            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    m = _LIVENESS_MARGIN
    return float(np.mean(np.abs(fa[m:-m, m:-m] - fb[m:-m, m:-m])))


def liveness_verdict(crops: list) -> Liveness:
    """Judge a burst of aligned face crops (one per frame, as `_gray_faces` returns them)."""
    n = len(crops)
    if n < MIN_LIVENESS_FRAMES:
        return Liveness("unknown", 0.0, n,
                        f"only {n} frame(s) with a face — too few to tell a person from a picture")
    diffs = [_aligned_diff(crops[i], crops[i + 1]) for i in range(n - 1)]
    motion = sum(diffs) / len(diffs)
    if motion < MIN_LIVENESS_MOTION:
        return Liveness("still", motion, n,
                        f"the face did not change across {n} frames (motion {motion:.2f} < "
                        f"{MIN_LIVENESS_MOTION}) — a photograph or a screen, not a person")
    return Liveness("live", motion, n, f"micro-motion across {n} frames (motion {motion:.2f})")


def _burst_crops(jpegs: list, limit: int = 6) -> list:
    """The first face crop from each of up to `limit` frames — the input to `liveness_verdict`.

    ponytail: first face, not largest. A second person in shot is already the multi-face case that
    `_enroll_from_jpegs` skips and `_majority_matched` votes on; picking a different face per frame
    would only inflate the motion figure, which errs toward "live" — the safe direction here.
    """
    out = []
    for j in jpegs[:limit]:
        faces = _gray_faces(j)
        if faces:
            out.append(faces[0])
    return out


def _enroll_from_jpegs(jpegs: list) -> int:
    """Enrol the owner from frames: store the signature of every detected face. APPENDS to any
    existing refs (each session adds lighting/angle diversity instead of discarding it), keeping the
    newest ``_MAX_REFS``. Returns the number of refs now stored.

    Frames showing more than one face are SKIPPED: every detected face was being enrolled as the
    owner, so anyone standing behind him during enrollment became a permanent "owner" reference, and
    with best-of-N matching one such ref is enough to let that person through forever.
    """
    import numpy as np

    solo = [j for j in jpegs if len(_gray_faces(j)) == 1]
    sigs = [_face_signature(f) for j in solo for f in _gray_faces(j)]

    # Write BOTH formats from the same frames. Enrolling only the backend that happens to be
    # available today leaves the other one silently unenrolled — and "unenrolled" degrades to
    # face-COUNTING, so the owner would find recognition had quietly stopped after an install.
    embs = [e for j in solo for e in _embed_faces(j)] if _arcface() is not None else []
    if embs:
        old_e = _owner_embeddings()
        if old_e is not None:
            embs = list(old_e) + embs
        _FACE_DIR.mkdir(parents=True, exist_ok=True)
        np.save(_OWNER_EMB, np.array(embs[-_MAX_REFS:]))

    if not sigs:
        return len(embs[-_MAX_REFS:]) if embs else 0
    old = _owner_refs()
    if old is not None:
        sigs = list(old) + sigs
    sigs = sigs[-_MAX_REFS:]
    _FACE_DIR.mkdir(parents=True, exist_ok=True)
    np.save(_OWNER_REFS, np.array(sigs))
    return len(sigs)


def _recognise(jpeg: bytes, refs) -> tuple[int, bool]:
    """(face_count, owner_matched) for a frame. Uses ArcFace when both the backend and an embedding
    enrollment are available, and the LBP signature otherwise — the two never mix.

    ``refs`` stays the LBP refs so every existing caller keeps working; the embeddings are looked up
    here because only this function knows which backend it ended up using.
    """
    import numpy as np

    embs = _owner_embeddings()
    if embs is not None:
        live = _embed_faces(jpeg)
        if live:
            thr = settings.face_embed_threshold
            return len(live), any(float((embs @ e).max()) >= thr for e in live)
        if _arcface() is not None:
            return 0, False       # backend is up and saw nobody — that is an answer, not a gap
    faces = _gray_faces(jpeg)
    if not faces:
        return 0, False
    thr = settings.face_match_threshold
    matched = any(_best_similarity(_face_signature(f), refs) >= thr for f in faces)
    return len(faces), matched


def _majority_matched(results: list) -> bool:
    """Did MOST of the frames that saw a face see the owner?

    ``visual_presence`` greets on any single matching frame, which is right for a greeting — a
    glance away should not make him invisible. Authorisation is the opposite trade: a burst of 12
    gives a stranger twelve independent chances to clear the bar once, so "any" turns a per-frame
    false-accept rate into a near-certainty. Measured per-frame FAR for this descriptor is ~14%
    (Olivetti, 40 people); any-of-12 makes that better than even odds, a majority of 12 makes it
    negligible, and the owner's own frames pass often enough that a majority still clears easily.

    Frames with no face at all are not votes against him — he looked away, that is all.
    """
    voting = [m for c, m in results if c > 0]
    return len(voting) >= 2 and sum(voting) * 2 > len(voting)


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
                return await _empty_or_unsure(jpegs)
            if n == 1:
                return "Someone's at the desk, sir, but I don't recognise them."
            return f"I can see {n} people, sir, but none I recognise as you."
        counts = [await asyncio.to_thread(_detect_faces, j) for j in jpegs]
        n = max(counts, default=0)
    except Exception as e:  # noqa: BLE001
        return tool_error("visual_presence", e)
    if n <= 0:
        return await _empty_or_unsure(jpegs)
    if n == 1:
        return "You're at your desk, sir — I can see you."
    return f"I can see {n} people in front of the camera, sir."


async def _empty_or_unsure(jpegs: list) -> str:
    """No frontal face: say which of the two things that means, instead of guessing the confident one.

    Only reached when the frontal count is zero, and it checks a few frames rather than all of them —
    the burst is 12 and the cascades are the expensive part, so this is the cheap half of a question
    that was previously answered wrongly for free.
    """
    for j in jpegs[:3]:
        if await asyncio.to_thread(_person_evidence, j):
            return ("Someone's there, sir, but not facing the camera — I can't tell if it's you.")
    return "No one's in view of the camera, sir."


async def verify_owner_present() -> dict:
    """I1 — a STRUCTURED owner verdict for use as a second authorisation factor.

    ``visual_presence`` answers the owner in prose, which is right for him and useless to a gate.
    This returns ``{"available": bool, "matched": bool, "faces": int, "evidence": bool,
    "live": bool | None}``:

      * ``available`` False — no camera, no enrolment, or the laptop is offline. The caller must then
        fall back to whatever it did before. A second factor that LOCKS THE OWNER OUT when a webcam is
        busy is worse than no second factor at all.
      * ``available`` True, ``matched`` False, ``faces`` 0, ``evidence`` False — the room is empty.
        Nobody is there to authorise anything, which is exactly the case a television talking at the
        microphone produces.
      * ``available`` True, ``matched`` False, ``faces`` 0, ``evidence`` True — a person IS in frame
        but not facing the camera (leaning back, turned to the other monitor). The frontal detector
        cannot rule on that, so this is a CAN'T TELL, not a negative verdict. Reporting it as an
        empty room told the owner "nobody is at the desk" while he sat at it.
      * ``available`` True, ``matched`` False, ``faces`` >0 — someone is there and it is not him.
      * ``live`` — False means the matching face never moved across the burst, i.e. a photograph or a
        screen (11.F3). That WITHDRAWS the verdict (``available`` goes False) rather than inverting
        it, so a very still owner is never accused of being an impostor. None means liveness was not
        consulted, which is every case where he did not match and it could change nothing.

    Not a tool: nothing should let the MODEL decide whether the owner is present.
    """
    raw = await _dispatch("camera_verify", {}, _verify_owner_present_local)
    try:
        d = json.loads(raw)
        return {"available": bool(d.get("available")), "matched": bool(d.get("matched")),
                "faces": int(d.get("faces") or 0), "evidence": bool(d.get("evidence")),
                # None = liveness was not consulted (he did not match, so it could change nothing).
                "live": None if d.get("live") is None else bool(d.get("live"))}
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        # _dispatch turns a dropped PC_LINK into a spoken sentence rather than JSON — that is the
        # "can't tell" case, not a negative verdict.
        return {"available": False, "matched": False, "faces": 0, "evidence": False, "live": None}


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
        faces = max((c for c, _ in results), default=0)
        # Only when no frontal face was found: distinguishing an empty room from a turned back is
        # the whole point, and running the cascades when a face WAS seen would be paying for an
        # answer already in hand.
        evidence = False
        if faces <= 0:
            for j in jpegs[:3]:
                if await asyncio.to_thread(_person_evidence, j):
                    evidence = True
                    break
        matched = _majority_matched(results)
        # 11.F3 — only when he MATCHED, because that is the only verdict liveness can change, and a
        # second haar pass over the burst is not free on a confirm-gate's critical path.
        live = None
        if matched:
            try:
                verdict = await asyncio.to_thread(lambda: liveness_verdict(_burst_crops(jpegs)))
                live = verdict.live
                if not live:
                    # WITHDRAW the verdict; never invert it. Reporting "matched: false" here would
                    # send the caller down the "someone is there and it is not him" path and BLOCK a
                    # very still owner — the same absence claim 11.F1 exists to forbid. Unavailable
                    # means can't-tell, and can't-tell behaves exactly as it did before the camera
                    # was a factor at all: the action proceeds on the spoken yes.
                    logger.warning(f"face second factor: WITHDRAWN — {verdict.reason}. If this "
                                   "repeats, check whether a photograph is propped in front of the "
                                   "camera.")
                    return json.dumps({"available": False, "matched": False, "faces": faces,
                                       "evidence": evidence, "live": False})
            except Exception as e:  # noqa: BLE001
                # A broken liveness check must not quietly retire the second factor — same rule as
                # the enrolment quality gate. Degrade to the behaviour that existed before it.
                logger.warning(f"face second factor: liveness check skipped ({type(e).__name__}: {e})")
        return json.dumps({"available": True, "matched": matched, "faces": faces,
                           "evidence": evidence, "live": live})
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
    # 09.F2 — judge the burst BEFORE anything is written. An unusable capture used to be reported as
    # one sentence covering three unrelated problems, and a burst full of two-face frames read as
    # "I couldn't spot a face" while faces were all it saw.
    try:
        verdict = capture_verdict(await asyncio.to_thread(_frame_measurements, jpegs))
    except Exception as e:  # noqa: BLE001 — a measurement failure must not block enrolment entirely
        logger.warning(f"camera: capture quality check skipped ({type(e).__name__})")
        verdict = None
    if verdict is not None and verdict.reject:
        logger.info(f"camera enrol rejected — {verdict.summary()}")
        return f"I didn't learn your face, sir: {verdict.reason}"
    try:
        n = await asyncio.to_thread(_enroll_from_jpegs, jpegs)
    except Exception as e:  # noqa: BLE001
        return tool_error("enroll_owner_face", e)
    if n <= 0:
        return ("I couldn't spot a face to learn, sir — sit facing the camera in good light and try "
                "'learn my face' again. If someone else is in shot, those frames are skipped.")
    margin = _enrollment_margin()
    return (f"Learned your face, sir — {n} reference{'s' if n != 1 else ''} captured. "
            f"I'll recognise you now.{margin}")


def _enrollment_margin() -> str:
    """One sentence of calibration: how well the owner's own frames match each other, against the
    threshold they must clear. The threshold is a constant carried from a public dataset, so without
    this the owner has no way to know it fits HIS camera and lighting until recognition quietly stops
    working (or quietly stops discriminating). Same-session frames are correlated, so treat this as a
    ceiling, not a guarantee — it is meant to catch a bad number, not to certify a good one."""
    import numpy as np

    # Measure whichever backend recognition will ACTUALLY use. Reporting the LBP margin while
    # ArcFace does the deciding would calibrate the path nobody is on.
    embs = _owner_embeddings()
    if embs is not None and _arcface() is not None and len(embs) >= 2:
        sims, thr, knob = embs @ embs.T, settings.face_embed_threshold, "AFON_FACE_EMBED_THRESHOLD"
        np.fill_diagonal(sims, -1.0)
        worst = float(sims.max(axis=1).min())
    else:
        refs = _owner_refs()
        if refs is None or len(refs) < 2:
            return ""
        thr, knob = settings.face_match_threshold, "AFON_FACE_MATCH_THRESHOLD"
        worst = float(min(np.minimum(refs, refs[i]).sum(axis=1)[np.arange(len(refs)) != i].max()
                          for i in range(len(refs))))
    if worst < thr:
        return (f" One caution: your own frames only reach {worst:.2f} against a {thr:.2f} bar, so "
                f"I'd miss you often — better light, or lower {knob}.")
    if worst < thr + 0.05:
        return f" Margin is thin though ({worst:.2f} against a {thr:.2f} bar) — worth re-running in the light you normally sit in."
    return f" Your own frames match at {worst:.2f} against a {thr:.2f} bar, sir — comfortable."


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
                    "frames": {"type": "integer",
                               "description": "How many frames to sample (default 24, 6-30)."},
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
