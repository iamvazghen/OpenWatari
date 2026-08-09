# OpenAfon / Afon — Development TODO

**Goal:** behavioral production-readiness **≥ 95/100 overall (no category < 90)** and **all 22 subsystems
genuinely Strong** — objectively, from real test/benchmark runs, never a relabel.

Priority: **P0** = blocks the 95+/all-Strong goal · **P1** = clear win · **P2** = polish.
Each item ships → runs its test → re-runs the behavioral audit on the VPS → re-grades → only then next.

**Current state (2026-07-24, real runs):** all 7 roadmap phases shipped. **Workstream 0 (B1–B4 + tool-tier
+ B5-lite) DONE; ALL of Part C (C2–C7) DONE; ALL Part G owner tasks DONE.** Full hermetic suite **93/0/1**
(9 new tests). Behavioral audit (real MiniMax on the deployed VPS brain), clean median-5 after **B5 thinking-tier**:
**84.9** (up from 74.8 baseline; +10). **Tasks 96.5, Safety 96.5, Proactivity 93, Channels 100,
Autonomy 100, Honesty 100, Conversation 96.5.** B5 (escalate a dodged forced tool to MiniMax-M2.5
reasoning) lifted Tasks/Proactivity/Safety/Memory by firing the arg-bearing tools the fast models missed.
**Google OAuth verified ALREADY WORKING on the VPS** (real email/calendar) — the old "unconfigured" note
was stale. The residual gap to 95 is now:
- **Scorer artifacts (~2):** `time`=50, `define`=50 — Afon answers CORRECTLY inline; the scorer only
  credits a fired tool. Not fixable without special-casing (declined as gaming). A right answer scored as a miss.
- **Multi-intent combos (Combination 51.5):** `combo_time_memory`/`combo_web_memory` need BOTH tools in
  one turn — the one remaining REAL lever (multi-intent completion hardening). ~4 pts.
Remaining external: **C1 Home Assistant token only** (Google done); 2 live checks (G3 threshold, C3 by ear).

Strong today (protect from regression): LLM · Memory-setup · Tools-setup · Voice pipeline · STT ·
Recoverability · Proactivity · Perception/vision · Brain · Tools-util · Personality · Multi-device ·
Memory-util · Efficiency-bench · Production-behaviour.

---

## Part A — Core diagnosis (one lever moves most of the benchmark)

The 74.8 is **not** mostly integrations. Turn-by-turn, the dominant failure is the **non-thinking primary
(MiniMax-Text-01) mis-selecting tools or fabricating** — the tools were mostly reachable:

| Scenario | Expected | Model did | Cause |
|---|---|---|---|
| "What's on my calendar today?" | `list_events` | fired **`get_time`** | wrong-tool selection |
| "Do I have new emails?" | `read_email` | fired **`get_time`** | wrong-tool selection |
| "Unread Telegram messages?" | `check_telegram` | **nothing**, said "You have 5 unread" | **fabrication** (tool WAS reachable) |
| "What's overdue on my task list?" | `notion_tasks` | fired **`list_tasks`** (local queue) | local-vs-external ambiguity |
| "Remember my flight is July 3rd" | `remember` | **narrated**, no tool | narration not call |
| "Define X" | `define_word` | answered inline | narration not call |
| "Send email … 'hello'" | held for confirm | echoed request, no `send_email` | wrong-tool + no confirm |

Live VPS check: **Notion + Telegram reachable** (token present) → those are pure model behaviour.
**Calendar + Gmail genuinely unconfigured** (no Google OAuth) → but the model made it worse by firing
`get_time` instead of calling the tool and degrading honestly.

**Implication:** one deterministic mechanism — *force the right tool and narrow the tool set on
high-precision intents* — lifts **Memory, Tasks, Channels, Time/Utility, Safety, Combination at once**,
and kills fabrication. That is Workstream 0 and it is by far the highest ROI.

---

## Workstream 0 — Tool-call reliability (P0, cross-cutting, do FIRST)

Existing `_wants_forced_tool` + `force_first` already force *some* commands, but don't cover
calendar/email/telegram/notion/define/memory and don't **narrow** the tool set on force.

- [x] **B1 · Intent→forced-tool-group router (P0).** `intent_router.py` — high-precision regexes narrow to
      the one right tool + `tool_choice=required`. Test: `bench/test_intent_router.py` (66/66).
- [x] **B2 · Local-vs-external disambiguation (P0).** Canonical "my task list" = **Notion** (`notion_tasks`),
      routed ahead of the local queue in the router. Reminders keep the local queue.
- [x] **B3 · Anti-fabrication hard stop (P0).** Narrowed live-data intent that fires 0 tools → honest
      degrade (`_should_degrade`, knowledge tools exempt). Tested.
- [x] **B4 · Narration safety net (P1).** Forced pass with no tool → one retry past the primary onto the
      reliable fallback tool-caller (`skip_primary`). Tested.
- [x] **B4.5 · Tool-tier routing.** Forced data/command turns skip the dodgy primary and go straight to groq
      (`tool_turns_prefer_fallback`); excludes `work_on_task` (kept on primary — Autonomy 100).
- [x] **B5-lite · Deterministic zero-arg reads (P0, the read guarantee).** Router-narrowed zero-arg reads
      (`list_events`/`read_email`/`check_telegram`/`notion_tasks`) fire **without any model round-trip** —
      no dodge possible. This is the real fix for the Channels/Tasks "tool didn't fire → 50" swing.
- [x] **B5 · Thinking-tier escalation — DONE & deployed.** A forced tool turn dodged by BOTH the fast
      primary AND the reliable fallback escalates ONCE to a MiniMax REASONING model
      (`minimax:MiniMax-M2.5-highspeed`, `llm.complete(prepend_model=...)`), which reliably fires the
      arg-bearing tools. Only on the dodge path (~+1s on the few turns that need it). In respond +
      respond_stream. Test `test_intent_router.py` (70). **Median 77.6 → 84.9.**
- [x] **Verify:** behavioral suite re-run on VPS after each batch; 74.8 → ~82 median, Autonomy/Honesty/
      Conversation 100. Delta recorded in memory.

---

## Part C — The 7 subsystems → genuine Strong

### C1 · Integrations-setup (Solid → Strong) — P0
- [x] **Google OAuth (Calendar + Gmail) ALREADY DONE & WORKING on the VPS** (verified 2026-07-24:
      `read_email` returns REAL mail, `list_events` works). `google_refresh_token` valid. No OAuth flow
      needed; Composio NOT needed for calendar/email. The old "unconfigured" note was stale.
- [ ] Add Home Assistant `AFON_HA_URL` + `AFON_HA_TOKEN` (code done, dark until set). **[needs your token —
      the ONLY remaining external cred; only matters if you want Afon controlling smart-home devices]**
- [ ] Add Giphy key (optional). **[needs your key]**
- [ ] Rotate `.env` secrets → `pass`; document the never-commit set.
- [ ] Live-verify each integration end-to-end. *Strong flip requires the creds above.*

### C2 · Integrations-util (Solid → **Strong ✓**) — DONE
- [x] Real inbound webhooks on the HTTP sidecar (`webhooks.py` + `server.py`): `/webhook/{stripe,github,
      gmail}` → `WORLD.note_event`, **HMAC-verified** (constant-time). Test: `test_webhooks.py` (11/11).

### C3 · TTS (Solid → **Strong ✓**) — DONE (1 live listen-check outstanding)
- [x] Affect → **ElevenLabs `voice_settings`** map (`affect.affect_to_voice`): steadier+slower when
      stressed/low, gentler when tired, livelier when upbeat. Wired through `voice_io.synthesize` and the
      Telegram voice-reply path (owner's inbound message shapes the reply's prosody). Test 23/23.
- [x] **EDGE streaming path DONE**: `edge/affect_tts.py` (`AffectTTS` processor) infers affect from each
      utterance and pushes a `TTSUpdateSettingsFrame` to ElevenLabs before the reply — live AirPods voice
      now adapts. Gated by `tts_affect_enabled`. Test `test_affect_tts_edge.py` (7/7). Restart the edge to
      activate (`scripts/restart_edge.ps1`).
- [ ] **[1 live listen-check]** calibrate the map values by ear (prosody ships starting values).

### C4 · Speed (Solid → **Strong ✓**) — DONE
- [x] Effective latency router already in place: conversational turns keep the fast primary; tool turns
      route to the reliable caller (Workstream 0 tool-tier) and zero-arg reads skip the model entirely
      (B5-lite). Fast-tier chain + first-token-deadline failover already tested (`test_llm_routing`).
- [x] **TTFW streaming budget**: first spoken word reaches the owner before the slow tail finishes.
      Test: `test_speed.py` (3/3).

### C5 · PC agent (Solid → **Strong ✓**) — DONE
- [x] **see → act → VERIFY** loop: `pc_agent._verify_effect` confirms file create/delete and process
      kill actually landed (or flags a mismatch) after each op. Undo already handled by `undo.py`.
      Test: `test_pc_verify.py` (7/7).
- [x] Cross-platform (macOS/Linux) is an explicit non-goal — single-owner Windows machine.

### C6 · Protocols (Partial → **Strong ✓**) — DONE
- [x] Drill mode (`run_protocol(..., drill=True)`) + a drill test per recovery protocol asserting steps
      fire without launching. Test: `test_protocol_drills.py` (15/15).
- [ ] *(deferred, P2)* Expand life-routine library + merge `routines`/`macros` overlap — cosmetic, not
      blocking Strong.

### C7 · Skills (Partial → **Strong ✓**) — DONE
- [x] Real **skill runtime**: `_SKILL_MANIFESTS` + `invoke_skill` tool + `run_steps` executor (shared with
      macros). Runnable skills advertised in `list_skills`. Test: `test_skill_runtime.py` (12/12).
- [ ] *(deferred, P2)* Convert more of the 19 markdown playbooks — 3 seeded (morning/comms/evening); add
      on demand.

---

## Part D — Category benchmark targets (mapped to the work)

| Category | Now | Cause | Fixed by | Target |
|---|---|---|---|---|
| Tasks | 29 | wrong tool (local vs Notion) | B1 + B2 | 90+ |
| Memory | 50 | narration, no tool call | B1 + B4 | 95 |
| Channels | 50 | get_time/fabrication + Cal/Gmail unconfigured | B1 + B3 + Google creds (C1) | 90+ |
| Safety | 70 | send_email not gated | B1 (force → confirm-gate) | 95 |
| Time/Utility | 86 | define not called | B1 | 95 |
| Combination | 74 | skipped web_search in a chain | B1 + multi-intent (exists) | 92 |

Already ≥90 (Honesty · Autonomy · Knowledge · Conversation · Proactivity · Web) — protect with
regression checks: B1's narrowing must not suppress a legitimately free-form turn.

---

## Part E — Sequencing & the "done" bar

1. **Workstream 0** first — unlocks 5 categories, cheapest, no external deps.
2. **Google / HA creds** (you provide) → Integrations-setup + Channels get *real data*.
3. **Subsystem depth** — TTS · Speed · Integrations-util · PC-agent · Protocols (parallel-ish).
4. **Skills runtime** last — the one real rearchitecture.
5. Re-run the behavioral audit on the VPS after each stage; re-grade only from real runs.

**Definition of done (objective):** every hermetic suite green **and** behavioral ≥ 95 overall with
**no category < 90** **and** each subsystem Strong with a code-grounded justification — never a relabel.

**Honest ceiling:** 95+ *overall* requires the Google OAuth creds (Calendar/Gmail can't return real data
without them). Without them the realistic cap is ~88–90 with honest graceful-degrade. The suite will not
be gamed to hide that.

---

## Part F — Decisions needed before executing (they fork the build)

- [x] **1. Thinking-tier fallback (B5)? — DONE.** Implemented with `minimax:MiniMax-M2.5-highspeed` on the
      dodge path only. Median 77.6 → 84.9 (Tasks/Proactivity/Safety to 93–96). The ~+1s cost is paid only on
      the few forced turns the fast models miss.
- [x] **2. Canonical "my task list"** — DECIDED: **Notion** (`notion_tasks`), routed ahead of the local queue.
- [ ] **3. Google + HA creds** — still needed for C1: Google OAuth (Calendar/Gmail) + an HA token on the VPS.
      Without them the realistic ceiling is ~88–90 with honest degrade. HA-less Integrations-setup Strong is
      defined explicitly. **This is the single biggest remaining lever to real 95+ and it's yours to provide.**

---

## Part G — Owner tasks (added 2026-07-24, do after the current list)

Legend: ⬜ not started · 🔄 in development · ✅ done & verified.

### G1 · Acknowledgement responses — polish & better utilization — ✅
- [x] **Variety**: `_immediate_ack` now rotates `_WORK_ACKS`/`_CHAT_ACKS`, never the same line twice running.
- [x] **De-stacking**: suppresses the generic immediate ack when a specific per-tool ack is imminent (a
      B5-lite zero-arg read) — one clean ack + the answer, not "Right away" + "Checking your calendar".
- [x] Intent tone: WORK pool for commands/tool turns, CHAT pool for conversational group turns.
- [x] *Test:* `test_acknowledgements.py` (6/6) — no double-ack on fast reads + no back-to-back repeat.

### G2 · Interruptions — behavioral test all paths, fix issues — ✅ (verified, no issues)
- [x] Ran `test_phase1_vad_bargein.py` — **27/27**. Barge-in state machine (one interrupt per bot turn,
      re-arm next turn, never on a solo user turn); both duplex modes assemble correctly; device-profile
      gating (AirPods→ON, speakers→OFF); brain cancels the in-flight turn on `InterruptionFrame`; spoken
      "cancel that" stops the busy task; a new request supersedes it. **No issues surfaced.**

### G3 · Voice-only response (owner verification) in production — ✅ (verified)
- [x] `speaker_id_enabled=True`, owner enrolled (`voiceprint.json`: 192-dim, L2-norm, 2026-07-20),
      `identity` extra installed (torch 2.12 + speechbrain), soxr resampling present. Gate LIVE.
- [x] Design confirmed: `SpeakerGate` (after STT, before brain) embeds every heard utterance + scores vs
      the owner voiceprint; non-owner transcripts dropped. `test_phase5_identity_bench.py` **25/25**
      (owner accepted, stranger rejected/dropped).
- [ ] **[flag, 1 live check]** `speaker_threshold=0.25` is permissive — tune from a real owner-vs-stranger
      recording. Not headlessly testable.

### G4 · Perception / face — up-to-date, knows my face — ✅ (verified, fresh)
- [x] `owner.npy` enrolled: **45 LBP references**, 2026-07-20 (fresh); `face_match_threshold=0.62`;
      OpenCV 4.13 installed. `test_face_recognition.py` **14/14** (owner recognized, stranger rejected).
- [x] No new pictures needed — enrollment is recent + solid. Re-enroll anytime with "learn my face"
      (one live action) if you want a refresh.

### G6 · Production edge health — ✅ (verified live 2026-07-24 evening)
- [x] **Edge running clean**: AfonEdge restarted post-sleep → fresh mic stream. `mic: Microphone Array`
      (built-in), wake words `['hey_jarvis','afon','hey_afon']` active, speaker-id ON, affect-tts ON,
      `brain link: connected` + `RemoteBrain linked`. pc_agent healthy (connected, activity_snapshot loop).
- [x] **Mic audio proven flowing**: pyaudio probe on the built-in array = RMS 0.024 (real ambient signal),
      opened in WASAPI shared mode alongside the live edge. The Deepgram `1011` blips are the STT idling
      *behind* the wake gate (no audio until a wake fires) — expected, not a fault.
- [x] **FIXED — output `-9999` outage**: with AirPods disconnected, `prefer_private_output` grabbed the
      always-listed built-in Realtek headphone JACK (`Headphones 1 … HD Audio … SST`, a WDM-KS endpoint
      that fails to open → Afon couldn't speak). Added `_INTERNAL_OUTPUT_CUES` exclusion so auto-route
      only picks a genuinely removable headset (AirPods/BT/USB), else the OS default. Now routes to
      `Speakers (index 3)` cleanly, no `-9999`. AirPods still auto-route when reconnected. Test:
      `test_audio_route.py` (3/3).
- [x] **Brain answers end-to-end**: sent a real `Utterance` over the edge's WS protocol → brain fired
      `get_time` and streamed back "Friday, 24 July 2026, 19:49". Full path edge↔brain↔tools verified live.
- [ ] **[you: 1 live check]** say "hey afon" / "hey afon" — the only link that needs a human voice.

### G5 · Inter-subsystem connectivity audit — ✅ (audit done + top fix shipped)
**Connectivity map (real wires traced):**
- ✅ edge↔brain: `VAD → WakeWord → BargeIn → STT → SpeakerGate → Brain → TTS → LeadIn → output` (verified live in the link log).
- ✅ speaker-id↔pipeline: SpeakerGate sits after STT, before brain — correct.
- ✅ memory↔reasoning: auto-RAG (`_recall_note`) folds relevant memory into every turn.
- ✅ webhooks↔world-model↔proactive: `handle_webhook → WORLD.note_event → anticipation reasoner → Signal → proactive → channels`.
- ✅ presence/activity↔proactive: `presence_signals` + `_recent_activity` feed the anticipation loop.
- [x] **FIXED — world-model↔REACTIVE turn:** was proactive-only; now `_world_note` folds FRESH events into
      the per-turn context (freshness-gated, ~zero cost when idle). "Anything new?" now surfaces a webhook
      payout/CI failure. Test: `test_connectivity.py` (5/5).
- [ ] **[room for improvement]** affect↔TTS: affect only becomes a text `manner_note`; Afon's *voice
      prosody* never changes with the owner's mood. → this is exactly **C3** (TTS affect→VoiceSettings).
- [ ] **[room for improvement, low pri]** face-recognition↔presence: camera owner-match is tool-only; an
      arrival greeting still keys off idle-transition, not the laptop camera seeing you. Deliberate given
      the VPS-brain/laptop-camera split; wire only if a persistent laptop-side presence feed is added.

---

## Part K — Behaviour quality: fast, efficient, well-utilised, timely (2026-08-08)

**Scope discipline: NO new capabilities.** Every item makes something Afon already has run
faster, cost less per turn, or fire at the right moment. Added after an owner review whose
brief was explicitly "current ones being fast, efficient, and well utilized, and utilized
timely".

Ordered by expected gain per unit of work. K1 is first because it doubles the cost of every
other item until it is done.

### K1 · One completion loop, not two (P0 — do before anything else in Part K)
- [x] **DONE 2026-08-08 (partially — the decisions, not the loop bodies).** `_prepare_turn()` is now
      the single decision path for both response modes, and `_completion_force()` the single
      completion rule. What remains duplicated is the ITERATION BODY — the B4/B5 escalation ladder
      and tool execution — which genuinely differs (one returns a string, one yields chunks) and is
      a larger, riskier refactor. The duplication that was actively costing double work on every
      behavioural change is gone; `bench/test_clause_completion.py` asserts both paths agree.
- [ ] **`agent.py` carries two near-duplicate completion loops** — `311/986/1002` and
      `1339/1397/1420`. Found while planning the `clause_tools` wiring. Every behavioural fix
      must be applied twice, and a fix applied ONCE produces a defect that appears only on one
      path: the same input behaves differently depending on how it arrived, which is the
      hardest class of bug to reproduce. This is not a tidiness item — until it is one loop,
      every K-item below costs double and carries a silent-divergence risk.
      Done means: one loop, both entry points calling it, and a test asserting that a forced
      tool fires identically through both.

### K2 · Tool catalogue — the dominant per-turn cost
- [ ] **Tier the catalogue by RECENCY as well as intent.** *(BLOCKED ON DATA — the blocker was
      removed 2026-08-09; see below. Leave open until the store has a real sample.)*
      Advertise the intent-narrowed set plus the owner's actual top-N by usage, leave the rest
      reachable on escalation.
      **Why it could not be done on 2026-08-09:** the usage data this depends on did not exist.
      `METRICS.incr(f"tool.{name}")` counted every call — *in memory only* — and the brain restarts
      daily at 01:00, so the evidence was destroyed nightly and never accumulated. Tiering on no
      data is guessing which tools are rare, and a wrong guess removes a capability **silently**,
      which is the exact failure class this file keeps recording.
- [x] **Built the missing input 2026-08-09: `src/afon/brain/tool_usage.py`.** Durable per-tool call
      counts at `~/.afon/tool_usage.json`, recorded next to the existing METRICS call in
      `agent.py`. Flat JSON, not SQLite (a few KB of small ints does not need a schema, a migration
      and a connection). Atomic `tmp.replace()` so a kill leaves the old file, never a torn one;
      debounced to one write per 60s, plus a deliberate first-record flush so a short-lived process
      still leaves evidence. `bench/test_tool_usage.py` 18/18, negative control confirmed (break the
      atomic replace → fails loudly). `bench/tool_usage_report.py` prints the ranking and **refuses
      to call anything "unused" below 200 recorded calls** — a tool absent from a short sample is
      untested, not unused, and reading an empty store as a broken pipeline has already happened
      twice in this repo (see K4a).
      **Sizing for when the data lands:** core surface is 54 tools / ~7,410 tok, and **55% of that
      is prose** (12,876 ch of tool descriptions + 3,417 ch of param descriptions). The 10 biggest
      are 32% of the surface — `browser` 377 tok, `run_protocol` 275, `send_telegram` 249,
      `update_task` 248, `add_task` 235. Trimming prose was considered and NOT done: it changes
      selection accuracy, which cannot be verified hermetically, whereas *moving a never-called
      tool into a lazy group* is verified by the same two-sided test the `"check my"` fix used.
- [x] **Cache the serialised catalogue per tier — DECLINED 2026-08-09 on measurement, exactly as
      this item asked ("verify before optimising").** Building the surface costs **0.08-0.30 ms**
      per turn and serialising it **0.66-0.89 ms**. There is nothing here to win; a cache would
      add invalidation risk to save under a millisecond.
      **The measurement did find the real cost, and it is tokens, not CPU:**
      | turn | tools advertised | approx prompt tokens |
      |---|---|---|
      | high-precision intent (`what's the weather in Berlin`) | 1 | ~144 |
      | pure chat (`tell me a joke`) | 0 | 0 |
      | anything else (`what do you think about that`) | 80 | **~11,525** |
      So narrowing and pure-chat both work well; the whole cost sits in the UN-NARROWED fallback.
      That is what the recency-tiering item above should target — and it is a token problem, so
      it is worth reframing that item around prompt size rather than CPU.
- [x] **Fixed one concrete instance of it: `"check my"` was a COACHING trigger.** So "check my
      email", "check my calendar" and "check my tasks" — three of the most common things the
      owner says — each loaded five German-quiz tools onto the turn. Nothing failed; it silently
      cost ~565 prompt tokens every time. Narrowed to "check my level/progress/german/french/
      spanish". Verified: `bench/test_pure_chat_tools.py` 51/51 with 8 new checks (both halves:
      the phrases that must NOT load coaching, and the ones that still must), and the old trigger
      was restored to confirm the checks FAIL on it.
- [x] **Wire `clause_tools` into the completion loop (the benchmark's own named lever).** DONE
      2026-08-08, together with K1 (the two loops), because the wiring was the reason to do K1 first.
      `_prepare_turn()` is now the single decision path for `respond()` and
      `_respond_stream_impl()` — ~26 duplicated lines (history, context notes, tool surface,
      narrowing, force_first) collapsed into one method, plus the new clause plan. The streaming
      mechanics still differ, correctly; only the DECISIONS were merged.
      `_completion_force()` replaces the prose nudge: the completion pass now asks which clause tool
      has NOT fired and forces THAT one by name. Same helper on both paths, so it cannot drift.
      The iteration budget is `_max_tool_iters + len(clause_plan)` — a 2-clause request needs more
      passes than a 1-clause one, and the old fixed budget ran out before the last clause could
      fire, ending the turn having satisfied everything except the part the user probably cared
      about most ("...and remember it").
      `bench/test_clause_completion.py` (10/10) runs the SAME request through both paths and asserts
      identical tools fire. **Its first version passed while the required tool never ran** — it
      asserted "two tools fired" rather than "the plan was satisfied"; corrected to assert the plan.
- [x] **B6.1 — DONE 2026-08-08 via option (a), decided here rather than escalated.**
      `_CLAUSE_ONLY_ROUTES` in `intent_router.py`: a small supplementary map consulted ONLY by
      `clause_tools`, never by `forced_tools`. So a compound request can see its time clause
      (`"what time is it and remember to call mum"` -> `['get_time', 'remember']`) while a bare
      `"what time is it"` still routes to nothing and keeps the fast inline answer — the earlier
      decision not to force `get_time` on single-intent turns stands untouched.
      Verified: `bench/test_clause_routing.py` 28/28 (5 new checks, and the single-intent
      no-narrowing half is asserted explicitly because that is the part that could regress
      silently); `test_clause_completion.py` 10/10; `test_intent_router.py` 70/70. Proved the new
      checks FAIL on wrong routing before trusting them.
      *Original entry, kept for the reasoning:*
- [ ] ~~**`combo_time_memory` is NOT fixed by the wiring above, and this was only visible after
      building it.** `forced_tools("what time is it")` returns `[]` — there is no time route in the
      intent router at all. So `clause_tools` splits the sentence correctly, finds only ONE routed
      clause, hits its `>= 2` guard and returns `[]`; the turn falls back to the prose nudge.
      `combo_web_memory` and the email/search variants DO work.
      The obvious fix — add a `get_time` route — collides with a decision already recorded above:
      forcing `get_time` on a single-intent "what time is it" was **declined as gaming the scorer**,
      since Afon answers correctly inline and faster. Options, owner's call:
      (a) give the CLAUSE router a supplementary route map so compound requests see the time clause
      while single-intent behaviour is untouched — surgical, no scorer gaming;
      (b) relax `clause_tools` to return a single routed clause when the text is multi-intent, so the
      completion pass can at least force the part it can name;
      (c) accept `combo_time_memory` as unfixable without special-casing, and say so in the grading.
      (a) is the honest one: the compound case is a real product failure, not a scoring artifact.
      Built and tested (`bench/test_clause_routing.py`, 23/23) and called from NOWHERE — see
      Workstream 0. Multi-intent scores **51.5 against ~96 everywhere else**; the TODO's own
      analysis puts this at ~4 of the ~10 points between 84.9 and the 95 goal. Must land in
      BOTH loops, or K1 first.~~

### K3 · Memory — seven stores, no unified recall
- [x] **One connection pool — MEASURED AND DECLINED 2026-08-08.** Per-call
      `sqlite3.connect()+query+close` costs **0.805ms**; a reused connection costs 0.117ms. So
      the overhead J3.3 objects to is **0.687ms per call** — roughly 3ms per turn against turns
      measured in seconds. That alone would make it low-value.
      What makes it actively WRONG is the threading fix above: recall now runs on worker
      threads, and SQLite connections are not shareable across threads without
      `check_same_thread=False` plus external locking. **The per-call connection pattern is what
      makes the thread offload safe.** Pooling would trade 0.7ms for a class of concurrency bug.
      Recorded as a decision, not an omission: the number is small, and the architecture changed
      underneath the original objection.
- [x] **J3.3b — DONE 2026-08-08. The graph is now a recall layer (`L5b`).**
      `MemoryStore.fused_recall` maps `_terms(query)` to `GRAPH.describe()` (capped at 6 terms ×
      4 facts, deduped), inside the same `asyncio.to_thread` offload as L1, scored 3.0 and ranked
      above L3 on ties — a graph hit is an exact entity match, a vault hit is a substring in a
      long note. Added to the DEFAULT layer tuple only; the per-turn `("L1","L2")` path is
      untouched and a test asserts it stays that way.
      Verified: `bench/test_graph_memory.py` 24/24 (3 new checks), memory suites 58/58.
      *(2026-08-08 measurement: the graph holds **772 triples on the VPS** — the machine the brain
      actually runs on — so wiring it in is worth doing. My first measurement said "0 triples" and
      was simply taken on the LAPTOP, where the store has never been populated because the
      background reviewer runs brain-side. Measure on the machine that runs the code, not the one
      you are typing on: the laptop reading gave exactly the wrong conclusion.)*
      Lookup cost is ~11ms for 4 query terms via `describe()`, entity-keyed, no free-text search —
      so integration means mapping query terms to entities, which `_terms()` already does.
      `fused_recall` covers L1 (learned) / L2 (journal) / L3 (vault) / L5 (semantic re-rank),
      but **L5b (the sqlite triple store) is not in it at all** — it is reachable only if the
      model explicitly calls a graph tool. So "what do we know about X" genuinely does miss a
      layer, exactly as J3.3 says, just not for the reason J3.3 gives.
      Before wiring it: measure graph recall the same way (`bench/profile_memory_recall.py`
      already has the ticker harness). If it is fast and local, add it to the DEFAULT layers
      only — the per-turn path must stay L1+L2, which is ~10ms and the reason turns are quick.
- [ ] **One recall facade** (the remaining half of J3.3) — see J3.3b. Seven memory
      stores, five holding their own SQLite connection, means every recall pays separate
      connection + query cost and nothing can answer "what do we know about X" in one hop. Not
      a rewrite: a single entry point the agent calls, fanning out internally.
- [ ] **Warm the hot path once per session, not per turn.**
- [x] **Measure the L5 semantic hop.** DONE 2026-08-08 via `bench/profile_memory_recall.py`.
      **Two things I had written were wrong, and measuring corrected both:**
      (a) the embedder is **api.jina.ai**, a third-party HTTPS call — not "hosted on the VPS";
      (b) it is **NOT on the per-turn path**. Auto-recall passes `layers=("L1","L2")`, so the
      ordinary turn never embeds. The per-turn cost is **~10ms**, which is fine.
      **What measuring found instead was far worse than the documented risk.** `recall()` is
      synchronous, `fused_recall()` is async and awaited it directly, and there was no
      `to_thread` anywhere. So an explicit `recall` ran blocking work ON THE EVENT LOOP:
      **287 SECONDS on a cold vault**, 7.5s warm. While stalled the brain answers nothing at
      all — other turns, the WebSocket and the scheduler are frozen together.
      Dominant cost was **L3 (vault), not L5**: `search_vault` is `async def` with a purely
      synchronous body (rglob + read_text over every .md, never awaiting), so it could not even
      be interrupted by a timeout — a 6s budget produced a 7.5s freeze, because `wait_for`
      cannot interrupt code that never yields.
      **Fixed:** L1 (and its Jina embed) offloaded with `asyncio.to_thread`; L2's file scan
      yields; L3 runs in a thread with a `memory_vault_search_budget_s` (6s) ceiling on the wait.
      **Worst loop stall 287,538ms -> 180ms.** Wall-clock recall is still ~8s, which is the
      right trade: latency is now separate from availability. Memory suites 79/79 green
      (phase9 20, autorecall 12, behavioral 18, graph 21, semantic 8).
- [x] **J3.3a — DONE 2026-08-08. L3 vault search: 5.1s -> 1.2s warm, and it no longer holds
      the loop for ANY caller.** Measured first: 10,670 files / 25.7MB, of which `rglob` is 400ms
      and `read_text` is **4.9s** — so the reads were the cost, not the walk.
      Three changes, in descending order of payoff:
      1. **Body cache keyed on `(mtime, size)`**, bounded to 32MB. A note whose mtime and size
         are unchanged cannot have different text, so every re-read was pure waste. Over budget
         it falls back to reading fresh — degrading to *slow*, never to *wrong*.
      2. **`body.lower()` hoisted out of the term loop** in `_score`. It was building one full
         lowercase copy of every note PER QUERY TERM; a 4-term query lowercased 25MB four times.
         Alone this took a 4-term query 3.4s -> 1.6s.
      3. **The `to_thread` moved INTO `search_vault`**, which was `async def` around a fully
         synchronous body. `fused_recall` had been working around that locally with a
         `to_thread(asyncio.run(...))` sandwich; every other caller — including the LLM invoking
         the tool directly — still froze the brain. Fixing it at the source deleted the
         workaround and covers all callers. The 6s recall budget now actually bites, because
         `wait_for` can finally interrupt it.
      Deliberately NOT an index: a schema, a writer, an invalidation story and a rebuild command,
      for the same 10x a dict gets. Build one if the vault outgrows the 32MB budget.
      Verified: new `bench/test_vault_search.py` 6/6 — proves an edited note is re-read, a
      deleted note stops matching, and the loop keeps ticking (worst gap 19ms) — plus
      `profile_memory_recall.py`: per-turn path unchanged at **9.3ms**, warm tool path 2.9s.
      *Caveat: the cache is per-process, so a fresh process still pays the 4.5s cold read once.
      The brain is long-lived, so it pays it at startup and not again.*
      **Test-writing note:** the first version of `test_vault_search.py` passed 6/6 while five of
      its six checks were inverted — `check(cond, label)` called as `check(label, cond)`, so a
      non-empty string was the condition. Caught within minutes only because the same phantom-pass
      failure mode was fresh from J6.5. A test that has never been seen to fail has not been tested.
- [ ] **Measure the L5 semantic hop.** *(superseded — see above)* Semantic recall runs against Jina on the VPS. A remote
      embedding call inside a turn is exactly the kind of cost that silently dominates latency
      while every local test stays green. Measure before assuming it is cheap.

### K4 · Proactivity — built, calibrated, and partly dormant
- [x] **Find out WHY coaching is silent before touching thresholds again.** DIAGNOSED 2026-08-08,
      and it is NOT a threshold problem. `jarvis_coaching.sqlite` on the VPS holds
      **`skills: 0, reviews: 0`, last written 2026-07-14 21:41** — it has never recorded a single
      row, on either machine. A too-high urgency gate would still populate `skills` and simply
      decline to voice them; an empty `skills` table means the pipeline fails BEFORE any gate is
      consulted. So the earlier recalibration to 0.61-0.66 was treating a symptom of something
      upstream, exactly as suspected.
      Next step is now specific rather than exploratory: find what is supposed to write `skills`
      and why it has not run since 14 July. Do NOT touch thresholds again until it does.
- [x] **K4a — CLOSED 2026-08-08: nothing is broken, and the reasoning above was wrong.**
      `~/jarvis/proactive_state.json` on the VPS records, per signal kind, how often it has been
      shown. Coaching: **`shown: 3`, last 2026-08-03 19:41**. The offer fires. It has fired
      recently. There is no plumbing fault and no dormant job.
      The empty tables have a duller explanation: the ONLY writers of `skills`/`reviews` are
      `Coaching.set_level` and `Coaching.record_review`, reachable solely through the
      `set_skill_level` / `record_skill_review` tools — which run at the END of a quiz the owner
      has to accept and complete. Nobody has taken up the offer, so nothing has been recorded.
      The store's 2026-07-14 mtime is its CREATION (`_init_db`), not a last write.
      **The reasoning error worth keeping:** "an empty table means the pipeline failed upstream"
      assumed the table is written by the pipeline. It is written by the *user finishing a
      conversation*. An empty table there is evidence about the owner's behaviour, not the
      code's. Two sessions running, this store was read as a defect because emptiness looks like
      failure — check what writes a table before inferring anything from it being empty.
- [x] **DONE 2026-08-09 — SUPPRESSED counters per proactive kind.** `held_*` counters now sit next
      to `shown` in `proactive_state.json.feedback[kind].stats`, one per gate: `threshold`,
      `repeat`, `outranked`, `quiet`, `busy`, `emit`, `emit_error`, plus `budget`/`mode` on an
      `_engine` pseudo-kind (those gates fire before any signal exists, so there is no kind to
      blame — one storage shape beat adding a second field). `bench/test_proactive_suppression.py`
      23/23, negative control confirmed (disable `_hold` → 9 checks fail). Two things worth keeping:
      **(a)** writes are gated on a dirty flag — the engine ticks continuously and most ticks hold
      nothing, so an unconditional save would turn a diagnostic counter into a per-tick disk write;
      **(b)** my first version of that very check asserted "no state file exists", which passes or
      fails for a reason unrelated to these counters (the first tick of any day saves, to reset the
      budget). It failed on the first run and the assertion was wrong, not the code.

- [x] **A hermetic test depended on the physical webcam — found 2026-08-08 by running the suite
      twice.** `test_companion_safety.py::test_confirm_gate` exercises the confirm gate, which for
      gated actions runs the FACE second factor — a real camera burst. So the result depended on
      whether a laptop webcam could see the owner at that instant: **125 passed on one run, then
      `35/37` with "REFUSED send_email — camera saw 0 face(s)" on the next, minutes apart, with no
      code change between them.** I nearly attributed it to the change I had just made.
      Fixed by stubbing `_face_second_factor` in that test — the camera path has its own coverage
      in `test_camera.py`, and what this test is for is the GATE. Verified stable: 37/37 three runs
      running, and ~14s faster.
      **The lesson is about the failure mode, not the camera:** an intermittent test is worse than
      a missing one, because the next real regression here reads as "the camera again".
      Swept the other 8 registered tests that mention a camera/mic: all clean. `test_camera`,
      `test_face_recognition` and `test_face_second_factor` all stub `_capture_burst` /
      `verify_owner_present` properly; the rest only mention the word. The reason this one slipped
      through is worth remembering — it never names the camera. It drives the confirm gate through
      the AGENT, and the face check is wired in behind that. **A test acquires its dependencies
      from everything it calls transitively, not from what it imports.**

### K5 · Voice pipeline — the latency actually felt
- [ ] **Collect the three days of turn-tracer numbers, then tune** (`stt/brain/tts/total`
      already instrumented). Nothing else in K5 should move first: without the distribution the
      wrong stage gets optimised.
- [ ] **Optimise TTFT, not total.** What the owner perceives is time-to-first-sound. If TTS
      waits for a complete response, streaming the first clause is worth more than shaving the
      whole turn.

### K6 · Channels — timeliness, not capability
- [ ] **Poll-vs-push audit.** Channels score 100, so they work; the open question is *when*
      they run. Any channel on a timer rather than reacting to a webhook is spending quota to
      be slower. The fleet already learned this expensively: a 5-minute heartbeat across 8
      agents produced 1,229 calls/day and an overload.

### K7 · Ops — where the silent failures live
- [x] **One `preflight` script asserting the invariants that actually broke.** DONE 2026-08-08 —
      `scripts/preflight.sh` (local by default, `--remote` adds VPS checks), wired into
      `deploy_vps.sh` as the FIRST gate so a manual run and a deploy assert identical things.
      Checks: env prefix in `config.py` vs the `.env` that will feed it (local AND remote) ·
      state dirs where the code looks, with a stranded-file diff against `~/.jarvis`/`~/.watari`
      · enrolled face refs actually load · remote dir exists · remote unit exists.
      **It caught the live one on first run:** 108 legacy-prefixed vars on the VPS against 0
      `AFON_` — i.e. deploying the renamed code today would silently strip every setting.
      Three bugs found in the checker itself while proving it fires, each of which had made it
      report success on a broken condition: `grep -P` unavailable in Git Bash's locale, a
      mangled sed capture that "passed" with an empty prefix, and a doubled `0
0` from an
      `|| echo 0` fallback that killed the script mid-check. A guard is worth only what its
      last failing run proved.
      Still open: **K7b — add the guard the other three items lack**, i.e. make preflight part
      of the ordinary test gate too, not just the deploy.
- [x] **K7b — DONE 2026-08-09. Preflight now runs FIRST in `bench/run_all_tests.py`.**
      Local checks only (no `--remote`), so the gate stays runnable offline. A broken invariant
      fails the run but does NOT abort it: hiding every code result behind an environment problem
      makes a gate people learn to bypass, so it reports at the end where it cannot be missed.
      **Wiring it up exposed three real defects in the ops layer — none visible from Git Bash,
      which is the only way anyone had ever run these scripts:**
      1. **`preflight.sh` and `deploy_vps.sh` had CRLF line endings.** Git Bash tolerates them;
         anything else dies with `$'
': command not found`. `deploy_vps.sh` is the production
         deploy script, so this was a live landmine, not a test-only issue. Both normalised to LF.
      2. **The gate invoked plain `bash`, which on this machine is WSL** — different filesystem
         root (`/mnt/c`), different `$HOME` (`/home/<user>`). Every path check then inspected a
         machine the brain does not run on and reported a missing state dir that was plainly
         there. Now resolves Git Bash by absolute path, and SKIPs cleanly if absent.
      3. **`$HOME` is not what the code uses.** The brain resolves state via `Path.home()`, so
         preflight now asks Python for the same value (stripping the CR that `python.exe` prints
         on Windows, which had been silently corrupting every derived path).
      Added a CRLF guard so this cannot come back — and it took THREE attempts to get right,
      each failing in an instructive way: a `grep -qU` version flagged all four scripts when the
      true count was zero, and the replacement embedded a real carriage return into the script,
      making preflight itself the CRLF file it was hunting. Verified by planting a genuine CRLF
      file, watching it fail, then restoring. `bash -n` clean on all four scripts.
      *Original entry:*
- [ ] ~~**Run preflight in the test gate, not only on deploy.** Three rename
      casualties in a single day — stranded face refs, wrong deploy path, wrong env prefix —
      every one silent, and **not one caught by 118 passing tests**. J4.4 names the cause: the
      shell/ops layer has no tests and no graph edges. Assert: remote dir exists · systemd unit
      exists · the env prefix on the target matches what `config.py` expects · state
      directories are where the code looks. Minutes of work against three failures that each
      cost more than that.~~

## Unverified / not-yet-implemented backlog (carry until each is verified live)

Everything below is **either not built, or built-but-not-verified-live** — tracked so nothing is assumed done.

**External-dependency-gated (code complete, dark until a credential/hardware step):**
- [ ] **Home Assistant (Phase 5.1)** — `tools/smarthome.py` done + confirm-gated; VPS `ha_url/ha_token=False`.
      Controls nothing until `AFON_HA_URL` + `AFON_HA_TOKEN` set. **[your hub token]**
- [ ] **Open-speaker AEC full-duplex (Phase 1.2)** — `edge/aec.py` seam done + tested (15/15); the working
      echo canceller is a **proprietary SDK** (krisp_audio/aic_sdk) not present. Install SDK +
      `AFON_AEC_FILTER=krisp` → open-speaker barge-in auto-enables. (Owner uses AirPods where barge-in
      already works — this is the general capability.) **[paid SDK license]**
- [ ] **Google OAuth (Calendar + Gmail)** — unconfigured on VPS; blocks real Channels data. **[your creds]**
- [ ] **Giphy key** — unconfigured. **[your key]**
- [ ] **TTS affect prosody** — needs one **live listen-check** once C3 lands. **[1 live check]**

**Parked scaffolds (not runnable):**
- [ ] **MentraOS glasses client** — `glasses/src/index.ts` is an UNFINISHED scaffold with TODO SDK calls;
      no `npm i`, no MentraOS account/console app, transcription stream not wired. Only brain-side device
      routing exists + is tested. To stand up: MentraOS account + `npm i` in `glasses/` + register app +
      finish `index.ts`. Parked per the 3.4 decision (phone/laptop cam is the eye). **[account + build]**

**Deliberately-not-enabled (decision, not a bug):**
- [ ] **WS gateway fast path** — gives no speedup (CLI cold-start 0.06s; the ~25s is ispir working). The
      4.4 circuit breaker routes straight to CLI. Enable only if you want streaming progress events; needs
      the gateway on localhost or TLS (would trade Tailscale remote access). Left `bind=tailnet`.

**Unbuilt (this TODO's core work — Workstream 0 + Part C, listed above):**
- [ ] B1–B5 tool-call reliability · C1–C7 subsystem-to-Strong. None built yet.

**Model-quality finding (objective, your call to act on):**
- [ ] MiniMax-Text-01 (the non-thinking primary chosen for speed/cost) narrates actions / mis-selects
      tools on memory/define/email/calendar/telegram. Workstream 0 mitigates in code; B5 (thinking-tier)
      is the deeper fix if needed. Documented, not silently "fixed."

**Runtime follow-ups (non-blocking):**
- [ ] Owner-face enroll needs the owner seated ("learn my face") — refs local to laptop only (VPS is
      cameraless). *(2026-08-08: the 75 refs enrolled on 07-20 were stranded by the rename and have
      been restored — see above — so a re-enrol is NOT required just to get back to working.)*
- [ ] **Replace the hand-rolled LBP recogniser with `uniface` (installed 2026-08-08, NOT yet wired).**
      `uv pip install uniface` done (3.7.1); added to a new `vision` extra in `pyproject.toml`
      alongside `opencv-python`, which had been an UNDECLARED runtime dep — `camera.py` imports cv2
      and only logs a warning, so a fresh install got a camera that silently did nothing.
      Only 4 new packages: onnxruntime 1.24.4 and cv2 4.13.0 were already present.
      Why it is worth doing: today's recogniser is a 256-bin LBP histogram compared by intersection
      (`camera.py:150`), a texture signature chosen to avoid a dependency. It degrades with pose,
      expression and lighting, and **cannot distinguish a face from a photograph of that face** —
      which matters, because this is the owner-identification path. uniface brings ArcFace/AdaFace
      embeddings, RetinaFace/SCRFD detection and MiniFASNet anti-spoofing.
      **The wiring is NOT a drop-in and must not be treated as one:** the two reference formats are
      incompatible (256-dim histograms + intersection >= 0.62 vs 512-dim embeddings + cosine), so
      comparing across them is meaningless. Plan: write embeddings to a SEPARATE `owner_emb.npy`
      with its own threshold setting, keep LBP as the fallback when uniface or its model download is
      unavailable, and have enrolment write BOTH so neither path is ever left unenrolled.
- [ ] Stale local dev tasks stuck `status=running` ~275h show on the HUD (cap limits flood) — hygiene TODO.
- [ ] 2.2 daily-schedule refresh job (currently refreshes lazily before reasoning — sufficient).
- [ ] Local freellmapi proxy (:3001) for local VLM tests (works on VPS otherwise; groq has no vision model).

---

## Part H — Graph-driven full-codebase review (2026-07-31)

Method: rebuilt the graph at commit `fc8b72d` (3,509 nodes · 6,436 edges · 257 communities · 357 files),
then walked it — every module, function, edge, community, and all 133 tool schemas — and **verified each
candidate against the source and against the live VPS before listing it here**. Graph signals alone
produced several false alarms (graphify resolves `module.fn()` to the module node, so ~30 "uncalled"
functions were in fact called; `run_supervised`, `record_cloud_error` and `daily_digest.due` were all
fine). Nothing below is a graph artifact — each was reproduced in the code or on the running system.

Registry health is clean: 133 handlers ↔ 133 schemas, no duplicate tool names, no handler without a
schema, no schema without a handler, and all 20 lazy tool groups have activation triggers.

### H0 · Highest severity — act before the next autonomous run

- [x] **H0.1 — Afon's coding tools point at the VPS deploy tree, which is a live git repo on the real
      remote.** `coding.py:27` sets `_REPO = Path(__file__).parents[4]`; on the VPS that resolves to
      `/home/openclaw/afon`, and that directory **is a git repo**: branch `master`, `origin =
      github.com/iamvazghen/OpenAfon`, HEAD stuck at the stale `f1b779f`, **141 files dirty** (the deploy
      untars over it, so every deployed file reads as modified). Verified live: `settings.coding_tools_enabled
      = True` on the VPS with no `.env` override. Consequences: (a) an owner-confirmed `git_commit` +
      `git_push` would commit tar-extracted deploy state on top of a stale commit and push it to the
      canonical repo; (b) every `write_source` self-improvement edit lands on the VPS and is **silently
      destroyed by the next deploy**. Pick one: disable coding tools on the VPS, route them to the laptop
      over PC_LINK like `browser`/`camera`, or strip `.git` from the deploy target.
- [x] **H0.2 — `notion_complete_task` can clear the entire task list with no confirmation.** `_is_bulk()`
      (`notion.py:710`) accepts `all=true` or a whole-list phrase and then marks **every open task** Done.
      `confirm_required("notion_complete_task", {"all": True})` returns **False** (verified). Meanwhile
      `notion_delete_task` — which archives exactly one task — *is* gated. The asymmetry is backwards: the
      bulk-mutation path is the unguarded one.
- [x] **H0.3 — Destructive tools still outside `CONFIRM_TIER`** (all verified `confirm_required(...) → False`):
      `delete_macro`, `delete_task`, `drop_objective`, `forget`, `write_vault`, `complete_objective`,
      `update_task`, `notion_update_task`. `forget` and `write_vault` touch durable memory; the rest destroy
      owner-visible state. (Carried from the 2026-07-30 tool review — still open.)
      **Done 2026-07-31, with one deliberate scope call.** Six gate unconditionally (`delete_macro`,
      `delete_task`, `forget`, `write_vault`, `drop_objective`, `complete_objective`). The two
      `update_*` tools gate *dynamically* instead: gating them outright would have broken the
      documented capture-by-voice design ("creating/updating/completing a task is frictionless by
      design"), so `_update_is_destructive` gates only the shapes that actually lose data —
      overwriting a title/description, or blanking a field — while a progress bump, an appended note
      or a moved deadline still flows without a question. No destructive path is left unguarded.

- [x] **H0.4 — (found 2026-08-01, not in the original review) The speaker gate makes a network call to
      huggingface.co at every boot, and the test runner's timeout was set below what that costs.**
      `speaker_id._ensure_embedder` calls `EncoderClassifier.from_hparams(source="speechbrain/…")`,
      which asks the hub for the current revision **even though all five checkpoint files are already
      cached** in `.speechbrain-ecapa/` — so Afon's startup depends on HF being reachable, and stalls
      behind it when it isn't. Separately this blocked **five consecutive deploys**: `build_worker`
      warms that embedder, and importing torch+speechbrain plus loading the checkpoint measured **183s**
      against the runner's **180s** per-test cap while Docker + 3 VS Code + Chrome held ~72% CPU and
      27 GB of 31.7 GB RAM. A different test tripped the wall each run and every one passed in
      isolation, which is what made it read as flakiness rather than a boundary condition.
      **Fixed:** pin `HF_HUB_OFFLINE` around that one call (restored in a `finally` — the flag is
      global, and leaving it set would break any *uncached* HF model in the same process, e.g. Whisper),
      and raise the runner cap to 420s (`AFON_TEST_TIMEOUT_S`). The offending test now passes in 238s.

### H1 · Capabilities that are broken or unreachable in production

- [x] **H1.1 — `localplay` plays audio on the headless VPS.** *(Done 2026-08-01. Worse than recorded:
      the VPS HAS ffplay at `/usr/bin/ffplay`, so playback started cleanly, logged "local playback
      started" and said "Playing … out loud now, sir" — the only symptom was silence in the room.
      All three primitives now dispatch to the laptop, audio bytes riding with the play op since the
      Telegram session lives brain-side; cap at 5.5MB raw (pc_agent's 8MB frame ÷ 4/3 for base64) with
      oversize degrading to phone delivery; `now_playing` registered as a tool. Verified live: the VPS
      brain answered "what's playing?" by forwarding `audio_now` to the laptop — pc_agent.log:10680.)* `localplay.py:27` launches `ffplay` on the
      brain host, so "play this out loud" streams into a server with no speakers. Needs PC_LINK routing.
      Its `now_playing()` (`localplay.py:62`) is **genuinely dead** — no tool or caller reaches it, so
      "what's playing?" cannot be answered even though the state is tracked.
- [x] **H1.2 — `read_document` reads the VPS filesystem, not the owner's.** *(Done 2026-08-01. NOT
      solved the obvious way: routing `docstore.load()` wholesale to the laptop would have broken every
      PDF, because `pypdf` is installed on the VPS (6.7.2) and not on the laptop. So the laptop returns
      raw BYTES and the brain parses/indexes them — `load()` split into `load_bytes()`, `_extract_pdf`
      taught to take bytes. Index stays brain-side so `ask_document` needs no extra hop. Cap 2.8MB:
      the brain's INBOUND frame limit is 4MB, tighter than pc_agent's 8MB outbound. Verified live on a
      file the VPS provably cannot see — `pc_agent.log:10741` shows the forwarded `doc_read`, and the
      brain log shows the same call answering "I can't find a file" before and "Loaded 'warehouse.md'"
      after.)* `documents.py:13` →
      `docstore.STORE.load(path)` resolves against the brain host, so any real laptop path fails.
      `camera` and `multimodal` already solved exactly this with `system._dispatch`; documents did not.
- [x] **H1.3 — There is no audio-output switching tool at all.** *(Done 2026-08-01. The tool was the
      easy half. The half that mattered: saving a preference changed nothing audible, because
      `resolve_output_index` is read only when the worker is BUILT and `route_should_change` watches
      devices appearing/vanishing, never the preference — so the command would have reported success
      while sound kept coming from the old device until the next restart. The watchdog now stats the
      preference file each poll and trips its existing rebuild path. New LAZY module `tools/audioout.py`
      (not localplay: lazy groups are per-module, and demoting localplay would take `stop_music` with
      it) keeps the per-turn surface at 56. Verified live end to end in 5.2s: pc_agent.log:10782
      received `audio_output_set`, edge.log:4473 logged "output preference changed — re-routing",
      edge.log:4483 rebuilt the stream. Also corrected `set_output`'s "next time I start speaking",
      which contradicted the new immediate behaviour.)* `src/afon/edge/switch_audio.py` states in
      its own docstring that it is "the mechanism the voice command 'Afon, switch to my headphones' calls
      (the brain registers it as a tool in Phase 2)". It does not: the module has **zero references anywhere
      in the repo** — no import, no tool, no script, no test. The capability is unimplemented and the
      docstring asserts otherwise.
- [x] **H1.4 — Integrations registered but unconfigured on the live VPS** *(Done 2026-08-01 — all three
      CONFIGURED, none deregistered, because the values all already existed. `mynews_url` →
      `https://mynews-nine.vercel.app` (found in `~/.openclaw/openclaw.json`; endpoint verified live,
      823 items) and `news_signals` now returns a real brief — headlines confirmed at a forced 09:00,
      still silent at 15:00. `firecrawl_api_key` and `brave_api_key` came from `pass`
      (`apis/firecrawl/api-key`, `apis/brave/key`) and BOTH authenticate HTTP 200 against their live
      APIs — a present-but-expired key would have re-created the same silent failure. Written to the
      VPS `.env` (backed up first, values never echoed); brain restarted and settings confirmed loaded.
      NOTE: `.env` is outside the deploy tar, so this config lives only on the VPS.)*, so they fail quiet forever:
      `mynews_url` UNSET (the `news_signals` proactive source is registered and ticks ~22×, always empty —
      the 16-topic morning brief never fires), `firecrawl_api_key` UNSET (scrape fallback loses its last
      hop), `brave_api_key` UNSET (search degrades to Tavily→Jina only). Decide per item: configure or
      deregister — a registered dead source costs a tick and buys nothing.
- [x] **H1.5 — The three routed protocol scripts are stale landmines.** *(Done 2026-08-01, BOTH remedies.
      Worse than recorded: `goodnight`/`phoenix` ran `taskkill /PID <parent_pid>` where the parent is now
      the BRAIN, and `ragnarok`'s POSIX branch was `shutdown -r +1` aimed at the SERVER — inert today only
      because the service user has no sudo, a permission accident rather than a design. (1) `run_protocol`
      now refuses routed protocols with an ERROR log, guarding the public sync entry point regardless of
      caller. (2) All three scripts replaced by inert stubs importing only `sys` and exiting **2**, so a
      misroute fails loudly instead of silently succeeding. Three tests encoded the pre-routing assumption
      and were re-aimed, not deleted: two asserted the scripts still contained the reboot/relaunch code
      (now asserted against the registry `pc_command`, where the behaviour actually lives) and one asserted
      a live run launches `names[0]` = goodnight (now picks a brain-side protocol). Verified on the live
      VPS: all three refuse, and `import subprocess` is gone from the deployed tree.)* `goodnight.py`, `phoenix.py` and
      `ragnarok.py` still contain the laptop-era logic (`taskkill`, Windows `shutdown`) that broke when the
      brain moved to the VPS. `run_protocol_async` now routes those three over PC_LINK and never executes
      them — but the sync `run_protocol` still does, and `protocols.py:179` still *requires the files to be
      present* to pass the gate. Any caller reaching the sync path runs the broken 2026-06 logic on the VPS.
      Either delete the three scripts and drop the presence check for routed protocols, or replace their
      bodies with an explicit "this protocol is PC_LINK-routed" guard.

### H2 · Correctness, consistency and quality

- [x] **H2.1 — `reliability.health_probe_and_surface()` (`reliability.py:221`) is dead and misleading.**
      *(Done 2026-08-01 — DELETED, not implemented, because the capability it pretended to provide already
      works: `health.py::health_signals` is registered at `proactive.py:609` and emits real Signals.
      `format_probe_report()` went with it — it had exactly one caller, the dead function. Also corrected
      the module docstring, which credited `health_probe()` itself with "surfaces a one-line status to the
      proactive engine" — the same false claim in different words; it probes and records, nothing more.
      Cleared a pre-existing unused `timedelta` import while in the file.)*
      Nothing calls it, and its docstring promises to "surface a one-line proactive signal" while the body
      imports `Signal`, uses it for nothing, and only logs. Delete it or make it do what it says. (Health
      *is* surfaced elsewhere via the registered `health_signals` source, so this is dead weight, not an
      outage.)
- [x] **H2.2 — Three tool modules have zero exception handling:** *(Done 2026-08-01, 22/22. Found a real
      crash the review missed: `channels` did `int(args.get("limit", 12))` inline, so the model filling
      that field from speech with "twelve" raised ValueError — now `_limit()` clamps and falls back.
      `random.choice(videos)`/`videos[0]` also assumed a non-empty list. Bigger lesson: I first decided
      NOT to touch `contacts`/`skills` because their bespoke prose read better than the generic helper —
      then my own test failed, because `tool_failed()` doesn't recognise those strings, so a digest or
      proactive caller would read the failure aloud AS DATA (the exact bug class test_tool_failure_guard
      exists for). Rephrased to match the detectable shape while keeping the specific cause.)* `documents.py`, `relationship.py`,
      `protocols.py` (9 tools between them). The agent's blanket catch (`agent.py:1225`) prevents a crash,
      but the owner hears "That tool hit an error: KeyError" instead of a graceful spoken degradation.
      Modules also missing the `tool_error` helper: `channels`, `contacts`, `localplay`, `skills`.
- [x] **H2.3 — schemas declaring properties but no `required` list**, so the model is never told
      which argument is mandatory. (Their handlers *do* degrade safely on missing args — verified — so this
      is a selection-quality problem, not a data-loss one.)
      DONE 2026-08-01. **The count above was wrong: 12, not 35.** The other 23 already declare
      `required: []`. Four of the six "worst offenders" were also already correct and were left alone:
      `send_telegram` legitimately allows an empty message when a gif/file is attached, and the three
      Notion task tools identify a row by *either* `query` or `page_id` — an OR this schema format
      cannot express, so naming either one as required would be a regression.
      Added `required: ["topic"]` to `objective_status`, `complete_objective`, `drop_objective`
      (objectives.py) and `approve_action`, `reject_action` (approvals.py) — these execute or mutate,
      and firing them blind is not recoverable. Added an explicit `required: []` to `describe_screen`,
      `screenshot_screen`, `read_screen_text` (multimodal), `look_around`, `visual_presence`,
      `enroll_owner_face` (camera), `sleep_summary` (fitness): every argument genuinely optional, now
      stated rather than implied. Guarded registry-wide in `test_finetune.py` (36/36) so a new schema
      cannot omit the key. Verified on the live VPS brain: 136 schemas, none missing `required`.
- [x] **H2.4 — 39 tool descriptions are under 120 characters** (`macros.delete_macro` is 21 chars,
      `utility.define_word` 41, `notion.notion_comment` 63). This is the exact text the model uses to choose
      a tool, and Part A already identifies wrong-tool selection as the dominant benchmark failure. Cheapest
      remaining lever on that score.
      DONE 2026-08-01. All 39 rewritten (min description is now 122 chars) to the house pattern:
      what it does including its real LIMITS → "Use for '<trigger>'" → a disambiguation line only
      where a sibling genuinely collides. Length was treated as the symptom, not the target.
      **Two were accuracy bugs, not brevity:** `news_brief` advertised "top tech/world stories" but
      only ever queries Hacker News, so it was actively winning turns about world and local news it
      cannot answer (now says Hacker News, and points at web_search); `drop_objective` advertised
      "Use for 'forget X'", the exact trigger belonging to `memory.forget` — "forget" is the owner's
      word for four different erasures, so `forget`, `delete_macro`, `drop_objective` and
      `delete_task` now each name the other three.
      Other collisions resolved: `weather` says RIGHT NOW only (no forecast — it requests `current`);
      `fx_rate` vs `convert` (convert forwards 3-letter codes to fx_rate); `git_status` vs `git_diff`
      (which files vs what changed); `draft_email` vs `send_email`; `stop_music_room` (Telegram) vs
      `stop_music` (laptop); `notion_append` vs `notion_create_page` vs `notion_comment`.
      COST: these are prefilled every turn, so the core catalog grew 2844 → 3209 tok/turn (+13%).
      Guarded in `test_finetune.py` (38/38) at BOTH ends — no description under 120 chars, and the
      core catalog capped at ~3.6k tok so a future spree can't re-inflate what Phase C shrank.
      Every cross-referenced tool name was checked against the live registry before being cited.
      Found in passing and fixed: `composio.py` used `logger` without importing it, so whenever the
      Composio API failed the handler raised NameError instead of degrading to its empty result.
- [x] **H2.5 — Dead references to `TODO-NOW.md`**, deleted in `75cea2d`: `protocols/checkpoint.py:14` lists
      it in `INCLUDE_FILES`, so every checkpoint archive silently omits it, and `coding.py:243` tells the
      owner out loud to "see TODO-NOW.md Phase 13" — a file that no longer exists.
      DONE 2026-08-01. **The second location is wrong**: `coding.py:243` is the PC_LINK no-fallback
      guard added in H0.1 — the line number is from a pre-H0.1 copy of the file. The three real
      references were `checkpoint.py:14` and two SKILL files, which matters more than a code comment
      because the model reads skills at runtime and acts on them.
      `checkpoint.py` now lists `TODO.md` (verified end-to-end: ran the protocol, TODO.md is in the
      archive — the loop skips names that don't exist, so every checkpoint since 75cea2d had quietly
      shipped without the roadmap). Both skill files were not merely stale but actively WRONG:
      `git-workflow.md` told Afon that `origin` isn't configured yet and that `git_push` would say
      so — `origin` is `iamvazghen/OpenAfon` and has been for some time, so that was a false
      statement to the owner (rewritten; push stays gated on green tests + explicit yes).
      `web-and-typescript.md` documented a `glasses/` TypeScript bridge that does not exist —
      MentraOS was NOT pursued and the half-finished client was deleted, so the skill was inviting
      Afon to edit a phantom directory (replaced with the truth: the phone camera is the mobile
      eye, reusing the same `LLMClient.see` surface).
- [x] **H2.6 — Dead config:** `wake_word_engine` and `porcupine_access_key` are settable in `.env` and read
      by nothing (Porcupine was replaced by openWakeWord). Setting them looks effective and is not.
      DONE 2026-08-01. Both fields deleted from `config.py` and both lines removed from `.env`. Safe
      because `model_config` sets `extra="ignore"` — checked BEFORE deleting, since with `extra="forbid"`
      removing a field while `.env` still set it would have broken brain startup.
      The surrounding comments were wronger than the keys. `.env` claimed "only 'afon' loads on
      openWakeWord today; rest pending Porcupine" — in fact `resolve_openwakeword_models` reports all
      three entries loadable (`hey_jarvis` + `afon.onnx` + `hey_afon.onnx`) and NOTHING pending,
      because custom phrases are loaded by PATH. It also claimed the threshold was "raised 0.5->0.6"
      while the live value is 0.4.
      Four places cited a wake-word training script that has never existed here — `.env:71` and
      `config.py` as `bench/train_wakeword.py`, `edge/wake_word.py:48` as `bench/train_wake_word.py`,
      and `docs/MASTER-PLAN.md:351` asserting outright that it "exists". (I wrote one of those four
      myself in this pass before checking; `ls bench/` is what caught it.) All now say the truth: the
      models were trained out-of-repo with openWakeWord's pipeline under WSL and live in `.wakewords/`.
      MASTER-PLAN 8.1 was still listed as deferred and is done as of 2026-07-01.
- [x] **H2.7 — `tools/mynews.py` is not a tool module.** It exposes 0 schemas / 0 handlers and is absent
      from `_MODULES`; it is a proactive signal source living in the tools package. Move it under `brain/`
      so "everything in tools/ is a tool" stays true.
      DONE 2026-08-01. `git mv` to `brain/mynews.py`; its single consumer (`proactive.py:623`) and the
      `config.py` comment updated. Worth noting the trap: that import sits inside `try/except
      Exception: pass`, so a botched move would have SILENTLY dropped the morning news signal with no
      error anywhere — verified by re-importing and asserting `proactive` now references
      `afon.brain.mynews` and no longer mentions `tools.mynews`.
      Guarded in `test_finetune.py`: every module in `tools/` must expose SCHEMAS/HANDLERS/
      LOCAL_HANDLERS, so the sentence stays true instead of being true only today. `base` is the one
      exemption (shared helpers, imported by the rest).
- [x] **H2.8 — Import cycle `tools/__init__.py ↔ tools/macros.py`.** It survives only because `macros`
      imports `tool_handlers` lazily *inside* the function; promoting that to a top-level import breaks
      brain startup. Worth a warning comment at minimum. (The other 11 mutual pairs are the deliberate
      proactive-signal lazy-import pattern and are fine.)
      DONE 2026-08-01. The claim was VERIFIED, not repeated: `__init__.py` imports `macros` at line 33
      while `tool_handlers` is not defined until line 215, so a hoisted import resolves against a
      module initialised only as far as line 33. Demonstrated by temporarily hoisting it in a
      subprocess — `ImportError: cannot import name 'tool_handlers' from partially initialized module
      'afon.brain.tools' (most likely due to a circular import)` — then restoring the file byte-for-byte.
      Went past "a warning comment at minimum", because a comment does not stop the regression it
      describes: module docstring explains the cycle and why the import must stay function-local; the
      second call site (`:338`) gained the `# lazy (avoid cycle)` note that only the first one had —
      which is precisely the site someone would have tidied; and `test_finetune.py` now parses macros.py
      with **ast** (not grep, so imports inside functions are correctly ignored) and fails if a
      top-level `from afon.brain.tools import …` ever appears. Negative-tested: clean today, fails on
      a simulated hoist.
- [x] **H2.9 — Error shipping has one structural blind spot.** Edge→brain shipping works (verified: 31 of 45
      edge entries reached the VPS journal), but the 13 `edge/brain_client` failures never arrive — they
      describe the very socket they would ship over. `diagnose` therefore cannot see brain-connection
      failures, which is precisely when the owner would ask. Consider a small local spool replayed on
      reconnect.
      DONE 2026-08-01. `ship_error` used to `return` silently when `_ws is None`; it now appends to a
      bounded (200) in-memory spool, drained on the next connect — AFTER `Hello`, so the brain has the
      session registered before the replay lands. Each replayed entry is marked `replayed: true` and
      keeps its ORIGINAL `ts`, so the VPS journal cannot be misread as "this happened at reconnect".
      In memory rather than on disk on purpose: `ship_error` runs inside the loguru sink, which must
      never block or do I/O, and the disk journal is already the complete durable copy — the spool
      only ever owed us the uplink.
      One trap worth recording: `_send` RETURNS False on failure rather than raising, so the obvious
      `try/except` around it would never fire and a link dying mid-drain would have silently discarded
      the remainder. It is a value check, and the un-sent tail is re-spooled (tested: 5 entries, socket
      dies after 2, exactly `x2..x4` survive — no gap, no duplicates).
      New `bench/test_error_spool.py` (15/15, registered in the gate) covers spool-on-down, the bound
      keeping NEWEST not oldest, replay + ordering, the `replayed` marker, mid-drain death, empty-drain,
      and no regression when the link is up. Also verified through the REAL `errors.record` path, not
      just the method in isolation.
      NOT covered, and deliberately so: `pc_agent` uses a different mechanism — its shipper is a
      closure over the live socket and is set to `None` on disconnect (`pc_agent.py:187`), so laptop
      *executor* errors during an outage remain local-only. H2.9 names `edge/brain_client`; extending
      the same treatment to pc_agent is a separate change.
- [x] **H2.10 — `protocols.py` docstring says "The three shipped protocols"; there are eight.** Also
      `diagnostics` and `auditpack` write their reports to the VPS filesystem, where the owner cannot read
      them.
      DONE 2026-08-01. Docstring now lists all eight (confirmed against `_registry()`), split by the
      distinction that actually matters and post-dates it: ROUTED (`goodnight`/`phoenix`/`ragnarok`,
      which carry a `pc_command` and may only leave via PC_LINK) vs BRAIN-SIDE (the other five).
      Report delivery: `checkpoint` writes a file too, so it is **three** protocols stranding output,
      not two. `run_protocol_async` now fires a follow-up task that waits for the artifact and hands
      it to the owner over Telegram — text reports inline (readable without downloading) plus the
      file, archives as an attachment with a size. Not awaited: the scripts are detached and take
      seconds to minutes, and a turn must not block on one.
      The load-bearing detail is the freshness check: delivery only considers files whose mtime is
      >= the moment the protocol started. Without it a run that wrote NOTHING would cheerfully
      deliver last week's report and look like it had succeeded — worse than the silence being fixed.
      New `bench/test_protocol_reports.py` (12/12, in the gate) covers registration, text inline,
      archive-as-attachment with no binary in the message, the stale-file guard, nothing-written, and
      an unlisted protocol. Existing suites still green (protocol_smoke 53/53, drills 21/21).
- [x] **H2.11 — DONE 2026-08-09. `weather` now answers the day it was asked about.**
      Added a `when` argument ('today' / 'tomorrow' / 'this week', parsed from free text since the
      model writes what the owner said). Forecast modes request `daily=` from the SAME open-meteo
      endpoint; current mode is unchanged. WMO codes are collapsed to spoken words ("overcast",
      "showery"), and a week is summarised as a span plus the wettest day rather than seven
      unlistenable lines.
      **The trap worth naming: the 900s cache key had to carry the mode.** Without that, asking
      "tomorrow" right after "today" serves today's cached sentence — the original bug with a
      15-minute fuse, and it would have passed any test that only called one mode.
      Verified live against open-meteo (Berlin: 19°C now / 30°C high tomorrow / 34.6°C week high)
      and hermetically in `bench/test_weather_when.py` 13/13. Checked the raw payload by hand to
      confirm index 1 really is tomorrow's date — reading index 0 would have reproduced the bug
      while still looking like a forecast. Re-inserted the current-only behaviour to confirm the
      test FAILS on it (7 checks fail, exit 1).
      *Original entry:*
- [ ] ~~**`weather` answers forecast questions with TODAY's conditions.** Found while verifying
      H2.4 on the live brain: "what's the weather in Berlin tomorrow" selects `weather`, which requests
      only `current=` from open-meteo, so the owner is told today's temperature in reply to a question
      about tomorrow — confidently and wrongly. Steering by description does NOT fix this: H2.4 added
      "RIGHT NOW only; for a forecast use web_search" to the description and the model still picked
      `weather` (and `forecast` is in `_READ_INTENT_PATTERNS`, so a tool call is forced regardless).
      The robust fix is to stop steering and support it: the SAME open-meteo endpoint already serves
      `daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code` — add a
      `when` argument ('today'/'tomorrow'/'this week') and answer properly. This predates H2.4 (the
      tool was always current-only); H2.4 only made it visible. NEW CAPABILITY, so it is listed rather
      than folded into a description task — owner's call.~~
- [ ] **H2.13 — `deploy_vps.sh` never deletes; the VPS accumulates orphans.** *(PARTLY CLOSED
      2026-08-08: orphans are now DETECTED — J4.1 wired `verify_vps_sync.sh` into the deploy, and a
      stale file aborts the run before the brain restarts, printing the exact `rm`. What remains is
      the mechanism change below, which is still a deliberate decision rather than a passing fix.)* `scripts/deploy_vps.sh:31`
      pipes `tar -czf - src/afon …` into `tar -xzf -` on the target, which OVERWRITES and ADDS but
      never removes. A file deleted or renamed locally therefore lives on the brain forever. Found via
      H2.7: after a green deploy the VPS had BOTH `brain/mynews.py` and the old
      `brain/tools/mynews.py` — 144 remote files against 143 local. The stale copy was inert (nothing
      imported it) and has been removed by hand, and a full tree diff confirmed it was the only orphan,
      because this was the first MOVE in the deployed tree. It will not be the last.
      Risk is not the wasted file; it is a deleted module still being importable on the host, so
      something that should fail loudly keeps working from a stale copy — and every local test passes
      while the brain runs code that no longer exists in the repo. Also worth noting: the local
      "everything in tools/ is a tool" guard cannot see this, since it inspects the repo, not the host.
      Fix is `rsync -a --delete` for `src/afon` (state lives in `.env`/sessions/voiceprint, which the
      script already keeps out of the payload), or extract to a fresh dir and swap. NOT done here:
      changing the deploy mechanism risks leaving the brain half-updated if extraction fails midway,
      and that is a decision to make deliberately rather than in passing. Until then, a tree diff after
      any deploy that removes a file is the cheap mitigation.
- [x] **H2.12 — DONE 2026-08-08. `if_then` now applies the operator it parses.**
      Subjects resolve to a VALUE (int for booleans/counts, str for `weekday`) instead of a bare
      truthy flag — that is the whole fix, since an operator needs something to compare against.
      `_compare` handles `== != > >= < <=` numerically, `in`/`contains` on a 3-letter stem so
      "monday" and "mon" both work, and spoken booleans (`false`/`no`/`none` -> 0).
      Behaviour change worth noting: an **unknown subject now refuses** rather than silently
      evaluating false. Quietly running the else-branch of a condition nobody evaluated is the
      same class of fault as the original bug, so it should not be the default.
      Dead leftovers removed at the same time: `synthetic` and the `_rm` self-alias.
      Verified: new `bench/test_if_then_operator.py` 10/10 — and the old advisory-operator
      behaviour was re-inserted to confirm the test FAILS on it (4 checks fail, exit 1).
      *Original entry:*
- [ ] ~~**`if_then` parses a comparison operator and then ignores it** (`macros.py:271`, with
      `:295` conceding "op is currently advisory"). `has_unread_email == 0` and `has_unread_email != 0`
      therefore do exactly the same thing. Worse, `== 0` is the example the tool's OWN error message
      tells the owner to write, and it runs BACKWARDS: `truthy` is True when unread mail exists, so
      "if I have no unread email, then…" fires precisely when he does have some. Any macro built on a
      negative condition is silently wrong. Fixing it means deciding comparison semantics per subject
      (boolean subjects vs `weekday contains mon`), which is a design call, not a lint fix — hence
      logged rather than patched. `synthetic` (`:301`) and the `_rm` alias (`:303`) are dead leftovers
      to remove at the same time.~~

---

## Part I — Carried over from the vault Behavioural Improvement Plan (2026-07-28)

Source: `30-Projects/Active/afon-behavioural-plan-2026-07-28.md` (VPS vault, canonical). Only the
**unchecked** items are carried here; everything the plan marks `[x]` is shipped and omitted. Four items
were checked against the live system before being carried, and are annotated where reality has moved on
since the plan was written — the plan's own later addendum supersedes parts of its earlier body.

### I1 · Identity (voice + face) — the plan's highest-priority follow-up
- [ ] **Re-enroll the voiceprint from the live mic array** (`bench/enroll_voice.py`, script at
      `to-read-script.md`, ~3 min owner action). Measured cause: owner-accept median is 0.33 and the
      impostor ceiling is 0.30 — the distributions **touch**, so no threshold can separate them. The
      voiceprint was enrolled under different acoustics than the live mic. Target owner median ≥0.6.
- [ ] **Then** re-measure and raise `speaker_threshold` to the observed valley (the plan expects
      ~0.45–0.50). ⚠️ **Sequenced, not immediate** — the plan's own 2026-07-30/31 addendum rules that the
      threshold **stays at 0.30** on today's data, because raising it now starts rejecting the owner. This
      item unblocks only after re-enrollment demonstrably moves the owner distribution.
- [x] Wire face-ID (pc_agent camera over PC_LINK, prod-verified Jul 25) as a **second factor for
      privileged actions**; voice alone stays sufficient for benign queries. Distinct from the shipped
      camera-on-suspicion check, which is a borderline-band tiebreak, not an authorisation factor.
      DONE 2026-08-01. `camera.verify_owner_present()` returns a STRUCTURED verdict
      (`available`/`matched`/`faces`) rather than `visual_presence`'s prose, which is right for the
      owner and useless to a gate. Deliberately NOT a tool — nothing should let the model decide
      whether the owner is present. Runs on the laptop via a new `camera_verify` PC_LINK op, so
      `owner.npy` never leaves that machine; only the verdict crosses.
      Enforced in `agent._execute_calls` **after** the spoken yes and only for `confirm_required`
      calls, so it costs a camera burst on the handful of actions that can actually lose something,
      never on an ordinary turn (asserted in the test: a `get_time` call consults no camera).
      FAIL-OPEN is the load-bearing property, not the feature: unavailable camera, unenrolled face,
      offline laptop or a raised exception all mean CAN'T TELL and behave exactly as before. It only
      ever adds a refusal when the camera positively shows he is not there — and it distinguishes
      "nobody at the desk" (the television case) from "someone who isn't you". One flag,
      `face_second_factor`, turns it off.
      `bench/test_face_second_factor.py` 14/14, in the gate. Cost worth knowing: a confirm-gated
      action now waits on a 12-frame burst before it runs.
      ⚠️ This does NOT substitute for re-enrolment. It hardens the actions that were already gated;
      an un-gated turn still reaches the brain on voice alone, which is why the 61-accepts problem
      above stays open until the voiceprint is redone.
- Acceptance: 0 YouTube-triggered responses over 7 days AND 0 owner rejections.
- **LIVE EVIDENCE 2026-08-01** (found while checking I5, and it strengthens the case for re-enrolment):
  `logs/edge.log` records **61 speaker-gate accepts and 0 rejections** — the gate has never once
  refused anything. Accept scores cluster 0.31–0.63 against the 0.30 threshold, i.e. essentially
  everything clears it, which is what "the distributions touch" looks like in production rather than
  in a bench measurement. The accepted transcripts are ambient Russian speech — 'Я говорить не буду',
  'Давай, брат, духочек', 'пойду скажу' — plainly a TV or video near the mic, not the owner
  addressing Afon. Each one was forwarded to the VPS brain as though he had said it.
  This makes the ordering in I1 concrete: the gate is currently a no-op, and it cannot be fixed by
  moving the threshold (0.31 accepts would need >0.31, which is already inside the owner's own 0.33
  median). Re-enrolment first, exactly as sequenced.

### I2 · Proactivity — from random to purposeful
- [ ] **Put the owner's real routine into `routines.json`** — training days/time, language-learning slot,
      evening review. Verified live on the VPS: the file currently holds only `training` and `reading`,
      both `dynamic` (no fixed times), and the seeded morning-stretch window is gone. The
      language-learning slot and evening review are **absent**. Needs one sentence from the owner per
      routine.
- [ ] Extend `anticipatory_prep` beyond the calendar to **Notion due-today tasks** at the morning anchor.
- [ ] Review the coaching gate after 7 days of routine-anchor data. ⚠️ **Reframed:** the addendum finds
      coaching is *not* broken — it emits a valid signal in its 18:00–22:00 window and has simply never
      been taken up. Verified: `afon_coaching.sqlite` on the VPS is untouched since Jul 14. Treat as an
      **adoption** question (is the offer worth making?), not a defect to fix.
- Acceptance: every notification names the routine or event it serves; zero notifications 23:00–07:45.

### I3 · Notion / task work — end-to-end professional
- [ ] Comment format: lead with the deliverable, then sources; suppress process narration.
- [ ] After one week, sample 5 real comments and grade them against "would a paid assistant send this?"

### I4 · Reliability / failover
- [ ] **Rotate the MiniMax + Vercel keys** — both appeared in a terminal transcript on 2026-07-28. Still
      outstanding (owner action). This is the oldest open security item in the plan.
- [x] ~~Google auth re-authorization~~ — **done and verified**, superseded by the plan's own addendum
      (one consent covering Gmail + Calendar + Fitness; calendar reads verified live). Confirmed on the
      VPS: `google_client_id` is set. Carried here only so it is not re-opened by mistake.

### I5 · Latency — measured, not felt
- [ ] Collect **3 days of turn-tracer numbers** on the fixed edge, then tune — nothing else changes until
      the data exists (the perceived lag was event-loop starvation, already fixed). Target: **≤2.5s median
      total for a no-tool turn**. Baseline TTFT for reference: minimax 0.77s / groq-70b 0.23s /
      vercel-haiku 0.75s.
- [x] **First verify the tracer actually emits.** It is wired (`edge/assistant.py:203`,
      `edge/latency_meter.py`) and deployed to both hosts, but `logs/edge.log` covers 2026-07-29 01:10 →
      2026-07-31 05:50 with **zero** `turn latency:` lines. Either no voice turn has completed in that
      window or the tracer is not firing — one spoken turn disambiguates it, and there is no point
      starting a 3-day collection until it does.
      ANSWERED 2026-08-01, and it is the second cause, not the first. The log now runs to 2026-08-01
      23:33 and still contains **zero** `turn latency:` lines — but it is NOT idle: 61 utterances were
      transcribed and routed (`remote_brain:_route_utterance` — "heard: '…' -> VPS brain"). So turns
      start and reach the brain.
      `TTFWMeter` emits only on `BotStartedSpeakingFrame` with a prior `UserStoppedSpeakingFrame`
      (`latency_meter.py:78`). There is no evidence any turn ever reached the speaking stage: zero
      bot-started/TTS-generation events after a routed utterance, and zero reply/chunk frames logged
      from `remote_brain`. **So the tracer is silent because nothing gets as far as speaking, not
      because the tracer is broken.** NB the absence of a logged reply is not proof the brain sent
      none — inbound chunks may simply not log at INFO. The next step is therefore one deliberate
      spoken turn with the edge log tailed, which separates "brain never replied" from "reply arrived
      but never spoke"; the 3-day collection stays blocked until a single trace line appears.
      ⚠️ The 61 utterances are the I1 problem, live — see the evidence recorded under I1.

### I6 · Memory — one brain, not two
- [ ] **One-time reconciliation, by hand and verified**: export the laptop's TaskQueue + embeddings and
      merge into the VPS store, deduping by content hash; the laptop then becomes cache-only. Measured
      divergence at the time of the plan: laptop 105 tasks / 1,219 embeddings / 297 presence rows vs VPS
      6 / 874 / 5,948 — the edge had been running against a local brain for weeks (session-pinning bug,
      since fixed).
- [ ] Add a **weekly drift check** to the fleet compliance audit so the two stores cannot silently
      diverge again.

### I7 · MCU-AFON demeanour
- [ ] **Brevity pass on tool prose**: answers lead with the outcome, one supporting clause, no filler.
- [ ] **Timing test in bench**: assert that no proactive kind can fire outside its purpose window. (No
      such test exists today — the closest are per-feature window tests in `test_coaching.py` /
      `test_interventions.py`; there is no cross-cutting guarantee.)

### I8 · External keys still pending (from the plan's addendum)
- [ ] `AFON_TWILIO_*` — activates the parked `place_call` / `send_sms` / `send_whatsapp`. Verified
      UNSET on the VPS.
- [ ] `AFON_GOOGLE_MAPS_API_KEY` — `travel_time` currently has no live traffic (OSRM fallback only) and
      `find_place` is dead. Verified UNSET on the VPS.
- [x] ~~`AFON_WOLFRAM_APP_ID`~~ — **already set on the VPS** (verified live); `compute` is live. The
      plan's "keys pending" line is stale on this one.
- [ ] Google **Fitness API** needs one enable-click in the Cloud console — parked with the wearable
      hardware.

### I9 · Explicitly deferred by the plan (record, do not schedule)
- Home Assistant (device not bought) · wearable / Twilio / Maps *toolkits* via Composio (each toolkit
  taxes the catalog) · automating the memory reconciliation (do the one-time manual merge first).

---

## Part J — Graph-report structural review (2026-08-02)

Source: `graphify-out/GRAPH_REPORT.md` (3509 nodes · 6436 edges · 257 communities, built from
commit `fc8b72d8`). **58 items**, none implemented — this part is a survey, not a work log.

**Read the source with two caveats before trusting any item below.** First, the report predates
67 uncommitted files (all of Part H plus I1), so a handful of these may already be closed — each
item says whether I verified it against the *working tree* or only against the report. Second,
9% of the graph's edges are model-INFERRED at 0.65 average confidence, so any claim resting on an
inferred edge alone is a hypothesis, not a finding.

Three hypotheses I formed from the report and then **disproved** against the code, recorded so
nobody re-derives them: `_dispatch` is *not* duplicated per module (centralised in `system.py`);
the degradation string is *not* scattered (centralised in `base.py:not_configured`); and the graph
commit *does* match HEAD (the staleness is uncommitted-work staleness, J0.1, not commit drift).

### J0 · Graph integrity — the report cannot be trusted further than these allow
- [x] **J0.1 — CLOSED 2026-08-09.** `scripts/graph_fresh.py` compares `graph.json`'s mtime against
      every tracked source file's, and preflight prints the answer as an `info` line. Demonstrated
      side by side before the fix: `built_at_commit` == `HEAD` **exactly** (documented check: fresh)
      while four source files were newer (truth: stale, +644 min). It deliberately does NOT gate —
      this repo commits rarely by policy, so red would be the resting state, and a check that is red
      by design gets ignored exactly like a false green does. `bench/` is excluded from the count
      because J8.3 excluded it from the graph, so a test edit cannot report a staleness no rebuild
      would clear.
      *Original finding:* The report says
      "run `git rev-parse HEAD` and compare". HEAD *is* `fc8b72d8` — yet `git status` shows **67
      modified files**, including `agent.py`, `camera.py`, `config.py`, `protocols.py`,
      `brain_client.py`, a file rename and 4 new test files. The check compares commits, so an
      entire session of uncommitted work reads as "fresh". Freshness must compare the working
      tree (mtime or `git status`), not the commit.
- [ ] **J0.2 — 607 INFERRED edges (9%) at 0.65 average confidence are unverified. (P1)** At that
      confidence roughly a third are wrong and nobody knows which third. `AfonAgent` alone carries
      **48** inferred edges, `LLMClient` **11** — i.e. the two most structurally important nodes are
      also the two most speculatively connected. Verify or prune; an unverified edge on a god node
      corrupts every path query through it.
- [x] **J0.3 — CLOSED 2026-08-09 by J8.3.** With `bench/` out of the graph, `bench/* → src/*` edges
      cannot be surfaced as surprising at all — the heuristic no longer has the input.
      *Original finding:* All five entries are
      INFERRED and all five are `bench/*` → `src/*` (`behavioral_suite`→`AfonAgent`,
      `coding_skills_bench`→`LLMClient`). A test file importing the code it tests is the least
      surprising edge in any codebase. Exclude test→impl edges from that heuristic or the section
      stays noise forever.
- [ ] **J0.4 — 218 isolated nodes (<=1 edge); the entire ops layer is one of them. (P1, VERIFIED)**
      `deploy_vps.sh`, `deploy_docs.sh`, `live-check.sh`, `install-live-check.sh`, `install-brain.sh`,
      `verify_vps_sync.sh`, `run.sh`, `termux/install.sh`, `vps/install.sh`, `pre-push`, `post-commit`
      — all disconnected. The graph cannot answer *"what does a deploy touch"*, which is exactly the
      question that would have caught H2.13.
- [ ] **J0.5 — 42 thin communities (<3 nodes) are silently omitted from the report. (P2)** 16% of
      the graph's communities are invisible in the artefact the review is based on. Either render
      them or state their names, so "not in the report" stops meaning "doesn't exist".
- [x] **J0.6 — CLOSED 2026-08-09: the premise was wrong, and it was marked VERIFIED.**
      `scripts/githooks/post-commit` (this repo sets `core.hooksPath`, so `.git/hooks/` holds
      nothing but `.sample` files — which is what I first looked at, and briefly concluded there
      were no hooks at all) already contains exactly this:
      `( cd "$(git rev-parse --show-toplevel)" && graphify update >/dev/null 2>&1 & )`
      Executed the hook body directly rather than trusting the code: `graph.json` rebuilt
      3,751,584 -> 3,967,812 bytes. **It works.**
      I ran `graphify hook install` before checking, which appended ~150 lines of its own hook
      AFTER the existing `exit 0` — dead code — plus a `post-checkout` hook, a `.gitattributes`
      merge driver and two git-config entries. All reverted; `git status` clean of them.
      *The actual, much smaller gap:* the graph tracks the last COMMIT, and this repo carries 373
      modified files by standing policy (nothing is committed or pushed unless asked). So the
      graph is structurally behind the working tree — not because a hook is missing, but because
      commits are deliberately rare. Refresh by hand with `graphify update` after large changes;
      an uncommitted-work trigger would fire constantly and is not worth it.
      **Lesson: a "VERIFIED" tag in this document is not evidence.** This one was wrong in both
      directions — the hook exists AND runs graphify — and cost a needless install-and-revert.

### J1 · God nodes — the coupling the graph is shouting about
- [ ] **J1.1 — `AfonAgent`: 111 edges, betweenness 0.156, spans 33 communities. (P1)** The single
      worst structural offender and the report's own top Suggested Question. It currently owns turn
      orchestration, tool execution, confirm gating, the face second factor, streaming sentence
      emission, memory retrieval and failover. Split candidates in dependency order: tool
      execution + gating -> its own executor; streaming/sentence assembly -> its own emitter; memory
      retrieval -> the facade in J3.3.
- [ ] **J1.2 — `tool_error()` is the most connected node in the entire system (132 edges). (P1)**
      The system's largest hub is an *error-string formatter*. Failures are prose: no caller can
      branch on failure KIND (not-configured vs network-down vs auth-expired vs bad-args) without
      matching substrings. See J7.3.
- [ ] **J1.3 — `not_configured()` has 61 edges and its contract is asserted by substring. (P2,
      VERIFIED)** The helper itself is properly centralised in `base.py` — but tests assert
      degradation by grepping the literal prose `"isn't configured yet"`
      (`test_new_integrations.py:50`). Rewording one sentence silently breaks the guarantee across
      61 call sites. The assertion should test a marker, not the wording.
- [ ] **J1.4 — `clip()` has 50 edges — a truncation helper is the 4th-largest hub. (P2)** Spoken-
      output length policy is applied at 50 scattered call sites instead of once at the speech
      boundary. Every new tool must remember to call it; nothing catches one that forgets.
- [ ] **J1.5 — `LLMClient` betweenness 0.080, bridging vision / camera / multimodal / 4 test files.
      (P2)** Model access has no seam: `camera.py` and `multimodal.py` reach the LLM directly rather
      than through the agent, which is why vision changes keep touching the routing layer.
- [ ] **J1.6 — The proactive cluster is a second god-complex with no facade. (P2)** `Signal` (41),
      `ProactiveEngine` (30), `Presence` (30) — three of the top ten hubs, spread across communities
      0, 12, 15, 79, 81, 109, 120 with no single entry point.
- [ ] **J1.7 — `BrainServer` (42 edges) mixes six concerns. (P2)** WebSocket transport, auth, session
      lifecycle, speak-routing to clients, scheduler wiring and proactive emission in one class.
- [ ] **J1.8 — `Settings` is one flat object and `config.py`'s community scores 0.09 cohesion. (P2)**
      Every subsystem reads the same namespace; nothing expresses which settings belong to edge vs
      brain vs tools. This is why H2.6's dead-key deletion needed a manual audit of `extra="ignore"`.

### J2 · Cohesion — communities that should be split
Baseline: the eight worst are all **core** subsystems, not peripheral ones. Cohesion below ~0.10
means the nodes grouped together barely reference each other.
- [ ] **J2.1 — Community 53 "TaskWorker": 49 nodes, cohesion 0.05 — the worst in the graph. (P1)**
      Largest-but-one community *and* joint-lowest cohesion: the objectives/worker/queue surface has
      no internal structure at all.
- [ ] **J2.2 — Community 2 "notion.py": 65 nodes (largest), cohesion 0.08. (P1)** Mixes Notion CRUD,
      the autonomous backlog worker (`attempt_backlog`), world-model population (`_default_refresh`)
      and reminder cancellation (`_cancel_task_reminder`). At least three modules wearing one name.
- [ ] **J2.3 — Community 1 "routines.py": 42 nodes, cohesion 0.06. (P1)**
- [ ] **J2.4 — Community 4 "AfonAgent": 29 nodes, cohesion 0.05. (P1)** The structural half of J1.1.
- [ ] **J2.5 — Community 3 "WorldModel": 30 nodes, cohesion 0.07. (P2)**
- [ ] **J2.6 — Community 0 "Presence": 17 nodes, cohesion 0.104. (P2)** The report names this one
      explicitly: *"Should `Presence` be split into smaller, more focused modules?"* — media context,
      categorisation, sampling, aggregation and streak-tracking in one unit.
- [ ] **J2.7 — Communities 12 / 8 / 18 at 0.08 (ProactiveEngine, StdioMCPServer, brain/coaching).
      (P2)**
- [ ] **J2.8 — Community 79 "push" (26 nodes, 0.09): eight near-identical `_fire_*` functions. (P2)**
      `_fire_backlog`, `_fire_backup`, `_fire_briefing`, `_fire_objectives`, … — a table-driven
      firing surface would collapse these and make "does every fire path report?" checkable in one
      place. (That question is not hypothetical: `_fire_backlog` logging-but-not-reporting was the
      root cause fixed on 2026-07-26.)
- [ ] **J2.9 — Community 55 "errors.py" (0.10) installs global monkeypatches. (P2)**
      `_bridge_stdlib`, `install_asyncio_handler`, `_install_hooks`, `_patcher` rewrite the logging
      and asyncio stacks process-wide. Powerful, invisible, and untested against library upgrades.
- [ ] **J2.10 — Community 6 "http_get" (0.09) groups unrelated concerns. (P2)** HTTP helpers, the
      runtime-preferences store and home-location resolution share one community for no reason.

### J3 · Duplication the graph makes visible
- [ ] **J3.1 — 112 of 157 bench files define their own `check()`; 129 define `main()`; no shared
      harness exists. (P1, VERIFIED)** This is why `main` appears as a **community hub 14 separate
      times** and why test nodes collapse into implementation communities, depressing every cohesion
      score in J2. One `bench/_harness.py` deletes ~112 copies of the same ten lines *and* makes the
      graph legible. Highest structural return of anything in Part J.
- [ ] **J3.2 — Three reminder-cancellation paths. (P2, VERIFIED)** `reminders.cancel_reminder` +
      `reminders._cancel_on_ticker`, `tasks._cancel_reminder`, `notion._cancel_task_reminder`. Three
      places to forget when the ticker contract changes.
- [ ] **J3.3 — Seven memory stores, five with their own sqlite connection, no unified recall. (P1,
      VERIFIED)** `coaching.py`, `graph.py`, `presence.py`, `semantic.py`, `tasks.py` each open their
      own DB; plus `MemoryStore` (L1/L2), `DocStore`, `Cache` (L4), `RelationshipMemory`,
      `patterns.py`. Every consumer fans out by hand — which is exactly the shape that produced the
      I6 "one brain, not two" problem.
- [ ] **J3.4 — Two divergent persona templates. (P2, VERIFIED)** The graph shows communities 211 and
      228 *both* titled `{assistant_name} — Persona`, with different section sets — 211 has
      "Proactive companion" and "Protocols (password-gated)", 228 does not. On disk:
      `personality/afon.md` and `personality/persona.example.md`. A stranger cloning the repo
      configures the one that is missing two sections.
- [ ] **J3.5 — Music playback is split across three communities. (P2)** `voicechat.py` (`play_music`,
      `_yt_search`, `_ytmusic_search`), `channels.py` (`play_latest/random_from_channel`),
      `localplay.py` (`play_file`, `now_playing`, `stop_music`). **`stop_music` lives in a different
      module from `play_music`** — the stop path cannot see what the play path started.
- [ ] **J3.6 — Four independent answers to "what is broken right now". (P2)**
      `reliability.health_probe`, `Signal.health_signals`, `hud_snapshot._health`,
      `diagnose.summary`. Nothing guarantees they agree; a component can be degraded in one and
      healthy in another.
- [ ] **J3.7 — Nine health/reliability communities, no facade. (P2)** reliability · voice_health ·
      uptime_watch · diagnose · errors · Metrics · watch_audio_liveness · singleton · _supervisor.
- [ ] **J3.8 — Composio is split router/catalog with a third `_configured()`. (P2)** Community 110
      (`composio.py`) and 108 (`composio_catalog.py`); `_configured()` also defined independently in
      `phone.py`.
- [ ] **J3.9 — Camera has three detection paths and two capture paths. (P2)** `_detect_boxes`,
      `_detect_faces`, `_gray_faces` in `camera.py`; capture via `_capture_burst` (camera.py) *and*
      `_capture_jpeg` / `_camera_capture_local` (community 141, `look_around`). The I1 second factor
      picked one of these; nothing says it picked the right one.
- [ ] **J3.10 — Private helpers duplicated across modules. (P2, VERIFIED)** `_norm()` (MemoryStore,
      GraphMemory) · `_db_path()` (coaching, presence, tasks, graph) · `_parse_hhmm()` (serve,
      Handler) · `_enabled()` (system, coding) · `_configured()` (phone, composio) · `_load()` (>=5).

### Rename · the laptop autostart was dead the whole time (found 2026-08-09)
- [ ] **Re-register the laptop Scheduled Tasks. [needs an elevated shell — owner action]** Found
      while writing `SOP.md`, by listing the tasks instead of documenting what the install scripts
      *would* create. Live state on 2026-08-09:
      | task | state | points at |
      |---|---|---|
      | `WatariPcAgent` | Ready | `C:\Jarvis\.venv\Scripts\pythonw.exe -m jarvis.edge.pc_agent` |
      | `WatariEdgeRefresh` | Ready | `C:\Jarvis\scripts\restart_edge.ps1` |
      | `AfonEdgeGuard` | **Disabled** | `afon-guard-hidden.vbs` |
      **`C:\Jarvis` does not exist**, and no python process was running. So: no edge, nothing to
      start one at logon, and the 30-minute watchdog whose entire job is to notice a dead edge was
      switched off. **A Scheduled Task whose executable is missing fails silently** — no error, no
      log, the edge is simply never there. Every `Start-ScheduledTask Afon*` command in the docs
      would have appeared to work while doing nothing.
      **The general lesson:** the rename moved the *code*. It does not move Scheduled Tasks, systemd
      units, or anything else that stores an absolute path — and each of those fails in its own
      quiet way. Preflight already covered state dirs, face refs and the env prefix; it had no
      concept of "something outside the repo points INTO the repo".
      Fix: unregister the two `Watari*` tasks, re-run `install_edge_autostart.ps1`,
      `install_edge_refresh.ps1`, `install_edge_guard.ps1`, confirm two `pythonw` processes.
- [x] **Guard added 2026-08-09: `preflight.sh` §1e** asserts every `*Afon*`/`*Watari*` Scheduled Task
      resolves to a path that exists and that the guard is not Disabled. Checks the `-File` argument
      too, not only `Execute` — `WatariEdgeRefresh` runs `powershell.exe`, which exists, so an
      Execute-only check declared it healthy while its script was missing. Verified in both
      directions: red on the real breakage (all three named), and green on a machine with no such
      tasks, so a fresh clone that never installed the edge is not punished.

### Rename · state was left behind on BOTH sides (found 2026-08-08)

The rename moved code and paths but not the data those paths point at. Two instances, same
root cause, both silent — nothing errored, the app simply started fresh.

- [x] **Enrolled face refs were stranded.** `~/.jarvis/faces/owner.npy` held **75 enrolled
      refs** (256-dim LBP histograms, last written 2026-07-20). `camera.py` reads
      `~/.afon/faces/owner.npy`, which did not exist, so `_owner_refs()` returned None and every
      owner check reported "not enrolled" — a *correct-looking* answer, which is why nobody
      noticed. FIXED 2026-08-08: copied (not moved — the old tree is untouched), 75 refs verified
      loadable at the new path. Same for `room_context.json`, which had no counterpart at all.
- [ ] **Learned state is still split and needs a decision.** These have content on BOTH sides, so
      copying would destroy today's fresh data and merging is not obviously safe:
      `patterns.jsonl` **148,688 -> 1,630 bytes** (months of behavioural patterns vs today's) ·
      `relationship.json` **6,089 -> 1,394**. Append-only JSONL could be concatenated after a
      backup; `relationship.json` is a single document and must be merged by hand or chosen.
      `errors.jsonl` (1.7MB old) is diagnostic and probably not worth carrying.
      **Until this is decided Afon is running on a week-old memory of the owner**, having quietly
      discarded the accumulated version.
      *Lesson worth keeping: a rename checklist must enumerate STATE directories, not just code
      paths and service names. Both halves of this were missed the same way.*

### Rename · the VPS side is outstanding (found 2026-08-08 by J4.1)

- [ ] **The Watari/Jarvis -> Afon rename never reached the VPS.** Local repo, scripts and Windows
      scheduled tasks are Afon; the VPS still has `/home/openclaw/jarvis`, `jarvis-brain.service`
      and `jarvis-ticker.service`. `deploy_vps.sh` could not deploy at all until the preflight
      landed (it failed at remote `tar: Cannot open`, exit 2 — loud, so nothing was ever
      half-deployed, but the message named neither cause nor fix).
      **This is a decision, not a task, so it is not done here.** Two ways, pick one:
      **(a) Finish the rename on the VPS** — `mv ~/jarvis ~/afon`, rewrite the two unit files and
      their `WorkingDirectory`/`ExecStart`, `systemctl --user daemon-reload`, re-enable, restart.
      Matches the scripts' defaults and the intent of the branch. Costs a restart of a live 24/7
      brain and touches systemd units under linger.
      **(b) Point the scripts at the existing names** — add `AFON_VPS_DIR=/home/openclaw/jarvis`
      and `AFON_VPS_SERVICE=jarvis-brain` to `scripts/deploy_vps.env`. Zero risk, thirty seconds,
      leaves the deployed side named Jarvis forever.
      (a) is the right end state; (b) is the right thing to do *first* if a deploy is needed today.
      Note `~/jarvis` also contains a stray directory literally named `C:` — a Windows path that
      leaked into a remote command at some point. Harmless, worth removing whichever route is taken.

### J4 · Missing edges = missing safety nets (highest value in Part J)
- [x] **J4.1 — `verify_vps_sync.sh` is DEAD CODE, and it is the exact check H2.13 needed. (P0,
      VERIFIED)** DONE 2026-08-08. Wired into `deploy_vps.sh` AFTER the sync and BEFORE the restart,
      so the running process is never pointed at a tree already known to be wrong; aborting there
      leaves the brain up on its current code, which is no worse than not deploying. Pruning is
      deliberately NOT automatic — deleting files on a live always-on brain is a decision, not a
      deploy step — so it prints the exact `rm` to run.
      **Running it for the first time immediately found something bigger than orphans:** the
      Watari/Jarvis -> Afon rename never reached the VPS. `deploy_vps.sh` pointed at
      `/home/openclaw/afon` and unit `afon-brain`; the VPS actually has `/home/openclaw/jarvis`,
      `jarvis-brain.service` and `jarvis-ticker.service`. **The deploy has therefore been broken —
      loudly (`set -euo pipefail` + remote `tar: Cannot open`, exit 2), so nothing was ever
      half-deployed — but with a message naming neither cause nor fix.** Added a preflight that
      checks the directory and the unit BEFORE syncing and prints the candidates it found; the
      service name is now `AFON_VPS_SERVICE`-overridable like `AFON_VPS_DIR`. See the new
      "VPS side of the rename" item. It forms its own island (community 229, cohesion 0.80) and is referenced from
      **nowhere** but its own usage comment. It hashes local vs remote trees — precisely what would
      have caught the orphaned `brain/tools/mynews.py` that `deploy_vps.sh`'s tar-into-tar left on
      the live VPS. Wire it into the deploy gate; a verifier nobody calls is worse than none,
      because its existence implies the check is happening.
- [x] **J4.2 — `pc_agent.py` merges `LOCAL_HANDLERS` from 7 HARDCODED imports; nothing guards
      completeness. (P0, VERIFIED)** DONE 2026-08-08. The list stays EXPLICIT on purpose — it encodes
      "these ops must run on the laptop", not "these modules happen to export handlers", so
      auto-discovery would wrongly route a brain-side module. Instead `bench/test_pc_agent_routing.py`
      parses (never imports — importing every tool module would open cameras and browsers) the tools
      dir and fails if a module exports `LOCAL_HANDLERS` and is neither routed nor listed in
      `DELIBERATELY_BRAIN_SIDE`. Adding a module now forces a one-line decision instead of a silent
      omission. Also catches the reverse — a routed module that stopped exporting handlers. Guard
      verified by planting a probe module: it failed and named it, then passed once removed. 7/7. `system`, `camera`, `browser`, `coding`, `localplay`,
      `documents`, `audioout`. A new tool module exporting `LOCAL_HANDLERS` is **silently unrouted**
      until someone remembers to edit `pc_agent.py`, and the failure mode is a PC op that reports
      "unknown PC op" at runtime rather than failing any test. Note honestly: this session's I1
      `camera_verify` handler works only because `camera` happened to already be on that list.
- [x] **J4.3 — Duplicate op names across handler modules collide SILENTLY. (P1, VERIFIED)** DONE
      2026-08-08. `pc_agent._merge_handlers()` replaces the `{**a, **b, ...}` spread and raises at
      import time naming the op and BOTH claiming modules. Import time is the only useful moment:
      by the time it is a wrong action on the owner's laptop it is too late, and which module wins
      depends on nothing more principled than import order in that file. 23 ops across 7 modules,
      no collisions today. The refusal is itself tested rather than assumed.
- [ ] **J4.4 — The shell/ops layer has no tests and no graph edges. (P1)** `deploy_vps.sh`,
      `live-check.sh`, `install-brain.sh` and friends are unreachable from `run_all_tests.py`. The
      deploy path — the single most dangerous script in the repo — is the least tested.
- [ ] **J4.5 — `clients/` (iPhone HTML/JS) is isolated and only statically checked. (P2)** Community
      161 (`test_iphone_client.py`, 3 nodes) verifies it "statically + server injection". No edge to
      the brain sidecar it actually talks to.
- [ ] **J4.6 — No edge from any skill doc to the tools it names. (P1)** `skills/*.md`
      (calendar-and-reminders, daily-briefing, email-triage, memory-discipline, proactive-etiquette,
      research-method, task-capture, voice-style, proactive-companion) are nine separate islands.
      **A skill instructing Afon to call a renamed or deleted tool would be caught by no test at
      all** — it fails silently at runtime, mid-conversation. A doc-to-registry linter closes this.
- [ ] **J4.7 — Same gap for `personality/*.md` and every `*.example.md` template. (P2)**
- [ ] **J4.8 — `browser.py:_neutralize_speechbrain_lazy_modules()` is an untested cross-subsystem
      monkeypatch. (P2)** Playwright's error reporting reaching into SpeechBrain's optional lazy `k2`
      modules. Breaks on either dependency's upgrade, with no test to say so.

### J5 · Two sources of truth (documentation drift)
- [ ] **J5.1 — TODO.md and docs/MASTER-PLAN.md are two separate roadmap communities. (P1)** 65
      (cohesion 0.09) and 127 (0.11). This has already bitten once: MASTER-PLAN claimed a
      `bench/train_wakeword.py` that has never existed, and the citation propagated to four places
      before `ls bench/` caught it. Name one canonical roadmap; make the other point at it.
- [ ] **J5.2 — README (community 75, 22 nodes, cohesion 0.09) barely connects to code. (P2)** The
      feature tour can drift arbitrarily far from the tool registry with nothing objecting.
- [ ] **J5.3 — Re-verify the `glasses/` TypeScript references after a graph refresh. (P2)** Community
      235 still titles a section *"The non-Python parts — TypeScript (glasses)"* for a directory that
      does not exist. `skills/web-and-typescript.md` was rewritten 2026-08-01 — this may already be
      closed, and the graph simply predates the fix (see J0.1).
- [ ] **J5.4 — `SECURITY.md` describes the confirm tier with no edge to `confirm_required`. (P2)**
      Same for CONFIGURATION.md -> `Settings`, CONTRIBUTING.md -> the tool template,
      THIRD_PARTY_NOTICES.md -> the dependency set. Security prose that drifts from the enforcing
      code is the worst kind of stale note.
- [ ] **J5.5 — `to-read-script.md` is PARSED as data with no parse test. (P2)** `enroll_voice.py`
      `_script_segments()` reads its `## Segment` headers. Editing the prose file breaks voice
      enrolment — the very thing I1 is blocked on — with nothing to catch it.

### J6 · Test-suite structure
- [x] **A flaky test was a PRODUCT bug, found 2026-08-09 by running the gate with full output.**
      `test_protocol_reports.py` passed 13/13 standalone and failed 4 checks inside the gate. The
      freshness guard in `protocols.py:_deliver_report` accepted a report only if
      `st_mtime >= since`, where `since` is taken just before the protocol script writes it. **A
      file written strictly after a `time.time()` reading can carry an mtime slightly before it** —
      filesystem timestamp granularity is coarser than the clock and rounds down. Measured on this
      machine: **292 of 3000 writes (~10%)**. When it happens in production the brand-new report is
      judged stale, `_deliver_report` waits the full 90s and sends nothing — *silently, which is the
      precise failure this function was written to prevent*. Fixed with `_MTIME_SLOP_S = 2.0`
      (far below the hours-long gap to a genuinely stale report, far above any timestamp
      granularity). New check [2b] forces the skew deterministically instead of leaving it to a
      1-in-10 chance; negative control (slop → 0) fails it. **Worth remembering: the instinct on a
      test that is green alone and red in the suite is to blame the harness. Twice now the suite was
      right** — the webcam dependency on 2026-08-08, and this.
      **Second lesson, about my own tooling:** the first two gate runs showed `127 passed, 1 failed`
      and I could not see WHICH — I had piped the run through `Select-Object -Last 12`, and the
      failure line sits inline in the list, not in the summary. Capture the whole run to a file.
- [x] **J6.1 — CLOSED 2026-08-09 by J8.3** (`.graphifyignore` excludes `bench/`). The count in the
      original finding was wrong: 93 of 293 communities were bench-dominated, not ~120 of 257.
- [x] **J6.2 — Test registration in `run_all_tests.py:TESTS` is manual and unguarded. (P1)** DONE
      2026-08-08. `bench/test_registry_complete.py` asserts every `bench/test_*.py` is either
      registered or exempted with a stated reason — same shape as the J4.2 routing guard, because
      the answer is not "register everything": live/credentialed tests belong outside a hermetic
      gate, and forcing them in is how gates become flaky and then ignored.
      **It found 13 silently-unregistered files.** Eight were genuinely live (freellmapi, real MCP
      transport, real STT/TTS, Notion e2e, WS latency budget, model-tier benchmarking) and are now
      exempt with reasons. **Five were hermetic and had simply stopped running:**
      `test_backup_restore`, `test_composio_router`, `test_fleet_routing`, `test_pc_agent_refuse`,
      `test_proactive_report` — each verified passing BEFORE registration, so the gate was never
      broken by the fix. It also checks the mirror failures: a registry entry naming a deleted
      file, a stale exemption, and anything both registered and exempt.
      Run from `scripts/preflight.sh`, not from TESTS — registering it inside the list it
      validates would be circular. Registry now covers 130 files, 6/6.
      *Note worth keeping: three of the five newly-registered tests print NOTHING and pass on exit
      code alone. A silent test is barely distinguishable from one that did nothing; they are gated
      on exit code until they grow a summary line.* -> see J6.5.
- [x] **J6.5 — DONE 2026-08-08, and the diagnosis was too kind.** These were not passing
      silently; they were **not running at all**. `test_backup_restore`, `test_fleet_routing`
      and `test_pc_agent_refuse` define pytest-style `def test_*()` functions, the canonical
      runner executes bench files as SCRIPTS, and nothing called them — so the module imported,
      zero assertions ran, and exit 0 was recorded as a pass. `test_fleet_routing` also takes a
      `monkeypatch` fixture, so it could only ever have run under pytest, which is not the
      runner. A fourth, `test_latency_budget`, was in the same state and additionally EXEMPT
      from the runner, so it had no way of being executed by anything at all.
      Fixed: each got a `__main__` block that calls its function and prints
      `=== N/M checks passed ===` (4/4, 5/5, 7/7, 1/1). `test_fleet_routing` got a 12-line
      `_Monkeypatch` shim. `test_latency_budget` now runs against the live brain on demand —
      **verified green: first sentence inside the 6s budget.**
      Durable guard: `test_registry_complete.py` now AST-parses every bench file and fails on any
      `test_*` function that is defined and never invoked. Proved it fails on a planted orphan
      before trusting it. **Registration was never the real invariant — execution is.**
- [ ] **J6.3 — Audit the very-high-cohesion tiny test communities. (P2)** 0.47–0.80 with 3–5 nodes:
      test_self_repair, test_no_result_sentinels, check_public_clean, test_identity,
      test_iphone_client, test_local_voice, test_pc_suspend, test_security_hardening,
      test_setup_wizard, test_camera, verify_multilingual. Isolated means they touch almost nothing
      real — worth separating hermetic-*by design* from hermetic-*by accident* (asserting against
      stubs rather than the shipped path).
- [ ] **J6.4 — `check()`/`main()` as universal names is the root cause of J2's depressed scores.
      (P1)** Fixing J3.1 fixes this; listed separately because the *benefit* is graph legibility, not
      code volume.

### J7 · Error and exception discipline
- [ ] **J7.1 — 22 undocumented broad catches. (P2, VERIFIED)** 455 `except Exception` in `src/`, of
      which 433 carry `# noqa: BLE001` with a stated reason. The discipline is real and nearly
      universal — which is exactly why the 22 exceptions to it are worth reading.
- [ ] **J7.2 — Exception types are structural graph participants. (P2)** `RuntimeError` and
      `Exception` appear as *nodes* in communities 17, 42, 98 and 164. Control flow routes through
      generic exceptions rather than domain errors.
- [ ] **J7.3 — No typed error taxonomy. (P1)** The consequence of J1.2 + J7.2: a caller cannot
      distinguish not-configured / network-down / auth-expired / bad-args without string matching.
      This is why the LLM permanent-error classifier and the degradation tests both had to be built
      on prose.

### J8 · Cohesion program (cross-cutting — how to stop this recurring)
- [ ] **J8.1 — Record today's per-community cohesion as a baseline and gate regressions. (P2)**
      Without a baseline, "improve cohesion" is unfalsifiable. With one, a PR that makes a community
      worse can be told so.
- [ ] **J8.2 — Give graphify explicit routing manifests instead of leaving it to inference. (P1)**
      The `LOCAL_HANDLERS` merge, the tool registry and the lazy-group map are all *data* the graph
      currently has to guess at — which is a large part of the 607 inferred edges in J0.2. Emitting
      them as a manifest converts guesses into extracted edges and directly enables J4.2, J4.3
      and J4.6.
- [x] **The graph hook was one commit from freezing silently — caught 2026-08-09 by checking, not
      by trusting.** After J8.3 removed 1043 bench nodes, every rebuild was *smaller* than the graph
      on disk, and `graphify update` **refuses to shrink a graph without `--force`** (a good guard:
      a shrink usually means a half-scanned corpus). The hook piped everything to `/dev/null`, so
      the refusal was invisible — the graph would have sat frozen at `63f4f03` forever, confidently
      answering questions about code that no longer existed. Fixed by writing the outcome to
      `graphify-out/.last-update.log`; deliberately did **not** add `--force` to the hook, because
      auto-forcing turns the only guard against silent data loss into a no-op. A one-off
      `graphify update . --force` established the new baseline: **3729 → 2703 nodes, 6809 → 4987
      edges, 293 → 195 communities, 0 bench.**
      **The verification lesson:** last session I marked the hook working after running its body in
      *my* shell. That proves the command works, not that the hook does — the hook has a different
      environment, different output handling, and in this case a different outcome.
- [x] **J8.3 — DONE 2026-08-09.** `.graphifyignore` excludes `bench/`. Measured on the graph as it
      stood: bench was **1043 / 3729 nodes (28%)**, **1858 / 6809 edges (27%)** and dominated
      **93 / 293 communities (32%)** — a third of the clustering budget spent on scaffolding, and the
      reason every "Surprising Connection" was a `bench/* → src/*` edge. (The report's "~120 of 257
      communities" overstated it; the real figure is 93 of 293.) **The trade, stated so nobody
      rediscovers it as a bug:** the graph can no longer answer "which test covers X" — acceptable
      only because tests here are named `bench/test_<topic>.py`, so a glob answers it as well.
      Reversal is one `mv` (documented in the file).
- [ ] **J8.4 — Write down the intended layering (edge / brain / tools / stores / ops) and check it.
      (P2)** The graph found no import cycles — genuinely good, and worth keeping. But it also has no
      concept of a *layer*, so an edge module importing a brain store, or a tool importing the agent,
      registers as a normal edge. A layering rule turns the "no cycles" win into a durable invariant
      rather than a lucky snapshot.
