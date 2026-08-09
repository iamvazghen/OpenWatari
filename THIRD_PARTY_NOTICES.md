# Third-party notices

OpenAfon itself is released under the **MIT License** (see [`LICENSE`](LICENSE)). It is a *framework*
that orchestrates a number of third-party libraries and cloud services. This file records the
licenses of the **direct** dependencies and the obligations that come with them, so you can
distribute a fork with confidence.

> This list is best-effort and informational, not legal advice. Before a public release, regenerate
> it from your locked environment — e.g. `uv run --with pip-licenses pip-licenses --format=markdown`
> (or `uvx pip-licenses`) — and review anything that isn't a permissive license.

## How to read this

- **Permissive (MIT / BSD / Apache-2.0 / Unlicense):** you may use, modify, and redistribute freely.
  Your only real obligation is to **retain the dependency's copyright + license notice** in
  distributions (this file plus the upstream packages' own `LICENSE` files satisfy that). Apache-2.0
  additionally grants patent rights and asks you to preserve any `NOTICE` file the package ships.
- **Weak copyleft (LGPL):** see the dedicated note below — it is compatible with shipping your own
  MIT code, with conditions.

## Direct dependencies

| Package | Role | License |
|---|---|---|
| `pipecat-ai` | real-time voice pipeline (VAD/STT/LLM/TTS) | BSD-2-Clause |
| `pydantic`, `pydantic-settings` | config + schema | MIT |
| `python-dotenv` | `.env` loading | BSD-3-Clause |
| `loguru` | logging | MIT |
| `pyaudio` | local mic/speaker I/O | MIT |
| `textual`, `rich` | terminal UI | MIT |
| `openwakeword` | wake-word detection | Apache-2.0 |
| `kokoro-onnx` | local TTS runtime | MIT |
| `openai` (SDK) | OpenAI-compatible client for the LLM | Apache-2.0 |
| `fastapi` | brain HTTP/WS surface | MIT |
| `uvicorn` | ASGI server | BSD-3-Clause |
| `websockets` | edge↔brain transport | BSD-3-Clause |
| `httpx` | HTTP client | BSD-3-Clause |
| `apscheduler` | proactive scheduler | MIT |
| `sqlalchemy` | SQLite job store | MIT |
| `yt-dlp` | resolve a track for free playback | Unlicense (public domain) |
| `ytmusicapi` | YouTube Music search | MIT |
| `redis` (redis-py) | optional L4 hot-cache | MIT |
| `telethon` | Telegram user client (read/send) | MIT |
| **`py-tgcalls` / `ntgcalls`** | **stream music into a Telegram voice chat** | **LGPL-3.0** |
| `playwright` | headless browser driver | Apache-2.0 |
| `speechbrain` | ECAPA speaker embeddings | Apache-2.0 |
| `torch`, `torchaudio` | tensor backend for speaker-ID | BSD-3-Clause |
| `numpy`, `scipy` | numerics | BSD-3-Clause |
| `ruff`, `pytest`, `pytest-asyncio` | dev tooling (not shipped at runtime) | MIT |

Each installed package keeps its full license text in its `*.dist-info/` directory inside your
environment; that is the authoritative copy.

## The one copyleft dependency: `py-tgcalls` (LGPL-3.0)

`py-tgcalls` and its native core `ntgcalls` are **LGPL-3.0**. They are pulled in **only** by the
optional `channels` extra and used **only** for streaming audio *into* a Telegram group voice chat
(the "Music Room" feature). LGPL-3.0 is *weak* copyleft:

- Using it as an **unmodified, separately-installed (`pip`) library** that your MIT code merely calls
  is permitted, and **does not** force your own code to become LGPL.
- You must (a) keep its LGPL license and notices, (b) not statically bundle a modified copy without
  also offering that modified source under LGPL, and (c) leave it replaceable by the end user (a
  normal `pip` dependency already satisfies this).
- **If you want a 100%-permissive stack:** simply don't install the voice-chat streaming piece. Skip
  `py-tgcalls` and Telegram **text** messaging still works via `telethon` (MIT); you only lose
  in-call music streaming.

## Cloud services (Terms of Service, not open-source licenses)

These are bring-your-own-key services. The framework ships no keys; you accept each provider's Terms
when you sign up. They are **not** covered by this project's MIT license:

- **ElevenLabs** (TTS), **Deepgram** (STT), **OpenAI-compatible LLM provider / freellmapi**,
  **Tavily** (search), **Jina Reader** (scrape), **Browserbase** (cloud browser),
  **Google** (Gmail/Calendar OAuth), **Notion**, **Home Assistant**, **Picovoice** (Porcupine
  custom wake words), **GitHub**, **ntfy**.
- **Media:** `yt-dlp` / YouTube Music playback is intended for **personal use** — respect YouTube's
  Terms of Service in your jurisdiction.

## Pretrained models

Some local engines download model weights on first use (openWakeWord wake words, Kokoro/Piper
voices, faster-whisper, SpeechBrain ECAPA, the optional sentence-transformers embedder). Model
weights can carry **their own** licenses distinct from the code that loads them — check the upstream
model card before redistributing any weights with your fork.

## Naming & trademark note

This project is **OpenAfon** and the assistant is **Afon** — names chosen specifically so the
public brand does **not** rely on a franchise-associated mark. *Afon* is referenced only as the
blueprint/inspiration and is **not** used as the project's brand; the internal Python package keeps
the short name `afon` (import paths/CLI) for stability, which is a code identifier, not a public
trademark. The MIT license grants **copyright** permissions, not **trademark** rights — if you fork
under a different public name, pick one that's your own and update the persona file
(`personality/afon.md`) and the wizard's display-name step accordingly.
