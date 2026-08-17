"""Speaker biometrics — "respond only to the owner's voice" (Phase 5).

Identity gate for the voice loop: after enrolling the owner's voice once, Afon compares each
spoken utterance's voiceprint to the enrolled one and **ignores commands from other voices**
(the TV, a guest). This is layered on top of the wake word + half-duplex gate as a final check.

Design
------
- **Backend = SpeechBrain ECAPA-TDNN** (``speechbrain/spkrec-ecapa-voxceleb``): a 192-dim speaker
  embedding, CPU-runnable on short clips. It's heavy (pulls torch), so it lives in the optional
  ``identity`` extra and is imported **lazily**. If it isn't installed (or no profile is enrolled,
  or the feature is off), the verifier **degrades to "accept everything"** — the pipeline never
  breaks, matching the rest of Afon's graceful-degradation contract.
- Enrollment (``bench/enroll_voice.py``) records the owner reading a script, keeps one L2-normalised
  vector per clip (not a mean — see ``_load_profile``), and saves a small JSON voiceprint to
  ``AFON_SPEAKER_PROFILE``. It then measures the owner against an impostor and records that band.
- At runtime, ``SpeakerGate`` (a Pipecat processor) buffers recent mic audio and, when a transcript
  is produced, embeds that audio and accepts the turn only if cosine-similarity clears the bar from
  ``accept_bar()`` — **derived from this profile's measured band**, not a constant (10.F3).

The gate **decision** is a pure function (``should_accept``) so it's unit-testable without torch.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import threading
from pathlib import Path

import numpy as np
from loguru import logger

from afon.config import settings
from afon.shared.paths import state_dir


#: Where the voiceprint used to live — the repo root. It is biometric data, and biometric data does
#: not belong in a source tree: it was only kept out of git by a `voiceprint.json*` ignore rule, one
#: line away from being committed, and it is state rather than code so a deploy or a fresh clone had
#: no business seeing it. It now sits with the face refs under ~/.afon/.
_LEGACY_PROFILE = Path(__file__).resolve().parents[3] / "voiceprint.json"


def _default_profile_path() -> Path:
    if settings.speaker_profile_path:
        return Path(settings.speaker_profile_path)
    return state_dir() / "voiceprint.json"


def _migrate_legacy_profile(path: Path) -> None:
    """Move a repo-root voiceprint (and its .bak) to the new home, once. Never overwrites: if both
    exist the new one wins, because re-enrolling is what created it."""
    if path.exists() or not _LEGACY_PROFILE.exists() or settings.speaker_profile_path:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        for src, dst in ((_LEGACY_PROFILE, path),
                         (_LEGACY_PROFILE.with_suffix(".json.bak"), path.with_suffix(".json.bak"))):
            if src.exists() and not dst.exists():
                dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                src.unlink()
        logger.info(f"voiceprint moved out of the repo to {path}")
    except OSError as e:
        logger.warning(f"could not move the voiceprint to {path}: {e}")


def should_accept(score: float, threshold: float, has_profile: bool, enabled: bool) -> bool:
    """Pure gate decision. Accept (don't gate) unless the feature is fully active AND we have a
    profile to compare against. Only then does the similarity score decide."""
    if not enabled or not has_profile:
        return True
    return score >= threshold


# -- where the accept bar comes from (SYSTEMS.md 10.F3) --------------------------------------------
# `settings.speaker_threshold` is one number for every profile, and cosine scores are not comparable
# across profiles: they depend on the mic, the room, and which acoustic conditions the enrolment
# happened to cover. The 0.30 default was in fact derived — by hand, once, from a day of live scores
# on 2026-07-25 — and then went stale the moment the profile changed, with nothing to notice.
#
# So the bar is now computed from THIS profile's measured separation, which enrolment records next to
# the vectors it measured it against. No separation recorded -> the setting, said out loud.

#: A gap narrower than this cannot be split by ANY bar: the profile is the problem, not the threshold.
MIN_SEPARATION = 0.10
#: No derived bar goes below this, however clean the enrolment looked. One impostor clip is one
#: impostor, and the set of voices that are not the owner is far larger than the sample; 0.30 is the
#: lowest bar a day of live scores supported (owner 0.34-0.45, television and guests 0.16-0.31).
MIN_DERIVED = 0.30
#: Window length for the floor/ceiling sample below. Roughly the length of a real spoken command, and
#: comfortably over embed()'s 0.5s floor.
FLOOR_WINDOW_S = 2.0


def derive_threshold(owner: float, impostor: float, fallback: float) -> tuple[float, str]:
    """Place the accept bar inside a MEASURED band. Returns (threshold, why).

    The midpoint, because that is all two point measurements support: biasing the bar toward the
    owner (fewer false rejects) or toward the impostor (fewer false accepts) needs the variance of
    each, and one clip apiece gives none.

    Both refusals return the fallback rather than a number this data cannot justify. A gate that
    quietly invents a bar out of a profile that cannot support one is the failure this task exists to
    remove, and swapping a stale constant for a confident wrong number is not an improvement.
    """
    gap = owner - impostor
    # Order matters, and it is the same order as `separation_verdict`: a too-narrow band and a
    # correctly-wide band that sits too low are different problems with opposite fixes.
    if gap < MIN_SEPARATION:
        return fallback, (f"kept the {fallback:.2f} setting — you score {owner:.2f} and the impostor "
                          f"{impostor:.2f}, a gap of {gap:+.2f}. No bar splits that; re-enrol on the "
                          f"mic you actually use.")
    mid = (owner + impostor) / 2.0
    if mid < MIN_DERIVED:
        if MIN_DERIVED >= owner:
            return fallback, (f"kept the {fallback:.2f} setting — the band {impostor:.2f}-{owner:.2f} "
                              f"lies entirely below the {MIN_DERIVED:.2f} floor, so any safe bar would "
                              f"also lock you out. Re-enrol.")
        return MIN_DERIVED, (f"derived {MIN_DERIVED:.2f} — the {impostor:.2f}-{owner:.2f} midpoint "
                             f"({mid:.2f}) is under the {MIN_DERIVED:.2f} floor, so the bar sits on "
                             f"the floor instead, with {owner - MIN_DERIVED:.2f} of headroom left.")
    return round(mid, 3), (f"derived {mid:.2f} from a measured {impostor:.2f}-{owner:.2f} band "
                           f"(gap {gap:.2f})")


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


class SpeakerVerifier:
    """Loads the enrolled voiceprint + (lazily) the ECAPA encoder; verifies utterance audio.

    ``embedder`` can be injected (tests pass a stub); otherwise SpeechBrain is loaded on first use.
    """

    def __init__(self, embedder=None) -> None:
        self._embedder = embedder           # callable(pcm_float32_mono_16k) -> np.ndarray | None
        self._tried_load = embedder is not None
        # Loading ECAPA takes ~56s (measured). It is warmed on a background thread at edge start so
        # the edge is listening in ~1s instead of ~57s, which means a real utterance CAN arrive
        # mid-load. Without this lock the second caller sees `_tried_load` already True, gets None,
        # and verify() fails OPEN — the gate would be silently off for the first minute after every
        # restart, and the audio watchdog restarts the edge on a device change mid-day.
        self._load_lock = threading.Lock()
        self._profile: np.ndarray | None = None
        #: (owner, impostor) as measured at the end of the enrolment that wrote THIS profile, or None.
        self._separation: tuple[float, float] | None = None
        self._load_profile()
        if self.has_profile:
            logger.info(f"speaker accept bar: {self.accept_bar()[1]}")

    # ---- profile ----------------------------------------------------------------------
    # The profile is a LIST of vectors (one per enrollment clip/condition), scored by MAX cosine —
    # same design as the face refs. A single mean vector could not cover the mic array's two
    # acoustic modes (Bluetooth audio active vs not): live data 2026-07-29 showed the owner at
    # 0.37-0.52 with AirPods off but 0.05-0.28 with AirPods on, against the SAME mean profile.
    def _load_profile(self) -> None:
        path = _default_profile_path()
        _migrate_legacy_profile(path)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                rows = data.get("embeddings") or ([data["embedding"]] if "embedding" in data else [])
                self._profile = np.asarray(rows, dtype=np.float32) if rows else None
                sep = data.get("separation")
                if isinstance(sep, dict) and {"owner", "impostor"} <= sep.keys():
                    self._separation = (float(sep["owner"]), float(sep["impostor"]))
                if self._profile is not None:
                    logger.info(f"speaker profile loaded ({self._profile.shape[0]} vector(s), "
                                f"{self._profile.shape[1]}-dim) from {path.name}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"speaker profile load failed: {e}")

    @property
    def has_profile(self) -> bool:
        return self._profile is not None

    @property
    def separation(self) -> tuple[float, float] | None:
        """(owner, impostor) as measured against this profile, or None if never measured."""
        return self._separation

    @staticmethod
    def save_profile(embedding: np.ndarray, *, append: bool = False, max_vectors: int = 12) -> Path:
        """Save the voiceprint. ``embedding`` is one vector (legacy) or a (n,192) stack. With
        ``append`` the new vectors join the existing ones (newest kept), so a second enrollment
        under different acoustics (AirPods connected) ADDS coverage instead of replacing it.
        Always leaves a ``.bak`` of the previous profile — recovery was impossible without it."""
        path = _default_profile_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        arr = np.atleast_2d(np.asarray(embedding, dtype=np.float32))
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        arr = arr / norms
        if append and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                old = data.get("embeddings") or ([data["embedding"]] if "embedding" in data else [])
                if old:
                    arr = np.vstack([np.asarray(old, dtype=np.float32), arr])
            except Exception as e:  # noqa: BLE001 — unreadable old profile -> just replace it
                logger.warning(f"could not append to old profile ({e}); replacing")
        arr = arr[-max_vectors:]
        if path.exists():
            try:
                path.with_suffix(".json.bak").write_text(path.read_text(encoding="utf-8"),
                                                         encoding="utf-8")
            except Exception:  # noqa: BLE001 — a failed backup never blocks saving
                pass
        # No "separation" key: a saved profile has no measured band yet. Deliberately dropped rather
        # than carried over, including in --append mode — the vectors just changed, so the old
        # owner/impostor numbers describe a profile that no longer exists. Enrolment measures the new
        # band a minute later and calls record_separation(); until it does, the gate uses the setting
        # and says so. A stale band silently reused is exactly the bug 10.F3 is about.
        path.write_text(json.dumps({"embeddings": arr.tolist()}), encoding="utf-8")
        return path

    @staticmethod
    def record_separation(owner: float, impostor: float) -> Path:
        """Attach the measured owner/impostor scores to the profile they were measured against.

        Enrolment already took both numbers (`bench/enroll_voice.py`) and did nothing with them but
        print advice to change a setting by hand. This is where that advice stops being advice.
        """
        path = _default_profile_path()
        data = json.loads(path.read_text(encoding="utf-8"))
        data["separation"] = {"owner": round(float(owner), 4), "impostor": round(float(impostor), 4),
                              "measured": _dt.datetime.now().astimezone().isoformat(timespec="seconds")}
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def accept_bar(self) -> tuple[float, str]:
        """The score an utterance must reach, and where that number came from."""
        from afon.config import settings as _s   # re-read: tests and /reload mutate it
        if self._separation is None:
            return _s.speaker_threshold, (f"using the {_s.speaker_threshold:.2f} setting — this "
                                          f"profile has no measured separation. Re-run "
                                          f"`uv run python bench/enroll_voice.py` to measure one.")
        return derive_threshold(*self._separation, _s.speaker_threshold)

    # ---- embedding backend ------------------------------------------------------------
    #: What a complete ECAPA cache looks like. All five must be present before we trust the cache
    #: enough to skip the hub check — a partial one has to be allowed to finish downloading.
    _ECAPA_FILES = ("hyperparams.yaml", "embedding_model.ckpt", "classifier.ckpt",
                    "label_encoder.ckpt", "mean_var_norm_emb.ckpt")

    def _ensure_embedder(self):
        if self._embedder is not None:
            return self._embedder
        # Serialise the load. A caller arriving mid-load BLOCKS here and then gets the real
        # embedder, rather than being told the backend is unavailable (which fails open).
        with self._load_lock:
            if self._embedder is not None or self._tried_load:
                return self._embedder
            return self._load_embedder()

    def _load_embedder(self):
        self._tried_load = True
        try:
            import torch  # noqa: F401
            from speechbrain.inference.speaker import EncoderClassifier

            savedir = Path(__file__).resolve().parents[3] / ".speechbrain-ecapa"
            # from_hparams asks huggingface.co for the current revision even when every file is
            # already sitting in savedir, so booting the speaker gate depends on the hub being
            # reachable. Once the cache is complete there is nothing to ask about, so pin it offline
            # — but only around THIS call: the flag is global, and leaving it set would break any
            # other HF model in this process (Whisper, say) that hasn't been downloaded yet.
            cached = all((savedir / f).exists() for f in self._ECAPA_FILES)
            prev = os.environ.get("HF_HUB_OFFLINE")
            if cached:
                os.environ["HF_HUB_OFFLINE"] = "1"
            try:
                clf = EncoderClassifier.from_hparams(
                    source="speechbrain/spkrec-ecapa-voxceleb", savedir=str(savedir)
                )
            finally:
                if cached:
                    if prev is None:
                        os.environ.pop("HF_HUB_OFFLINE", None)
                    else:
                        os.environ["HF_HUB_OFFLINE"] = prev

            def _embed(wav: np.ndarray) -> np.ndarray:
                import torch as _t

                t = _t.tensor(wav).unsqueeze(0)
                with _t.no_grad():
                    emb = clf.encode_batch(t).squeeze().cpu().numpy()
                return emb.astype(np.float32)

            self._embedder = _embed
            logger.info("speaker verifier: SpeechBrain ECAPA loaded")
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"speaker verifier disabled (backend unavailable: {type(e).__name__}). "
                "Install the 'identity' extra to enable: uv sync --extra identity"
            )
        return self._embedder

    def embed(self, pcm16: bytes, sample_rate: int = 16000) -> np.ndarray | None:
        """Embed int16 mono PCM. Returns None if the backend isn't available."""
        emb_fn = self._ensure_embedder()
        if emb_fn is None:
            return None
        wav = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        # ECAPA expects 16 kHz. The live mic often delivers 44.1/48 kHz; feeding that raw makes the
        # waveform ~3x too fast -> a garbage embedding (~0.07 vs the 16 kHz-enrolled profile, the cause
        # of "Afon ignores me"). Resample so live matches enrollment (which is already 16 kHz).
        if sample_rate and sample_rate != 16000:
            import soxr

            wav = soxr.resample(wav, sample_rate, 16000)
        if wav.size < 8000:  # < 0.5s at 16 kHz is too short to be reliable
            return None
        try:
            return emb_fn(wav)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"speaker embed failed: {e}")
            return None

    def score(self, pcm16: bytes, sample_rate: int = 16000) -> float | None:
        """Similarity of this audio to the enrolled profile, or None if it cannot be measured.

        Best match against ANY enrolled vector — each vector covers one acoustic condition.
        """
        emb = self.embed(pcm16, sample_rate)
        if emb is None or self._profile is None:
            return None
        return max(cosine(emb, row) for row in np.atleast_2d(self._profile))

    def verify(self, pcm16: bytes, sample_rate: int = 16000) -> tuple[bool, float]:
        """Return (accept, score). Degrades to (True, 1.0) when unavailable/unenrolled/off."""
        if not settings.speaker_id_enabled or not self.has_profile:
            return True, 1.0
        score = self.score(pcm16, sample_rate)
        if score is None:
            return True, 1.0  # backend missing -> don't lock the owner out
        accept = should_accept(score, self.accept_bar()[0], self.has_profile, True)
        return accept, score

    def window_scores(self, pcm16: bytes, sample_rate: int = 16000) -> list[float]:
        """Score one clip in short overlapping windows. Empty if nothing could be measured.

        Why this exists, and why the bar depends on it: the enrolment clips are six seconds of the
        owner reading deliberately, seconds after he enrolled — his BEST case — while a live turn is
        "yes" or "lights off" in whatever voice he happens to have. A bar derived from the six-second
        score sits ABOVE his typical live score, and TODO I1 is explicit that raising the bar on data
        like that starts rejecting the owner (0.29 rejected while the same phrase cleared 0.36 seconds
        later). Short windows are the cheapest honest sample of that floor: same clip, no extra
        recording, and it measures the length of audio the gate will actually see.
        """
        step = int(FLOOR_WINDOW_S * sample_rate) * 2      # bytes, 16-bit mono
        hop = max(2, step // 2)
        out = []
        for i in range(0, max(1, len(pcm16) - step + 1), hop):
            s = self.score(pcm16[i:i + step], sample_rate)
            if s is not None:
                out.append(s)
        return out
