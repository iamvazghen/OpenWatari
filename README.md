<div align="center">

<img src="assets/openafon-logo.png" alt="OpenAfon" width="140">

# OpenAfon

**An open-source framework for building your own 24/7, voice-first, multi-device AI companion — "Afon".**

Wake-word listening · streaming natural voice · its own reasoning LLM + tools · six-layer memory ·
proactive companion · phone / laptop / glasses · self-improving — local-first, self-hosted, yours.

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)
![Platform](https://img.shields.io/badge/edge-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)

📖 **Documentation:** **[openafon.vercel.app](https://openafon.vercel.app)** — the full docs site

</div>

<img src="assets/openafon-banner.png" alt="OpenAfon — build your own voice-first AI companion" width="100%">

---

> **Naming.** The project is **OpenAfon**; the assistant you build and talk to is **Afon**.
> *Afon* — the fictional assistant — is only the **blueprint/inspiration** for what a personal AI
> companion should be; this is an independent, from-scratch implementation. The Python package and
> CLI keep the short internal name `afon` (import paths, `afon-setup`, `python -m afon.…`) for
> stability; everything user-facing is Afon / OpenAfon.

## What this is

**OpenAfon is a framework, not a product.** Clone it, run the [setup wizard](#-quick-start-5-minutes),
point it at the voice/LLM providers you like, and you have a personal assistant you **talk to**: it
listens for a wake word, answers in a natural streaming voice, remembers across sessions, acts on
your machine and your accounts, reaches you proactively when it matters, and can even **improve its
own code** — safely and reversibly.

It is built from scratch in Python, runs on a no-GPU laptop, and is designed to be *on all the time*:
its **brain** runs as a service on an always-on host (a cheap VPS), while the **voice front-end** runs
wherever you are — laptop, phone, smart-glasses — all sharing one brain and one memory.

Everything **degrades gracefully**: a capability with no credentials simply says "that isn't
configured yet" instead of crashing, so you light up integrations one at a time.

> **Afon is its own agent.** It has its own reasoning model, its own memory, and its own
> personality (`personality/afon.md`). Pipecat is the voice shell; the LLM + tool loop is the mind.
> An *optional* external multi-agent fleet can be **consulted** as one tool among many — it never
> becomes the assistant.

### Why a framework

- **Bring your own everything.** Cloud quality (ElevenLabs + Deepgram) or 100% local CPU
  (Piper/Kokoro + Whisper/Moonshine + openWakeWord) — a flag, not a rewrite. Same for the LLM
  (any OpenAI-compatible endpoint: a free proxy, OpenAI, or local Ollama).
- **One brain, many devices.** The brain is a WebSocket server; a Windows/Linux laptop, a **Mac**, an
  iPhone or **Android** phone (no app), and Mentra glasses all reach the same agent and memory — over
  your **Tailnet**.
- **Safe by construction.** Confirm-before-acting is **enforced in code** (not just prompted),
  reversible-only git, repo-scoped self-edits, password-gated privileged routines, a redacted audit
  log, deny-by-default fleet. See **[SECURITY.md](SECURITY.md)**.
- **Personal but forkable.** The character, knowledge, and skills are plain Markdown files you edit;
  no code change needed to make it *yours*.

---

## Table of contents
- [Feature tour](#feature-tour)
- [Architecture](#architecture)
- [Quick start](#-quick-start-5-minutes)
- [The setup wizard](#the-setup-wizard)
- [Networking: the Tailnet requirement](#networking-the-tailnet-requirement)
- [Devices](#devices)
- [Configuration](#configuration)
- [Memory — six layers](#memory--six-layers)
- [The tool belt](#the-tool-belt)
- [Proactive companion](#proactive-companion)
- [Self-improvement](#self-improvement)
- [Testing & benchmarks](#testing--benchmarks)
- [Documentation site](#documentation-site)
- [Deployment](#deployment)
- [Security](#security)
- [Project layout](#project-layout)
- [License](#license)
- [Contributing](#contributing)

---

## Feature tour

- **Natural voice.** Wake on a phrase, understand you across multiple languages (English / French /
  German / Russian / Armenian / Ukrainian via Whisper), and reply in a streaming voice with **barge-in**
  (talk over it on headphones).
- **Thinks for itself.** Its own LLM (any OpenAI-compatible endpoint) with a model fallback chain,
  its own personality, and a tool-calling loop — answering directly and fast.
- **Remembers.** A six-layer memory: the live conversation, durable learned facts, a daily journal,
  your Obsidian vault (read **and** write on the authoritative host), a hot-cache, and optional
  semantic recall.
- **Acts.** Files, processes, PowerShell (incl. elevated), a real visible browser, music, reminders
  & phone push, Telegram (read *and* send), Gmail, Google Calendar, Notion, Home Assistant.
- **Looks things up.** Weather, crypto, stocks, FX, news, Wikipedia, dictionary, unit/currency
  conversion — mostly key-free.
- **Reaches you first.** A proactive engine that surfaces reminders, calendar events, and alerts
  within an interruption budget and quiet hours — speaking if you're listening, pushing if you're not
  — and **remembers what it said**, so you can answer "yes, do that" a minute or ten days later.
- **Improves itself.** Reads and edits its own source, runs its own test suite, and makes
  **reversible** git commits — with hard guardrails so nothing destructive is possible.
- **Controls a PC remotely.** An optional edge executor lets the always-on brain drive a laptop
  end-to-end (open apps, manage processes, run scripts) from your phone, over your Tailnet.

---

## Architecture

Two cooperating processes over one streaming WebSocket protocol:

```
┌──────────────── EDGE — laptop / phone / glasses (where you are) ────────────┐
│  Mic → wake word (openWakeWord) → VAD (Silero) → STT (Deepgram/Whisper)     │
│      → [BrainBridge] ──WebSocket──┐                                         │
│  Speaker ← TTS (ElevenLabs/Piper) ←┘   (barge-in, smart turn-taking, AEC)   │
└────────────────────────────────────────────┬────────────────────────────────┘
                          streaming StreamEvents │   (Tailnet + Bearer-token auth)
┌──────────────── BRAIN — always-on host / VPS (24/7) ───────────▼─────────────┐
│  Agent loop: own LLM (+fallback chain) · tool-calling · session + memory     │
│  Confirmation tier ENFORCED in code · smart idle session reset               │
│  Tools: vault(r/w) · web · telegram · gmail · calendar · notion · smart-home │
│         · utilities · system/PC-control · browser · reminders · coding/git · │
│  Memory L0–L5 · proactive tick (durably logged) · scheduler · audit · health │
│  HTTP sidecar: /talk (iPhone Siri voice) · /control (remote PC executor)     │
│  Optionally consults an external multi-agent fleet as ONE tool               │
└──────────────────────────────────────────────────────────────────────────────┘
```

- **Edge** (`src/afon/edge/`) keeps audio + STT/TTS **local** (privacy, low mic latency).
- **Brain** (`src/afon/brain/`) holds the 24/7 obligations (reasoning, memory, scheduler,
  channels) and can run in-process **or** as a shared WebSocket server (`brain/server.py`) so a
  laptop, an iPhone, and glasses all share **one** brain + memory.
- The Pipecat "LLM stage" is replaced by a thin **`BrainBridge`** processor, so swapping reasoning
  backends or adopting a different transport never touches the audio pipeline.

---

## 🚀 Quick start (5 minutes)

Requires **Python 3.11+** and [`uv`](https://github.com/astral-sh/uv).

```bash
git clone https://github.com/iamvazghen/OpenAfon openafon && cd openafon

# 1. Install the base + the stack you want (this also makes `afon-setup` available):
uv sync --extra edge --extra cloud-voice --extra brain --extra channels --extra identity --extra dev
#   ↳ for a 100% local/offline voice stack, drop `cloud-voice` and add `local-voice`.

# 2. Configure interactively — writes your .env:
uv run afon-setup

# 3. Verify everything (offline, no keys needed to pass):
uv run python bench/run_all_tests.py        # → all green

# 4. Talk to it:
uv run python -m afon.edge.assistant      # local voice loop on this machine
#   …or run the shared brain for phone/glasses:
uv run python -m afon.brain.server
```

`uv sync` **prunes** extras you don't list — install the full set you intend to use **in one go**.
The optional extras are: `edge` `cloud-voice` `local-voice` `brain` `channels` `browse` `identity`
`dev` (see [pyproject.toml](pyproject.toml) for what each pulls).

After the wizard, **[`TODO-NOW.md`](TODO-NOW.md)** is the step-by-step deployment checklist: every
one-time login/credential, voice enrollment, and the full real-device test plan.

---

## The setup wizard

`uv run afon-setup` (a Rich terminal UI; falls back to plain text on a bare install) walks a forker
through the decisions that matter and writes a ready `.env`:

1. **Identity** — display name + wake phrase (from the pre-trained set; custom phrases use Porcupine).
2. **Voice** — cloud (ElevenLabs + Deepgram, asks for keys) or local (Piper/Kokoro + Whisper/Moonshine).
3. **Brain** — LLM backend: a free OpenAI-compatible proxy, OpenAI, or local Ollama.
4. **Knowledge** — the path to your Obsidian/Markdown notes (long-term L3 memory).
5. **Deployment** — *single* machine (loopback) or *VPS* (binds `0.0.0.0`, auto-generates an auth token).
6. **Security** — auto-generates strong passwords for all eight privileged protocols.
7. **Integrations** — optionally wire Telegram, Tavily, Google, Notion, ntfy now (or later).
8. **Behaviour** — proactivity on/off; fleet consult stays off by default.

It **never prints a secret back**, backs up any existing `.env`, and preserves every inline comment
in `.env.example` for the knobs you didn't touch. Re-run it any time to reconfigure.

---

## Networking: the Tailnet requirement

OpenAfon is multi-device, and the secure way to connect a phone, a laptop, and glasses to the same
24/7 brain on a VPS is a **private mesh VPN** — a **Tailnet** ([Tailscale](https://tailscale.com),
WireGuard under the hood). **Install Tailscale and sign in on _every_ device** — the VPS/brain host,
your laptop, your iPhone, and (via its companion phone) the glasses. Then:

- Every device gets a stable `100.x.y.z` address reachable only inside *your* tailnet.
- The brain binds `0.0.0.0` but is only routable to your own devices — **never the public internet** —
  and is still guarded by the `AFON_API_AUTH_TOKEN` bearer the wizard generates.
- The edge connects to `ws://<brain-tailnet-ip>:8765/voice`; the iPhone Siri Shortcut posts to
  `http://<brain-tailnet-ip>:8766/talk`; the PC-control executor dials the brain's `/control` — all
  over the tailnet, no port-forwarding, no public exposure.

**Rule of thumb:** if a device should talk to Afon, it must be **on the tailnet and logged in**.
Off the tailnet, only the public fallbacks work (Telegram bot messages, ntfy push). See the docs
site's *Networking* page for the exact Tailscale steps.

---

## Devices

One brain, reached many ways — all sharing memory, all over the tailnet:

| Device setup | How it connects | Barge-in | Notes |
|---|---|---|---|
| **Laptop** (Windows/Linux, built-in mic/speakers) | `afon.edge.assistant` → brain WS | off (open speakers) | baseline local pipeline |
| **Mac** (macOS, built-in or external) | `afon.edge.assistant` → brain WS | off (open speakers) | same Python/PyAudio edge, runs natively |
| **Laptop/Mac + headphones** (AirPods/BT → host) | same, auto-routes to headphones | **on** (private) | interrupt mid-sentence |
| **iPhone** (no app) | "Hey Siri, Afon" → `POST /talk` | n/a | Siri dictation → spoken reply |
| **Android** (no app) | Assistant/Tasker → `POST /talk`, or Termux edge-lite | n/a | dictation → spoken reply; or full mic stream via Termux |
| **Phone + headphones** (AirPods/BT → phone) | same shortcut (`android-headphones`/`phone-headphones` hint) | **on** (private) | reply plays in the earbuds |
| **Mentra OS glasses** | TS bridge (`glasses/`) → brain WS | on | mic/speaker/display bridge |
| **Home Assistant** | brain → HA REST (local) | n/a | states + control (locks confirm-gated) |
| **Remote PC control** | host executor → brain `/control` | n/a | brain drives a laptop from anywhere |

Every setup is modelled in `src/afon/edge/device_profile.py` (`SUPPORTED_DEVICES`: laptop, **mac**,
iphone, **android**, airpods, mentra) and verified in `bench/test_phase6_multidevice.py`. Each has a
step-by-step acceptance test (TTFW numbers, auto-route, barge-in, Siri/Assistant voice, music room,
proactive voice, shared memory) in **[`TODO-NOW.md`](TODO-NOW.md) §3** and the docs site's *Devices*
page.

---

## Configuration

Everything is driven by environment variables (prefix `AFON_`) read from `.env`. See
**[`.env.example`](.env.example)** — every knob is documented inline. Nothing is hard-coded; the same
codebase runs CPU-local-only or cloud-quality just by flipping provider flags. **Never commit `.env`**
(it's gitignored). The assistant's *character* is `personality/afon.md`; its *knowledge* is the
Markdown under `memory/`; its *skills* are `skills/*.md` — all editable without touching code.

Notable knobs added for safety/memory:
- `AFON_VAULT_WRITABLE` — `true` only on the host that *owns* the vault; lets Afon save notes
  into it (`write_vault`). Off on the laptop (its mirror gets clobbered by the one-way sync).
- `AFON_SESSION_IDLE_RESET_MINUTES` — after this idle gap the brain journals the prior conversation
  and clears working memory, so stale context can't bleed into a fresh conversation hours later.

---

## Memory — six layers

| Layer | What | Where |
|---|---|---|
| **L0 Working** | the live conversation (rolling turns; smart-reset on long idle) | RAM |
| **L1 Learned** | durable facts it saves (`remember`/`recall`/`forget`) | `memory/learned/*.md` |
| **L2 Journal** | daily summaries + a durable log of proactive nudges | `memory/journal/*.md` |
| **L3 Vault** | your Obsidian knowledge base (read always; **write** on the authoritative host) | `AFON_VAULT_PATH` |
| **L4 Hot-cache** | fronts the slow paths (search, utilities) | in-process TTL + optional Redis |
| **L5 Semantic** | recall by *meaning*, not just keywords | optional local embedder |

L4/L5 are graceful accelerators: no Redis → in-process cache; no embedder → keyword recall. The
Markdown layers are always the source of truth.

---

## The tool belt

All tools live in `src/afon/brain/tools/` and self-degrade when unconfigured. Highlights:

- **Knowledge** — `search_vault`, `read_vault_note`, **`write_vault`** (save a note on the
  authoritative host), `web_search` (Tavily), `scrape_url` (Jina Reader — free, keyless),
  `browse_web` (Browserbase).
- **Memory** — `remember`, `recall`, `forget`, `read_journal`.
- **Channels** — `check_telegram`, `read_chat`, `mark_telegram`, `send_telegram`, `read_email` /
  `draft_email` / `send_email` (Gmail), `notion_search` / `read` / `append` / `comment` / `create`.
- **Calendar & home** — `list_events`, `create_event`, `set_home_location`; Home Assistant tools.
- **Utilities** — `weather`, `crypto_price`, `stock_price`, `fx_rate`, `news_brief`, `wiki_lookup`,
  `define_word`, `convert`.
- **Media** — `play_music` (YouTube Music, free; or stream into a Telegram voice chat), `stop_music`.
- **The machine** — `file_op`, `process_op`, `run_powershell` (incl. elevated), `browser`.
- **Proactive** — `set_reminder`, `list_reminders`, `cancel_reminder`, `send_push`.
- **Routines & health** — `routine` (briefing/focus/lockdown/guest/commute/panic/backup), `self_health`.
- **Self-improvement** — `read_source`, `write_source`, `run_tests`, `lint`, `git_status/diff/log`,
  `git_new_branch/commit/push/revert`, `list_skills`, `read_skill`.
- **The team** — `delegate_to_fleet` (gated off by default).

Outward-facing / destructive tools are in a **confirmation tier that is enforced in code**: the agent
holds the call, reads the action back, and runs it only after you say yes. Every tool call is written
to a redacted audit log (`audit/*.jsonl`).

---

## Proactive companion

A background tick weighs signals (routine, calendar, unread, open threads, self-health) and decides
whether to say something **unprompted** — within an **interruption budget** and **quiet hours**, so
it's helpful, never noisy. It speaks to a listening device or falls back to a Telegram voice note /
ntfy push when you're away. Crucially, **every proactive line is recorded** — into working memory
(so you can answer "yes, do it" right away) and into the L2 journal (so you can refer back days
later: "that thing you suggested last week"). Mute it on demand by voice: *"focus mode for an hour"*,
*"lockdown"*, *"normal"*, *"give me my briefing"*. Toggle with `AFON_PROACTIVE_ENABLED`.

---

## Self-improvement

Afon can improve its own codebase, with a hard safety rail: **every change is reversible and
verified.** Repo-scoped, secret-blocked file I/O; `run_tests`/`lint` before trusting a change;
**reversible-only git** (no reset/force-push/rebase/branch-delete — a revert is a new commit);
writes/commits/pushes are confirm-gated. A background "review" pass also distils durable facts from
conversations into L1 memory every few turns. Ask: *"read your self-improvement skill, then make
recall faster."* It branches, edits, tests, reads the change back, waits for your yes, commits — and
reverts cleanly if anything regresses.

---

## Testing & benchmarks

- **`uv run python bench/run_all_tests.py`** — the single gate. Each phase has a hermetic
  `bench/test_*.py` (offline, no real network/keys). Network/fleet tests report **SKIP** (not FAIL)
  when their backend is unreachable, so an offline run still passes.
- **`uv run python bench/efficiency_report.py`** — measures the hot paths (memory recall, cache, brain
  TTFT, full-turn latency, prompt size) against efficiency targets. See
  [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) and the audit at [`docs/AUDIT.md`](docs/AUDIT.md).

**Publish gate:** this repo stays **private** until the suite is green *and* the efficiency report
meets its targets. (Current state: suite green; one marginal item — streaming TTFT — tracked in the
audit.)

---

## Documentation site

A Next.js documentation site lives in **[`website/`](website/)** and is built to deploy on **Vercel**:

```bash
cd website
npm install
npm run dev      # local preview at http://localhost:3000
# Deploy: import the repo in Vercel, set Root Directory = "website" (framework preset: Next.js).
#   or:  npm i -g vercel && vercel --cwd website
```

It documents everything end-to-end: architecture, the setup wizard, the **Tailnet** requirement, all
device setups (Mentra OS, Home Assistant, laptop, laptop+headphones, phone, phone+headphones),
configuration, memory, security, and licensing.

---

## Deployment

1. Run `uv run afon-setup`, then work through **[`TODO-NOW.md`](TODO-NOW.md)** (Tailscale on every
   device, voice enrollment, VPS ticker, Google/Notion/Telegram logins, GitHub repo, optional
   Redis/embedder, proactive switch-on, and the real-device test plan).
2. `uv run python bench/run_all_tests.py` → all green; `efficiency_report.py` → targets met.
3. Run the **brain** as a service on an always-on host and the **edge** on your laptop (helper scripts
   in `scripts/`; a systemd unit pattern in `deploy/vps/`). The VPS ticker delivers recurring
   reminders even with the PC off.

---

## Security

Afon is powerful — it runs shell commands, drives a browser, sends messages, and edits its own
code. The full safety model (secret handling, **enforced** confirmation tier, protocol passwords,
self-improvement guardrails, fleet gating, speaker biometrics, audit log, the Tailnet posture, what's
kept out of git) is in **[SECURITY.md](SECURITY.md)**. **Read it before deploying**, and keep your
fork **private** if it carries personal `memory/` or persona content.

---

## Project layout

```
src/afon/        # package keeps the short internal name `afon`
  edge/            # voice pipeline: wake word, VAD, STT/TTS, device routing, barge-in, PC executor
  brain/           # the agent: LLM, memory, cache, semantic, proactive, audit, health, protocols
    tools/         # the tool belt (one module per capability; SCHEMAS + HANDLERS)
  shared/          # the edge↔brain WebSocket protocol
  setup_wizard.py  # `afon-setup` — interactive .env generator
personality/       # afon.md — who Afon is (system-prompt persona; edit to make it yours)
memory/            # what it knows (Markdown): about, projects, tools, learned/, journal/
skills/            # on-demand playbooks (self-improvement, architecture, python, pc-control, …)
clients/iphone/    # the phone client assets
glasses/           # Mentra OS bridge (TypeScript)
website/           # the Next.js documentation site (deploy to Vercel)
deploy/vps/        # the always-on recurring-reminder ticker
scripts/           # edge service install/uninstall helpers
bench/             # the test suite + benchmarks + one-time login helpers
docs/              # ROADMAP, EXPANSION-PLAN, BENCHMARKS, AUDIT, multi-device, TESTING
LICENSE · THIRD_PARTY_NOTICES.md · SECURITY.md · TODO-NOW.md · .env.example
```

---

## License

OpenAfon is released under the **[MIT License](LICENSE)** — free to use, modify, and redistribute,
including commercially. You don't "acquire" or pay for MIT; you just keep the `LICENSE` file (with its
copyright line) in copies of the code.

**Dependencies keep their own licenses.** Almost all are permissive (MIT / BSD / Apache-2.0 /
Unlicense). The **one** copyleft dependency is `py-tgcalls`/`ntgcalls` (**LGPL-3.0**), pulled in only
by the optional `channels` extra for streaming music into a Telegram voice chat — using it as an
unmodified `pip` library is compatible with shipping your own MIT code, and you can omit it for a
100%-permissive stack. The full breakdown plus cloud-service Terms is in
**[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)**.

> The name **OpenAfon / Afon** is the project's own. *Afon* is referenced only as the
> blueprint/inspiration and is not used as this project's brand. Before a public release, run
> `uvx pip-licenses --format=markdown` over your locked environment as a final check.

**Project & governance files** (all in the repo root):

| File | What |
|---|---|
| [`LICENSE`](LICENSE) | MIT license (the code grant) |
| [`NOTICE`](NOTICE) | attribution + naming notice |
| [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) | dependency licenses (incl. the one LGPL dep) + cloud ToS |
| [`ACCEPTABLE_USE.md`](ACCEPTABLE_USE.md) | responsible-use policy for an autonomous, tool-using AI agent |
| [`SECURITY.md`](SECURITY.md) | the enforced safety model + how to report a vulnerability |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | dev setup, the test gate, how to add a tool |
| [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) | Contributor Covenant v2.1 |

---

## Contributing

Issues and PRs welcome — see **[CONTRIBUTING.md](CONTRIBUTING.md)** for dev setup, the test gate, and
how to add a tool. By participating you agree to the **[Code of Conduct](CODE_OF_CONDUCT.md)** and the
**[Acceptable Use policy](ACCEPTABLE_USE.md)**. Before opening a PR: run
`uv run python bench/run_all_tests.py` (green) and `uv run ruff check src`; add a hermetic
`bench/test_*` check for any new capability. Security issues should be reported privately per
[SECURITY.md](SECURITY.md) §10, not as public issues.

---

<div align="center">
<sub><b>OpenAfon</b> — build your own Afon. A from-scratch, local-first companion framework;
Afon was the inspiration, not the implementation.</sub>
</div>
