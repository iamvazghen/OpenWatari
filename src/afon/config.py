"""Central configuration for Afon (edge + brain).

All secrets and provider choices come from environment / a local `.env` file.
Nothing here is hard-coded so the same codebase runs CPU-local-only or cloud-quality
just by flipping provider flags. See `.env.example` for the full surface.
"""

from __future__ import annotations

from enum import Enum

from pydantic_settings import BaseSettings, SettingsConfigDict


class STTProvider(str, Enum):
    deepgram = "deepgram"      # cloud, streaming, best accuracy (default for "understands me well")
    whisper = "whisper"        # local faster-whisper int8 (offline fallback)
    moonshine = "moonshine"    # local, CPU-fast (lowest-latency offline)


class TTSProvider(str, Enum):
    elevenlabs = "elevenlabs"  # cloud, streaming — the chosen "Afon voice" (default)
    piper = "piper"            # local, fastest on CPU (offline fallback)
    kokoro = "kokoro"          # local, higher quality than Piper, still CPU-runnable


class LLMBackend(str, Enum):
    """The model that powers Afon's OWN brain / reasoning.

    OpenClaw is deliberately NOT in this enum: it is an external team Afon can
    consult, not the engine of his mind. Afon always thinks and speaks as himself.
    """

    freellmapi = "freellmapi"  # default — free OpenAI-compatible proxy (on the VPS)
    ollama = "ollama"          # fully-offline local model
    openai = "openai"          # any OpenAI-compatible endpoint


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_prefix="AFON_"
    )

    # --- Identity (make the SAME code YOUR assistant — the framework personalization layer) ----
    # The persona file (personality/<persona_file>) is a TEMPLATE: build_system_prompt() fills these
    # in, so a new user personalises their assistant entirely from .env / the setup wizard, without
    # editing any prompt or code. Defaults are intentionally generic for the framework.
    assistant_name: str = "Afon"        # what the assistant calls itself
    user_name: str = ""                   # your name (blank = he won't use a name)
    # How he addresses you: an honorific ("sir", "ma'am", "boss"), your name, or "" for none.
    user_address: str = ""
    understood_languages: str = "English"  # comma-list of languages he can UNDERSTAND (STT side)
    reply_language: str = "English"        # the single language he always REPLIES in
    persona_file: str = "afon.md"        # which file in personality/ holds the persona template
    # The owner's local timezone (IANA name, e.g. "America/New_York", "Asia/Tokyo"). Drives
    # get_time, the proactive/scheduler clocks, and calendar event creation. Generic default = UTC;
    # the personal instance sets AFON_USER_TZ in .env.
    user_tz: str = "UTC"

    # --- Provider selection (the knobs that define a deployment) -------------------------
    # Cloud is the PRIMARY for quality/latency; if it can't be built (missing key, engine not
    # installed, construction error) we automatically fall back to the LOCAL engine so the voice
    # pipeline always comes up (see edge/stt.py + edge/tts.py, voice_local_fallback below).
    #   * STT: Deepgram (cloud) -> Whisper (local). Deepgram can't do Armenian/Ukrainian — if you
    #     speak those TO Afon, set AFON_STT_PROVIDER=whisper (local auto-detects all six).
    #   * TTS: ElevenLabs (cloud) -> Piper (local).
    stt_provider: STTProvider = STTProvider.deepgram
    tts_provider: TTSProvider = TTSProvider.elevenlabs
    # When a CLOUD provider above can't be constructed, fall back to its local counterpart instead of
    # failing the whole pipeline. The local fallback engines need the `local-voice` extra installed.
    voice_local_fallback: bool = True
    tts_fallback_provider: TTSProvider = TTSProvider.piper
    # C3: adapt the ElevenLabs voice (stability/style/speed) to the owner's inferred mood on the live
    # path — steadier when stressed, livelier when upbeat. Off = one fixed voice. ElevenLabs only.
    tts_affect_enabled: bool = True
    stt_fallback_provider: STTProvider = STTProvider.whisper
    llm_backend: LLMBackend = LLMBackend.freellmapi   # Afon's OWN reasoning model

    # --- Wake word ----------------------------------------------------------------------
    wake_word_enabled: bool = True
    # NB: there is no engine selector. Porcupine was evaluated and dropped, and `wake_word_engine` /
    # `porcupine_access_key` sat here afterwards being read by nothing — settable in .env, and
    # setting them did precisely nothing. Custom phrases are openWakeWord models (trained
    # out-of-repo with openWakeWord's own pipeline under WSL; the .onnx files live in .wakewords/)
    # and are named by PATH in wake_words below.
    # The owner's wake set. An entry ending .onnx/.tflite is loaded directly as a trained model;
    # anything else must be a pretrained openWakeWord phrase or it is skipped.
    wake_words: str = "afon,alfred,robbin,assist,time to work,wake up,six-one-nine"
    wake_word_threshold: float = 0.5
    # Speakers barge-in: on a shared (half-duplex) endpoint, saying the wake word OVER Afon's
    # speech interrupts him mid-sentence (detection stays hot at threshold+0.15; audio is still
    # never forwarded to STT during TTS). VAD barge-in remains headphones-only.
    wake_barge_in_enabled: bool = True
    wake_listen_window_s: float = 8.0     # how long the mic stays open after a wake/reply
    # Hot-mic mode: after the FIRST wake word of the session, keep the mic open continuously
    # until the edge restarts (or `AFON_HOT_MIC_IDLE_MINUTES` minutes of silence pass).
    # No need to repeat the wake word for follow-ups; VAD + endpointing drive utterance boundaries.
    hot_mic_after_wake: bool = False
    hot_mic_idle_minutes: int = 30        # close the window after this many minutes of silence
    # Edge reflexes (subsystem #24): answer truly-local read-now turns (time/date) ON THE EDGE with no
    # brain WS round-trip — the biggest perceived-latency cut for the most common quick queries, and it
    # works offline. High-precision match; anything else falls through to the brain unchanged.
    edge_reflexes_enabled: bool = True
    # Spoken acknowledgement the instant a wake word fires, so you KNOW Afon heard you and is
    # actively listening — before you say the command. Pipe-separated choices are picked at random
    # for natural variety; set empty ("") to disable.
    wake_ack_phrase: str = "Yes, sir?|I'm listening, sir.|Sir?|Go ahead, sir."
    # A soft audible "heartbeat" pulse while the edge is idle and waiting for the wake word — so you
    # always KNOW it's up and actively listening. It stops the instant a wake word fires, and resumes
    # when the conversation goes idle again. Set false to disable.
    listening_pulse: bool = True
    listening_pulse_period_s: float = 2.5   # seconds between pulses while idle

    # --- VAD & barge-in (Phase 1) -------------------------------------------------------
    # Silero VAD (CPU, bundled) detects speech start/stop. Barge-in lets you interrupt
    # Afon mid-sentence by speaking. Barge-in requires the mic NOT to hear Afon's own
    # voice, so it only works on HEADPHONES (or with AEC) — never enable it with half_duplex
    # on open speakers, or Afon's own playback interrupts himself. See README audio notes.
    vad_enabled: bool = True
    barge_in_enabled: bool = False        # interrupt Afon mid-speech on detected speech
    # Smart barge-in: 'auto' detects the live output device and enables barge-in ONLY when
    # you're on a private endpoint (headphones / AirPods / smart-glasses / phone-with-earbuds),
    # where the mic can't re-hear Afon's TTS — and keeps it OFF on open speakers. 'on'/'off'
    # force it. See edge/device_profile.py. (barge_in_enabled above is the legacy hard switch
    # honoured when mode='auto' can't tell — e.g. a forced True still wins for AEC setups.)
    barge_in_mode: str = "auto"           # auto | on | off
    # Phase 1.2 — acoustic echo cancellation, for full-duplex barge-in on OPEN speakers. 'none' keeps
    # the speaker-safe half-duplex behaviour. A real echo-canceller ('krisp' / 'aic') removes Afon's
    # own TTS from the mic so 'auto' barge-in turns ON even on shared speakers — but each is a
    # PROPRIETARY, license-gated SDK you install (krisp_audio / aic_sdk); without it, this degrades to
    # half-duplex. See edge/aec.py. (rnnoise is noise suppression, not echo — it does NOT enable this.)
    aec_filter: str = "none"              # none | krisp | aic
    # For remote transports (Mentra glasses, iPhone/Android client) the local audio device name says
    # nothing about how YOU hear Afon, so the client declares it: 'glasses', 'phone-headphones',
    # 'android-headphones', 'phone-speaker', 'android', 'headphones', 'speakers'. A local Mac/laptop
    # leaves this blank and the output device is name-classified. Blank = classify the local output.
    device_hint: str | None = None
    vad_confidence: float = 0.6           # Silero speech-probability threshold (0..1)
    vad_start_secs: float = 0.2           # speech must persist this long to count as "started"
    vad_stop_secs: float = 0.6            # silence this long ends the turn
    vad_min_volume: float = 0.5           # gate out very quiet room noise

    # --- ElevenLabs (the Afon voice) --------------------------------------------------
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str | None = None          # the specific voice you want Afon to have
    elevenlabs_model: str = "eleven_flash_v2_5"     # lowest-latency streaming model

    # --- Deepgram (cloud STT for accurate, streaming recognition) -----------------------
    deepgram_api_key: str | None = None
    deepgram_model: str = "nova-3"
    # 'multi' = nova-3 multilingual code-switching: understands English, French, German, Russian
    # (+ Spanish/Hindi/Portuguese/Italian/Dutch/Japanese). Deepgram does NOT support Armenian, and
    # Ukrainian isn't in 'multi' — for those use the Whisper provider (stt_provider=whisper), which
    # auto-detects and transcribes ALL of the owner's six languages. Afon always replies in English.
    deepgram_language: str = "multi"
    # Endpointing: ms of trailing silence before Deepgram finalises an utterance and we hand it to the
    # brain. Lower = snappier replies, higher = fewer mid-sentence cut-offs. 700ms is a natural-speech
    # default that tolerates normal thinking pauses; drop to 400-500 for snappier back-and-forth.
    deepgram_endpointing_ms: int = 700

    # --- Audio routing (speakers <-> headphones / AirPods) ------------------------------
    # Output target: a name fragment or alias ("speakers", "headphones", "airpods") or a
    # numeric device index. None = OS default. A saved voice-command preference overrides
    # this at startup (see edge/audio_devices.py). Input stays on the laptop mic by default
    # so Bluetooth stays in high-quality A2DP output mode (using AirPods as mic forces HFP).
    audio_output_device: str | None = None
    # Pin a specific INPUT mic by PyAudio device index (None = system default). The built-in Intel
    # Smart Sound array can deliver garbled audio; set this to a USB mic/headset's index. List them:
    # uv run python bench/list_audio_devices.py
    audio_input_device_index: int | None = None
    # Pin the INPUT mic by NAME substring (e.g. "airpods", "usb"). Survives Bluetooth reconnects,
    # which shuffle PyAudio indices. Takes precedence over audio_input_device_index when it matches;
    # if no device matches at startup, falls back to the index, then the system default.
    audio_input_device_name: str | None = None
    # Pin a specific OUTPUT device by PyAudio index (None = auto-route). Needed for a Bluetooth headset
    # in HFP mode (mic active) where the A2DP "Headphones" output is unavailable and the auto-route
    # can't find the raw HFP endpoint. audio_out_sample_rate must match the device (HFP = 16000).
    audio_output_device_index: int | None = None
    # Output playback rate. 22050 is a valid ElevenLabs pcm format (TTS negotiates pcm_22050 to match,
    # so playback is clean). 16000 for a BT HFP headset. Must be a rate the output device supports.
    audio_out_sample_rate: int = 22050
    # Auto-route to a connected private endpoint (AirPods Pro Max / headphones) when no explicit
    # output is set: "if they're connected to the laptop, send everything to my headphones".
    auto_route_headphones: bool = True
    # Smart per-device MIC routing: when a RELIABLE wired/USB headset mic is plugged in, listen through
    # it; otherwise keep the built-in mic. Deliberately EXCLUDES AirPods/Bluetooth "Hands-Free" — that
    # HFP mic is telephone-grade AND intermittently drops the stream (Afon goes deaf), so AirPods use
    # the built-in mic for input while output still auto-routes to them. Takes precedence over the pinned
    # name below when a headset mic is present. Off = always use the pin / built-in.
    auto_route_headset_mic: bool = True

    # --- Local engine assets (used when provider == local) ------------------------------
    whisper_model: str = "base"           # faster-whisper size; "small" for more accuracy
    # Language to transcribe. "en" (default) is far more reliable than auto-detect, which mis-fires
    # to Russian/other on short English utterances. Set to your spoken language (e.g. "hy" Armenian,
    # "ru", "fr") or "auto" for per-utterance detection across all six.
    whisper_language: str = "en"
    # Moonshine (STT_PROVIDER=moonshine): English-only but ~3x faster than whisper base on CPU
    # (~0.4s vs ~1.4s). 'moonshine/tiny' is fastest; 'moonshine/base' a touch more accurate, slower.
    moonshine_model: str = "moonshine/tiny"
    # Proper-noun biasing (Phase 1.5): names/places/projects the STT should favour so "Yerevan"
    # stops becoming "your event". Comma-separated; the assistant/owner names, the owner's address,
    # and contact names are added automatically (see edge.proper_nouns.hotwords_list). Passed to
    # Whisper as `hotwords=` and to Deepgram nova-3 as `keyterm=`.
    stt_hotwords: str = ""
    # Software boost for quiet mics (Intel Smart Sound arrays capture ~3% full-scale at mono 16k,
    # too quiet for openWakeWord). 1.0 = off. Raise the Windows mic level too, then lower this.
    mic_gain: float = 1.0
    piper_voice: str = "en_US-ryan-high"
    kokoro_voice: str = "am_adam"

    # --- Brain / orchestrator -----------------------------------------------------------
    # brain_mode: "local" = run the agent in-process on the edge (fastest, no network dep);
    # "remote" = the edge is a thin client to the 24/7 VPS brain over brain_ws_url (ONE shared
    # Afon + memory across all devices); "auto" = try remote, fall back to local if unreachable.
    brain_mode: str = "local"
    brain_ws_url: str = "ws://127.0.0.1:8765/voice"   # edge -> brain socket
    brain_host: str = "127.0.0.1"                     # set 0.0.0.0 to reach from phone/glasses
    brain_port: int = 8765
    client_http_port: int = 8766                      # serves clients/iphone/ over HTTP
    api_auth_token: str | None = None                 # empty = loopback-only, no auth

    # Self-improvement loop (Hermes-style background_review): after every N turns a background pass
    # extracts durable facts about the owner into L1 learned memory. Off the hot path; never slows a turn.
    self_improve_enabled: bool = True
    self_improve_every_turns: int = 6
    # CODE self-improvement (4.12): a bounded loop that lets Afon improve his OWN source — branch,
    # edit, run tests, commit locally. OFF by default (opt-in): it edits real code, so it only runs
    # when explicitly armed. It is branch-only and NEVER pushes autonomously — the push stays a
    # human-confirmed step. Arm per-deploy via AFON_CODE_SELF_IMPROVE_ENABLED=true.
    code_self_improve_enabled: bool = False
    code_self_improve_max_steps: int = 8

    # DEDICATED inbound bot for the 24/7 Telegram bridge (DM Afon from any device). MUST be a
    # SEPARATE bot from telegram_bot_token — that one is OpenClaw's, and two pollers fighting over
    # getUpdates steal each other's messages. The bridge only runs if this is set.
    telegram_bridge_bot_token: str | None = None

    # PC-control executor: the laptop runs edge/pc_agent.py, which connects to this control URL so
    # the (VPS) brain can run files/processes/PowerShell/open-app on the laptop. Defaults to the
    # brain_ws_url host with the /control path. Set to the VPS for laptop->VPS control.
    pc_control_url: str | None = None

    # OpenClaw Gateway (existing fleet) — an EXTERNAL team Afon can DELEGATE to, by
    # messaging ispir through the gateway. It is ONE of Afon's tools, not his brain.
    openclaw_delegation_enabled: bool = True
    # Whether Afon may CONSULT the fleet (via ispir) without a per-session OK. Default OFF: the
    # fleet touches shared VPS infra, so it stays a deliberate opt-in. Set AFON_FLEET_AUTHORIZED=
    # true in .env to arm it for a 24/7 deployment. (Afon still always re-voices results as himself.)
    fleet_authorized: bool = False
    # The fleet is a SEPARATE, optional system (your own OpenClaw deployment). It ships fully
    # disabled with NO host baked in — set these in .env only if you run a gateway. Blank = the
    # delegate_to_fleet tool simply tells you the bridge isn't configured.
    openclaw_gateway_url: str = ""        # e.g. http://<your-gateway-host>:3200
    openclaw_token: str | None = None
    openclaw_router_agent: str = "ispir"
    openclaw_request_timeout_seconds: int = 30
    openclaw_cli_path: str = "openclaw"   # path to the OpenClaw CLI on the gateway host
    openclaw_cli_ssh_target: str | None = None  # e.g. user@your-gateway-host (for the CLI fallback)

    # freellmapi proxy — Afon's OWN reasoning LLM (runs on the VPS, tunneled to localhost).
    freellmapi_base_url: str = "http://localhost:3001/v1"
    freellmapi_api_key: str | None = None
    # Primary + ordered fallbacks (rate-limit/error -> next model). See bench/llm_bench.py.
    # Primary is MiniMax-Text-01 (owner's paid token plan) — NON-thinking, native tool-calling, no <think>
    # block, and (critically) high daily limits so it can carry the full per-turn tool surface. groq
    # llama-3.3-70b has a lower TTFT but a free-tier 100k tokens/DAY cap that the ~10k-token tool schema
    # exhausts in a handful of turns (429), so it CAN'T be the always-on primary — it's the fast first
    # fallback, reached for NARROWED (small-surface) tool turns via the tool-tier. Reasoning models are
    # deliberately absent here — reached ONLY via the B5 thinking-tier on a dodged forced tool. The real
    # latency lever is not the model but the tool-surface size (see _tools_for_turn / conversational
    # suppression): a chat turn carries no tools and answers in ~0.5s, tool turns narrow to a few.
    llm_primary_model: str = "minimax:MiniMax-Text-01"
    llm_fallback_models: str = ("groq:llama-3.3-70b-versatile,"
                                "groq:llama-3.1-8b-instant,gemini-3.5-flash")
    # Vision (Phase 3 — Perception): models that accept images (screen/camera). Provider-prefixed like
    # the LLM chain. `minimax:MiniMax-Text-01` is a PAID, reliable vision model on the owner's token
    # plan (verified: correctly reads images via the OpenAI image_url path) — the dependable primary;
    # gemini via the freellmapi proxy is the fallback. First that answers a `see()` call wins. (groq
    # has no vision model on this plan — 'MiniMax-VL-01'/'abab6.5s-chat' are 400 unknown-model here.)
    vision_models: str = "minimax:MiniMax-Text-01,gemini-3.5-flash"
    # Per-model attempt cap. A real-time voice turn must never block a full minute on one hung
    # model, so this is tight: a stalled attempt is abandoned and the chain fails over to the next
    # model within this window. The fast primary (Groq) normally answers in 1-2s, so this only
    # bites on a genuine hang. Raise it if you make a heavy non-voice batch call.
    llm_request_timeout_seconds: int = 20
    # Latency tuning (see docs/AUDIT.md). Any chain entry may be prefixed with a provider:
    #   * no prefix          -> the freellmapi proxy (freellmapi_base_url)
    #   * "groq:<model>"     -> Groq DIRECTLY (api.groq.com) with AFON_GROQ_API_KEY — TTFT ~0.3s,
    #                           very consistent; the fastest reliable primary. e.g.
    #                           AFON_LLM_PRIMARY_MODEL=groq:llama-3.3-70b-versatile
    groq_api_key: str | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # Cerebras: the fastest inference provider (~2000 tok/s), OpenAI-compatible, free tier with a
    # SEPARATE rate-limit pool from Groq — so "groq:…,cerebras:…" in the chain rarely both rate-limit
    # at once. Get a free key at cloud.cerebras.ai. Use as e.g. "cerebras:llama-3.3-70b".
    cerebras_api_key: str | None = None
    cerebras_base_url: str = "https://api.cerebras.ai/v1"
    #   * "ollama:<model>"   -> a LOCAL Ollama server (ollama_base_url, no key) for true local-first
    #                           reasoning when cloud/proxy is down. e.g.
    #                           AFON_LLM_FALLBACK_MODELS=...,ollama:llama3.2 keeps a fully-offline
    #                           tail on the chain. Pull the model first (`ollama pull llama3.2`).
    ollama_base_url: str = "http://localhost:11434/v1"
    # MiniMax: a DIRECT, OpenAI-compatible provider (api.minimax.io) — a paid, reliable reasoning
    # model, independent of Groq's free-tier daily quota and the freellmapi proxy. Use as e.g.
    # "minimax:MiniMax-M2.5-highspeed" (highspeed = lower TTFT, better for a voice turn) or
    # "minimax:MiniMax-M3" for max quality. Needs AFON_MINIMAX_API_KEY + account credits.
    minimax_api_key: str | None = None
    minimax_base_url: str = "https://api.minimax.io/v1"
    #   * "vercel:<model>"   -> Vercel AI Gateway (OpenAI-compatible), a PAID aggregator that reaches
    #                           many providers behind one key. Deliberately positioned as the ABSOLUTE
    #                           LAST rung of every chain: it only ever answers when MiniMax and every
    #                           free/self-hosted model ahead of it has already failed, so it is the
    #                           "never go mute" backstop rather than a routine route. Model names are
    #                           provider-qualified, e.g. "vercel:anthropic/claude-sonnet-4.5".
    vercel_ai_gateway_api_key: str | None = None
    vercel_ai_gateway_base_url: str = "https://ai-gateway.vercel.sh/v1"
    # If no FIRST token arrives within this many seconds, cancel and fail over to the next model —
    # turns a slow/hung primary into a fast recovery instead of a full-timeout stall.
    #
    # 2.0, lowered from 4.0 on MEASURED data (2026-08-11, 10 streamed turns against the live
    # primary): TTFT was 0.61 0.64 0.68 0.70 0.74 0.75 0.78 0.81 0.92 and one 3.69 outlier —
    # median 0.74s. So 2.0s is ~2.7x the median and trips on roughly 1 turn in 10.
    #
    # Tripping on a healthy turn is normally the thing a deadline must avoid. Here it is NOT, and
    # that inverts the usual tradeoff: the first fallback (groq llama-3.3-70b) has a measured TTFT
    # of 0.18s, so abandoning at 2.0s and letting IT answer costs ~2.2s, against 3.7s spent waiting.
    # Failing over is simply faster than being patient. The old 4.0s was set just above the outlier,
    # which optimised for "never abandon the primary" — the wrong objective when the alternative is
    # four times quicker.
    # The one real cost, the primary being benched, is handled separately: a first-token timeout
    # raises _SlowFirstToken and benches for 5s instead of the 45s a genuine failure gets, so a
    # slow turn does not migrate a tenth of the day onto the quota-capped fallback.
    llm_first_token_timeout_seconds: float = 2.0
    # After a provider/model fails with a timeout, rate limit, API error, or unusable empty response,
    # skip that chain entry briefly on later live turns while any healthy fallback exists.
    llm_unhealthy_cooldown_seconds: float = 45.0
    # Optional two-tier: a fast model tried FIRST (prepended to the chain) for snappier first words;
    # the normal chain stays as the quality fallback. Blank = single-tier. e.g. "llama-3.1-8b-instant".
    llm_fast_model: str | None = None
    # Tool-tier routing: on a FORCED tool turn (a clear command / data-read / narrowed intent), start
    # the chain PAST the primary. The non-thinking primary (MiniMax-Text-01, chosen for conversational
    # speed/cost) is a poor tool-caller — it dodges ~half of forced calls, so every tool turn otherwise
    # pays a wasted round-trip before the B4 fallback rescues it. The first fallback (groq 70B) is a fast,
    # reliable native tool-caller, so forced turns go straight to it; conversational turns keep the primary.
    # Objectively measured (behavioural audit): lifts Tasks/Channels/Web/Combination and cuts tool latency.
    tool_turns_prefer_fallback: bool = True
    # B5 thinking-tier (last resort): when a FORCED tool turn is dodged by BOTH the fast primary and the
    # reliable fallback caller, escalate ONCE to a MiniMax REASONING model — a much stronger tool-caller
    # that reliably fires arg-bearing tools (set_reminder / create_event / notion_create) the fast models
    # miss. Only on the dodge path, so the ~+1s reasoning cost is paid on the few turns that need it, never
    # on conversation. Blank = disabled. M2.5-highspeed = lowest TTFT of the reasoning line (best for voice).
    llm_thinking_model: str | None = "minimax:MiniMax-M2.5-highspeed"

    # Shared secret for inbound integration webhooks (Stripe/GitHub/Gmail → /webhook/<source>). The
    # sender signs the raw body HMAC-SHA256 with this; the brain rejects anything unsigned/mismatched.
    webhook_secret: str | None = None

    # --- Channels & knowledge -----------------------------------------------------------
    telegram_bot_token: str | None = None
    vault_path: str | None = None
    audit_log_dir: str | None = None
    # Local contact book (Phase 4.3) — resolve a NAME to an email/Telegram/phone before a send/draft.
    # Plain-text, one contact per line; blank = <repo>/contacts.md. Optional + gitignored.
    contacts_path: str | None = None

    # --- Phase 3: knowledge & channel tools ---------------------------------------------
    # Every Phase 3 tool degrades gracefully: when its credentials are absent it returns a
    # short "not configured yet" note that Afon re-voices, so the brain never crashes on a
    # missing integration. Flip a key in .env to light each one up.
    http_timeout_seconds: int = 20

    # J1.4 — the backstop on a single tool result entering the message list. Deliberately WELL above
    # every per-tool clip() limit (the widest is vault_read_max_chars at 4000): this must never fight
    # a tool's own sizing decision, only catch the one that forgot. Raise it if a legitimate tool
    # genuinely needs more; that is the rollback, and no code change is required.
    tool_result_ceiling: int = 12000

    # Obsidian vault (read/search the LOCAL mirror). vault_path is defined above.
    vault_search_max_results: int = 6
    vault_read_max_chars: int = 4000
    # Writing into the vault is OFF by default because on the laptop the vault is a one-way
    # VPS->local sync target (local edits get clobbered). On the AUTHORITATIVE host (the VPS that
    # owns the vault) set AFON_VAULT_WRITABLE=true and the write_vault tool can save notes there.
    vault_writable: bool = False

    # --- Acknowledgement + long-task progress ("Afon-like" feel) ------------------------------
    # Afon speaks a brief acknowledgement BEFORE running a tool ("On it, sir — checking your
    # calendar."), then runs it in the background, then answers. If a tool runs longer than
    # tool_slow_warn_seconds he says it's taking longer than expected, and repeats every
    # tool_long_update_seconds while it's still working (so a long fleet job never goes silent).
    ack_before_tools: bool = True
    tool_slow_warn_seconds: float = 8.0
    tool_long_update_seconds: float = 45.0   # a 5-min stall = update at 8s, 53s, 98s… never silent
    # Speed vs polish (A/B): when ON, a SHORT channel/task read (calendar/email/Notion-tasks/Telegram)
    # is spoken verbatim, skipping the summary LLM pass (~1-3s faster on those turns). OFF by default so
    # the summary keeps polishing a raw multi-item dump into a clean spoken sentence. Long results always
    # summarise even when ON (see _CHANNEL_DIRECT_MAX in agent.py).
    direct_speak_channel_reads: bool = False

    # --- Background task queue (status-keeping) -------------------------------------------------
    # Long work (a fleet delegation) runs in the background and is tracked here so Afon can answer
    # "how's that going?" and announce completion by voice. SQLite so it survives a 24/7 restart.
    tasks_db_path: str | None = None     # blank = <repo>/afon_tasks.sqlite

    # --- Session hygiene: smart reset ----------------------------------------------------------
    # When the gap since the last turn exceeds this many minutes, the brain SOFT-RESETS working
    # memory: it journals the prior conversation (L2) and clears the rolling history, so a new
    # conversation hours later doesn't drag stale context/anaphora ("set it back" pointing at a
    # 'it' from this morning). Durable memory (L1/L2/L3) is untouched. 0 disables.
    session_idle_reset_minutes: int = 180
    # Restart-durable working memory: snapshot the rolling conversation thread to disk each turn so a
    # brain restart/crash resumes mid-conversation instead of starting blank. Durable layers (L1/L2/
    # L3) already persist; this covers the short-term thread. A snapshot older than the idle-reset
    # window is treated as expired (journalled + dropped) on load. Blank = <repo>/afon_session.json.
    session_persist_path: str | None = None

    # --- Phase 9: persistent memory (learned facts L1 + daily journal L2) ----------------
    # Markdown-backed long-term memory under memory/learned and memory/journal. The vault
    # (above) is L3 and should ALWAYS be configured so he can read it; it's validated at start.
    memory_enabled: bool = True
    memory_recall_limit: int = 5        # facts returned by the recall tool
    memory_digest_max: int = 10         # recent learned facts injected into the system prompt
    memory_digest_fact_chars: int = 80  # per-fact char cap in the digest (keeps the prompt bounded)
    #                                     (capped so a full digest keeps the prompt <=2000 tok)
    # Auto-recall (RAG per turn): before each turn, retrieve the facts/journal relevant to what the
    # owner just said and inject them as an ephemeral system note, so durable memory reaches the model
    # WITHOUT it having to decide to call `recall`. Keyword L1+L2 on the hot path (fast, local, free);
    # the explicit recall tool still offers the full semantic + vault search. Skips trivial turns.
    memory_autorecall_enabled: bool = True
    memory_autorecall_limit: int = 4       # max hits injected per turn
    # Ceiling on the L3 (Obsidian vault) leg of fused_recall. `search_vault` does synchronous
    # work inside an async signature, so while it runs the whole brain is unresponsive —
    # measured at 5.5s warm and 287s on a cold vault. 6s keeps an explicit `recall` usable
    # while making a multi-minute freeze impossible.
    memory_vault_search_budget_s: float = 6.0
    memory_autorecall_min_words: int = 3   # utterances shorter than this are skipped (no grounding)
    # Rebuild the system-prompt learned-digest every N turns so facts learned mid-session (by the
    # background reviewer) surface without waiting for a brain restart (the digest was startup-frozen).
    memory_digest_refresh_every_turns: int = 8
    redis_url: str | None = None        # L4 hot-cache (Phase 9b); blank = no cache (graceful)
    # L5 semantic recall (Phase 9c): rank learned facts by MEANING, not just keywords, so "how are my
    # bunnies?" finds "rabbit farm". Powers the explicit `recall` tool (auto-recall stays keyword-only
    # for hot-path speed). ON by default but fully GRACEFUL: it uses a local `sentence-transformers`
    # model if installed, else the Jina embeddings API when AFON_JINA_API_KEY is set (the small-VPS
    # path — no ~1GB torch), else it silently degrades to keyword recall. Query embeddings are cheap;
    # per-fact embeddings are cached persistently (afon_vectors.sqlite) so a fact re-embeds only when
    # it changes. Set False to force keyword-only.
    memory_semantic_enabled: bool = True
    memory_semantic_model: str = "all-MiniLM-L6-v2"
    memory_semantic_weight: float = 4.0
    # Semantic FLOOR: cosine similarity below this doesn't count toward a fact's score. Without it,
    # every fact scores slightly > 0 (cosine is essentially never 0), so recall would surface weak,
    # unrelated facts for any query ("submarines" -> a random fact). The floor keeps meaning-only hits
    # honest — only a genuinely related fact (e.g. "bunnies" -> "rabbit farm", ~0.34) clears it.
    memory_semantic_min_score: float = 0.25
    memory_vector_db_path: str | None = None   # persistent L5 embedding cache; blank = <repo>/afon_vectors.sqlite
    memory_graph_db_path: str | None = None     # L5b entity-relation graph; blank = <repo>/afon_graph.sqlite

    # Web search (Tavily) + page scrape (Jina Reader) + headless interactive browse (Browserbase).
    tavily_api_key: str | None = None
    # Page scraping uses Jina Reader (https://r.jina.ai) — free and KEYLESS. This optional key only
    # raises rate limits; leave blank and scraping still works.
    jina_api_key: str | None = None
    # Fallback providers (Phase 4.5) so a single provider outage never removes the capability:
    #   * web_search: Tavily -> Brave (AFON_BRAVE_API_KEY) -> Jina Search (s.jina.ai, KEYLESS).
    #     The keyless Jina tail means search works even with NO keys at all.
    #   * scrape_url: Jina Reader (keyless) -> Firecrawl (AFON_FIRECRAWL_API_KEY).
    brave_api_key: str | None = None
    firecrawl_api_key: str | None = None
    # MCP servers (Phase 4.6) — expose external Model Context Protocol tools through the normal
    # registry. A JSON object (inline or a path to a .json file) mapping name -> {command, args, env}.
    # ONLY listed servers are launched (no default); blank = no MCP tools. e.g.
    #   {"filesystem": {"command": "npx", "args": ["-y","@modelcontextprotocol/server-filesystem","/dir"]}}
    mcp_servers: str | None = None
    # MyNews (the owner's RSS aggregator) — base URL for the proactive morning-brief signal
    # (brain/mynews.py). Blank = source off. On-demand news tools come via mcp_servers.
    mynews_url: str | None = None
    # Composio (breadth layer): API key for the 250+ OAuth-managed app integrations. Used by the
    # accounts health-check (bench/test_composio_accounts.py) and, once an MCP server URL is added to
    # mcp_servers, by the live tool path. Blank = no Composio.
    composio_api_key: str | None = None
    # The Composio entity/user the connected accounts live under. Blank = auto-detect from the first
    # connected account at first use (cached).
    composio_user_id: str | None = None
    browserbase_api_key: str | None = None
    browserbase_project_id: str | None = None

    # Telegram: a Telethon USER client is required to READ unread DMs (the Bot API cannot);
    # sending uses the simpler Bot API. telegram_bot_token is defined above.
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_session: str = "afon"            # Telethon session name (one-time login)
    telegram_default_chat: str | None = None    # default recipient/chat_id for "send a telegram"
    telegram_phone: str | None = None           # your number, intl format (+...), for the one-time login
    giphy_api_key: str | None = None             # for "@gif <query>" search; falls back to Giphy's public key
    # Personal music: a Telegram chat/group where you keep audio tracks ("my playlist"). Afon
    # finds a track there (by name, or random) and delivers it to telegram_music_target so it
    # surfaces on your PHONE Telegram (one tap to play). id / @username / exact title all work.
    telegram_playlist_chat: str | None = None
    telegram_music_target: str = "me"            # where the chosen track is sent ('me' = Saved Messages → phone)
    # "Afon Music Room" group: Afon streams tracks into its voice chat (via pytgcalls) so they
    # play LIVE on your phone when you join that voice chat. Telethon marked id (-100…).
    telegram_music_room_chat: str | None = None

    # Default music backend for "play X". 'ytmusic' = YouTube Music (free, music-tuned search,
    # no account). 'telegram' = your personal Telegram playlist. 'youtube' = plain YouTube.
    music_source: str = "ytmusic"

    # --- Phase 11: Gmail + Calendar (one Google OAuth app) + Home Assistant -------------
    # The owner runs the one-time consent (bench/google_login.py) -> a refresh token below. The same
    # app/token serves Gmail (read/draft/send) and Calendar (list/create). All degrade to a spoken
    # "not configured" note until set. send_email + create_event are confirm-gated (outward-facing).
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_refresh_token: str | None = None
    google_oauth_redirect: str = "http://127.0.0.1:8585/oauth2callback"  # must match the Google app
    # Twilio — real phone calls on the owner's behalf (confirm-gated). PARKED until the owner
    # creates the Twilio account; SMS/WhatsApp removed 2026-07-29 (Telegram covers texting).
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None     # the Twilio number, E.164 (+1415...)
    owner_phone_number: str | None = None     # the owner's own phone for "call me"
    # Google Maps — live travel times + place search (read-only).
    google_maps_api_key: str | None = None
    owner_home_address: str | None = None     # default origin for "how long to X"
    # Wolfram Alpha — exact computation instead of guessed numbers.
    wolfram_app_id: str | None = None
    # Home Assistant — local-first smart home. A long-lived access token from your HA profile, and
    # the base URL of your HA instance (e.g. http://homeassistant.local:8123). Locks/alarms confirm.
    ha_url: str | None = None
    ha_token: str | None = None

    # Notion — internal integration token (https://www.notion.so/my-integrations). SHARE the pages
    # you want Afon to touch with the integration (Notion is deny-by-default). read/append/comment/
    # create; writes are confirm-gated. Degrades until set.
    notion_token: str | None = None
    notion_version: str = "2022-06-28"
    # Your Notion TASKS database (the dashboard). Share that database with the "Personal Assistant"
    # integration, then set its id here so Afon can read what's due today / overdue / upcoming and
    # brief you by voice. The id is the 32-hex chunk in the database URL. Degrades until set.
    notion_tasks_db_id: str | None = None
    # Daily consolidated catch-up (past-due tasks + important email) delivered ONCE per day (HH:MM,
    # user timezone): speaks to a listening device, else a Telegram voice note, else an ntfy push.
    # The SAME digest is also appended to the owner's first live-edge turn of the day (see
    # brain/daily_digest.py). Blank ("") disables the timed briefing (the first-turn addendum still
    # works). Default 06:00 — a morning heads-up before the day starts.
    task_briefing_time: str = "06:00"
    # Autonomous daily BACKLOG pass (Phase 3.1): pull overdue + undated-inbox Notion tasks and have the
    # bounded worker attempt the SAFE work (research/draft/summarise), posting its result as a Notion
    # comment. Outward/destructive steps are always DEFERRED by the worker. OFF by default (it acts
    # unattended); only scheduled when enabled AND a tasks DB is configured.
    backlog_enabled: bool = False
    backlog_time: str = "09:30"            # HH:MM, user timezone
    backlog_max_tasks: int = 2            # how many tasks to attempt per daily pass
    # Multi-day OBJECTIVES (Phase 4.1): objectives the owner hands Afon to DRIVE across days. A daily
    # job advances the top active ones ONE safe step each (bounded worker; outward steps deferred), logs
    # dated progress, and reports unprompted. OFF by default (it acts unattended, like the backlog pass).
    objectives_enabled: bool = False
    objectives_time: str = "09:45"        # HH:MM, user timezone (just after the backlog pass)
    objectives_max: int = 2               # how many objectives to advance per daily pass
    objectives_max_steps: int = 6         # bounded worker step budget per objective
    # Task co-pilot (Phase 3): plan_task / execute_task let Afon plan a to-do WITH the owner
    # (clarify -> plan -> confirm) and then execute it via the bounded worker or the fleet, linking
    # progress back to the to-do. Execution always DEFERS outward/destructive steps. AUTO-PILOT (acting
    # on a plan without an explicit go-ahead) is opt-in and OFF by default — Afon asks before doing.
    copilot_autopilot_enabled: bool = False
    # Cap on how many steps the co-pilot's background worker takes per execution (bounded work loop).
    copilot_max_steps: int = 6

    # --- System control (files / processes / PowerShell) --------------------------------
    # Afon can manage the local machine: create/delete files & folders, list/kill/start
    # processes, and run PowerShell (optionally elevated, which raises a Windows UAC prompt).
    # These are powerful — the persona rule is to confirm anything destructive first.
    system_tools_enabled: bool = True
    # Paths under these roots are refused for deletion (defence against catastrophic rm).
    system_protected_paths: str = "C:\\Windows,C:\\Program Files,C:\\Program Files (x86)"

    # --- Phase 13: coding & self-improvement (Git + GitHub) -----------------------------
    # Afon can read/write his own source, run the test suite, lint, and make REVERSIBLE git
    # commits (never force-push / reset / rewrite history). Secrets (.env, sessions, voiceprint,
    # audit/, backups/) are hard-blocked from read/write. Commits + pushes are confirm-gated.
    coding_tools_enabled: bool = True
    skills_enabled: bool = True
    git_author_name: str = "Afon"              # the author on Afon's own (self-improvement) commits
    git_author_email: str = "afon@vazghen.local"
    # A fine-grained GitHub PAT (Issues: read/write) + the default "owner/repo". Lets Afon file an
    # issue in ONE tool call (create_github_issue) instead of the 2-step Composio router — the most
    # common dev action by voice. Blank = the tool degrades to a spoken "not configured" note.
    github_token: str | None = None
    github_repo: str | None = None               # e.g. "iamvazghen/OpenAfon"

    # --- Local interactive browser (visible window, persistent login) -------------------
    # A real Chromium Afon drives with Playwright: open windows, click, type (incl.
    # passwords/email when you ask him to log in), navigate. Visible by default so you can
    # watch and complete anything he hands back. Persistent profile keeps you logged in.
    browser_tools_enabled: bool = True
    browser_headless: bool = False
    browser_profile_dir: str | None = None       # default: <repo>/.afon-browser
    browser_nav_timeout_ms: int = 30000

    # --- Protocols (password-gated executable routines, FRIDAY/AFON-style) -------------
    # Named programs Afon runs ONLY when given the matching password. Set each in .env (or the
    # password store) — UNSET MEANS DISABLED, deliberately. These carried real defaults until
    # 36.F5: a password published in a public repo is the password of every install that never
    # changed it, and "ragnarok valhalla" restarts the laptop. A secret with a fallback in tracked
    # source has two homes, and the weaker one is the one an attacker uses.
    protocols_enabled: bool = True
    protocol_goodnight_password: str | None = None      # stops Afon
    protocol_phoenix_password: str | None = None        # restarts Afon
    protocol_ragnarok_password: str | None = None       # restarts the laptop
    protocol_backup_password: str | None = None         # backs up Afon memory
    protocol_ping_password: str | None = None           # sends a phone push test
    protocol_diagnostics_password: str | None = None    # writes a local diagnostics report
    protocol_auditpack_password: str | None = None      # archives audit logs
    protocol_checkpoint_password: str | None = None     # archives key non-secret context

    # --- Phase 4: proactivity & notifications -------------------------------------------
    scheduler_db_path: str | None = None          # default: <repo>/afon_jobs.sqlite
    ntfy_topic: str | None = None                 # ntfy.sh topic for push when voice is unavailable
    ntfy_server: str = "https://ntfy.sh"
    # Phase 4b — always-on VPS ticker (deploy/vps/) that owns RECURRING (daily) reminders so they
    # push even with the PC off. Optional: when set, set_reminder(daily=…) also registers there.
    ticker_url: str | None = None                 # e.g. http://127.0.0.1:8770 (on the VPS host)
    ticker_token: str | None = None               # optional bearer secret matching the ticker

    # --- Phase 10: proactive engine (initiate, interrupt, clarify, confirm) -------------
    # A background tick gathers signals (routine, calendar, unread, open threads, self-health)
    # and decides whether to say something *unprompted* — helpful, never noisy. OFF by default
    # because it speaks on its own; flip AFON_PROACTIVE_ENABLED=true to switch the companion on.
    # ON by default for the 24/7 production companion (he initiates within budget + quiet hours).
    # For a quiet testing session, set AFON_PROACTIVE_ENABLED=false.
    proactive_enabled: bool = True
    # When an autonomous driver (e.g. the daily backlog pass) TAKES an action on the owner's behalf,
    # report it unprompted — what he did, why, and his reasoning — via the proactive path (edge voice →
    # Telegram voice note → push). Set false to let autonomous work run silently.
    proactive_action_reports: bool = True
    proactive_tick_seconds: int = 300            # how often the tick evaluates signals
    proactive_quiet_hours: str = "23:00-07:00"   # no unprompted voice in this window (local time)
    proactive_daily_budget: int = 6              # max unprompted interjections per day
    proactive_relevance_threshold: float = 0.6   # only speak when a signal's urgency clears this
    proactive_repeat_suppress_minutes: int = 120  # don't repeat the same interjection within this
    # An exceptionally urgent signal (>= this) may still reach him in quiet hours — as a silent
    # phone push, never spoken aloud. Everything below it waits until quiet hours end.
    proactive_quiet_override_urgency: float = 0.95
    # Persisted proactive state (last-fired per signal key + today's used-budget + per-kind feedback
    # penalties) so a 24/7 brain RESTART doesn't reset suppression, budget, or what it learned. Blank
    # -> a file next to the tasks DB. In-memory only in tests (engine constructed without a path).
    proactive_state_path: str | None = None
    # Phase 1 context-gating: a ROUTINE interjection is HELD for a better moment while the owner is
    # busy (deep in code/docs, in a meeting, watching media). A signal whose urgency clears THIS is
    # important enough to interrupt anyway (below quiet_override, which also overrides quiet hours).
    proactive_context_override_urgency: float = 0.85
    # Phase 4 — conflict-only interventions. Afon may INTERRUPT the owner (pausing his media) ONLY
    # when he's watching/listening to something AND a real, timed calendar commitment is imminent — a
    # concrete named conflict, one-word dismissible, subject to the same dismissal-learning as any
    # nudge. This is the most intrusive behaviour, so it's OFF by default (arm it deliberately). Its
    # urgency clears the context-override (so it interrupts media) but stays below quiet_override (so
    # it never fires in quiet hours).
    interventions_enabled: bool = False
    intervention_lead_minutes: int = 10          # fire when a commitment starts within this many min
    intervention_urgency: float = 0.9            # > context_override (interrupts), < quiet_override
    # Where "what's the weather" and the morning briefing default to when no place is named.
    home_location: str | None = None

    # --- Phase 0 companion: activity/presence perception (laptop screen-time + context) ---------
    # Afon polls the laptop's foreground window + idle time so he KNOWS what you're doing (context
    # for proactivity) and can report screen-time. Local-only (a SQLite DB on the brain host); no
    # data leaves the machine. Privacy off-switch: AFON_ACTIVITY_TRACKING_ENABLED=false, or say
    # "pause activity tracking" at runtime. Phone usage is intentionally NOT tracked (no clean API).
    activity_tracking_enabled: bool = True
    presence_poll_seconds: int = 45              # how often to sample the active window
    presence_idle_threshold_seconds: int = 90    # idle beyond this = "away" (not counted as screen time)
    presence_engaged_idle_seconds: int = 25      # idle below this while in an editor/doc = deep-work (don't interrupt)
    presence_retention_days: int = 30            # prune activity samples older than this
    presence_away_seconds: int = 300             # idle beyond this = genuinely "away" -> greet on return (Phase 3 Perception)
    presence_db_path: str | None = None          # blank -> a file next to the tasks DB
    # Phase 6.3 — calibrated wellbeing pushback: after this many minutes of unbroken heads-down work,
    # the proactive engine may gently suggest a break (kind='wellbeing', so etiquette-learning tunes it).
    wellbeing_session_minutes: int = 180
    # Owner face recognition (Phase 3 — Perception). Local spatially-blocked LBP match on haar-detected
    # faces (no cloud, no dlib). Histogram-intersection similarity 0..1; >= this = "it's the owner".
    # NOT identity-grade — a lighting/pose-tolerant "owner-at-desk vs stranger" heuristic.
    # 0.48 belongs to the 8x8-grid descriptor (2026-08-11) and comes from the largest real sample
    # available: Olivetti, 40 people x 10 photos, all 79,800 pairs — same-person 0.561+-0.074,
    # different-person 0.439+-0.039, error-equalising threshold 0.480. The old 0.62 belonged to the
    # global histogram, which separated nobody from anybody (23.6% false-accept at ITS best setting,
    # and best-of-N overlapped outright) — do NOT carry that number forward.
    # PROVISIONAL: absolute similarity scales with image sharpness, and Olivetti is pre-aligned where
    # a haar crop jitters, so this wants one calibration against the owner's own re-enrollment —
    # which is why enroll_owner_face now reports his measured worst-case next to this number.
    face_match_threshold: float = 0.48
    # ArcFace cosine threshold — the REAL recogniser (uniface, `uv sync --extra vision`), used
    # whenever the backend loads and an embedding enrollment exists. Nothing to do with the LBP
    # number above: different space, different metric, and comparing across them is meaningless,
    # which is why they are two settings and two files (owner.npy vs owner_emb.npy).
    face_embed_threshold: float = 0.45
    # I1 — face as a SECOND factor on privileged (confirm-gated) actions. The voice gate cannot carry
    # authorisation on its own: measured on the live mic the owner's accept median is 0.33 while the
    # impostor ceiling is 0.30, and logs/edge.log shows 61 accepts against 0 rejections — ambient
    # television was being forwarded to the brain as though he had said it. So for actions that are
    # gated anyway, "he said yes" must also mean he was THERE.
    # Deliberately fail-open: if the camera is unreachable, unenrolled, or the laptop is offline, this
    # falls back to the confirm-only behaviour it has today. It can only ever ADD a refusal when the
    # camera positively shows the owner is not present — never lock him out of his own assistant.
    face_second_factor: bool = True

    # --- Phase 2 companion: field coaching (skill reviews + progress in your focus areas) --------
    # Afon tracks your LEVEL + progress in skill fields (e.g. German) and offers an evening review
    # at your level. The quiz itself is conversational; this just tunes when he offers it.
    coaching_enabled: bool = True
    coaching_fields: str = "german"              # comma-list of skill fields to coach (lowercase)
    coaching_evening_hours: str = "18:00-22:00"  # window in which he offers a review (local time)
    coaching_db_path: str | None = None          # blank -> a file next to the tasks DB

    # --- Phase 5: speaker biometrics (respond only to the owner's voice) ------------------
    speaker_id_enabled: bool = False      # gate commands by speaker match (off until enrolled)
    speaker_profile_path: str | None = None  # default: ~/.afon/voiceprint.json (biometric
    #                                       data belongs in state, not in the source tree)
    room_check_on_suspicion: bool = True  # unrecognized/overlapping voice -> one camera look (3-min
    #                                       cooldown) to understand who's in the room; never a routine poll
    speaker_threshold: float = 0.30       # ECAPA cosine accept threshold. Live data (2026-07-25): owner
    #                                       on the built-in far-field array scores 0.34-0.45 (AirPods-
    #                                       enrolled profile, so depressed); TV/guests 0.16-0.31. 0.30
    #                                       hears the owner with margin + rejects the TV. Re-enrolling on
    #                                       THIS mic would lift the owner to ~0.6 and allow a higher bar.

    # --- Latency / behaviour ------------------------------------------------------------
    # NB: "directed only" (ignore ambient speech & own playback) is enforced by the wake-word gate,
    # not a flag; and there's no aec flag — AEC (Krisp) isn't implemented, echo is handled by the
    # half-duplex gate (mic muted while speaking) on shared speakers.
    # (mic muted while speaking) on shared speakers, and by physical isolation on headphones/glasses.
    ttfw_target_ms: int = 1200            # Time-To-First-Word goal, surfaced in the TUI

    @property
    def duplex_mode(self) -> str:
        """'full' = mic open while Afon speaks (barge-in, needs headphones/AEC);
        'half' = mic muted while Afon speaks (speakers-safe, no barge-in)."""
        return "full" if self.barge_in_enabled else "half"

    @property
    def using_cloud_voice(self) -> bool:
        return (
            self.stt_provider == STTProvider.deepgram
            or self.tts_provider == TTSProvider.elevenlabs
        )

    @property
    def wake_words_list(self) -> list[str]:
        return [w.strip() for w in self.wake_words.split(",") if w.strip()]

    @property
    def llm_chain(self) -> list[str]:
        """Optional fast tier first, then primary, then ordered fallbacks (deduped, blanks dropped)."""
        chain = []
        if self.llm_fast_model and self.llm_fast_model.strip():
            chain.append(self.llm_fast_model.strip())
        chain += [self.llm_primary_model] + [
            m.strip() for m in self.llm_fallback_models.split(",") if m.strip()
        ]
        seen: set[str] = set()
        return [m for m in chain if not (m in seen or seen.add(m))]

    @property
    def vision_chain(self) -> list[str]:
        """Ordered vision-capable models (provider-prefixed), deduped, blanks dropped."""
        seen: set[str] = set()
        return [m for m in (x.strip() for x in self.vision_models.split(","))
                if m and not (m in seen or seen.add(m))]


settings = Settings()
