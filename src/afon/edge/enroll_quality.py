"""Is this recording good enough to enrol on? — asked at capture time (SYSTEMS.md 08.F2).

The owner's voiceprint scores **0.47** against a 0.60 target, and every identity decision downstream
inherits that number. Nobody chose it: enrolment recorded whatever the mic produced, embedded it,
and reported "captured ✓". A clip that was four seconds of room hum, or a segment read while an
extractor fan ran, went into the profile exactly like a good one — and the cost showed up weeks
later as a threshold that could not be placed (`separation_verdict`, the other half of this file's
gate) rather than as a message during the five minutes the owner was sitting at the mic.

So this module answers, per clip, *before* it joins the profile: is it long enough, loud enough,
clean enough, and does it actually contain speech rather than a steady tone?

Four measurements, and the fourth is the one that catches the case the others miss:

  * **duration** — an embedding from a very short clip is dominated by whatever the first syllable
    happened to be.
  * **level** — too quiet means the mic gain or the distance was wrong; clipping means it was too
    hot, and a clipped waveform is distorted in exactly the band ECAPA reads.
  * **SNR** — loud frames against the noise floor. This is what a fan, a fridge or a街 of traffic
    costs.
  * **variety** — frame-to-frame spectral change. A pure tone, a hum, or a stretch of silence can
    all be loud AND clean AND long, and contain no speech at all. Without this the other three pass
    a recording of a dial tone.

**The thresholds are defaults, not measurements, and that is stated rather than implied.** There is
no enrolment corpus in this repo (08.R1 is where the per-condition corpus lives), so these come from
the physics — 16 kHz int16, 25 ms frames — and from what the numbers do on synthesised signals in
`bench/test_enroll_script_parse.py`. They are deliberately loose: a false REJECT costs the owner
thirty seconds of re-reading, while a false ACCEPT costs weeks of a weak profile, so the bar sits
where obvious garbage fails and anything arguable passes with a warning.

    from afon.edge.enroll_quality import score_pcm
    q = score_pcm(pcm_bytes, 16000)
    if q.reject: print(q.reason)      # say it now, not after the profile is written
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Below this a clip is too short for a stable embedding, in seconds.
MIN_SECONDS = 2.5
#: Speech frames quieter than this are either a distant mic or a gain problem. dBFS.
MIN_LEVEL_DBFS = -40.0
#: More than this fraction of samples at full scale is clipping, which distorts the spectrum.
MAX_CLIPPING = 0.005
#: Speech-to-noise-floor ratio, dB. A fan or a fridge lands a clip in the teens.
MIN_SNR_DB = 12.0
#: Mean frame-to-frame spectral change. A hum or a tone sits near zero; speech is an order up.
MIN_VARIETY = 0.05

_FRAME = 400        # 25 ms at 16 kHz
_HOP = 160          # 10 ms
_FULL_SCALE = 32768.0


@dataclass
class Quality:
    """One clip, measured. `reject` is the verdict; `reason` is what to say to the owner."""

    seconds: float
    level_dbfs: float
    noise_dbfs: float
    snr_db: float
    variety: float
    clipping: float
    reason: str = ""

    @property
    def reject(self) -> bool:
        return bool(self.reason)

    def summary(self) -> str:
        return (f"{self.seconds:.1f}s, speech {self.level_dbfs:.0f} dBFS, noise "
                f"{self.noise_dbfs:.0f} dBFS, SNR {self.snr_db:.0f} dB, variety {self.variety:.2f}")


def _dbfs(rms: float) -> float:
    return -120.0 if rms <= 0 else float(20.0 * np.log10(min(1.0, rms / _FULL_SCALE)))


def score_pcm(pcm: bytes, sample_rate: int = 16000) -> Quality:
    """Measure one enrolment clip of 16-bit mono PCM."""
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    seconds = len(samples) / float(sample_rate or 1)
    if len(samples) < _FRAME:
        return Quality(seconds, -120.0, -120.0, 0.0, 0.0, 0.0,
                       reason=f"only {seconds:.1f}s of audio — hold the key and speak for at least "
                              f"{MIN_SECONDS:.0f} seconds.")

    # Frame energies. The noise floor is the 10th percentile and speech the 90th: continuous reading
    # still dips between syllables, so this separates them without needing real pauses.
    # ponytail: percentiles, not a VAD. A VAD is the right tool if this ever has to work on a clip
    # that is 90% speech or 90% silence; for a read-aloud enrolment segment it would be two more
    # dependencies to reach the same two numbers.
    n_frames = 1 + (len(samples) - _FRAME) // _HOP
    frames = np.lib.stride_tricks.sliding_window_view(samples, _FRAME)[::_HOP][:n_frames]
    energies = np.sqrt(np.mean(frames ** 2, axis=1))
    noise_rms = float(np.percentile(energies, 10))
    speech_rms = float(np.percentile(energies, 90))
    level, noise = _dbfs(speech_rms), _dbfs(noise_rms)
    snr = max(0.0, level - noise)
    clipping = float(np.mean(np.abs(samples) >= _FULL_SCALE - 2))

    # Spectral variety: how much the spectrum MOVES between frames. Normalised per frame so it
    # measures change in shape rather than in loudness — otherwise a loud tone outscores quiet speech.
    # Frames at or above the median energy. An earlier version used `noise_rms * 2`, which on a clip
    # of pure noise selected NOTHING — every frame has the same energy — and then reported variety
    # 0.0, i.e. "no speech detected", for a recording that is nothing but speech-band noise. A
    # measurement that silently returns zero when it could not measure is worse than no measurement:
    # it diagnoses the wrong problem confidently. The median always selects half the frames.
    loud = frames[energies >= np.median(energies)]
    if len(loud) >= 2:
        spec = np.abs(np.fft.rfft(loud * np.hanning(_FRAME), axis=1))
        spec /= (spec.sum(axis=1, keepdims=True) + 1e-12)
        variety = float(np.mean(np.abs(np.diff(spec, axis=0)).sum(axis=1)))
    else:
        variety = 0.0

    q = Quality(seconds, level, noise, snr, variety, clipping)
    # Order matters: report the FIRST thing that would make a re-record worthwhile, and report the
    # cause rather than the symptom. A clipped clip also looks like a loud one; a hum also looks
    # like a clean one.
    if seconds < MIN_SECONDS:
        q.reason = (f"only {seconds:.1f}s — needs at least {MIN_SECONDS:.0f}s. Read the whole "
                    f"segment before it stops recording.")
    elif clipping > MAX_CLIPPING:
        q.reason = (f"clipping on {clipping * 100:.1f}% of samples — too loud or too close. Back off "
                    f"a hand's width from the mic, or turn the input gain down.")
    elif variety < MIN_VARIETY:
        q.reason = (f"no speech detected (variety {q.variety:.3f} < {MIN_VARIETY}) — this is a steady "
                    f"tone, hum or silence. Check the right microphone is selected.")
    elif level < MIN_LEVEL_DBFS:
        q.reason = (f"too quiet ({level:.0f} dBFS) — move closer to the mic or raise the input gain.")
    elif snr < MIN_SNR_DB:
        q.reason = (f"too noisy (SNR {snr:.0f} dB, floor {noise:.0f} dBFS) — turn off the fan or "
                    f"fridge, close the window, and read a little louder.")
    return q


def _synth(kind: str, seconds: float = 5.0, sr: int = 16000) -> bytes:
    """Signals with known properties, for the selfcheck and the gate. Not a corpus — a ruler."""
    t = np.arange(int(sr * seconds)) / sr
    rng = np.random.default_rng(7)
    if kind == "tone":                                  # loud, clean, long, no speech
        x = 0.3 * np.sin(2 * np.pi * 220 * t)
    elif kind == "silence":
        x = np.zeros_like(t)
    elif kind == "noise":                               # broadband, no structure, no quiet frames
        x = 0.05 * rng.standard_normal(t.size)
    elif kind == "clipped":
        x = np.clip(3.0 * np.sin(2 * np.pi * 180 * t) + 0.5 * rng.standard_normal(t.size), -1, 1)
    elif kind == "quiet_speech":
        x = 0.002 * _speechlike(t, rng)
    elif kind == "noisy_speech":
        x = 0.25 * _speechlike(t, rng) + 0.06 * rng.standard_normal(t.size)
    else:                                               # "speech" — the case that must PASS
        x = 0.25 * _speechlike(t, rng)
    return (np.clip(x, -1, 1) * (_FULL_SCALE - 1)).astype(np.int16).tobytes()


def _speechlike(t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """A crude voiced signal: a wandering pitch with formant-ish noise, gated into syllables.

    Not speech, and not trying to be — it just has to have the two properties real speech has and a
    tone does not: a spectrum that moves, and an envelope with gaps.
    """
    pitch = 110 + 30 * np.sin(2 * np.pi * 0.7 * t)
    voiced = np.sin(2 * np.pi * np.cumsum(pitch) / 16000)
    formant = np.sin(2 * np.pi * (700 + 400 * np.sin(2 * np.pi * 1.3 * t)) * t)
    syllables = (np.sin(2 * np.pi * 4.0 * t) > -0.3).astype(float)
    return (voiced + 0.5 * formant + 0.05 * rng.standard_normal(t.size)) * syllables


def _selfcheck() -> None:
    """python -m afon.edge.enroll_quality"""
    good = score_pcm(_synth("speech"))
    assert not good.reject, f"a usable clip must pass: {good.reason} [{good.summary()}]"
    for kind, expect in (("silence", "no speech"), ("tone", "no speech"), ("noise", "noisy"),
                         ("clipped", "clipping"), ("quiet_speech", "quiet"),
                         ("noisy_speech", "noisy")):
        q = score_pcm(_synth(kind))
        assert q.reject and expect in q.reason, f"{kind}: {q.reason!r} [{q.summary()}]"
    short = score_pcm(_synth("speech", seconds=1.0))
    assert short.reject and "at least" in short.reason, short.reason
    print(f"enroll_quality selfcheck ok — good clip: {good.summary()}")


if __name__ == "__main__":
    _selfcheck()
