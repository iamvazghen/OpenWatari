"""The edge must not be deaf for a minute after every restart — and must not fail OPEN while warming.

Measured: loading ECAPA takes ~56s on this machine. It was warmed synchronously while the pipeline
was being built, so the edge did not start listening until it finished. That is invisible at 03:20
when the refresh task runs, and very visible when the audio watchdog restarts the edge on a device
change at midday: Afon simply does not answer, with nothing in the log that says why.

Moving the warm-up to a background thread is only safe because of the second half. `_ensure_embedder`
used to set `_tried_load = True` on entry, so a real utterance arriving mid-load would see "already
tried", get None back, and `verify()` fails OPEN by design (a missing backend must never lock the
owner out). The gate would therefore have been silently off for the first minute after every restart
— strictly worse than the delay it was meant to fix. The load is now serialised: a caller arriving
mid-load waits and then gets the real embedder.

    uv run python bench/test_speaker_warmup.py
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

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
    from afon.edge.speaker_id import SpeakerVerifier

    print("[1] a caller arriving mid-load WAITS for the backend, it does not get None")
    # The whole point: None means "no backend", and no backend means verify() accepts everyone.
    calls = {"n": 0}
    ready = threading.Event()

    class _SlowVerifier(SpeakerVerifier):
        def _load_embedder(self):          # stands in for the real ~56s ECAPA load
            calls["n"] += 1
            self._tried_load = True
            ready.set()
            time.sleep(0.4)
            self._embedder = lambda wav: np.ones(192, dtype=np.float32)
            return self._embedder

    v = _SlowVerifier(embedder=None)
    v._tried_load = False
    got: list = []
    warm = threading.Thread(target=v._ensure_embedder, daemon=True)
    warm.start()
    ready.wait(2.0)                        # the load is now in flight
    t = time.perf_counter()
    got.append(v._ensure_embedder())       # the "utterance arrives mid-load" caller
    waited = time.perf_counter() - t
    warm.join(3.0)
    check(got[0] is not None, "the mid-load caller receives a real embedder",
          "it got None — verify() would fail OPEN and the gate is off during warm-up")
    check(waited > 0.1, f"...because it waited for the load ({waited * 1000:.0f}ms)",
          "it returned instantly, which means it did not wait for anything")
    check(calls["n"] == 1, "the model is loaded exactly once, not once per caller", str(calls["n"]))

    print("\n[2] a backend that genuinely fails is still only tried once")
    # Retrying a broken import on every utterance would put the import cost on every turn.
    tries = {"n": 0}

    class _BrokenVerifier(SpeakerVerifier):
        def _load_embedder(self):
            tries["n"] += 1
            self._tried_load = True
            return None

    b = _BrokenVerifier(embedder=None)
    b._tried_load = False
    for _ in range(4):
        b._ensure_embedder()
    check(tries["n"] == 1, "one attempt, then it stays disabled", f"{tries['n']} attempts")

    print("\n[3] an injected embedder is never overridden by a load")
    inj = SpeakerVerifier(embedder=lambda wav: np.zeros(192, dtype=np.float32))
    check(inj._ensure_embedder() is not None and inj._tried_load is True,
          "a supplied embedder short-circuits the loader (tests stay hermetic)")

    print("\n[4] the edge does not block on the warm-up")
    src = (ROOT / "src" / "afon" / "edge" / "assistant.py").read_text(encoding="utf-8")
    seg = src[src.index("Phase 5 — speaker biometrics"):]
    seg = seg[:seg.index("stages.append(gate)")]
    check("Thread(" in seg and "_ensure_embedder" in seg,
          "the pipeline builder warms ECAPA on a thread",
          "a synchronous warm-up means ~56s of deafness after every restart")
    check("daemon=True" in seg,
          "...as a daemon, so a half-loaded model cannot hold the process open on shutdown")

    print("\n[5] the gate still fails OPEN when there is genuinely no backend")
    # Not a regression to fix — it is the deliberate contract. A missing backend must never lock
    # the owner out of his own assistant; only a LOADED backend may reject him.
    from afon.config import settings

    saved = settings.speaker_id_enabled
    try:
        settings.speaker_id_enabled = True
        nb = _BrokenVerifier(embedder=None)
        nb._tried_load = False
        nb._profile = np.ones((1, 192), dtype=np.float32)
        accept, score = nb.verify(b"\x01\x02" * 16000)
        check(accept is True and score == 1.0,
              "no backend -> accept (the owner is never locked out by an install problem)",
              f"{accept} {score}")
    finally:
        settings.speaker_id_enabled = saved

    print("\n[6] the voiceprint lives in state, not in the source tree")
    # It is biometric data. It was sitting at the repo root, kept out of git by a single
    # `voiceprint.json*` ignore line, and shipped nowhere useful — it is state, so a fresh clone or
    # a deploy had no business seeing it. Moved to ~/.afon/ with the face refs, with a one-time
    # migration so an existing enrolment is not silently abandoned (which reads as "Afon went deaf").
    import afon.edge.speaker_id as SI
    from afon.config import settings as cfg

    check(str(SI._default_profile_path()).replace("\\", "/").endswith(".afon/voiceprint.json")
          or bool(cfg.speaker_profile_path),
          "the default path is under ~/.afon/, not the repo",
          str(SI._default_profile_path()))
    check(not (ROOT / "voiceprint.json").exists(),
          "no voiceprint is left at the repo root", "the migration did not run")

    import tempfile

    saved_legacy, saved_cfg = SI._LEGACY_PROFILE, cfg.speaker_profile_path
    d = Path(tempfile.mkdtemp())
    try:
        cfg.speaker_profile_path = None
        SI._LEGACY_PROFILE = d / "old" / "voiceprint.json"
        SI._LEGACY_PROFILE.parent.mkdir(parents=True)
        SI._LEGACY_PROFILE.write_text('{"embeddings": [[1.0, 0.0]]}', encoding="utf-8")
        SI._LEGACY_PROFILE.with_suffix(".json.bak").write_text('{"embeddings": [[0.0, 1.0]]}',
                                                               encoding="utf-8")
        new = d / "new" / "voiceprint.json"
        SI._migrate_legacy_profile(new)
        check(new.exists() and not SI._LEGACY_PROFILE.exists(),
              "a legacy profile is MOVED, not copied and left behind",
              "two copies of a biometric is worse than one in the wrong place")
        check(new.with_suffix(".json.bak").exists(),
              "...and its .bak comes too (it is the only recovery path from a bad re-enrol)")

        # And it must never clobber: re-enrolling is what created the new one.
        SI._LEGACY_PROFILE.write_text('{"embeddings": [[9.0, 9.0]]}', encoding="utf-8")
        SI._migrate_legacy_profile(new)
        check('9.0' not in new.read_text(encoding="utf-8"),
              "an existing new-location profile is never overwritten by an old one")
    finally:
        SI._LEGACY_PROFILE, cfg.speaker_profile_path = saved_legacy, saved_cfg

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
