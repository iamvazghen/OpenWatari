"""Verify a trained custom openWakeWord model in the EDGE venv.

Loads a .onnx wake model and scores it on WAV clips (e.g. the model's own positive
test clips + a negative sample), reporting max score per clip so we can confirm the
model actually fires on its phrase and stays quiet otherwise.

    uv run python bench/verify_wakeword.py .wakewords/afon.onnx clip1.wav clip2.wav ...
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from openwakeword.model import Model


def score(model: Model, wav_path: str) -> float:
    data, sr = sf.read(wav_path, dtype="int16", always_2d=True)
    samples = data[:, 0]  # first channel
    if sr != 16000:  # openWakeWord expects 16k
        import soxr

        samples = soxr.resample(samples.astype(np.float32), sr, 16000).astype(np.int16)
    key = next(iter(model.models.keys()))
    best = 0.0
    model.reset()
    for i in range(0, max(1, len(samples) - 1280), 1280):
        best = max(best, model.predict(samples[i : i + 1280]).get(key, 0.0))
    return best


def main() -> None:
    onnx, *clips = sys.argv[1:]
    m = Model(wakeword_models=[onnx], inference_framework="onnx")
    print(f"loaded {Path(onnx).name} -> classes {list(m.models.keys())}")
    for c in clips:
        print(f"  {score(m, c):.3f}  {Path(c).name}")


if __name__ == "__main__":
    main()
