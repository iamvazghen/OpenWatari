"""Phase 2 — wake-word assistant with Afon's real brain.

    mic -> WakeWordGate("Hey Afon") -> HalfDuplexGate -> Deepgram STT
        -> AfonBrain (own LLM + personality + memory + tools) -> ElevenLabs TTS -> speaker

It responds only after a wake word, ignores ambient speech / its own playback, reasons as
Afon using his memory, and can consult the OpenClaw fleet as a tool (once authorized).
Run:

    uv run python -m afon.edge.assistant
"""

from __future__ import annotations

# MUST run before pipecat/huggingface_hub import: when STT=moonshine, force HF Hub offline so its
# cached-model revision check can't make the network call that HANGS the windowless edge at startup.
# huggingface_hub freezes the offline flag at import time, so setting it later (in the builder) is
# too late. Scoped to moonshine so whisper's first-run model download still works.
import asyncio  # noqa: E402
import os  # noqa: E402

from afon.config import settings as _settings  # noqa: E402 — cheap, no HF import

if _settings.stt_provider.value == "moonshine":
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

print("Afon: loading audio stack (first start can take ~15-30s)…", flush=True)

from loguru import logger  # noqa: E402
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.worker import PipelineWorker  # noqa: E402
from afon.edge.tts import build_tts  # noqa: E402
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams  # noqa: E402
from pipecat.workers.runner import WorkerRunner  # noqa: E402

from afon.config import TTSProvider, settings  # noqa: E402
from afon.edge.audio_devices import resolve_output_index  # noqa: E402
from afon.edge.audio_gate import HalfDuplexGate  # noqa: E402
from afon.edge.brain_bridge import AfonBrain  # noqa: E402
from afon.edge.device_profile import resolve_barge_in  # noqa: E402
from afon.edge.stt import build_stt  # noqa: E402
from afon.edge.tts_leadin import TTSLeadInSilence  # noqa: E402
from afon.edge.vad_bargein import BargeInProcessor, build_vad_processor  # noqa: E402
from afon.edge.wake_word import WakeWordGate, resolve_openwakeword_models  # noqa: E402


def build_worker(brain: AfonBrain | None = None) -> PipelineWorker:
    """Assemble the pipeline. Construction loads the wake-word model + Afon's brain."""
    # Resolve which speaker/headphone Afon plays through (config or saved voice pref).
    out_index, out_label = resolve_output_index(
        settings.audio_output_device, auto_route_headphones=settings.auto_route_headphones
    )
    logger.info(f"audio output -> {out_label} (index {out_index})")

    # Phase 1.2 — acoustic echo cancellation. If a real echo-canceller is configured + installed, it
    # removes Afon's own TTS from the mic, which makes barge-in on OPEN speakers safe (full-duplex).
    from afon.edge.aec import aec_active as _aec_active, build_input_filter
    aec_filter = build_input_filter(settings.aec_filter)
    aec_on = _aec_active(aec_filter)

    # Smart barge-in: decide full vs half duplex from the LIVE endpoint (headphones/glasses/
    # phone-earbuds → barge-in auto-ON; open speakers → OFF unless an echo-canceller is running), not
    # a static flag.
    barge_in, out_kind, why = resolve_barge_in(
        settings.barge_in_mode,
        output_name=out_label,
        device_hint=settings.device_hint,
        legacy_enabled=settings.barge_in_enabled,
        aec_active=aec_on,
    )
    logger.info(f"barge-in: {'ON' if barge_in else 'OFF'} — {why} [{out_kind.value}]")
    params = LocalAudioTransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        audio_in_sample_rate=16000,   # 16k for openWakeWord + Deepgram
        # MUST be set: left None, pipecat opens the output at the device's native rate but writes
        # Piper's 22.05k samples without resampling -> shredded playback. Match the device's rate.
        audio_out_sample_rate=settings.audio_out_sample_rate,
        audio_in_filter=aec_filter,   # echo-canceller on the mic (None = unchanged half-duplex path)
    )
    if settings.audio_output_device_index is not None:
        params.output_device_index = settings.audio_output_device_index
        logger.info(f"speaker: pinned output device index {settings.audio_output_device_index}")
    elif out_index is not None:
        params.output_device_index = out_index
    in_dev = None
    # Smart per-device mic (owner's choice): a RELIABLE wired/USB headset mic wins when plugged in, so he
    # can walk away from the laptop and still be heard; AirPods/BT + the built-in array fall through to the
    # pinned name below (AirPods listen on the built-in mic on purpose — HFP is unreliable). Output still
    # auto-routes to the headphones regardless.
    if settings.auto_route_headset_mic:
        from afon.edge.audio_devices import find_headset_input
        hs = find_headset_input()
        if hs is not None:
            in_dev = hs
            logger.info(f"mic: routed to headset '{hs.name}' (index {hs.index}) — reliable wired/USB mic")
    if in_dev is None and settings.audio_input_device_name:
        from afon.edge.audio_devices import find_input_device
        in_dev = find_input_device(settings.audio_input_device_name)
        if in_dev is None:
            logger.warning(
                f"mic: no input matches name '{settings.audio_input_device_name}' — falling back"
            )
        else:
            logger.info(f"mic: pinned by name '{settings.audio_input_device_name}' -> [{in_dev.index}] {in_dev.name}")
    if in_dev is not None:
        params.input_device_index = in_dev.index
    elif settings.audio_input_device_index is not None:
        params.input_device_index = settings.audio_input_device_index
        logger.info(f"mic: pinned input device index {settings.audio_input_device_index}")
    transport = LocalAudioTransport(params)
    stt = build_stt()  # Deepgram-multi (EN/FR/DE/RU) or Whisper (all six incl. Armenian)
    tts = build_tts()  # ElevenLabs (cloud) | Piper | Kokoro (both fully local) per AFON_TTS_PROVIDER

    # Liveness probe FIRST (before any gate that mutes the mic) so it sees the raw stream — the
    # watchdog uses it to detect a dead mic after a device change and trigger a fresh restart.
    from afon.edge.audio_watchdog import AudioLivenessProbe
    liveness_probe = AudioLivenessProbe()
    stages: list = [transport.input(), liveness_probe]
    wake_gate = None  # set below if wake-word gating is enabled; drives the idle listening pulse

    # Boost a quiet mic BEFORE anything analyses it (VAD/wake/STT), so a far-field/low-gain array
    # mic still triggers the wake word. No-op at gain 1.0.
    if settings.mic_gain != 1.0:
        from afon.edge.audio_gate import MicGain
        stages.append(MicGain(settings.mic_gain))
        logger.info(f"mic gain: x{settings.mic_gain}")

    # Silero VAD first, so every later stage sees speech-start/stop frames (used for
    # barge-in now, endpointing later). It runs on raw mic audio regardless of wake state.
    if settings.vad_enabled:
        stages.append(build_vad_processor())

    if settings.wake_word_enabled:
        models, pending = resolve_openwakeword_models(settings.wake_words_list)
        if pending:
            logger.warning(
                f"wake words pending custom engine (Porcupine/training): {pending}"
            )
        if models:
            wake_gate = WakeWordGate(
                models=models,
                threshold=settings.wake_word_threshold,
                listen_window_s=settings.wake_listen_window_s,
                suppress_during_tts=not barge_in,
                ack_phrase=settings.wake_ack_phrase,
                # On shared speakers (half-duplex) the VAD can't barge in — Afon's own voice
                # would trip it. The wake WORD can: saying it over TTS interrupts him.
                barge_in=(not barge_in) and settings.wake_barge_in_enabled,
                hot_mic_after_wake=settings.hot_mic_after_wake,
                hot_mic_idle_minutes=settings.hot_mic_idle_minutes,
            )
            stages.append(wake_gate)
        else:
            logger.warning("no loadable wake words — mic ungated (open)")

    # Duplex follows the smart decision above: full (private endpoint) opens the mic during TTS
    # + enables barge-in; half (shared speakers) mutes the mic while Afon speaks.
    if barge_in:
        if not settings.vad_enabled:
            logger.warning("barge-in wanted but vad_enabled=False — falling back to half-duplex")
            stages.append(HalfDuplexGate())
        else:
            logger.info("duplex=full — barge-in ON (mic open during TTS)")
            stages.append(BargeInProcessor())
    else:
        stages.append(HalfDuplexGate())
        logger.info("duplex=half — mic muted while Afon speaks (speakers-safe, no barge-in)")

    stages.append(stt)

    # Phase 5 — speaker biometrics: after STT, drop transcripts that aren't the owner's voice.
    # No-op (graceful) until a voiceprint is enrolled and AFON_SPEAKER_ID_ENABLED=true.
    if settings.speaker_id_enabled:
        from afon.edge.speaker_gate import SpeakerGate

        gate = SpeakerGate()
        if gate._verifier.has_profile:
            # Warm the ECAPA embedder now: it otherwise cold-loads on the FIRST utterance (~35s),
            # which the owner experiences as a long lag before the first reply. One-time cost at boot.
            gate._verifier._ensure_embedder()
            logger.info("speaker-id: ON — only the owner's enrolled voice will be obeyed")
        else:
            logger.warning("speaker-id enabled but no voiceprint — run bench/enroll_voice.py "
                           "(gate is a no-op until enrolled)")
        stages.append(gate)

    # C3 — mood-adaptive voice (ElevenLabs only): infer affect from each owner utterance and nudge the
    # TTS prosody before the reply. Pushes a settings frame downstream to the TTS; no-op on local voices.
    if settings.tts_affect_enabled and settings.tts_provider == TTSProvider.elevenlabs:
        from afon.edge.affect_tts import AffectTTS

        stages.append(AffectTTS())
        logger.info("affect-tts: ON — Afon's voice adapts to the owner's mood")

    # #24 — edge reflexes: answer truly-local turns (time/date) here, before the brain, with no VPS hop.
    # Placed after speaker-gate + affect so a reflex is still owner-verified; a non-reflex passes through.
    if settings.edge_reflexes_enabled:
        from afon.edge.reflexes import ReflexGate
        stages.append(ReflexGate())
        logger.info("edge reflexes: ON — time/date answered locally (no brain round-trip)")

    # #23 — per-turn latency tracer (user-stop→STT→brain→TTS→first word); pure pass-through, logged.
    from afon.edge.latency_meter import TTFWMeter

    stages += [brain or AfonBrain(), tts, TTSLeadInSilence(), TTFWMeter(), transport.output()]

    # An always-on wake-word assistant SITS idle waiting for "hey afon" — that is its normal state.
    # Pipecat's default (idle_timeout_secs=300) would otherwise cancel the whole pipeline after 5 min
    # of silence, forcing a ~90s reboot during which the edge is deaf. idle_timeout_secs=None turns the
    # idle machinery off entirely (no detection, no cancel); the cancel flags are belt-and-suspenders.
    worker = PipelineWorker(
        Pipeline(stages),
        idle_timeout_secs=None,
        cancel_on_idle_timeout=False,
        cancel_runner_on_idle_timeout=False,
    )
    worker._wake_gate = wake_gate  # main() reads this to drive the idle listening pulse
    worker._liveness_probe = liveness_probe   # audio watchdog reads this to detect a dead mic
    worker._out_device_name = out_label       # ...and this to detect the output device vanishing
    worker._out_device_index = params.output_device_index  # listening pulse plays through this device
    worker._out_is_private = out_kind.is_private  # on AirPods now? watchdog uses it to follow connect
    return worker


async def build_brain():
    """Pick the brain: LOCAL in-process agent (default, fastest) or REMOTE thin-client to the 24/7
    VPS brain (one shared Afon + memory). 'auto'/'remote' fall back to local if the VPS is down."""
    mode = (settings.brain_mode or "local").lower()
    if mode in ("remote", "auto"):
        from afon.edge.remote_brain import RemoteBrain

        rb = RemoteBrain(headphones_connected=False)
        if await rb.start():
            logger.info(f"brain: REMOTE — unified VPS brain at {settings.brain_ws_url}")
            return rb
        if mode == "remote":
            # Don't demote for the whole session on one slow handshake. A cold edge start contends
            # with model loading, so the first connect regularly misses the 3s gate even though the
            # link is healthy a second later — and stopping the client here pinned the edge to the
            # LOCAL brain (weaker chain, separate memory) until the next restart. RemoteBrain already
            # routes PER TURN and answers on a warm local standby while the link is down, so keeping
            # the supervised client alive promotes back to the VPS the moment it connects.
            logger.warning("brain: VPS brain not up yet — keeping the supervised REMOTE link and "
                           "answering on the local standby until it connects")
            return rb
        await rb.stop()
    brain = AfonBrain()
    await brain.warmup()  # prime the LLM so the first reply isn't a cold ~3s TTFT
    logger.info("brain: LOCAL — in-process agent")
    return brain


async def main() -> None:
    logger.info(
        f"Afon assistant | wake={settings.wake_words_list} "
        f"vad={settings.vad_enabled} duplex={settings.duplex_mode} "
        f"STT={settings.stt_provider.value} TTS={settings.tts_provider.value}"
    )
    logger.info('Say "Hey Afon", then your command. Ambient speech is ignored. Ctrl-C to stop.')
    brain = await build_brain()
    runner = WorkerRunner()
    worker = build_worker(brain)
    await runner.add_workers(worker)

    # Soft 'listening' ping: ONE gentle ping shortly after start so you KNOW the edge is up and
    # listening before you say the first wake word. It sounds exactly once, then stays silent — the
    # wake acknowledgement ("I'm listening, sir.") covers every later turn.
    pulse = None
    if settings.wake_word_enabled and settings.listening_pulse:
        from afon.edge.listening_pulse import ListeningPulse

        pulse = ListeningPulse(delay_s=settings.listening_pulse_period_s,
                               output_device_index=getattr(worker, "_out_device_index", None))
        pulse.start()

    # Audio watchdog: if the mic stream dies or the output device vanishes (a device change staled
    # the streams), trip `dead` → return so run_supervised rebuilds the edge with fresh streams.
    from afon.edge.audio_watchdog import watch_audio_liveness

    dead = asyncio.Event()
    from afon.config import STTProvider, TTSProvider
    cloud_desired = (settings.stt_provider == STTProvider.deepgram
                     or settings.tts_provider == TTSProvider.elevenlabs)
    watch = asyncio.create_task(watch_audio_liveness(
        worker._liveness_probe, dead, getattr(worker, "_out_device_name", None),
        cloud_desired=cloud_desired,
        on_private=getattr(worker, "_out_is_private", False),
        auto_route=settings.auto_route_headphones))
    run_task = asyncio.create_task(runner.run())
    try:
        await asyncio.wait({run_task, asyncio.create_task(dead.wait())},
                           return_when=asyncio.FIRST_COMPLETED)
        if dead.is_set():
            logger.warning("edge: audio watchdog tripped — restarting to recover live audio streams")
    finally:
        watch.cancel()
        run_task.cancel()
        # Bound the teardown. An unbounded `await run_task` is how a 68s mic stall became 21 MINUTES of
        # deafness on 2026-07-30: the watchdog tripped at 22:32:45, pipecat's runner never finished
        # unwinding (a dead PyAudio stream / half-closed cloud socket doesn't always end its await),
        # and the supervisor only got control back at 22:53:57. Recovering with streams that haven't
        # fully released is recoverable — the watchdog simply trips again in 60s — but staying deaf is
        # not, so stop waiting and let the rebuild proceed.
        try:
            await asyncio.wait_for(run_task, timeout=15.0)
        except asyncio.TimeoutError:
            logger.error("edge: pipeline teardown did not finish in 15s — rebuilding anyway rather "
                         "than staying deaf (stale streams get re-resolved on the way up)")
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 — teardown, releases the devices
            pass
        if pulse:
            pulse.stop()


if __name__ == "__main__":
    # Supervised: relaunch on any exit (crash or clean pipeline end) + log to logs/edge.log, so a
    # brain restart or a transient audio glitch never leaves the laptop silently without Afon.
    from afon.edge._supervisor import run_supervised

    run_supervised("edge", main)
