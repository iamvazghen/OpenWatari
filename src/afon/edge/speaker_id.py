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
- Enrollment (``bench/enroll_voice.py``) records a few seconds of the owner, averages the embeddings,
  L2-normalises, and saves a small JSON voiceprint to ``AFON_SPEAKER_PROFILE``.
- At runtime, ``SpeakerGate`` (a Pipecat processor) buffers recent mic audio and, when a transcript
  is produced, embeds that audio and accepts the turn only if cosine-similarity ≥ threshold.

The gate **decision** is a pure function (``should_accept``) so it's unit-testable without torch.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
from loguru import logger

from afon.config import settings


def _default_profile_path() -> Path:
    return Path(settings.speaker_profile_path or
                str(Path(__file__).resolve().parents[3] / "voiceprint.json"))


def should_accept(score: float, threshold: float, has_profile: bool, enabled: bool) -> bool:
    """Pure gate decision. Accept (don't gate) unless the feature is fully active AND we have a
    profile to compare against. Only then does the similarity score decide."""
    if not enabled or not has_profile:
        return True
    return score >= threshold


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
        self._profile: np.ndarray | None = None
        self._load_profile()

    # ---- profile ----------------------------------------------------------------------
    # The profile is a LIST of vectors (one per enrollment clip/condition), scored by MAX cosine —
    # same design as the face refs. A single mean vector could not cover the mic array's two
    # acoustic modes (Bluetooth audio active vs not): live data 2026-07-29 showed the owner at
    # 0.37-0.52 with AirPods off but 0.05-0.28 with AirPods on, against the SAME mean profile.
    def _load_profile(self) -> None:
        path = _default_profile_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                rows = data.get("embeddings") or ([data["embedding"]] if "embedding" in data else [])
                self._profile = np.asarray(rows, dtype=np.float32) if rows else None
                if self._profile is not None:
                    logger.info(f"speaker profile loaded ({self._profile.shape[0]} vector(s), "
                                f"{self._profile.shape[1]}-dim) from {path.name}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"speaker profile load failed: {e}")

    @property
    def has_profile(self) -> bool:
        return self._profile is not None

    @staticmethod
    def save_profile(embedding: np.ndarray, *, append: bool = False, max_vectors: int = 12) -> Path:
        """Save the voiceprint. ``embedding`` is one vector (legacy) or a (n,192) stack. With
        ``append`` the new vectors join the existing ones (newest kept), so a second enrollment
        under different acoustics (AirPods connected) ADDS coverage instead of replacing it.
        Always leaves a ``.bak`` of the previous profile — recovery was impossible without it."""
        path = _default_profile_path()
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
        path.write_text(json.dumps({"embeddings": arr.tolist()}), encoding="utf-8")
        return path

    # ---- embedding backend ------------------------------------------------------------
    #: What a complete ECAPA cache looks like. All five must be present before we trust the cache
    #: enough to skip the hub check — a partial one has to be allowed to finish downloading.
    _ECAPA_FILES = ("hyperparams.yaml", "embedding_model.ckpt", "classifier.ckpt",
                    "label_encoder.ckpt", "mean_var_norm_emb.ckpt")

    def _ensure_embedder(self):
        if self._embedder is not None or self._tried_load:
            return self._embedder
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

    def verify(self, pcm16: bytes, sample_rate: int = 16000) -> tuple[bool, float]:
        """Return (accept, score). Degrades to (True, 1.0) when unavailable/unenrolled/off."""
        if not settings.speaker_id_enabled or not self.has_profile:
            return True, 1.0
        emb = self.embed(pcm16, sample_rate)
        if emb is None:
            return True, 1.0  # backend missing -> don't lock the owner out
        # Best match against ANY enrolled vector — each vector covers one acoustic condition.
        score = max(cosine(emb, row) for row in np.atleast_2d(self._profile))
        accept = should_accept(score, settings.speaker_threshold, self.has_profile, True)
        return accept, score
