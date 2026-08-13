"""Face recognition must actually RECOGNISE — not just detect a face and call it the owner.

Measured against the LIVE enrolled refs on 2026-08-11, the shipping descriptor (one global 256-bin
LBP histogram over the whole 100x100 crop) carried no identity at all:

    a photo of a different person   -> 0.815 best-of-75, clearing 48 of the 75 refs at thr 0.62
    the owner's own frames          -> 0.712 median
    stranger mean-to-refs 0.700     vs   owner mean-to-refs 0.709

Confirmed on real multi-person data (Olivetti, 40 people x 10 photos, all pairs):

    descriptor          same-person   different-person   best achievable FAR / FRR
    global 256 (old)    0.904+-0.030  0.855+-0.034       23.6% / 20.3%   <- and best-of-5 OVERLAPS
    grid 8x8   (new)    0.561+-0.074  0.439+-0.039       14.5% / 14.1%

So the fix is real and worth having — but read the second row honestly: ~14% equal error is a
"probably him" heuristic, NOT identity. Nothing may treat a single frame of it as authorisation;
`_majority_matched` exists for exactly that reason. Identity grade needs a face-embedding model
(dlib / insightface), which is the upgrade path noted in config.py.

The cause of the old failure is structural, not a tuning miss: a global histogram records WHICH
textures a crop contains and discards WHERE they sit, and where they sit is the whole of face
identity. This test pins that PROPERTY rather than a score — take one face and shuffle its cells,
and the pixels, textures and global histogram are all but unchanged while the face is destroyed.
Separation NUMBERS have to come from real photographs (above); a synthetic face would only ever
measure the fixture, which is the trap two earlier drafts of this file fell into.

    uv run python bench/test_face_identity_separation.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def a_face(seed: int = 1) -> np.ndarray:
    """A textured 100x100 stand-in for a face crop: soft shading plus fine grain, so LBP has real
    content in every cell (a flat synthetic drawing has none, and would flatter any descriptor)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:100, 0:100].astype(np.float64)
    img = 150 + 40 * np.exp(-(((xx - 50) ** 2 + (yy - 52) ** 2) / 2400))       # head shading
    for sx in (-13, 13):
        img -= 70 * np.exp(-(((xx - (50 + sx)) ** 2 + (yy - 42) ** 2) / 45))   # eyes
    img -= 45 * np.exp(-(((xx - 50) ** 2 / 120 + (yy - 70) ** 2 / 20)))        # mouth
    img -= 20 * np.exp(-(((xx - 50) ** 2 / 12 + (yy - 57) ** 2 / 90)))         # nose
    img += rng.normal(0, 9, (100, 100))                                        # skin grain
    return cv2.equalizeHist(np.clip(img, 0, 255).astype(np.uint8))


def shuffle_cells(img: np.ndarray, grid: int = 8, seed: int = 7) -> np.ndarray:
    """Same cells, rearranged. Nearly the same texture inventory; not a face any more."""
    step = img.shape[0] // grid
    cells = [img[y * step:(y + 1) * step, x * step:(x + 1) * step]
             for y in range(grid) for x in range(grid)]
    order = np.random.default_rng(seed).permutation(len(cells))
    out = img.copy()
    for i, src in enumerate(order):
        y, x = divmod(i, grid)
        out[y * step:(y + 1) * step, x * step:(x + 1) * step] = cells[src]
    return out


def main() -> None:
    from afon.brain.tools.camera import (_best_similarity, _face_signature, _lbp_hist,
                                         _majority_matched, _similarity, _SIG_LEN)

    owner = a_face()
    scrambled = shuffle_cells(owner)

    print("[1] the OLD global histogram is blind to layout — the production bug, reproduced")
    g_keep = _similarity(_lbp_hist(owner), _lbp_hist(scrambled))
    print(f"      face vs the SAME face with its cells shuffled: {g_keep:.3f}")
    check(g_keep > 0.90,
          "a destroyed face is still ~the same histogram (so anyone's face matches anyone's)",
          f"{g_keep:.3f} — if this drops, re-check whether the global form was really the fault")

    print("\n[2] the spatially blocked signature is not")
    s_keep = _similarity(_face_signature(owner), _face_signature(scrambled))
    g_drop, s_drop = 1.0 - g_keep, 1.0 - s_keep
    print(f"      shuffled scores {s_keep:.3f} — layout response {s_drop:.3f} vs {g_drop:.3f}")
    # Deliberately a RATIO, not an absolute score: an absolute bar here would just be a statement
    # about the synthetic fixture. How much harder the descriptor reacts to rearrangement is the
    # property that distinguishes the two, and it holds whatever the fixture.
    check(s_drop > 5 * g_drop,
          "rearranging the face costs the signature many times what it costs the histogram",
          f"{s_drop:.3f} vs {g_drop:.3f} = {s_drop / max(g_drop, 1e-9):.1f}x")

    print("\n[3] a burst VOTES — it does not accept on any single frame")
    # Recognition scores against every ref and takes the best, and the burst is 12 frames. With a
    # ~14% per-frame false-accept rate, "matched if ANY frame matched" is better than even odds for
    # a stranger. Authorisation therefore needs a majority of the frames that saw a face at all.
    check(_majority_matched([(1, True)] + [(1, False)] * 11) is False,
          "one lucky frame out of twelve is NOT the owner")
    check(_majority_matched([(1, True)] * 5 + [(1, False)] * 7) is False,
          "...nor is a strong minority")
    check(_majority_matched([(1, True)] * 7 + [(1, False)] * 5) is True,
          "a majority IS the owner")
    check(_majority_matched([(1, True)] * 3 + [(0, False)] * 9) is True,
          "frames where he looked away are not votes against him")
    check(_majority_matched([(1, True)]) is False,
          "a single frame is never enough to authorise")
    check(_majority_matched([]) is False, "no frames at all is not a match")

    print("\n[4] stale refs are refused, not mis-scored")
    import afon.brain.tools.camera as cam

    frames = [a_face(seed) for seed in (1, 2, 3)]
    refs = np.array([_face_signature(f) for f in frames])
    # A FIXED name in the repo root is a landmine: two runs of this file at once (the suite plus a
    # developer re-running it) each delete the other's scratch file mid-test. Own the name.
    tmp = Path(tempfile.mkdtemp()) / "owner-refs.npy"
    real = cam._OWNER_REFS
    cam._OWNER_REFS = tmp
    try:
        np.save(tmp, np.array([_lbp_hist(f) for f in frames]))     # old 256-wide format
        check(cam._owner_refs() is None,
              "old-format refs load as 'not enrolled' (they cannot be compared)")
        np.save(tmp, refs)
        got = cam._owner_refs()
        check(got is not None and got.shape[1] == _SIG_LEN,
              "current-format refs load normally", str(None if got is None else got.shape))
    finally:
        cam._OWNER_REFS = real
        tmp.unlink(missing_ok=True)

    print("\n[5] enrollment does not learn a bystander as the owner")
    # Every detected face in a frame used to become an owner ref, and best-of-N means ONE such ref
    # would let that person in permanently.
    seen = {"n": 0}

    def fake_gray_faces(_jpeg):
        seen["n"] += 1
        return [owner, scrambled] if seen["n"] == 1 else [owner]

    orig_gray, cam._gray_faces = cam._gray_faces, fake_gray_faces
    cam._OWNER_REFS = tmp
    blank = cv2.imencode(".jpg", np.zeros((10, 10), np.uint8))[1].tobytes()
    try:
        n = cam._enroll_from_jpegs([blank, blank])
        check(n == 1, "the two-face frame is skipped; only the solo frame is learned", f"n={n}")
        stored = np.load(tmp)
        check(_best_similarity(_face_signature(owner), stored)
              > _best_similarity(_face_signature(scrambled), stored),
              "...and the stored ref is the owner's, not the second face's")
    finally:
        cam._gray_faces = orig_gray
        cam._OWNER_REFS = real
        tmp.unlink(missing_ok=True)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
