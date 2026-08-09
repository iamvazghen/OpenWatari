"""Phase 0 — "hello voice".

The smallest end-to-end proof of the voice loop:

    mic -> Deepgram STT -> EchoBrain (stand-in) -> ElevenLabs TTS -> speaker

It speaks back what you said in the chosen Afon voice, proving the streaming
audio path, the STT, and the ElevenLabs voice all work before we add wake word,
the real brain, and barge-in. Run:

    uv run python -m afon.edge.hello_voice
"""

from __future__ import annotations

# Printed BEFORE the heavy pipecat/numpy/onnxruntime import below, so a slow cold
# start on Windows (antivirus scanning) doesn't look like a freeze.
print("Afon: loading audio stack (first start can take ~15-30s)…", flush=True)

import asyncio  # noqa: E402

from loguru import logger  # noqa: E402
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.worker import PipelineWorker  # noqa: E402
from pipecat.services.deepgram.stt import DeepgramSTTService  # noqa: E402
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService  # noqa: E402
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams  # noqa: E402
from pipecat.workers.runner import WorkerRunner  # noqa: E402

from afon.config import settings  # noqa: E402
from afon.edge.audio_gate import HalfDuplexGate  # noqa: E402
from afon.edge.echo_brain import EchoBrain  # noqa: E402


def build_worker() -> PipelineWorker:
    """Assemble the Phase-0 pipeline. Construction only — no audio I/O until run."""
    if not settings.deepgram_api_key:
        raise RuntimeError("AFON_DEEPGRAM_API_KEY is not set (.env)")
    if not (settings.elevenlabs_api_key and settings.elevenlabs_voice_id):
        raise RuntimeError("ElevenLabs api key / voice id not set (.env)")

    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        )
    )

    stt = DeepgramSTTService(api_key=settings.deepgram_api_key)

    tts = ElevenLabsTTSService(
        api_key=settings.elevenlabs_api_key,
        settings=ElevenLabsTTSService.Settings(
            voice=settings.elevenlabs_voice_id,
            model=settings.elevenlabs_model,
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),   # mic
            HalfDuplexGate(),    # drop mic audio while Afon speaks (no self-hearing)
            stt,                 # speech -> text
            EchoBrain(),         # stand-in brain
            tts,                 # text -> Afon's voice
            transport.output(),  # speaker
        ]
    )
    return PipelineWorker(pipeline)


async def main() -> None:
    logger.info(
        f"Afon hello-voice | STT={settings.stt_provider.value} "
        f"TTS={settings.tts_provider.value} voice={settings.elevenlabs_voice_id}"
    )
    logger.info("Speak into the mic — Afon will echo you. Ctrl-C to stop.")
    runner = WorkerRunner()
    await runner.add_workers(build_worker())
    await runner.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("stopped")
