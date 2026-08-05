# Watari & OpenWatari — Master Plan (single source of remaining work)

Date: 2026-06-24. **This file replaces all previous docs** (ROADMAP, FINISH-AND-OPENWATARI-ROADMAP,
AUDIT, PHASE-VERIFICATION, BENCHMARKS, TESTING, TASK-QUEUE-PLAN, EXPANSION-PLAN, multi-device, and the
`next-steps/` set: TODO-NOW, fine-tuning, WATARI-PRODUCTION-RECOMMENDATIONS, MY-WATARI-CHECKLIST,
IPHONE-WATARI-CHECKLIST). Everything from those that is **not verified done** is captured below; nothing
was dropped. Finished-and-verified history was intentionally not copied — only open work lives here.

Two tracks share one codebase:
1. **Watari** — Vazghen's personal 24/7 voice assistant (deploy + hardware acceptance).
2. **OpenWatari** — the same code as a public, BYO-key voice-agent framework (de-personalize + package).

**Ground rule (unchanged):** no task is "done" until a repeatable benchmark, test, or live-device check
backs it. Standing constraint: changes stay in the working tree for review — **do not commit/push**
unless explicitly asked.

**Verification commands**
- `uv run python bench/run_all_tests.py` — full hermetic suite + 1 online brain test (the gate).
- `uv run python bench/efficiency_report.py` — prompt/tool-surface/latency vs targets (informational).
- `uv run python bench/check_public_clean.py` — no secrets/private-hosts/personal-email in tracked files.
- `uv run python bench/test_live_integrations.py` — real API calls per configured integration.
- `uv run ruff check src bench` — lint gate.

**Latest measured state (2026-06-25):** aggregate **41 passed / 0 failed / 1 skipped** (gated fleet);
ruff clean; public-clean guard clean (now also enforces no owner-name/personal-tz in `src/`/templates);
efficiency report "all within target" (L1 ~3.5ms, TTFT 129ms warm, full-turn 2310ms, tool surface
46/48); live integrations **9 live / 0 failed / 1 not configured (HA)**; behavioral suite — **median-of-3
(objective) 80.5/100** (a single lucky run hits ~86; the median is the trustworthy number). Post-lever
categories: **Autonomy 100** (was 19), **Safety 89.5** (was 47), **Combination 81.5** (was 78),
Conversation 96.5, Honesty 85. The wall is now **live-API latency** on Channels (59.7) + Tasks (64.5) —
calendar/email/notion reads consistently exceed the 10–12s budgets (efficiency = 25% of every score).
That is the **faster-model lever (b)**, not a correctness gap; the code levers did exactly what they
targeted, consistently (safety_delete [100,100,100], work_on_task [93,100,100]).

**Behavioral hardening (2026-06-25):** two deterministic code levers (`bench/test_safety_autonomy.py`,
23/23): (1) **catastrophic-command refusal** — "delete C:\Windows\System32 / format c: / rm -rf /" is
refused BEFORE the model, no tool fired → `safety_delete` 25→**100**, Safety 47→**84.5**; (2)
**work_on_task intent routing** — "research X and write me a summary" gets a work nudge + forced tool so
the model hands off to the background worker → `work_on_task` 19→**69** (tool now fires), Autonomy 19→69.
Path to 90+: (a) **DONE** — multi-intent completion: a bounded forced retry when the model stops after
only one tool on a >1-part request, so `combo_web_memory` fires BOTH web_search+remember (regression in
`test_brain_voice_grade` [8]); (c) **DONE** — `behavioral_suite.py --median N` runs each scenario N times
and takes the median, de-noising the ±10 run-to-run swing so the grade is objective. (b) **NOT code, owner
call** — a faster/stronger primary model is the only remaining lever for the efficiency axis (25% of every
score; many turns exceed the 7–14s budgets on the free Groq-proxy). I deliberately did NOT loosen the
latency budgets to inflate the score — that would be gaming the grade, not earning it. Crossing 90 reliably
requires a paid/faster model tier; the capabilities themselves are production-grade.

**Partial code mitigation for (b) — channel-read direct-speak (flag, default OFF).**
`JARVIS_DIRECT_SPEAK_CHANNEL_READS` extends the single-tool short-circuit to SHORT calendar/email/
Notion-tasks/Telegram reads (speak verbatim, skip the summary LLM pass, ~1-3s faster on the exact turns
that drag Channels/Tasks); long multi-item dumps still summarise (length cap `_CHANNEL_DIRECT_MAX=360`).
It's OFF by default because it trades the summary's polish for speed — a deliberate A/B knob, not a
default, so we don't game the grade. Hermetic test in `test_brain_voice_grade` [9].

**Session 2026-06-25 — completed code work:** Phase 5.1 de-personalization DONE+enforced (tz + owner
name + guard); brain polish 3.5/3.6/3.7 done (3.2/3.4 evaluated, see Part 3); Phase 3.1 autonomous
backlog routine BUILT + tested (`brain/backlog.py`, scheduler cron, opt-in); Composio integration
decided = via existing MCP client (see Part 7), documented in `.env.example`. Speaker-ID gate verified
LIVE. All in the working tree, not committed/pushed (standing rule).

---

## Part 1 — `.env` key audit (wired? useful? tested?)

Every key currently in the personal `.env`, what it powers, and its live status. "Tested live" = exercised
by `bench/test_live_integrations.py` on 2026-06-24 unless noted.

| Key(s) | Powers | Wired in code | Live status |
|---|---|---|---|
| `FREELLMAPI_*`, `GROQ_API_KEY`, `LLM_PRIMARY_MODEL`, `LLM_FALLBACK_MODELS` | Brain reasoning (primary `groq:llama-3.3-70b-versatile` + provider-diverse failover) | `brain/llm.py` | ✅ used live (brain answers; TTFT ~128ms warm) |
| `DEEPGRAM_API_KEY` / `_MODEL` / `_LANGUAGE` (+ bare `DEEPGRAM_API_KEY`) | STT (nova-3, multi) | `edge/stt.py` | ✅ key present; exercised by voice path (no standalone live row) |
| `ELEVENLABS_API_KEY` / `_VOICE_ID` / `_MODEL` / `_STREAMING` (+ bare) | TTS (Watari voice) | `edge/tts.py` | ✅ key present; exercised by voice path |
| `REDIS_URL` | L4 hot-cache across restarts | `brain/cache.py` | ⚠️ **configured but Redis not running on this box right now** → graceful in-process fallback. Start the local Redis (or accept in-process). |
| `BRAIN_HOST/PORT`, `BRAIN_WS_URL`, `API_AUTH_TOKEN`, `PC_CONTROL_URL` | Brain WS server + edge↔brain + PC control | `brain/server.py`, `edge/*` | ✅ wired (remote brain mode) |
| `TAVILY_API_KEY` | `web_search` primary | `tools/web.py` | ✅ live (answered "capital of France") |
| `JINA_API_KEY` | `scrape_url` (keyless primary; key raises limits) | `tools/web.py` | ✅ live (scraped example.com) |
| `BROWSERBASE_API_KEY` / `_PROJECT_ID` | `browse_web` cloud browser | `tools/browser.py` | ✅ live (browsed example.com) |
| `NOTION_TOKEN`, `NOTION_TASKS_DB_ID` | Notion read/write/tasks | `tools/notion.py` | ✅ live (search + tasks bucketed) |
| `GOOGLE_CLIENT_ID/SECRET/REFRESH_TOKEN` | Gmail + Calendar | `brain/google.py`, `tools/gmail.py`, `tools/calendar.py` | ✅ live (read 3 messages; calendar listed) |
| `TELEGRAM_API_ID/HASH/SESSION/PHONE`, `_DEFAULT_CHAT`, `_PLAYLIST_CHAT`, `_MUSIC_*` | Telethon user client (read DMs, playlist, Music Room stream) | `tools/telegram.py`, `tools/voicechat.py` | ✅ live (read returned cleanly) |
| `TELEGRAM_BOT_TOKEN` | OpenClaw fleet bot (reserved) | fleet | ⚠️ reserved; not the Watari bridge |
| `TELEGRAM_BRIDGE_BOT_TOKEN` | **Dedicated 24/7 Watari DM bot** (@watari_iamvazghen_bot) | `brain/telegram_bridge.py` | ◻️ verify the bridge poller is live on the VPS brain |
| `NTFY_TOPIC` / `_SERVER` | Phone push fallback | `tools/notify.py` | ✅ live (push delivered) |
| `TICKER_URL` / `_TOKEN` | VPS ticker for recurring PC-off reminders | `brain/scheduler.py` | ◻️ **verify the ticker is actually deployed + reachable on the VPS** (see Part 4) |
| `VAULT_PATH`, `AUDIT_LOG_DIR` | L3 Obsidian memory + audit JSONL | `brain/context.py`, `brain/audit.py` | ✅ live (241 notes matched 'project') |
| `WAKE_WORD_*`, `PORCUPINE_ACCESS_KEY` (blank) | Wake word ("hey jarvis" via openWakeWord) | `edge/wake_word.py` | ✅ loads; **custom "watari" model still TODO** (Part 3) |
| `SPEAKER_ID_ENABLED=true`, `SPEAKER_THRESHOLD` | Speaker biometrics gate | `edge/speaker_id.py`, `speaker_gate.py` | ✅ **LIVE** — `voiceprint.json` enrolled (192-dim, L2-normalized), ECAPA backend installed; verified cosine(you,you)=1.000→accept, stranger 0.185→reject. Live mic test on device remains (Part 4.8). |
| `OPENCLAW_*`, `FLEET_AUTHORIZED=false` | Fleet consult via ispir | `brain/fleet.py` | ◻️ off by design; gateway live but un-armed |
| `PROTOCOL_*_PASSWORD` (×8) | Password-gated protocols | `brain/protocols.py` | ✅ set (Armenian-city passwords) |
| `HOME_LOCATION`, `OFFLINE_STT/TTS_ENABLED=false`, `BARGE_IN_ENABLED=false`, `BRAIN_MODE=remote` | Runtime prefs | various | ✅ wired |
| `GITHUB_REPO`, `GITHUB_TOKEN` | Self-improvement git push | `tools/coding.py` | ⚠️ **verify PAT has Contents: Read+Write** (push blocked otherwise) |
| `USER_NAME=Vazghen`, `USER_ADDRESS=Sir`, `UNDERSTOOD_LANGUAGES`, `REPLY_LANGUAGE`, `OPENCLAW_CLI_SSH_TARGET` | Identity personalization layer | `brain/context.py`, `config.py` | ✅ wired (framework defaults stay generic) |

**`.env` action items**
- **A1.** Start local Redis or accept in-process cache (it's only a cross-restart speed win). *Low.*
- **A2.** Confirm the dedicated Watari DM bridge bot is polling on the VPS brain (not just configured).
- **A3.** Verify the VPS ticker deploy is live and `TICKER_URL` reachable (recurring PC-off reminders).
- **A4.** Confirm the GitHub PAT scope is Contents: Read+Write (self-improvement push).
- **A5.** ✅ DONE — voiceprint enrolled + verified; ECAPA backend installed; gate is LIVE (rejects non-enrolled voices). Live mic confirmation on device remains.
- **A6.** Key hygiene (optional): Tavily/Browserbase/Deepgram keys arrived via chat in plaintext — rotate.

---

## Part 2 — OpenWatari framework readiness (active code work)

This is the main remaining **code** track: make a fresh public clone useful and safe for a non-Vazghen user.

### 2.1 Deep de-personalization (Phase 5.1 — DONE 2026-06-24)
- **Completed this session:** `user_tz` setting added (default UTC; `.env` sets Europe/Berlin) and wired
  across 8 files (agent/proactive/modes/scheduler/routines/calendar/notion); owner name "Vazghen"
  genericized to "the owner" across 39 `src/` files (103 occurrences) incl. all model-facing tool
  descriptions; `get_time` description de-hardcoded; `check_public_clean.py` extended with path-scoped
  name+timezone checks on `src/`/`personality/`/`memory/*.example.md` (tests + docs exempt) and passes
  clean. Re-verified: ruff clean, guard clean (198 files), affected suites green. Historical detail below.
- **Done:** private memory is an overlay (gitignored real files + tracked `*.example.md`); the VPS/Tailscale
  IP scrubbed from all tracked files (`verify_remote_brain.py` reads `JARVIS_BRAIN_WS_URL`); personal email
  genericized; `bench/check_public_clean.py` guard added + wired into the suite (passes clean).
- **Remaining:** the owner name **"Vazghen"** (~114 occurrences across ~41 `src/` files) and the hardcoded
  timezone **"Europe/Berlin"** (8 files) are still in runtime strings — model-facing tool descriptions
  ("Confirm with Vazghen first", "on Vazghen's PC"), the `get_time` description, and `USER_TZ` constants.
  - **Plan:** add a `user_tz` setting (default "UTC"; personal `.env` sets Europe/Berlin); replace
    `ZoneInfo("Europe/Berlin")` with `ZoneInfo(settings.user_tz)` in `agent.py`, `proactive.py`, `modes.py`,
    `scheduler.py`, `routines.py`, `tools/calendar.py`, `tools/notion.py`. Genericize model-facing strings
    to "the owner" (persona/system prompt already carries the real identity, so personal instance is
    unaffected). Extend `check_public_clean.py` to also flag the personal name in `src/`/`personality/`/
    `memory/*.example.md` (author-attribution files allowlisted), making it an enforceable gate.
  - **Verify:** prompt-affecting, so re-run `run_all_tests.py` + `test_brain_voice_grade.py` +
    `test_phase11_notion.py` + `efficiency_report.py` after; the public-clean guard must stay green.
- **Also (cosmetic, from old AUDIT #9):** stray "Jarvis"/"Vazghen" in log/journal strings; same pass.
- **Honorific:** `", sir."` is hardcoded across ~40 spoken-string sites; the framework default
  `user_address` is "". Decide whether to parameterize (template from `settings.user_address`) or leave the
  personal instance's "sir" as-is and only fix for the framework. *Medium; prompt/string-affecting.*

### 2.2 OpenClaw optional & clean (Phase 5.2 — DONE)
- `openclaw_gateway_url` defaults to "" and `fleet_authorized` to False; a clean install never contacts
  private infra; `delegate_to_fleet` degrades to a spoken "the bridge isn't configured." Optional polish:
  a formal plugin/provider transport boundary for fleet transports (not required).

### 2.3 Harden setup wizard for strangers (Phase 5.3)
- `jarvis-setup` must start from a blank machine and ask: assistant name, owner name, local/remote brain
  mode, LLM provider, STT/TTS providers, wake word, vault path, optional integrations. Public providers
  first (Ollama, OpenAI-compatible, Groq, OpenAI, local voice). Generate strong protocol passwords + API
  auth token. Seed generic memory/persona templates.
- **Acceptance:** on a fresh checkout, `jarvis-setup` writes a working `.env` with no private assumptions;
  the offline suite passes afterward. **Verify** via `bench/test_setup_wizard.py` (extend it).

### 2.4 Provider-agnostic LLM story (Phase 5.4)
- Default framework docs to BYO OpenAI-compatible endpoint or Ollama; treat freellmapi as one optional
  endpoint, not the public default. Add provider health checks to setup.
- **Acceptance:** a new user picks local Ollama or their own endpoint without editing code.

### 2.5 Clean-checkout test profile (Phase 5.5 — PARTIAL)
- Done: the public-clean guard + offline-tagged suite. Remaining: a formal "no credentials" CI mode that
  runs with **no** `.env`/sessions/voiceprint/vault/keys and proves every external tool self-degrades; keep
  personal live-integration tests separate from public regression tests.
- **Acceptance:** CI green on a bare checkout.

### 2.6 Packaging & release (Phase 5.6)
- Confirm wheel/sdist build; add versioning + CHANGELOG; publish install + architecture docs; add a
  security policy for the high-power local tools (shell/file/browser/protocol/MCP/fleet).
- **Acceptance:** a public user can install, run setup, start edge/brain, and complete one voice/text turn.

### 2.7 Publish gate (Phase 6 — meta)
Open the repo only after: Watari personal gates green or separated from framework gates; all private
overlays gitignored and absent from tracked files; clean-checkout tests pass with no credentials; docs
don't promise unbuilt features; benchmarks show current numbers; security review updated for browser/
shell/file/protocol/MCP/fleet tools. **Decision still open:** accept streaming TTFT goal-with-tolerance
(~1.3s historical vs 1.2s target; now ~128ms warm on Groq-direct, so likely resolved) or hold.

---

## Part 3 — Brain & behavior polish (code, model-agnostic)

Items from the behavioral audit + production recommendations not yet verified done. Re-run
`bench/behavioral_suite.py` after each; bars should climb.

- **3.1 `work_on_task` wired to the backlog.** The bounded worker exists (`brain/worker.py`,
  `test_work_on_task.py`), but a **daily routine** that pulls overdue+inbox Notion tasks and auto-attempts
  the safe ones (research/draft/summarize), leaving a Notion comment (outward actions still confirm-gated),
  is **not built**. *(scheduler.py / proactive.py)*
- **3.2 Adaptive `max_tool_iters`.** Default 1–2 iters; raise to 4 only on genuine multi-step intent (the
  multi-intent detector can signal it). Stops weak models burning iterations. **Verify it's implemented**;
  if not, add. *(agent.py)*
- **3.3 First-token failover window tuning under load.** Tune `llm_first_token_timeout_seconds` so a
  stalled model is abandoned fast; with the warmup probe this removes the 20–30s outliers. *(llm.py)*
- **3.4 Startup native-tool-call probe + chain self-pruning.** Warmup sends each chain model a tiny
  tool-bearing request; deprioritize any that answer with text instead of a native `tool_calls`. **Verify**;
  partial work may exist in `warmup()`. *(llm.py)*
- **3.5 `scrape_url` never confirm-gated.** It's read-only; make "read me that page" one step (no "shall I
  open it?"). *(web.py / agent.py confirm heuristic)*
- **3.6 Sentinel replies for empty results.** `recall` returns "I don't have anything on that, sir";
  reminders return a fully-formed confirmation the agent speaks verbatim — so a weak model can't dress an
  empty result as a guess. *(memory.py, reminders.py)* *Low.*
- **3.7 Cancelled-mid-stream turn (old AUDIT #7).** A streaming turn cancelled by a proactive interjection
  can leave a user message with no assistant reply → two consecutive user turns next time. Append a short
  placeholder assistant turn on cancel. *Low.* *(agent.py)*
- **3.8 Per-device / per-speaker sessions (old AUDIT #6).** The shared brain uses one history for all
  devices (enables cross-device follow-ups, but two unrelated conversations interleave). Smart idle reset
  limits staleness. True per-connection/per-speaker sessions = a larger change; **by-design for now**.
- **3.9 Behavioral suite hardening.** Median-of-3 + `--ci --floor` are done. Remaining: an `answered_by`
  column (needs per-turn telemetry), and add scenarios for fleet delegate, `work_on_task`, multi-device
  handoff, more safety tools (telegram send / file delete / calendar delete), and a latency-only regression
  (assert P50 < target). *(behavioral_suite.py)*
- **3.10 Fleet live test (un-skip when sanctioned).** New `bench/test_fleet_live.py`: when delegation is
  enabled and a CLI path is reachable (local `openclaw` on the VPS, or `ssh <target> openclaw --version`),
  send ispir a trivial brief and assert a non-empty answer; SKIP only when genuinely unreachable. Add an
  opt-in `prefer_cli`/`JARVIS_FLEET_TRANSPORT=cli`; decouple the test gate from the runtime session gate;
  then assert stream events surface as spoken progress + a backgrounded delegation lands in the TaskQueue.
  *(brain/fleet.py, bench/run_all_tests.py)*
- **3.11 Known model-quality limitation (no code fix).** A weak model still occasionally over-calls a tool
  on trivial turns (`2+2` picks a dictionary tool). Forcing isn't applied here on purpose; the fast Groq
  path makes the wasted call cheap. Documented, accepted.

---

## Part 4 — Watari personal deployment (Vazghen + hardware)

These finish the personal instance; most need Vazghen, an SSH session, or the physical devices.

**Deploy/refresh the live brain (do first)**
- **4.1** Upgrade the GitHub PAT to Contents: Read **and** write, then push the committed framework work.
- **4.2** **Redeploy the brain to the VPS** — it runs older code, MISSING: provider warm-up (TTFT), the
  background task queue, the recurring/deadline morning briefing, cloud→local voice fallback, and
  config-driven identity. Convert `~/jarvis` (tarball) → a `git clone`, then `bash deploy/vps/install-brain.sh`.
- **4.3** Add identity keys to the VPS `.env` (`USER_NAME`, `USER_ADDRESS`, `UNDERSTOOD_LANGUAGES`,
  `REPLY_LANGUAGE`; verify `GROQ_API_KEY` + `NOTION_TASKS_DB_ID` survive the re-clone).
- **4.4** Restart the laptop edge (`JarvisEdge` task) so it loads the new `build_tts` + cloud→local fallback
  + wake-ack.
- **4.5** Deploy the **VPS ticker** if not already live: `scp -r deploy/vps …`, run `install.sh` with the
  ntfy topic + `TICKER_HOST=0.0.0.0` + token; point laptop `TICKER_URL`/`TICKER_TOKEN` at it. Confirm a
  recurring reminder fires with the laptop **off**.

**Voiceprint & devices**
- **4.6** Enroll the voiceprint: `uv sync --extra identity` → `uv run python bench/enroll_voice.py --script
  "to-read-script.md"` (3-min) → confirm `voiceprint.json`. Speaker-ID then gates to Vazghen's voice.
- **4.7** iPhone Siri path: install Tailscale on the phone; build the **Watari** Shortcut (Dictate →
  POST `/talk?token=…` → play audio); **update host to your brain's Tailscale IP** (current brain); if it
  can't connect, add the Windows firewall rule for TCP 8765,8766.
- **4.8** Live hardware acceptance (the single whole-system test, ~45 min): laptop mic+speaker baseline;
  laptop+AirPods (auto-route + barge-in on + disconnect fallback); iPhone Siri (voice reply, no page);
  iPhone+AirPods; VPS→laptop control from the phone (list/open/file/kill incl. elevated); Music Room live
  videochat stream; proactive voice note to the phone; shared memory across devices; morning briefing
  (overdue+today+week+recurring); recurring reminder with PC off; Notion tasks by voice; record **TTFW**
  means for laptop + headphones (target ≲1.2s) and a **VAQI** baseline.

**Parked on hardware purchase**
- **4.9** Mentra OS glasses — bridge + routing built (`glasses/`, `device_id="mentra"`); remaining is
  hardware-only: `npm install`, register in the MentraOS console, wire the SDK transcription stream to
  `sendUtterance()`, run a live on-device round-trip.
- **4.10** Home Assistant — `tools/smarthome.py` built and self-degrading; set `JARVIS_HA_URL` +
  `JARVIS_HA_TOKEN` once a hub exists, then verify a live device action.

**Optional personal enhancements**
- **4.11** Fully-local voice run (`STT=whisper`, `TTS=piper`, offline flags) — one 100%-offline spoken turn.
- **4.12** Self-improvement from the VPS — one controlled run (branch → edit → tests → confirm → push).
- **4.13** Semantic memory — `uv pip install sentence-transformers` (recall by meaning; ~90 MB first use).
- **4.14** Tune wake threshold / barge-in mode / proactive budget to taste.

---

## Part 5 — Live verification & benchmarks still pending

The hermetic suite proves each piece degrades gracefully in isolation; these prove the **live** experience.

- **5.1 TTFW + VAQI on real hardware** — `bench/voice_live_bench.py` is built (instant perceived ack;
  VAQI ~45 with the tuned chain). Needs a real mic/speaker 10-utterance run for true numbers (Part 4.8).
- **5.2 Barge-in / AEC live check** — interrupts on headphones; no self-interruption on speakers. Hardware.
- **5.3 Live integration happy-paths with writes** — extend `bench/test_live_integrations.py` behind
  `--allow-writes` (one real write per integration against a scratch target). Today reads are green (9/0/1).
- **5.4 Offline (local-first) loop** — `bench/voice_offline_check.py` confirms local engines construct; a
  true network-disabled mic→speaker turn on the device is the remaining proof.
- **5.5 24/7 deployment proof (on the VPS)** — `/healthz` OK, `systemctl kill jarvis-brain` auto-restarts
  within ~5s, reminder fires with the laptop off, edge auto-reconnects after a brain restart, the daily
  04:00 memory-hygiene job leaves `memory/learned/archive/` + `memory/journal/archive/`.
- **5.6 Forced-provider-failure run (Phase 2.1 acceptance)** — disable the first N models and confirm a turn
  fails over to a healthy provider in <500ms P50 without waiting through N timeouts.

---

## Part 6 — Documentation website (Next.js, `website/`)

- **6.1 UI/UX polish — scrollbar + font.** Improve the docs site's reading experience (custom scrollbar
  styling, font/typography pass). *Tooling to use is TBD — see open question about the "hallmark" plugin;
  `ui-ux-pro-max-skill` is the installed UI/UX plugin and the likely intended tool.* Content is hardcoded
  in `.tsx` (no markdown build dependency), so the docs files can be consolidated/deleted independently.
- **6.2 Reflect the master plan.** The site should describe **framework setup** (BYO-key, local/remote
  brain), not Vazghen's private deployment. Surface the consolidated remaining-work view if a status page
  is wanted.
- **6.3 Dependency risk.** `npm audit` reports 5 website vulns; the offered fix jumps to **Next 16** — treat
  as a separate, deliberate framework upgrade, not a lint fix.

---

## Part 7 — Composio integration (decision pending — see chat analysis)

Evaluate routing web-enabled integrations through **Composio** (a managed tool/auth provider with hundreds
of pre-built, OAuth-handled app integrations) **via the existing MCP client** (`brain/mcp_client.py`),
keeping Telegram (Telethon) and the OpenClaw VPS as native. Composio exposes an MCP server, so no new agent
code is needed — only config. **Recommendation (summary):** adopt it as an **additive breadth layer**
behind MCP for apps Watari doesn't already have first-class (Slack, GitHub issues/PRs, Linear, Jira,
Google Drive/Docs/Sheets, Outlook, HubSpot/CRM, Calendly, etc.) and to offload OAuth refresh — but **keep
the hand-written, latency-tuned tools** for the hot paths already live and fast (Gmail, Calendar, Notion,
web search/scrape, utilities). Do **not** route everything through Composio: it adds a network hop + a
third-party dependency + per-call latency that would hurt the voice-grade hot path, and it can't replace
Telegram-user-client DM reading or the fleet. **Status: analysis only, not yet implemented.** If approved,
the build is: add a `composio` MCP server entry to `JARVIS_MCP_SERVERS` with the Composio API key, pick the
specific app toolkits to enable, namespace them `mcp__composio__*`, and behavioral-test a couple end to end.

**Framework/language decision (for the Composio playground guide): OpenAI (Agents / tool-calling) provider
+ Python.** Watari is Python and its LLM chain (Groq, freellmapi, Mistral) is OpenAI-compatible and already
emits OpenAI-format `tool_calls`. Composio's OpenAI provider (`composio_openai`) returns tools in that exact
function-calling schema, so they drop into the existing registry/agent loop with the least adaptation — same
shape as `brain/tools/*.py` SCHEMAS and `mcp_client.to_openai_schema`. Avoid **Vercel AI SDK** (TypeScript —
wrong language for our Python brain) and **Anthropic Agent SDK** (formats tools in Anthropic's schema; we
don't use Claude as the brain). If a plain **MCP** guide is offered, that's even better (zero new deps via
the existing `brain/mcp_client.py`); among the three listed, pick **OpenAI + Python**.

**Chosen implementation (2026-06-25): Composio-via-MCP, not the SDK Runner.** On reviewing the fetched
OpenAI-Agents-SDK guide, its example spins up a *separate* `Agent`/`Runner` — a second agent loop that
would bypass Watari's voice-tuned, confirm-gated, streaming loop. That's the wrong shape for us. Watari
already speaks MCP over stdio (`brain/mcp_client.py`, tested 9/9), and Composio exposes a hosted MCP
server, so the integration needs **zero new code/deps**: create a Composio MCP server in their dashboard,
connect the app accounts there, and add one entry to `JARVIS_MCP_SERVERS` (documented in `.env.example`,
bridged to stdio via `npx mcp-remote <url>`). Composio tools then join the existing registry as
`mcp__composio__*`, advertised per turn, with the confirm-tier still gating outward actions. Go-live steps
(owner): (1) get a Composio account + API key; (2) in the dashboard pick toolkits (GitHub/Slack/Drive/
Linear/…) and connect those accounts; (3) generate the MCP server URL; (4) paste the `JARVIS_MCP_SERVERS`
line; (5) restart the brain and confirm `MCP server 'composio': exposed N tool(s)` in the log. The
SDK-provider path (`composio_openai`, registering tools without the Runner) remains a fallback if a
non-MCP route is ever needed, but is not required.

**Accounts tested (2026-06-25): 17/17 ACTIVE.** `bench/test_composio_accounts.py` (key in gitignored
`.env` as `JARVIS_COMPOSIO_API_KEY`; `composio_api_key` setting added) enumerates every connected account
+ live status; real execution proven end-to-end (`GITHUB_GET_THE_AUTHENTICATED_USER` → returned the
owner's GitHub profile). Connected + active: github (846 tools), stripe (425), slack (158), supabase
(116), googledrive (77), gmail (61), youtube (48), googlesheets/googlecalendar (45), linear (46),
googledocs (41), coinbase (28), airtable (24), google_maps/linkedin/reddit (20-22), instagram. **Tool-
count warning confirmed:** github 846 / stripe 425 / slack 158 — exposing raw tools per-turn would blow
the ≤48 budget, so the live wiring MUST use Composio's **Tool Router** (semantic search/execute meta-
tools). REMAINING to make them usable in Watari's voice loop: create the Tool Router MCP server in the
Composio dashboard, paste its URL into `JARVIS_MCP_SERVERS`, then `bench/test_composio_connect.py` lists
the routed tools and a live round-trip confirms. (gmail/googlecalendar are also native in Watari — prefer
native for those; Notion stays native via its integration key.)

---

## Part 8 — Deferred / scale-only / explicitly skipped

- **8.1** ~~Custom **"hey watari" wake-word** openWakeWord model~~ — DONE 2026-07-01: `watari.onnx` and
  `hey_watari.onnx` are trained and live in `.wakewords/`, both listed in `JARVIS_WAKE_WORDS`. Trained
  out-of-repo with openWakeWord's pipeline under WSL — there is no `bench/train_wakeword.py`, and this
  line used to claim there was.
- **8.2** iPhone **mic over HTTPS** (in-browser capture): `tailscale serve` routes (tailnet-only, additive)
  → brain HTTP/WS; rewrite the client to capture via `getUserMedia`+`MediaRecorder` and POST audio to a new
  brain `audio` handler running Deepgram REST STT; build `wss://<tailnet-host>/voice`. Needs the phone.
- **8.3** **"Clean up Task Manager"** skill — a curated `process_op` helper that surfaces idle/duplicate/
  heavy processes as safe-to-kill candidates and confirms before killing.
- **8.4** **Maps/transit** tool ("how long to the airport") — optional Jarvis-flavored utility.
- **8.5** **Android (Termux) edge-lite** — stripped edge (mic → brain WS → TTS) reusing the protocol;
  `device_id="android"`. Later.
- **8.6** **Neo4j graph memory (L5b)** — associative recall over a knowledge graph; only once flat Markdown
  memory is demonstrably the bottleneck. Heavy (2 GB heap).
- **8.7** **Real vector store** (LanceDB / sqlite-vss / Chroma) behind L5 semantic recall — only when the
  learned-facts count is large. In-process embedder is fine now.
- **8.8** **Observability** (structured metrics → small dashboard) for the 24/7 service. Per-turn telemetry
  (3.9 `answered_by`) is the cheap first step.
- **8.9** **n8n** workflow automation — **SKIP** (overlaps Phase 4 scheduler/tools/proactive). Retire the
  legacy container.
- **8.10** **LangChain/LangGraph as the core agent — SKIP.** The voice-tuned streaming/confirm-gated loop
  stays primary. Reconsider LangGraph **only** if `work_on_task` grows into a real branching state machine.
- **8.11** **Legacy cleanup** — the old `D:\JARVIS`/`C:\openjarvis` Docker containers point at the abandoned
  project; stop/remove them.

---

## Execution order (recommended)

1. **Finish OpenWatari de-personalization (2.1)** — the active code task; gate it with the extended
   public-clean guard; re-test behaviorally.
2. **Brain polish quick wins (3.1–3.7)** — backlog worker routine, iter cap, failover tuning, scrape
   no-confirm, sentinels, cancel-placeholder.
3. **Framework packaging (2.3–2.6)** — wizard, provider-agnostic, clean-checkout CI, wheel/changelog/docs.
4. **Personal deploy (4.1–4.8)** — PAT+push, VPS redeploy, ticker, voiceprint, iPhone host, hardware
   acceptance + TTFW/VAQI.
5. **Live verification (5.x)** — writes, offline loop, 24/7 proof, forced-failover.
6. **Composio (7)** — if approved, additive MCP breadth layer.
7. **Website (6)** + **publish gate (2.7)** — last.
8. Deferred (8.x) as wanted.
