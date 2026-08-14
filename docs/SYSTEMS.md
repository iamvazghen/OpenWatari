# Afon — the 50-system master plan

**What this document is.** One section per system, fifty of them, each with a floor to reach, a
sequence of small raises, and a verification gate on every step. `TODO.md` remains the **canonical
roadmap** and the live task ledger — it owns dated findings, parts A–N, and the running record of
what was tried. This document owns the *shape of done*: for each system, what "wired", "verified",
and "elite in production" concretely mean, and which test decides. Where a task already exists in
`TODO.md`, this document cites it rather than restating it. **Together the two define the finish
line; neither does alone.**

**Written 2026-08-14.** State at that moment: hermetic suite **151 passed / 0 failed / 0 skipped**;
last behavioural grade **84.9/100** (target ≥95, no category <90); **136 registered tools**; Afon
**parked** by `~/.afon/MAINTENANCE` on both hosts, deliberately out of production until this plan's
floors are met.

---

## How to read a section

```
### S07 · Session & Context Management
Status      — one of: complete-for-now · built, not well structured · half-built · missing
Surfaces    — the modules that own it today
The bar     — the observable behaviour that counts as elite. Written as what the owner sees.
Budget      — the latency / token / memory numbers it must hold
Floor       — make it exist and stop it lying. Nothing else in the system starts until these pass.
Raise       — small, ordered, each independently shippable and independently verified.
Elite       — the last mile: behaviour under real production conditions, not test conditions.
Blocked     — needs the owner (a key, a decision, a recording). Never silently skipped.
```

**Conventions used throughout.**

- Test files are named bare — `test_camera.py` — and all live in `bench/`, registered in
  `bench/run_all_tests.py`. A task without a named gate is not a task; it is a wish.
- `[ ]` unstarted · `[~]` in progress · `[x]` done **and verified by its gate on a real run**.
- **No relabeling.** A status only moves when its gate passes in a suite run that is recorded.
- Every task is small enough to ship in one commit with one test. If a task needs three tests, it
  is three tasks.
- Systems marked *missing* get a **deliberately small floor** — one narrow, real, useful capability
  wired end-to-end and gated — never a framework. The floor is the point: it converts a zero into a
  one, and ones can be raised.

---

## Standing gates — these apply to every task in this document

| # | Gate | Enforced by |
|---|---|---|
| G-A | The full hermetic suite stays green. No task lands red. | `run_all_tests.py`, pre-push hook |
| G-B | Every new capability ships with a bench test **registered in the runner** | `test_run_all_tests_classifier.py` |
| G-C | No new tool may enter the registry undocumented or unrouted | `test_registry_complete.py` |
| G-D | Any tool that mutates the world outside Afon enters the confirm tier | `test_confirm_tier_documented.py` |
| G-E | No doc may cite a path that does not exist | `test_doc_paths.py` |
| G-F | No layering violation (edge→brain internals, tools→agent) | `test_layering.py` |
| G-G | Degradation is typed, never prose-matched (`is_not_configured`, `tool_failed`) | `test_no_result_sentinels.py`, `test_error_taxonomy.py` |
| G-H | Nothing autostarts while `~/.afon/MAINTENANCE` exists | `test_maintenance_lock.py` |
| G-I | Per-turn cost does not regress: prefill tokens, p50/p95 turn latency | `test_speed.py`, `efficiency_report.py` |
| G-J | Behavioural grade never drops below the previous recorded median-5 | `behavioral_suite.py` |
| G-K | This document stays honest: 50 sections, a gate on every task, a scoreboard that matches | `test_systems_plan.py` |

**The efficiency clause.** The owner's requirement is "as fast and as efficient as possible with no
bugs and lags and overlaps". That is not a per-system task; it is a constraint every section
inherits. Concretely, three prohibitions apply everywhere:

1. **No overlap.** Two modules may not answer the same question differently. Where they must both
   exist (health, memory recall, config probes), one is the source and the other *asks it* — the
   pattern established in J3.6 and J3.8.
2. **No unbounded prefill.** Anything added to the system prompt or tool catalogue must show its
   token cost and pay for it. The tool catalogue is the dominant per-turn cost (TODO K2).
3. **No silent work.** Every background loop declares its period, its budget, and its kill switch,
   and appears in the HUD.

---

# Wave plan — the order these are actually executed

The fifty are not fifty independent projects; several are load-bearing for the rest. Execute in
waves, and do not start a wave until the previous wave's floors are green.

| Wave | Systems | Why first |
|---|---|---|
| **0 — Unpark safely** | S31, S36, S22, S23 | Nothing goes back into production until self-monitoring, security, recovery and reachability floors hold. |
| **1 — The organs** | S01, S02, S03, S30, S24 | Brain, LLM, tools, memory, world model. Every other system's quality is capped by these. |
| **2 — Identity & senses** | S08, S09, S10, S11, S18, S26, S27 | Afon cannot be trusted with autonomy until he knows who is speaking and what he is looking at. |
| **3 — The daily loop** | S07, S14, S16, S17, S21, S13, S33 | The behaviour the owner meets every single day. |
| **4 — Reach & control** | S04, S05, S12, S19, S20, S28, S29, S38, S42, S43 | Hands, devices, homes, channels. |
| **5 — Judgment** | S25, S41, S44, S45, S37, S39, S48 | Personality, research, ethics, explanation, delegation. |
| **6 — New ground** | S06, S15, S32, S34, S35, S40, S46, S47, S49, S50 | The systems that are missing or half-built; floors first, then raise. |

---

# The fifty systems

## S01 · Brain / Core Intelligence

**Status** built, not well structured · **Surfaces** `src/afon/brain/agent.py`,
`src/afon/brain/intent_router.py`, `src/afon/brain/context.py`, `src/afon/brain/modes.py`

**The bar.** Afon decides *what kind of turn this is* before he spends anything on it: a greeting
costs one small call and no catalogue; a multi-clause instruction plans its clauses, fires each,
and reports what it could not do. He states a confidence he actually holds — "I don't know" is a
first-class answer, not a failure mode — and never answers an unknowable with the same certainty as
a lookup.

**Budget.** Pure-chat turn ≤1 LLM call, ≤2k prefill tokens. Tool turn p50 ≤2.5s to first audio,
p95 ≤5s. Planning overhead ≤120ms of wall clock outside the model.

**Floor**
- [x] 01.F1 One completion loop, not two — multi-intent and clause paths unified (TODO K1).
      *gate:* `test_clause_completion.py`, `test_clause_routing.py`
- [ ] 01.F2 Uncertainty is representable. A calibrated "I don't know / I'd have to check" is a
      supported outcome and the scorer credits it. **B06 measured 2%** — he answers unknowables at
      full confidence today. *gate:* new `test_uncertainty.py` — 12 unanswerable prompts, none
      receives a confident assertion; 12 answerable ones are still answered.
- [ ] 01.F3 The turn tracer records, per turn: intent class, tools considered, tools fired, prefill
      tokens, wall clock per stage. Three days of data before any further tuning (TODO K2).
      *gate:* `test_metrics.py` extended — every turn emits a complete trace row.

**Raise**
- [ ] 01.R1 Intent classes explicit and testable (chat · lookup · act · multi-clause · ambiguous)
      rather than emergent from routing. *gate:* new `test_intent_classes.py` — 40 labelled prompts,
      ≥90% class accuracy.
- [ ] 01.R2 Ambiguity resolution: a genuinely ambiguous instruction asks **one** clarifying question
      instead of guessing, and never asks twice about the same thing in a session.
      *gate:* `test_intent_classes.py` [ambiguous] + `test_pure_chat_tools.py`
- [ ] 01.R3 Goal attribution: the agent can name the objective a turn serves (from `objectives.py`),
      or say it serves none. *gate:* new `test_goal_attribution.py`
- [ ] 01.R4 Self-model: "what can you do / what can't you do" answers from the real registry and the
      real config, never from prompt prose. *gate:* `test_registry_complete.py` extended — the
      spoken capability list is generated, not written.

**Elite**
- [ ] 01.E1 Multi-intent combinations ≥90 (last measured **51.5** — the one remaining real lever to
      95). *gate:* `behavioral_suite.py` combination category, median-5.
- [ ] 01.E2 Overall behavioural ≥95, no category <90. *gate:* G-J.

**Cross-refs** TODO Part K1, Part A, B06.

---

## S02 · LLM Integration

**Status** complete for now · **Surfaces** `src/afon/brain/llm.py`, `src/afon/config.py`

**The bar.** The owner never perceives a model problem. A primary that stalls is abandoned before
the silence is audible; a rate limit is a routing decision, not an outage; the expensive model is
spent only where it changes the answer.

**Budget.** First-token failover ≤1.2s. Fallback switch adds ≤400ms to the turn. Thinking-tier
escalation fires on <15% of turns.

**Floor**
- [x] 02.F1 Fast failover on first-token stall, not on request timeout (TODO K5a).
      *gate:* `test_brain_llm.py`, `test_resilience.py`
- [x] 02.F2 Tool-tier and thinking-tier routing. *gate:* `test_model_tiers.py`
- [ ] 02.F3 Provider health is *learned*, not just configured: a provider that failed twice in five
      minutes is deprioritised for a cooldown, and the HUD says so.
      *gate:* new `test_provider_cooldown.py`

**Raise**
- [ ] 02.R1 Per-tier cost accounting in `metrics` — tokens and money per intent class, in the HUD.
      *gate:* `test_metrics.py`
- [ ] 02.R2 Prompt-cache discipline: the stable prefix (persona, rules) is byte-identical across
      turns so the cache actually hits. *gate:* new `test_prompt_prefix_stable.py`
- [ ] 02.R3 Streaming correctness under failover — no duplicated or truncated sentence when the
      chain switches mid-stream. *gate:* `test_streaming.py` extended.

**Elite**
- [ ] 02.E1 A full outage of primary + first fallback is inaudible in the voice path: the owner
      hears one continuous answer. *gate:* `test_resilience.py` chaos case, plus one live drill
      recorded in TODO.

**Blocked** MiniMax key rotation (owner). **Cross-refs** TODO K5a, I4.

---

## S03 · Tool Utilization

**Status** complete for now · **Surfaces** `src/afon/brain/tools/` (42 modules, 136 tools),
`src/afon/brain/tools/base.py`, `src/afon/brain/tool_reliability.py`,
`src/afon/brain/tool_usage.py`

**The bar.** The right tool fires the first time, with the right arguments, and a tool that fails
says *why* in a way the agent can act on. Afon knows which of his own tools are flaky and stops
leaning on them.

**Budget.** Catalogue presented per turn ≤20 tools / ≤2.5k tokens (today: 58 tools ≈10k — the
dominant per-turn cost). Tool-call error rate <2% of calls.

**Floor**
- [x] 03.F1 Typed failure contract everywhere — `tool_failed()`, `is_not_configured()`, no
      prose-matching. *gate:* `test_no_result_sentinels.py`, `test_tool_failure_guard.py`
- [x] 03.F2 Missing-argument handling asks rather than fabricates. *gate:* `test_missing_arg.py`
- [ ] 03.F3 **Catalogue narrowing is measured, not assumed.** Per-turn tool count and token cost land
      in the trace; the 58-tool prefill is the number to beat (TODO K2).
      *gate:* `test_speed.py` asserts a hard ceiling on presented-catalogue tokens.

**Raise**
- [ ] 03.R1 Two-stage selection: a cheap router picks a *family* (10–20 tools), the model picks
      within it. *gate:* new `test_catalogue_narrowing.py` — 60 prompts, correct tool still reachable
      ≥98% at ≤20 presented.
- [ ] 03.R2 Argument validation before dispatch, with a repair prompt on the first failure only.
      *gate:* `test_reminder_args.py`, `test_tool_error_handling.py`
- [ ] 03.R3 Chaining: a result that obviously feeds another (search→open, contact→message) chains in
      one turn, not across two. *gate:* new `test_tool_chaining.py`
- [ ] 03.R4 Dead-tool sweep: every tool not fired in 90 days is either exercised by a bench case or
      retired. *gate:* `test_tool_usage.py` extended with a staleness report.

**Elite**
- [ ] 03.E1 Wrong-tool selection <2% across the behavioural suite (the original 74.8 diagnosis was
      dominated by this). *gate:* `behavioral_suite.py` per-turn tool audit.

**Cross-refs** TODO K2, Part A.

---

## S04 · Device Control

**Status** complete for now · **Surfaces** `src/afon/edge/pc_agent.py`,
`src/afon/brain/pc_link.py`, `src/afon/brain/tools/system.py`, `src/afon/brain/tools/audioout.py`

**The bar.** "Open my email", "mute this", "put the laptop to sleep in ten minutes" all work from
any room, with a confirm for anything destructive and a refusal for anything catastrophic. A
disconnected PC says so instead of silently succeeding.

**Budget.** PC_LINK round-trip p95 ≤400ms on LAN. Reconnect after a link drop ≤5s.

**Floor**
- [x] 04.F1 Twelve operations over PC_LINK with typed results. *gate:* `test_pc_agent_routing.py`
- [x] 04.F2 Destructive operations refuse or confirm. *gate:* `test_pc_agent_refuse.py`,
      `test_security_hardening.py`
- [x] 04.F3 Sleep/suspend and post-resume behaviour. *gate:* `test_pc_suspend.py`
- [ ] 04.F4 A dead PC link is reported as unreachable within one turn, never as success.
      *gate:* `test_pc_verify.py` extended with a link-down case.

**Raise**
- [ ] 04.R1 Verify-after-act: an action that can be checked (app opened, volume set) is checked, and
      the answer reports the *verified* state. *gate:* `test_pc_verify.py`
- [ ] 04.R2 Named targets resolve to the right machine when more than one is online (laptop, VPS,
      phone). *gate:* `test_device_handoff.py` extended.
- [ ] 04.R3 Window and focus awareness — "close that" resolves to the foreground app.
      *gate:* new `test_pc_focus.py`

**Elite**
- [ ] 04.E1 Ten consecutive real device commands in one session, zero false successes, all inside
      budget. *gate:* `demo_live_actions.py` recorded run.

**Cross-refs** TODO H1.

---

## S05 · Browser Control

**Status** complete for now · **Surfaces** `src/afon/brain/tools/browser.py`,
`src/afon/brain/tools/web.py`

**The bar.** Afon uses a real browser the way the owner would: navigates, reads, fills, clicks,
keeps a session, and tells the truth about what he saw on the page — including when a page blocked
him.

**Budget.** Page open→readable text p95 ≤6s. Scrape fallback chain resolves in ≤3 attempts.

**Floor**
- [x] 05.F1 Twelve browser operations over PC_LINK with a persistent session.
      *gate:* `test_pc_agent_routing.py`, `test_screenshot_transport.py`
- [x] 05.F2 Scrape/search degradation chain is typed. *gate:* `test_web_fallback.py`
- [ ] 05.F3 A blocked, paywalled or JS-empty page is reported as such, never summarised from the
      title alone. *gate:* `test_web_fallback.py` extended [empty-body case].

**Raise**
- [ ] 05.R1 Form interaction with a confirm gate before any submit that spends money or sends data.
      *gate:* `test_confirm_tier_documented.py` extended.
- [ ] 05.R2 Multi-tab task state — a research task keeps its tabs and can resume after an
      interruption. *gate:* new `test_browser_session.py`
- [ ] 05.R3 Downloads land in a known directory and are reported by path.
      *gate:* `test_browser_session.py` [download]

**Elite**
- [ ] 05.E1 A five-step real web task (search → open → extract → cross-check → report) completes
      unattended with citations. *gate:* new `test_web_task_e2e.py` (recorded fixtures).

---

## S06 · Document Creation

**Status** half-built — reading is strong, *creation* is three ad-hoc writers ·
**Surfaces** `src/afon/brain/tools/documents.py`, `src/afon/brain/tools/vault.py`,
`src/afon/brain/tools/notion.py`, `src/afon/brain/tools/gmail.py`

**The bar.** "Write it up and put it where it belongs" produces a real document — structured,
formatted, in the right place, with a version history — and Afon can revise it later by name.

**Budget.** Generation ≤1 LLM call per document section; write + verify ≤500ms.

**Floor** *(this system has no spine today — the floor is one narrow real capability, gated)*
- [ ] 06.F1 A single `create_document(kind, title, body, destination)` tool that owns creation for
      vault notes, Notion pages and local markdown, replacing the three separate paths.
      *gate:* new `test_document_create.py` — three destinations, each written and read back.
- [ ] 06.F2 Every created document is verified by reading it back before Afon reports success.
      *gate:* `test_document_create.py` [read-back]
- [ ] 06.F3 Documents are addressable afterwards: "the note you wrote yesterday about X" resolves.
      *gate:* `test_documents_routing.py` extended.

**Raise**
- [ ] 06.R1 Templates — meeting note, decision record, project brief, weekly review — chosen by kind.
      *gate:* `test_document_create.py` [template fidelity]
- [ ] 06.R2 Revision instead of rewrite: edit an existing document in place, keeping the prior
      version. *gate:* new `test_document_revise.py`
- [ ] 06.R3 Export formats (md → pdf/docx) where the destination needs it.
      *gate:* `test_document_revise.py` [export]

**Elite**
- [ ] 06.E1 Dictated in the car, written to the vault, revised by voice the same evening, both
      versions retrievable. *gate:* `behavioral_suite.py` new scenario + one live run.

**Cross-refs** vault write protocol (writes go to the VPS, never the local replica).

---

## S07 · Session & Context Management

**Status** built, not well structured · **Surfaces** `src/afon/brain/context.py`,
`src/afon/brain/agent.py` (history + trim), `src/afon/brain/presence.py`

**The bar.** A conversation survives a walk from the desk to the kitchen, a reconnect, and a night's
sleep. "That one" and "the thing we discussed" resolve. Context never grows without bound and never
silently drops the sentence that mattered.

**Budget.** Context assembly ≤80ms. Working context ≤6k tokens, hard-capped, with the trim decision
logged.

**Floor**
- [ ] 07.F1 Session identity is explicit: one session id per conversation, carried across edge
      reconnects and across devices. *gate:* new `test_session_identity.py`
- [ ] 07.F2 Trim is lossy *on purpose* — what is dropped is summarised into the session record, not
      discarded. *gate:* `test_session_identity.py` [trim retains gist]
- [ ] 07.F3 Reference resolution ("it", "that", "the second one") is tested, not assumed.
      *gate:* new `test_reference_resolution.py` — 20 cases, ≥90%.

**Raise**
- [ ] 07.R1 Session summaries written at close, retrievable by date and topic.
      *gate:* `test_session_identity.py` [summary persisted]
- [ ] 07.R2 Topic segmentation inside a long session so recall does not drag in unrelated turns.
      *gate:* `test_reference_resolution.py` [topic boundary]
- [ ] 07.R3 Interruption and resumption: an interrupted turn can be resumed by "carry on".
      *gate:* `test_streaming.py` extended (interruption path already at 27/27).

**Elite**
- [ ] 07.E1 A conversation started on the phone, continued at the desk, closed by voice — one
      coherent thread with one summary. *gate:* `test_phase6_multidevice.py` + a live run.

**Cross-refs** S12, S43. Depends on S30.F1 (one memory origin).

---

## S08 · Voice Enrollment

**Status** built, **measurably weak** · **Surfaces** `bench/enroll_voice.py`, `to-read-script.md`,
`src/afon/edge/speaker_id.py`

**The bar.** Enrolment takes five minutes once, and afterwards the owner's voice scores far enough
above every other voice that the threshold is not a compromise.

**Budget.** Owner median similarity ≥0.60 (today **0.47**), non-owner median ≤0.30, separation ≥0.25.

**Floor**
- [ ] 08.F1 **Re-enrol on good audio** — the current profile is below target and every downstream
      identity decision inherits that. *gate:* `test_phase5_identity_bench.py` — owner median ≥0.60.
- [ ] 08.F2 Enrolment quality is *reported at enrolment time*: too short, too noisy, too uniform is
      rejected on the spot rather than discovered later.
      *gate:* `test_enroll_script_parse.py` extended with quality thresholds.

**Raise**
- [ ] 08.R1 Multi-condition enrolment: near mic, across the room, headset, with music — stored as
      separate references. *gate:* `test_phase5_identity_bench.py` [per-condition medians]
- [ ] 08.R2 Continuous refinement — confidently-identified turns extend the profile, with a cap and a
      rollback. *gate:* new `test_voice_profile_drift.py`
- [ ] 08.R3 Guest enrolment: a second known voice can be added and named.
      *gate:* `test_face_identity_separation.py` sibling for voice.

**Elite**
- [ ] 08.E1 Separation holds across a week of real turns with no manual threshold edits.
      *gate:* `test_voice_profile_drift.py` on a week of recorded scores.

**Blocked** owner must record the enrolment script.

---

## S09 · Face Enrollment

**Status** built, **never run on the current backend** · **Surfaces**
`src/afon/brain/tools/camera.py` (`enroll_owner_face`), refs at `~/.afon/faces/`

**The bar.** The reference set covers the faces Afon will actually see: glasses on and off, lit and
dim, front and three-quarter — and a new reference can be added in one command.

**Budget.** Owner match ≥0.65 cosine on ArcFace, impostor ≤0.35, at ≤120ms per frame.

**Floor**
- [ ] 09.F1 **Enrol on ArcFace.** The backend is wired and tested but only the legacy LBP references
      exist. *gate:* `test_face_arcface_backend.py` — a real owner embedding present and loadable.
- [ ] 09.F2 Enrolment rejects unusable captures (no face, two faces, too dark) at capture time.
      *gate:* `test_face_recognition.py` extended.

**Raise**
- [ ] 09.R1 Multi-condition capture set with per-condition scores reported after enrolment.
      *gate:* `test_phase5_identity_bench.py`
- [ ] 09.R2 Re-enrolment is non-destructive — the old set is kept and can be restored.
      *gate:* `test_backup_restore.py` extended.
- [ ] 09.R3 Household separation: a second enrolled face never matches the owner.
      *gate:* `test_face_identity_separation.py`

**Elite**
- [ ] 09.E1 Zero owner false-negatives across a day of desk sessions.
      *gate:* recorded run of `test_face_recognition.py` against a day of frames.

**Blocked** owner must sit for the ArcFace capture.

---

## S10 · Voice Recognition (speaker identification)

**Status** built, not well structured · **Surfaces** `src/afon/edge/speaker_id.py`,
`src/afon/edge/speaker_gate.py`, `src/afon/edge/audio_gate.py`

**The bar.** Afon answers the owner and ignores the television. A stranger asking for something
protected is refused by identity, not by luck. When he is unsure, he says he is unsure rather than
guessing in either direction.

**Budget.** Gate decision ≤150ms after end-of-utterance. Owner false-reject <2%, stranger
false-accept <1%.

**Floor**
- [x] 10.F1 The gate scores the **utterance**, not the room (N2, fixed 2026-08-11).
      *gate:* `test_speaker_gate_scoping.py`
- [x] 10.F2 Cold-start warm-up does not mis-score the first utterance. *gate:* `test_speaker_warmup.py`
- [ ] 10.F3 The threshold is derived from the enrolled profile's measured separation, not a constant
      (depends on S08.F1). *gate:* `test_phase5_identity_bench.py` — threshold computed, asserted
      inside the separation band.

**Raise**
- [ ] 10.R1 Three-way verdict — owner · not-owner · **unsure** — with unsure routed to a soft
      confirm instead of a refusal. *gate:* new `test_speaker_verdict.py`
- [ ] 10.R2 Diarization for two speakers in the room: attribute each utterance.
      *gate:* `test_speaker_verdict.py` [two-speaker]
- [ ] 10.R3 Media rejection: TV and music voices never pass the gate.
      *gate:* `test_speaker_verdict.py` [broadcast audio fixtures]

**Elite**
- [ ] 10.E1 A week of real turns with zero owner false-rejects and zero stranger accepts, no manual
      threshold edits. *gate:* recorded scores + `test_voice_profile_drift.py`

**Cross-refs** TODO N2, N3. Blocked behind S08.F1.

---

## S11 · Face Recognition

**Status** built, not well structured · **Surfaces** `src/afon/brain/tools/camera.py`,
`src/afon/brain/tools/multimodal.py`

**The bar.** The camera is a *second factor*, never a gatekeeper: it can strengthen a decision, and
it can say "I can't tell", but it may never assert absence it cannot see. A photograph does not pass.

**Budget.** Verify ≤400ms per attempt, ≤120ms per frame for embedding.

**Floor**
- [x] 11.F1 "Nobody there" and "can't tell" are different answers; the confirm gate fails open on
      the latter (fixed 2026-08-13). *gate:* `test_camera.py`, `test_face_second_factor.py`
- [x] 11.F2 ArcFace backend wired with a legacy fallback. *gate:* `test_face_arcface_backend.py`
- [ ] 11.F3 **Liveness.** A printed photo or a phone screen currently passes. Add a cheap
      anti-spoof (blink / micro-motion / texture) before the camera is ever load-bearing.
      *gate:* new `test_face_liveness.py` — static-image fixtures rejected, live frames accepted.

**Raise**
- [ ] 11.R1 Recognition under real desk conditions: backlight, partial profile, glasses.
      *gate:* `test_face_recognition.py` extended fixture set, ≥90% owner recall.
- [ ] 11.R2 Multi-frame fusion — a verdict is taken over N frames, not one.
      *gate:* `test_camera.py` [fusion]
- [ ] 11.R3 Privacy: frames are never persisted, only embeddings and verdicts; asserted, not assumed.
      *gate:* `test_security_hardening.py` extended.

**Elite**
- [ ] 11.E1 Identity end-to-end: voice and face agree on the owner ≥95% of desk turns, and disagree
      loudly rather than silently when they differ.
      *gate:* new `test_identity_fusion.py` + a live day.

**Cross-refs** TODO N1, N3, I1.

---

## S12 · Multi-Device Coordination

**Status** built, not well structured · **Surfaces** `src/afon/edge/device_profile.py`,
`src/afon/edge/edge_lite.py`, `src/afon/edge/brain_client.py`, `src/afon/brain/pc_link.py`,
`clients/`

**The bar.** Speak to whichever device is nearest; only one answers; the conversation is the same
conversation. A device that goes away does not take the session with it.

**Budget.** Handoff ≤1s. Duplicate-answer rate 0%.

**Floor**
- [x] 12.F1 Device profiles and edge-lite clients. *gate:* `test_edge_lite.py`,
      `test_phase6_multidevice.py`
- [x] 12.F2 Reconnect without losing the brain link. *gate:* `test_edge_reconnect.py`
- [ ] 12.F3 **One answering device.** Arbitration when two edges hear the same wake word — nearest or
      loudest wins, others stay silent. *gate:* `test_device_handoff.py` extended [double-wake]

**Raise**
- [ ] 12.R1 Session follows the owner between devices (depends on S07.F1).
      *gate:* `test_session_identity.py` [cross-device]
- [ ] 12.R2 Per-device capability advertisement — the brain knows which device has a camera, a
      speaker, a screen. *gate:* new `test_device_capabilities.py`
- [ ] 12.R3 Graceful degradation to a text-only or push-only device.
      *gate:* `test_edge_lite.py` extended.

**Elite**
- [ ] 12.E1 Walk from desk to kitchen mid-sentence; the answer follows without a repeat.
      *gate:* live drill recorded in TODO + `test_device_handoff.py`

**Note** the glasses client was deleted; it is not part of this plan until hardware exists.

---

## S13 · Notification Management

**Status** built, not well structured · **Surfaces** `src/afon/brain/tools/notify.py`,
`src/afon/brain/tools/telegram.py`, `src/afon/brain/proactive.py` (quiet hours, suppression)

**The bar.** Nothing arrives that the owner would not have wanted; everything urgent does arrive.
Afon knows whether a notification was *seen*, and escalates only what deserves it.

**Budget.** Push delivery p95 ≤3s. Duplicate notifications 0/day (was 7–8/day before the dedup fix).

**Floor**
- [x] 13.F1 Push + Telegram delivery with quiet hours and suppression.
      *gate:* `test_proactive_suppression.py`, `test_proactive_windows.py`
- [x] 13.F2 No duplicate delivery — one sender, one path. *gate:* `test_acknowledgements.py`
- [ ] 13.F3 **Acknowledgement tracking.** Afon must know delivered vs seen vs acted-on; today he does
      not. *gate:* `test_acknowledgements.py` extended — an unacknowledged urgent item is re-raised
      once and only once.

**Raise**
- [ ] 13.R1 Channel choice by urgency and context: voice if present, push if away, Telegram if
      asleep. *gate:* new `test_notify_routing.py`
- [ ] 13.R2 Batching — non-urgent items accumulate into the next brief rather than interrupting.
      *gate:* `test_notify_routing.py` [batch]
- [ ] 13.R3 Escalation ladder with a stop condition, so nothing can loop.
      *gate:* `test_deadmans_switch.py` extended.

**Elite**
- [ ] 13.E1 A week with zero unwanted interruptions and zero missed urgent items, measured against
      the owner's own after-the-fact judgement. *gate:* proactivity log review recorded in TODO.

---

## S14 · Proactivity Engine

**Status** complete for now · **Surfaces** `src/afon/brain/proactive.py`,
`src/afon/brain/proactive_signals.py`, `src/afon/brain/proactive_report.py`,
`src/afon/brain/interventions.py`, `src/afon/brain/anticipation.py`

**The bar.** Afon speaks first only when it earns its interruption, and afterwards he can say why he
chose to. Ignored suggestions become rarer suggestions of that kind. Nothing autonomous happens
silently.

**Budget.** Tick ≤200ms in-process. ≤6 unprompted interventions/day by default.

**Floor**
- [x] 14.F1 Signals, urgency, value, quiet hours, budget, ignore-penalty learning.
      *gate:* `test_phase10_proactive.py`, `test_proactive_learning.py`,
      `test_proactive_thresholds.py`
- [x] 14.F2 Every autonomous act produces a report: what, why, and the reasoning.
      *gate:* `test_proactive_report.py`
- [ ] 14.F3 Every signal kind is asserted to be *reachable* — a dormant kind is a bug, not a
      preference (five companion capabilities were dormant for weeks at urgency <0.60).
      *gate:* `test_proactive_thresholds.py` extended — each kind fires at least once in fixtures.

**Raise**
- [ ] 14.R1 Outcome tracking: did the intervention help? Feed the answer back into value estimation.
      *gate:* `test_proactive_learning.py` [outcome loop]
- [ ] 14.R2 Timing model — the same suggestion at a better moment, learned from acceptance by
      hour and context. *gate:* new `test_proactive_timing.py`
- [ ] 14.R3 Suppression when the owner is in flow (depends on S27 context).
      *gate:* `test_proactive_suppression.py` [focus state]

**Elite**
- [ ] 14.E1 Proactivity behavioural ≥95 with acceptance rate ≥60% over a real week.
      *gate:* `behavioral_suite.py` + the proactivity log.

**Cross-refs** TODO K4, I2.

---

## S15 · Recommendation Engine

**Status** **missing as a system** — nearest neighbours are `src/afon/brain/coaching.py` and
memory resurfacing, both narrow and hand-tuned.

**The bar.** "What should I do next / what should I read / where should we eat" answers from what
Afon actually knows about the owner — history, stated preferences, current constraints — with a
reason attached and a memory of whether it landed.

**Budget.** Recommendation ≤1 LLM call plus one memory query; ≤600ms.

**Floor** *(convert zero into one: a single domain, end to end)*
- [ ] 15.F1 A `recommend(domain, context)` tool covering **one** domain first — next task to work on
      — sourced from `objectives.py` + `tasks.py` + calendar, with an explicit reason.
      *gate:* new `test_recommend.py` — the reason cites the data it used.
- [ ] 15.F2 Every recommendation is logged with its context, so acceptance can be learned later.
      *gate:* `test_recommend.py` [logged]
- [ ] 15.F3 Afon declines to recommend when he has no basis, instead of inventing taste.
      *gate:* `test_recommend.py` [no-basis case]

**Raise**
- [ ] 15.R1 Acceptance feedback loop — accepted/ignored/rejected updates a per-domain preference
      model. *gate:* `test_recommend.py` [learning]
- [ ] 15.R2 Second and third domains (reading/media, food/places) using the same spine.
      *gate:* `test_recommend.py` [multi-domain]
- [ ] 15.R3 Constraint awareness: time, money, energy, location filter the candidate set.
      *gate:* `test_recommend.py` [constraints]

**Elite**
- [ ] 15.E1 Owner accepts ≥50% of unprompted recommendations over a month, and every rejection is
      explainable from the log. *gate:* recommendation log review recorded in TODO.

---

## S16 · Task Queue & Execution

**Status** built, not well structured · **Surfaces** `src/afon/brain/tasks.py`,
`src/afon/brain/worker.py`, `src/afon/brain/backlog.py`, `src/afon/brain/tools/tasks.py`,
`src/afon/brain/tools/notion.py`, `src/afon/brain/scheduler.py`

**The bar.** Anything the owner asks for that cannot finish now becomes a tracked task with an
owner, a due date and a next step — and Afon works the ones he can, reporting progress without
being asked. Nothing is silently dropped.

**Budget.** Queue operations ≤50ms. Background task heartbeat every ≤60s with progress in the HUD.

**Floor**
- [x] 16.F1 Local queue, background worker, restart/expiry semantics.
      *gate:* `test_task_todos.py`, `test_background_tasks.py`, `test_task_restart_expiry.py`
- [x] 16.F2 The autonomous worker defers every outward action to approval.
      *gate:* `test_safety_autonomy.py`
- [ ] 16.F3 **The external queue pointer is validated at startup.** The Notion database id went
      stale for weeks unnoticed; a 404 must be loud. *gate:* `test_phase11_notion.py` extended —
      unreachable queue raises a health signal.

**Raise**
- [ ] 16.R1 Dependencies: task B blocked by task A, surfaced as "waiting on".
      *gate:* new `test_task_dependencies.py`
- [ ] 16.R2 Priority that reflects deadlines and objectives rather than insertion order.
      *gate:* `test_task_dependencies.py` [ordering]
- [ ] 16.R3 Progress narration — a long task reports at meaningful milestones, not on a timer.
      *gate:* `test_work_on_task.py` extended.

**Elite**
- [ ] 16.E1 A multi-day task carried from creation to completion with no owner reminder needed.
      *gate:* recorded run + `behavioral_suite.py` tasks category ≥95.

---

## S17 · Morning Brief

**Status** complete for now · **Surfaces** `src/afon/brain/daily_digest.py`,
`src/afon/brain/mynews.py`, `src/afon/brain/tools/calendar.py`

**The bar.** One brief, at the right time, that the owner would have assembled himself: what is
fixed today, what is at risk, what changed overnight, what he asked to be reminded of — and nothing
else.

**Budget.** Assembled in ≤8s, spoken in ≤90s, ≤1 LLM call per section.

**Floor**
- [x] 17.F1 Digest at 06:00 with calendar, tasks, news, reminders. *gate:* `test_daily_digest.py`
- [x] 17.F2 No duplicate delivery. *gate:* `test_daily_digest.py`, `test_acknowledgements.py`
- [ ] 17.F3 A failed source degrades the section, never the brief — and says which source failed.
      *gate:* `test_daily_digest.py` extended [source down]

**Raise**
- [ ] 17.R1 Adaptive timing from the wake pattern rather than a fixed 06:00.
      *gate:* new `test_digest_timing.py`
- [ ] 17.R2 Relevance filter: the brief drops sections with nothing worth saying, and says so in one
      clause. *gate:* `test_daily_digest.py` [empty section]
- [ ] 17.R3 An evening counterpart — what happened, what slipped, what tomorrow needs.
      *gate:* `test_digest_timing.py` [evening]

**Elite**
- [ ] 17.E1 A month of briefs where the owner never has to check calendar or inbox afterwards to
      find something the brief should have carried. *gate:* review recorded in TODO.

---

## S18 · Microphone & Speaker Management

**Status** complete for now · **Surfaces** `src/afon/edge/audio_devices.py`,
`src/afon/edge/switch_audio.py`, `src/afon/edge/audio_watchdog.py`, `src/afon/edge/aec.py`,
`src/afon/edge/vad_bargein.py`, `src/afon/edge/voice_health.py`

**The bar.** Plug in AirPods mid-sentence and nothing breaks. He never goes deaf without noticing;
he never talks over himself; barge-in works at conversational volume.

**Budget.** Device switch recovery ≤2s. Watchdog detects a dead stream ≤30s. Barge-in latency ≤300ms.

**Floor**
- [x] 18.F1 Watchdog for stale streams on device change. *gate:* `test_audio_watchdog.py`
- [x] 18.F2 Output routing and switching. *gate:* `test_audio_output_switch.py`, `test_audio_route.py`
- [x] 18.F3 VAD + barge-in. *gate:* `test_phase1_vad_bargein.py`
- [x] 18.F4 The echo path had two causes; both fixed (L3c). *gate:* `test_aec.py`

**Raise**
- [ ] 18.R1 Input level normalisation across devices so the gate threshold means the same thing on
      every mic. *gate:* `test_voice_io.py` extended.
- [ ] 18.R2 Room acoustics adaptation (near-field vs across-room) feeding the speaker gate.
      *gate:* `test_speaker_verdict.py` [distance]
- [ ] 18.R3 Speaker health self-check — a periodic inaudible probe confirms output actually plays.
      *gate:* `test_uptime_watch.py` extended.

**Elite**
- [ ] 18.E1 A day of AirPods connect/disconnect churn with zero deaf periods and zero echo.
      *gate:* `edge_readiness.py` recorded run.

**Blocked** Krisp AEC SDK (paid) for the last increment of echo suppression.

---

## S19 · Composio / MCP Integration

**Status** built, not well structured · **Surfaces** `src/afon/brain/tools/composio.py`,
`src/afon/brain/composio_catalog.py`, `src/afon/brain/mcp_client.py`

**The bar.** A new external capability is one connection away, appears in the catalogue with a
correct description, and fails loudly and typed when its account is not connected.

**Budget.** MCP tool discovery does not add to per-turn prefill unless the tool is in the narrowed
family (see S03.R1).

**Floor**
- [x] 19.F1 Connect flow, account listing, routing. *gate:* `test_composio_connect.py`,
      `test_composio_accounts.py`, `test_composio_router.py`
- [x] 19.F2 `_context()` memoised on its *values* rather than on success, so a failed lookup with a
      configured user id cached "no active toolkits" for the whole process lifetime, and a
      successful empty one re-hit the network on every call. Memoises on success now (J3.8).
      *gate:* `test_composio_context_cache.py`
- [ ] 19.F3 An unconnected account produces `is_not_configured`, never a plausible-sounding failure.
      *gate:* `test_no_result_sentinels.py` extended.

**Raise**
- [ ] 19.R1 Catalogue hygiene: only connected apps' tools enter the narrowed family.
      *gate:* `test_catalogue_narrowing.py` [composio]
- [ ] 19.R2 MCP server health surfaced in the HUD with last-success timestamps.
      *gate:* `test_mcp_client.py` extended.
- [ ] 19.R3 Per-tool rate limiting and retry policy shared with S20.
      *gate:* `test_new_integrations.py` extended.

**Elite**
- [ ] 19.E1 Three real Composio-backed workflows run unattended for a week without a manual repair.
      *gate:* reliability log review.

---

## S20 · External API Integrations

**Status** complete for now · **Surfaces** `src/afon/brain/tools/` (weather, maps, wolfram, gmail,
calendar, notion, telegram, music, web), `src/afon/brain/google.py`

**The bar.** Every integration either works, or says precisely what it needs — a key, a scope, a
connection — and never invents an answer in the meantime.

**Budget.** External call p95 ≤2s with a 1-retry policy; total turn budget unaffected by any single
slow provider (hard timeout).

**Floor**
- [x] 20.F1 Typed "not configured" contract across all integrations.
      *gate:* `test_no_result_sentinels.py`, `test_phase11_integrations.py`
- [x] 20.F2 Google OAuth verified live on the VPS. *gate:* `test_new_integrations.py`
- [ ] 20.F3 Every integration declares a timeout and a retry policy in one place, not per module.
      *gate:* new `test_api_policy.py`

**Raise**
- [ ] 20.R1 Response caching where the data is slow-moving (weather, fx, maps) with explicit TTLs.
      *gate:* `test_phase9b_cache.py` extended.
- [ ] 20.R2 Quota awareness — an integration near its limit degrades before it fails.
      *gate:* `test_api_policy.py` [quota]
- [ ] 20.R3 Credentials move out of `.env` into the password store, one source.
      *gate:* `check_config.py` extended.

**Elite**
- [ ] 20.E1 Zero fabricated answers from an unconfigured or failing integration across the whole
      behavioural suite. *gate:* `behavioral_suite.py` honesty category = 100.

**Blocked** owner: Home Assistant token, Twilio, Google Maps key, Fitness API enablement, key
rotation, `.env` → password-store migration.

---

## S21 · Personal Time Management

**Status** built, not well structured · **Surfaces** `src/afon/brain/tools/calendar.py`,
`src/afon/brain/tools/routines.py`, `src/afon/brain/modes.py`,
`src/afon/brain/interventions.py`, `src/afon/brain/tools/activity.py`

**The bar.** Afon protects the owner's time: he knows the shape of a normal day, notices when it is
being eaten, and intervenes before the day is lost rather than reporting it afterwards.

**Budget.** Schedule reasoning ≤300ms, no LLM call for conflict detection.

**Floor**
- [ ] 21.F1 **`routines.json` holds the owner's real routines.** It is effectively empty today — the
      language slot and the evening review are absent, so everything downstream reasons about a day
      that does not exist. *gate:* `test_scheduler_brain.py` extended — routines non-empty and
      structurally valid.
- [ ] 21.F2 Conflict detection: double-booked, no-travel-time, no-breaks are detected and named.
      *gate:* `test_calendar_dates.py` extended.
- [ ] 21.F3 Date and time handling is robust across timezones and phrasings.
      *gate:* `check_date_robustness.py`, `test_weather_when.py`

**Raise**
- [ ] 21.R1 Time-blocking proposals for committed work, offered not imposed.
      *gate:* new `test_time_blocking.py`
- [ ] 21.R2 Actual-vs-planned tracking: where the day really went, from `activity.py`.
      *gate:* `test_time_blocking.py` [actuals]
- [ ] 21.R3 Energy-aware scheduling — hard work into the hours it has historically worked.
      *gate:* `test_time_blocking.py` [energy]

**Elite**
- [ ] 21.E1 A month where every routine commitment either happened or was consciously renegotiated
      with Afon. *gate:* routine adherence log.

---

## S22 · System Recoverability

**Status** complete for now · **Surfaces** `src/afon/brain/protocols.py`,
`src/afon/protocols/` (backup, checkpoint, phoenix, ragnarok, diagnostics, goodnight, ping,
auditpack), `src/afon/brain/health.py`, `scripts/restart_edge.ps1`

**The bar.** Any single failure — process, disk, config, model, network — is recovered from without
the owner learning about it, and every recovery leaves a record. A total loss is restorable from
backup within an hour.

**Budget.** Process restart ≤30s. Checkpoint restore ≤5min. Backup age ≤24h at all times.

**Floor**
- [x] 22.F1 Backup, checkpoint and restore protocols with drills.
      *gate:* `test_backup_restore.py`, `test_protocol_drills.py`
- [x] 22.F2 Daily freshness restarts on both hosts. *gate:* `test_uptime_watch.py`
- [x] 22.F3 Self-repair paths for known failure shapes. *gate:* `test_self_repair.py`
- [ ] 22.F4 Backup **restore** is verified on a schedule, not just backup creation — an untested
      backup is a rumour. *gate:* `test_backup_restore.py` extended [scheduled restore drill]

**Raise**
- [ ] 22.R1 Config rollback: a bad config change is detected and reverted automatically.
      *gate:* `test_setup_wizard.py` extended.
- [ ] 22.R2 State corruption detection on the memory stores with a repair path.
      *gate:* `test_vector_store.py` extended.
- [ ] 22.R3 Recovery reports reach the owner as a summary, not silence.
      *gate:* `test_protocol_reports.py`

**Elite**
- [ ] 22.E1 A blind drill: kill the brain, the edge, and the network in one hour; Afon is back
      without owner action and can narrate what happened. *gate:* recorded drill in TODO.

---

## S23 · 24/7 Reachability

**Status** complete for now — **currently parked by design** · **Surfaces** VPS `jarvis-brain`
systemd user unit, `deploy/vps/afon_ticker.py`, `src/afon/brain/tools/notify.py`,
`src/afon/brain/telegram_bridge.py`

**The bar.** Afon is reachable from anywhere, always, by voice or text — and when he cannot answer,
the message is held and answered later rather than lost.

**Budget.** Cold reach-to-first-word ≤4s. Uptime ≥99.5% monthly excluding deliberate parks.

**Floor**
- [x] 23.F1 Brain runs 24/7 on the VPS with linger and a ticker.
      *gate:* `test_uptime_watch.py`, `test_warm_standby.py`
- [x] 23.F2 Messages arriving while down are held and forwarded. *gate:* `test_error_spool.py`
- [x] 23.F3 **The park switch honours itself on every host and role** — no autostart while
      `~/.afon/MAINTENANCE` exists. *gate:* `test_maintenance_lock.py`
- [ ] 23.F4 Unpark is as deliberate as park: a documented checklist that verifies each floor before
      the lock is removed. *gate:* `scripts/preflight.sh` gains an unpark mode that refuses on any
      unmet Wave-0 floor.

**Raise**
- [ ] 23.R1 Reachability monitoring from outside the VPS, so "up" is not self-reported.
      *gate:* `test_connectivity.py` extended.
- [ ] 23.R2 Degraded mode: no LLM available still answers time, reminders, and status.
      *gate:* `test_resilience.py` [degraded]
- [ ] 23.R3 Scheduled maintenance windows announced in advance rather than discovered.
      *gate:* `test_protocol_reports.py` extended.

**Elite**
- [ ] 23.E1 Thirty days at ≥99.5% with every interruption explained by a record.
      *gate:* uptime log review.

---

## S24 · Knowledge & World Model

**Status** built, not well structured · **Surfaces** `src/afon/brain/world_model.py`,
`src/afon/brain/graph.py`, `src/afon/brain/semantic.py`, `src/afon/brain/docstore.py`

**The bar.** Afon holds a model of the owner's world — people, places, projects, obligations,
recurring shapes — and reasons *from* it rather than re-deriving it each turn. Asked "what do you
think is going on with X", he answers with entities and relations, dated.

**Budget.** World-model consultation ≤50ms, in-process, no LLM call.

**Floor**
- [ ] 24.F1 Entity resolution: one person, one node, regardless of spelling or channel
      (`resolve_contact` covers part of this today). *gate:* `test_proper_nouns.py`,
      `test_contacts.py` extended.
- [ ] 24.F2 The world model states what it does *not* know about an entity when asked.
      *gate:* `test_world_model.py` extended.

**Raise**
- [ ] 24.R1 Temporal validity — facts carry "true from / true until", so a moved apartment does not
      stay true forever. *gate:* new `test_world_model_time.py`
- [ ] 24.R2 Belief revision on contradiction, with an audit line. *gate:* `test_world_model.py`
- [ ] 24.R3 The model is consulted in the *prompt path*, not only by tools — it should shape answers.
      *gate:* `test_memory_autorecall.py` extended.

**Elite**
- [ ] 24.E1 "What's going on with <project>?" produces a dated, sourced, entity-grounded briefing in
      one turn. *gate:* `behavioral_suite.py` new scenario.

**Cross-refs** TODO K3, J0.

---

## S25 · Personality & Interaction Style

**Status** complete for now · **Surfaces** `personality/`, `src/afon/brain/affect.py`,
`src/afon/edge/affect_tts.py`, `src/afon/brain/relationship.py`, `src/afon/brain/prefs.py`

**The bar.** One recognisable character across voice, text and push: dry, precise, unhurried, never
sycophantic. He reads the room — brief when the owner is busy, fuller when he is not — and the
warmth is earned rather than performed.

**Budget.** Persona costs ≤900 prompt tokens and is byte-stable for cache hits (see S02.R2).

**Floor**
- [x] 25.F1 Persona, operating rules and affect→TTS wired and at template parity.
      *gate:* `test_affect_voice.py`, `test_affect_tts_edge.py`, `test_skill_docs_resolve.py`
- [x] 25.F2 Relationship memory informs address and familiarity. *gate:* `test_relational.py`
- [ ] 25.F3 Persona is one file, not several — no second definition can drift (the fleet's
      IDENTITY.md/SOUL.md lesson). *gate:* `test_skill_docs_resolve.py` extended [single source]

**Raise**
- [ ] 25.R1 Register adapts to channel: a push is not a monologue, a voice answer is not a document.
      *gate:* new `test_register.py`
- [ ] 25.R2 Verbosity adapts to measured context (in a meeting, walking, at the desk).
      *gate:* `test_register.py` [context]
- [ ] 25.R3 Humour and banter have a budget and a mute; both are tested.
      *gate:* `test_companion_safety.py` extended.

**Elite**
- [ ] 25.E1 Conversation category ≥95 with no answer the owner would call obsequious or padded.
      *gate:* `behavioral_suite.py` + `test_brain_voice_grade.py`

**Cross-refs** TODO I7.

---

## S26 · Multi-Modal Perception

**Status** built, not well structured · **Surfaces** `src/afon/brain/tools/camera.py`,
`src/afon/brain/tools/multimodal.py`, `src/afon/brain/tools/activity.py`,
`src/afon/brain/presence.py`

**The bar.** Afon perceives more than words: who is present, what is on the screen, what the room
sounds like — and fuses those into one situational read rather than three unrelated facts.

**Budget.** A perception snapshot ≤500ms total, ≤1 vision call, cached for 60s.

**Floor**
- [x] 26.F1 Vision on demand with a VLM path. *gate:* `test_vision.py`, `test_screenshot_transport.py`
- [ ] 26.F2 A single `perceive()` snapshot that returns presence + visual + activity together, so
      callers stop assembling their own. *gate:* new `test_perception_snapshot.py`
- [ ] 26.F3 Every perception carries a freshness stamp; stale perception is never presented as
      current. *gate:* `test_perception_snapshot.py` [staleness]

**Raise**
- [ ] 26.R1 Audio scene classification — speech, music, TV, silence — feeding the speaker gate.
      *gate:* `test_speaker_verdict.py` [scene]
- [ ] 26.R2 Screen understanding: what application, what document, what state.
      *gate:* `test_vision.py` extended.
- [ ] 26.R3 Anomaly detection: something unusual in the room or on the screen is *noticed*.
      *gate:* `test_perception_snapshot.py` [anomaly]

**Elite**
- [ ] 26.E1 "What am I looking at / what's going on here" answered correctly ≥90% across a mixed
      fixture day. *gate:* `test_vision.py` fixture suite.

---

## S27 · Contextual Awareness

**Status** built, not well structured · **Surfaces** `src/afon/brain/presence.py`,
`src/afon/brain/modes.py`, `src/afon/brain/tools/activity.py`, `src/afon/brain/tools/maps.py`

**The bar.** The same sentence gets a different answer depending on where the owner is, what he is
doing, and how his day has gone — and Afon can state which context he assumed.

**Budget.** Context read ≤30ms in-process; never an LLM call.

**Floor**
- [ ] 27.F1 One context object — location, presence, activity, mode, time-of-day, calendar state —
      assembled once per turn and passed down, not re-derived per tool.
      *gate:* new `test_context_object.py`
- [ ] 27.F2 The assumed context is stated when it changes the answer.
      *gate:* `test_context_object.py` [disclosure]

**Raise**
- [ ] 27.R1 Location granularity: home / desk / away / travelling, with the source named.
      *gate:* `test_presence_arrival.py` extended.
- [ ] 27.R2 Availability inference (in a call, in a meeting, in flow) driving S13 and S14.
      *gate:* `test_proactive_suppression.py` [availability]
- [ ] 27.R3 Context history — "what was I doing when X happened" is answerable.
      *gate:* `test_context_object.py` [history]

**Elite**
- [ ] 27.E1 A day where no interruption lands at a wrong moment and no answer assumes the wrong
      place. *gate:* proactivity + context log review.

---

## S28 · Environmental / IoT Orchestration

**Status** built and **dark** — complete code, no token, controls nothing ·
**Surfaces** `src/afon/brain/tools/smarthome.py`, `docs/home-assistant.md`

**The bar.** "It's cold", "I'm going to bed", "I'm leaving" each do the right physical thing, are
confirmed by *reading the device state back*, and never surprise anyone else in the flat.

**Budget.** Device command → verified state ≤3s.

**Floor**
- [ ] 28.F1 **Home Assistant token** installed and the connection verified live. This is the single
      blocker for the whole system. *gate:* `test_new_integrations.py` — a real entity listed.
- [ ] 28.F2 Read-back verification: Afon reports the state he *observed*, not the command he sent.
      *gate:* new `test_smarthome_verify.py`
- [ ] 28.F3 Anything that affects other people (lights in shared rooms, locks, heating) is in the
      confirm tier. *gate:* `test_confirm_tier_documented.py`

**Raise**
- [ ] 28.R1 Scenes: named multi-device states ("bed", "leaving", "focus") with one command.
      *gate:* `test_smarthome_verify.py` [scene]
- [ ] 28.R2 Presence-driven automation tied to S27, with an explicit off switch.
      *gate:* `test_smarthome_verify.py` [presence rule]
- [ ] 28.R3 Device-offline handling: a dead bulb is reported, not ignored.
      *gate:* `test_smarthome_verify.py` [offline]

**Elite**
- [ ] 28.E1 A week of automated scenes with zero surprises and zero silent failures.
      *gate:* smarthome audit log.

**Blocked** owner: Home Assistant long-lived token (TODO C1 — the last external blocker from Part C).

---

## S29 · Automation & Workflow Engine

**Status** built, not well structured · **Surfaces** `src/afon/brain/tools/macros.py`,
`src/afon/brain/tools/routines.py`, `src/afon/brain/tools/skills.py`,
`src/afon/brain/scheduler.py`, `src/afon/brain/approvals.py`

**The bar.** "When X, do Y" means exactly that, including when X is a negative condition. Automations
are visible, listable, pausable, and every autonomous run leaves a report.

**Budget.** Rule evaluation ≤20ms per tick; scheduler drift ≤5s.

**Floor**
- [x] 29.F1 The parsed comparison operator is actually applied — `== 0` and `!= 0` no longer mean
      the same thing (H2.12, fixed). *gate:* `test_if_then_operator.py`
- [x] 29.F2 Macro guard against runaway/self-triggering chains.
      *gate:* `test_singleton_and_macro_guard.py`
- [x] 29.F3 Human approval gates for outward actions. *gate:* `test_approvals.py`
- [ ] 29.F4 Every automation is listable with its last run, next run, and outcome.
      *gate:* `test_skill_runtime.py` extended.

**Raise**
- [ ] 29.R1 Dry-run mode: "what would this do" before enabling.
      *gate:* new `test_automation_dryrun.py`
- [ ] 29.R2 Failure policy per automation — retry, skip, alert — declared rather than defaulted.
      *gate:* `test_skill_runtime.py` [failure policy]
- [ ] 29.R3 Automations can be authored by voice and are read back for confirmation before saving.
      *gate:* new `test_automation_authoring.py`

**Elite**
- [ ] 29.E1 Ten owner-authored automations running for a month with zero misfires and a monthly
      report of what each did. *gate:* automation audit log.

**Cross-refs** TODO H2.12 (the `if_then` operator finding — closed).

---

## S30 · Persistent Memory

**Status** built, not well structured · **Surfaces** `src/afon/brain/memory.py`,
`src/afon/brain/semantic.py`, `src/afon/brain/graph.py`, `src/afon/brain/patterns.py`,
`src/afon/brain/relationship.py`, `src/afon/brain/docstore.py`, `src/afon/brain/cache.py`

**The bar.** One question — "what do we know about X?" — reaches every store once, ranked, with
provenance and a date. Nothing the owner said last week is lost because it was said to the other
host.

**Budget.** Unified recall p95 ≤300ms. Auto-recall adds one store round-trip per turn, never N.

**Floor**
- [ ] 30.F1 **Resolve the two-host split.** Laptop and VPS hold divergent learned state; he is
      running on a week-old memory of the owner (TODO I6 and the rename findings). Decide the merge
      direction, execute it, record the decision. *gate:* new `test_memory_single_origin.py` — one
      store path per environment, no second candidate directory present.
- [ ] 30.F2 **One recall facade.** Seven stores, five holding their own SQLite handle, no unified
      entry point (TODO K3 / J3.3). A single `recall()` fans out and merges; callers stop touching
      stores directly. *gate:* `test_layering.py` extended — no module outside the facade opens a
      memory connection.
- [ ] 30.F3 Every stored fact carries source, timestamp and confidence.
      *gate:* `test_memory_salience.py` extended.

**Raise**
- [ ] 30.R1 Ranked merge with dedup across stores (L0–L5 + graph + patterns).
      *gate:* new `test_unified_recall.py` — 30 queries, expected fact in top-3 ≥90%.
- [ ] 30.R2 Contradiction handling: two stores disagreeing produces "I have two versions", not a
      silent pick. *gate:* `test_unified_recall.py` [contradiction]
- [ ] 30.R3 Forgetting is a policy, not a leak: retention per layer, verified rotation.
      *gate:* `test_memory_hygiene.py`
- [ ] 30.R4 The 607 unverified inferred graph edges are verified, weighted down, or dropped (TODO
      J0). *gate:* `test_memory_graph_learn.py` extended — no unverified edge below the confidence
      floor participates in recall.

**Elite**
- [ ] 30.E1 Memory behavioural category ≥95 with recall latency inside budget on the VPS.
      *gate:* `test_memory_behavioral.py` + `profile_memory_recall.py`

**Blocked** merge-direction decision (owner). **Cross-refs** TODO K3, I6, J3.3, J0.

---

## S31 · Self-Monitoring, Diagnostics & Self-Healing

**Status** complete for now · **Surfaces** `src/afon/brain/reliability.py`,
`src/afon/brain/health.py`, `src/afon/brain/hud.py`, `src/afon/brain/metrics.py`,
`src/afon/brain/tools/diagnose.py`, `src/afon/brain/audit.py`

**The bar.** Afon knows what is wrong with himself before the owner does, says it in one sentence,
repairs what he can, and escalates the rest with evidence. No surface ever calls a broken component
healthy.

**Budget.** Health tick ≤150ms. Probe schedule ≤1/5min. HUD render ≤200ms.

**Floor**
- [x] 31.F1 Four health surfaces that **agree**: structural check, functional probe, HUD, and the
      error journal (J3.6). *gate:* `test_health_agreement.py`
- [x] 31.F2 Typed error taxonomy with turn correlation. *gate:* `test_error_taxonomy.py`,
      `test_error_tracking.py`
- [x] 31.F3 Tool-level reliability learned from the audit trail. *gate:* `test_tool_reliability.py`
- [ ] 31.F4 Every background loop appears in the HUD with its last tick, its period and its budget —
      "no silent work". *gate:* new `test_loop_registry.py`

**Raise**
- [ ] 31.R1 Trend detection: degrading before failing (rising p95, rising retry rate) raises a
      signal. *gate:* `test_metrics.py` [trend]
- [ ] 31.R2 Self-repair coverage extended to the top five recurring failure shapes from the journal.
      *gate:* `test_self_repair.py`
- [ ] 31.R3 A weekly self-report: what broke, what repaired itself, what needs the owner.
      *gate:* `test_protocol_reports.py` extended.

**Elite**
- [ ] 31.E1 A month where every incident was self-detected before the owner noticed.
      *gate:* incident log review.

---

## S32 · Redundancy & Failover

**Status** **partially missing at the architecture level** — model failover is real; there is one
VPS, one edge, no replication. **Surfaces** `src/afon/brain/llm.py`,
`src/afon/edge/_supervisor.py`, `src/afon/shared/singleton.py`

**The bar.** No single machine's death takes Afon away. Losing the VPS degrades him to local-only;
losing the laptop degrades him to cloud-only; both are announced, and neither is silent.

**Budget.** Failover detection ≤30s. Degraded mode announced within one turn.

**Floor**
- [x] 32.F1 LLM provider failover chain. *gate:* `test_brain_llm.py`, `test_resilience.py`
- [x] 32.F2 Single-instance enforcement (no two brains, no two edges).
      *gate:* `test_singleton_and_macro_guard.py`
- [ ] 32.F3 **Explicit degraded modes.** Define and implement three: brain-down (edge answers what it
      can), edge-down (text channels only), network-down (local model + local tools). Each announces
      itself. *gate:* new `test_degraded_modes.py`
- [ ] 32.F4 Health of the *other* host is known to each host, not assumed.
      *gate:* `test_connectivity.py` extended.

**Raise**
- [ ] 32.R1 State replication: memory and task stores mirrored between hosts on a schedule, with a
      verified restore. *gate:* `test_backup_restore.py` [cross-host]
- [ ] 32.R2 Automatic promotion — the laptop takes over brain duties when the VPS is unreachable for
      N minutes. *gate:* `test_degraded_modes.py` [promotion]
- [ ] 32.R3 Split-brain prevention when both come back. *gate:* `test_degraded_modes.py` [reconcile]

**Elite**
- [ ] 32.E1 A live drill: pull the VPS for an hour; Afon stays usable and reconciles cleanly
      afterwards. *gate:* recorded drill.

**Depends on** S30.F1 (one memory origin) — replication before that is replicating a conflict.

---

## S33 · Goal & Project Management

**Status** built, not well structured · **Surfaces** `src/afon/brain/objectives.py`,
`src/afon/brain/backlog.py`, `src/afon/brain/worker.py`, `src/afon/brain/copilot.py`

**The bar.** Afon holds the owner's actual objectives, knows which are moving and which are stalled,
and connects daily work to them without being asked. A stalled objective is raised, not buried.

**Budget.** Objective review ≤1 LLM call weekly; daily attribution is in-process.

**Floor**
- [x] 33.F1 Objectives with progress notes and deferred items. *gate:* `test_objectives.py`
- [ ] 33.F2 Milestones and target dates, so "stalled" is computable rather than felt.
      *gate:* `test_objectives.py` extended [milestones]
- [ ] 33.F3 Every task can name the objective it serves, or is explicitly ad-hoc (pairs with 01.R3).
      *gate:* `test_goal_attribution.py`

**Raise**
- [ ] 33.R1 Stall detection and escalation into proactivity. *gate:* new `test_objective_stall.py`
- [ ] 33.R2 Conflict detection between objectives competing for the same hours.
      *gate:* `test_objective_stall.py` [conflict]
- [ ] 33.R3 Weekly review protocol — what moved, what did not, what to drop.
      *gate:* `test_protocol_reports.py` extended.

**Elite**
- [ ] 33.E1 A quarter where no objective goes stale unnoticed for more than a week.
      *gate:* objective log review.

---

## S34 · Health & Wellness Monitoring

**Status** half-built — tools exist, the data source is not enabled ·
**Surfaces** `src/afon/brain/tools/fitness.py`, `src/afon/brain/tools/activity.py`,
`src/afon/brain/coaching.py`

**The bar.** Afon notices the shape of the owner's body-clock — sleep, movement, screen hours — and
says something useful about it at the right moment, without nagging and without pretending to be a
doctor.

**Budget.** Daily aggregation ≤1 external call per source; no per-turn cost.

**Floor**
- [ ] 34.F1 **Enable the fitness data source** and verify a real read; today the tool exists and the
      API is not enabled. *gate:* `test_new_integrations.py` [fitness]
- [ ] 34.F2 Screen-time and activity signals are real and dated, not estimated.
      *gate:* `test_phase12_utility.py` extended.
- [ ] 34.F3 Health talk stays inside a stated boundary — observations and patterns, never diagnosis.
      *gate:* `test_companion_safety.py` extended.

**Raise**
- [ ] 34.R1 Sleep-pattern tracking feeding S17's adaptive timing.
      *gate:* `test_digest_timing.py` [sleep]
- [ ] 34.R2 Break and posture nudges gated on real focus state, capped per day.
      *gate:* `test_proactive_thresholds.py` [wellness kind]
- [ ] 34.R3 Weekly wellness summary with trends, not single readings.
      *gate:* `test_coaching.py` extended.

**Elite**
- [ ] 34.E1 A month of wellness nudges with ≥50% acceptance and zero the owner calls nagging.
      *gate:* acceptance log.

**Blocked** owner: enable the Fitness API.

---

## S35 · Crisis & Emergency Response

**Status** half-built — system-level crisis protocols exist, *owner*-level does not ·
**Surfaces** `src/afon/protocols/ragnarok.py`, `src/afon/protocols/phoenix.py`,
`src/afon/brain/reliability.py`, `src/afon/brain/tools/phone.py`

**The bar.** In a real emergency Afon does the simplest useful thing fast: reaches someone, gives
the right information, and does not require a conversation. Outside emergencies he never
mis-classifies one.

**Budget.** Emergency path ≤3s to first outward action, no LLM dependency on the critical path.

**Floor**
- [ ] 35.F1 An explicit emergency classifier with a **conservative** threshold and a manual trigger
      phrase, on a path that does not depend on the LLM chain.
      *gate:* new `test_emergency_path.py` — false-positive rate 0 on 50 ordinary prompts.
- [ ] 35.F2 An emergency contact list and a defined action per category, stored locally.
      *gate:* `test_emergency_path.py` [contacts resolved offline]
- [x] 35.F3 System-level protocols (ragnarok/phoenix) remain drill-tested.
      *gate:* `test_protocol_drills.py`

**Raise**
- [ ] 35.R1 Escalation ladder: notify → call → contact a third party, each with a stop condition.
      *gate:* `test_emergency_path.py` [ladder]
- [ ] 35.R2 Silence detection — a missed check-in after an at-risk signal triggers the ladder.
      *gate:* `test_deadmans_switch.py` extended.
- [ ] 35.R3 Post-incident record written automatically. *gate:* `test_protocol_reports.py`

**Elite**
- [ ] 35.E1 Two rehearsed drills (one voice-triggered, one silence-triggered) completed end to end.
      *gate:* recorded drills.

**Blocked** owner: Twilio credentials (the calling path), emergency contact list.

---

## S36 · Security & Access Control

**Status** complete for now · **Surfaces** `src/afon/brain/tools/base.py` (confirm tier),
`src/afon/brain/audit.py`, `src/afon/edge/speaker_gate.py`, `src/afon/brain/tools/camera.py`,
`src/afon/shared/maintenance.py`, `SECURITY.md`

**The bar.** Nothing irreversible happens without the right person asking; nothing catastrophic
happens at all; everything that happened is on the record; and there is always a switch that stops
him.

**Budget.** Authorisation decision ≤50ms, no network call.

**Floor**
- [x] 36.F1 A documented confirm tier covering every mutating tool.
      *gate:* `test_confirm_tier_documented.py`
- [x] 36.F2 Deterministic refusal of catastrophic actions, independent of the model.
      *gate:* `test_security_hardening.py`, `test_safety_autonomy.py`
- [x] 36.F3 Identity as a second factor for protected actions, failing *open* only when the sensor
      is honestly unsure. *gate:* `test_face_second_factor.py`
- [x] 36.F4 A park switch that halts every role on every host.
      *gate:* `test_maintenance_lock.py`
- [ ] 36.F5 Secrets have exactly one home (password store), and the repo proves it.
      *gate:* `check_public_clean.py` extended.

**Raise**
- [ ] 36.R1 Session-scoped elevation: a confirmed sensitive action does not silently authorise the
      next one. *gate:* `test_security_hardening.py` [elevation scope]
- [ ] 36.R2 Anomaly alerting on the audit trail — unusual command patterns raise a signal.
      *gate:* `test_error_tracking.py` extended.
- [ ] 36.R3 Guest mode: a non-owner gets a strictly reduced surface, by construction.
      *gate:* new `test_guest_mode.py`

**Elite**
- [ ] 36.E1 An adversarial pass: 30 attempts to talk Afon past the gates, zero successes.
      *gate:* new `test_adversarial_prompts.py`

**Blocked** owner: `.env` → password-store migration, key rotation.

---

## S37 · Privacy & Data Governance

**Status** half-built — good hygiene, no policy · **Surfaces** `src/afon/shared/errors.py`
(scrubbing), `bench/check_public_clean.py`, `SECURITY.md`, local-first processing paths

**The bar.** The owner can ask "what do you know about me, where is it, and how long will you keep
it" and get a real answer — and can delete any of it in one command.

**Budget.** Retention sweep runs daily, ≤30s.

**Floor**
- [ ] 37.F1 **A data inventory**: every store, what personal data it holds, where it lives, who can
      read it. Generated from the code, not written by hand.
      *gate:* new `test_data_inventory.py` — every memory/audit store appears.
- [ ] 37.F2 A retention policy per store, enforced by the hygiene job, not by intention.
      *gate:* `test_memory_hygiene.py` extended [per-store TTL]
- [ ] 37.F3 `forget X` actually removes X from every store, verified by re-querying.
      *gate:* new `test_right_to_forget.py`

**Raise**
- [ ] 37.R1 Sensitivity classification — biometric, financial, medical, ordinary — with rules per
      class (e.g. biometrics never leave the laptop, already true for face refs).
      *gate:* `test_data_inventory.py` [classification]
- [ ] 37.R2 Egress accounting: what left the machine, to which provider, when.
      *gate:* new `test_egress_log.py`
- [ ] 37.R3 Consent gates for anything shared with a third party.
      *gate:* `test_confirm_tier_documented.py` extended.

**Elite**
- [ ] 37.E1 A full privacy report the owner can read in five minutes and act on.
      *gate:* generated report reviewed and recorded.

---

## S38 · Communication Hub

**Status** built, not well structured · **Surfaces** `src/afon/brain/tools/gmail.py`,
`src/afon/brain/tools/telegram.py`, `src/afon/brain/tools/channels.py`,
`src/afon/brain/tools/phone.py`, `src/afon/brain/tools/contacts.py`

**The bar.** One inbox in Afon's head: he knows what is waiting across every channel, what needs an
answer, and can draft or send in the owner's voice with the owner's approval.

**Budget.** Inbox sweep ≤5s across all channels; drafting ≤1 LLM call.

**Floor**
- [x] 38.F1 Email and Telegram send/read, with contact resolution.
      *gate:* `test_phase11_integrations.py`, `test_contacts.py`
- [ ] 38.F2 **A unified "what's waiting" view** across channels, deduplicated by thread.
      *gate:* new `test_unified_inbox.py`
- [ ] 38.F3 Nothing is sent without approval; drafts are always shown first.
      *gate:* `test_approvals.py`

**Raise**
- [ ] 38.R1 Triage: urgent / needs reply / FYI / ignore, learned from what the owner actually
      answers. *gate:* `test_unified_inbox.py` [triage]
- [ ] 38.R2 Thread awareness — a reply knows the conversation, not just the last message.
      *gate:* `test_unified_inbox.py` [thread]
- [ ] 38.R3 Voice channel (calls) once credentials exist. *gate:* `test_phase11_integrations.py`

**Elite**
- [ ] 38.E1 A week where the owner never opens an email client first — Afon surfaces everything that
      mattered. *gate:* inbox review.

**Blocked** owner: Twilio credentials.

---

## S39 · Multi-Agent Delegation

**Status** built, thin · **Surfaces** `src/afon/brain/fleet.py`, `src/afon/brain/worker.py`,
`src/afon/brain/tools/coding.py`, `src/afon/brain/code_improve.py`

**The bar.** Work that belongs elsewhere goes elsewhere — to the fleet, to a background worker, to a
specialist — with a clear brief, a deadline, and a result that comes back verified rather than
trusted.

**Budget.** Delegation overhead ≤1s; no delegated task runs unmonitored for more than its declared
period.

**Floor**
- [x] 39.F1 Reachable delegation to the external fleet router. *gate:* fleet live check in
      `run_all_tests.py` (gated on authorisation).
- [ ] 39.F2 Every delegation is tracked as a task with a deadline and a result check — nothing is
      fire-and-forget. *gate:* new `test_delegation_tracking.py`
- [ ] 39.F3 A delegated result is **verified** before being reported as fact (anti-fabrication
      applies to other agents too). *gate:* `test_delegation_tracking.py` [verification]

**Raise**
- [ ] 39.R1 A routing policy: which kinds of work go to which agent, declared and testable.
      *gate:* `test_delegation_tracking.py` [routing]
- [ ] 39.R2 Timeout and takeback — a delegation that stalls returns to Afon.
      *gate:* `test_delegation_tracking.py` [timeout]
- [ ] 39.R3 Cost and value accounting per delegation.
      *gate:* `test_metrics.py` extended.

**Elite**
- [ ] 39.E1 Ten real delegations over a month, all tracked, all verified, none lost.
      *gate:* delegation log review.

**Cross-refs** S48 (the coordination layer above this).

---

## S40 · Financial & Asset Management

**Status** **missing as a system** — only read-only market lookups exist
(`stock_price`, `crypto_price`, `fx_rate` in `src/afon/brain/tools/utility.py`).

**The bar.** Afon knows what the owner owns and owes, notices what changed, and answers "can I
afford this / what did I spend on that" from data rather than memory. He never moves money.

**Budget.** Portfolio snapshot ≤2s, cached daily.

**Floor** *(deliberately small, deliberately read-only)*
- [ ] 40.F1 A local holdings file the owner controls, read by a `portfolio_snapshot` tool that values
      it with the existing price tools. *gate:* new `test_portfolio.py`
- [ ] 40.F2 **No transaction capability, by construction** — asserted, not merely absent.
      *gate:* `test_portfolio.py` [no mutating tool in the finance family]
- [ ] 40.F3 Values carry their as-of time and source; a stale price says so.
      *gate:* `test_portfolio.py` [staleness]

**Raise**
- [ ] 40.R1 Change notification — a threshold move raises a proactive signal.
      *gate:* `test_proactive_thresholds.py` [finance kind]
- [ ] 40.R2 Spending awareness from statements the owner supplies; categories and monthly trend.
      *gate:* `test_portfolio.py` [spending]
- [ ] 40.R3 Subscription and recurring-charge detection.
      *gate:* `test_portfolio.py` [recurring]

**Elite**
- [ ] 40.E1 A monthly financial brief the owner trusts enough not to re-check.
      *gate:* brief review recorded in TODO.

**Note** payment execution is explicitly **out of scope** for this plan. Revisit only as its own
decision record.

---

## S41 · Research & Synthesis

**Status** half-built · **Surfaces** `src/afon/brain/tools/web.py`,
`src/afon/brain/tools/browser.py`, `src/afon/brain/mynews.py`,
`src/afon/brain/tools/documents.py`, `skills/`

**The bar.** Ask a real question, get a real answer with sources, contradictions surfaced rather
than smoothed, and a note of what he could not establish.

**Budget.** A research task declares a budget in calls and minutes before it starts, and honours it.

**Floor**
- [x] 41.F1 Search/scrape with a typed fallback chain. *gate:* `test_web_fallback.py`
- [ ] 41.F2 **Citations are mandatory** for factual claims sourced from the web, and are checked to
      resolve. *gate:* new `test_citations.py`
- [ ] 41.F3 A research task states its budget and reports when it stopped early.
      *gate:* `test_citations.py` [budget]

**Raise**
- [ ] 41.R1 Source reliability weighting — not every page counts the same.
      *gate:* `test_citations.py` [weighting]
- [ ] 41.R2 Contradiction surfacing across sources instead of picking one.
      *gate:* `test_citations.py` [conflict]
- [ ] 41.R3 Synthesis into a document via S06, filed and retrievable.
      *gate:* `test_document_create.py` [research output]

**Elite**
- [ ] 41.E1 Three real research questions answered to a standard the owner would not redo himself.
      *gate:* owner review recorded in TODO.

---

## S42 · Media & Entertainment Control

**Status** built, not well structured · **Surfaces** `src/afon/brain/tools/music.py`,
`src/afon/brain/tools/localplay.py`, `src/afon/brain/tools/voicechat.py`,
`src/afon/brain/tools/channels.py`

**The bar.** "Play something", "stop", "louder", "what is this" work regardless of which of the
several playback paths is actually running — and "stop" always stops the thing that is playing.

**Budget.** Play/stop round trip ≤600ms.

**Floor**
- [x] 42.F1 **Stop routes to whichever path is playing** — desktop or music room; the two now ask
      each other (fixed 2026-08-13). *gate:* `test_localplay_routing.py`
- [ ] 42.F2 One playback-state owner, so "what's playing" has a single answer.
      *gate:* `test_localplay_routing.py` extended [single source of playing state]
- [ ] 42.F3 Volume and mute are the same concepts across paths.
      *gate:* `test_audio_output_switch.py` extended.

**Raise**
- [ ] 42.R1 Queue and history — "play that again", "skip", "what was the last one".
      *gate:* new `test_media_queue.py`
- [ ] 42.R2 Context-aware playback: pause on a call, duck on speech, resume after.
      *gate:* `test_media_queue.py` [ducking]
- [ ] 42.R3 Taste memory feeding S15's media domain. *gate:* `test_recommend.py` [media]

**Elite**
- [ ] 42.E1 A week of daily use with zero "nothing is playing" when something is.
      *gate:* media log review.

---

## S43 · Presence & Continuity

**Status** half-built · **Surfaces** `src/afon/brain/presence.py`,
`src/afon/edge/listening_pulse.py`, `src/afon/edge/device_profile.py`

**The bar.** Afon feels continuously present rather than repeatedly summoned: he knows the owner
arrived, knows he left, and picks up where the conversation stopped without being re-briefed.

**Budget.** Presence state change detected ≤10s.

**Floor**
- [x] 43.F1 Presence tracking and arrival detection. *gate:* `test_presence.py`,
      `test_presence_arrival.py`
- [ ] 43.F2 Departure and return are symmetric — a return gets a "while you were gone" only when
      there is something worth saying. *gate:* `test_presence_arrival.py` extended.
- [ ] 43.F3 Presence is a fused signal (voice + face + device activity), not any single source.
      *gate:* `test_perception_snapshot.py` [presence fusion]

**Raise**
- [ ] 43.R1 Context migration across devices — the thread, not just the session id (with S07, S12).
      *gate:* `test_session_identity.py` [migration]
- [ ] 43.R2 Ambient acknowledgement: a quiet signal that he is listening, without speaking.
      *gate:* `test_wakeword.py` extended.
- [ ] 43.R3 Long-absence handling — a week away is not resumed as if it were ten minutes.
      *gate:* `test_presence.py` [long absence]

**Elite**
- [ ] 43.E1 A day where the owner never has to re-establish context after moving or pausing.
      *gate:* live day recorded.

---

## S44 · Ethical Reasoning, Safety & Alignment

**Status** complete for now · **Surfaces** `personality/operating-rules.md`,
`src/afon/brain/tools/base.py`, `src/afon/brain/approvals.py`, `src/afon/brain/worker.py`

**The bar.** Afon refuses the things that must be refused, deterministically, regardless of which
model is answering; he never fabricates; and when he declines he says so plainly and offers what he
can do.

**Budget.** Safety checks are in-process and add ≤10ms.

**Floor**
- [x] 44.F1 Deterministic catastrophic refusal, model-independent.
      *gate:* `test_safety_autonomy.py`, `test_security_hardening.py`
- [x] 44.F2 Anti-fabrication protocol with a verification path.
      *gate:* `test_tool_failure_guard.py`, `test_no_result_sentinels.py`
- [x] 44.F3 Autonomous work defers every outward action. *gate:* `test_safety_autonomy.py`
- [ ] 44.F4 Refusals are graded: refuse · confirm · proceed-with-note, and the grade is tested.
      *gate:* new `test_refusal_grades.py`

**Raise**
- [ ] 44.R1 Value conflicts (helpfulness vs privacy vs safety) resolved by a stated precedence order,
      not by the model's mood. *gate:* `test_refusal_grades.py` [precedence]
- [ ] 44.R2 Third-party impact check for actions affecting other people.
      *gate:* `test_companion_safety.py` extended.
- [ ] 44.R3 Adversarial robustness (shares its gate with S36.E1).
      *gate:* `test_adversarial_prompts.py`

**Elite**
- [ ] 44.E1 Safety and honesty categories at 100 across five consecutive behavioural runs.
      *gate:* `behavioral_suite.py`

---

## S45 · Explainability & Transparency

**Status** complete for now · **Surfaces** `src/afon/brain/proactive_report.py`,
`src/afon/brain/tools/diagnose.py`, `src/afon/brain/audit.py`, `src/afon/brain/hud.py`

**The bar.** Every answer can be unpacked: which tools, which sources, which memory, how confident,
and why *now*. Nothing Afon does autonomously is unexplainable after the fact.

**Budget.** "Why did you do that" answers from the audit trail in ≤300ms, no LLM call needed for the
factual part.

**Floor**
- [x] 45.F1 Autonomous actions produce reports with reasoning. *gate:* `test_proactive_report.py`
- [x] 45.F2 A full audit trail with scrubbed values. *gate:* `test_phasex_audit_health_modes.py`
- [ ] 45.F3 `why` as a first-class question about the *last turn*: tools used, sources, confidence.
      *gate:* new `test_why_last_turn.py`

**Raise**
- [ ] 45.R1 Confidence is reported alongside answers where it varies (pairs with 01.F2).
      *gate:* `test_uncertainty.py` [reported confidence]
- [ ] 45.R2 Source attribution in spoken answers, short form ("from your calendar", "from the web").
      *gate:* `test_why_last_turn.py` [attribution]
- [ ] 45.R3 Decision replay: reconstruct a past turn's inputs from the audit trail.
      *gate:* `test_why_last_turn.py` [replay]

**Elite**
- [ ] 45.E1 Any turn from the past 30 days can be explained on request.
      *gate:* audit replay drill.

---

## S46 · Predictive Analytics & Forecasting

**Status** **missing** — `src/afon/brain/anticipation.py` is a calendar look-ahead, not a model.

**The bar.** Afon says useful things about what is *likely*: this meeting will overrun, this week is
overbooked, at this rate the deadline slips. Every prediction is scored afterwards.

**Budget.** Forecast computation in-process, ≤200ms, no LLM call.

**Floor** *(one predictor, honestly scored, before any second one)*
- [ ] 46.F1 A single predictor: **will today's plan fit the day**, from calendar + task estimates +
      historical overrun. *gate:* new `test_forecast.py`
- [ ] 46.F2 Every prediction is recorded with its outcome so accuracy is measurable.
      *gate:* `test_forecast.py` [scoring ledger]
- [ ] 46.F3 A prediction below a confidence floor is not spoken.
      *gate:* `test_forecast.py` [suppression]

**Raise**
- [ ] 46.R1 A second predictor once the first beats a naive baseline: task-duration estimation.
      *gate:* `test_forecast.py` [baseline comparison]
- [ ] 46.R2 Trend extrapolation on the signals already collected (screen time, sleep, spend).
      *gate:* `test_forecast.py` [trends]
- [ ] 46.R3 "What if" — one scenario at a time, stated as a scenario.
      *gate:* `test_forecast.py` [scenario]

**Elite**
- [ ] 46.E1 Predictions beat the naive baseline over a month, with the ledger to prove it.
      *gate:* forecast ledger review.

---

## S47 · Inventory & Resource Management

**Status** **fully missing.**

**The bar.** Afon knows what the owner has, where it is, and when it runs out — for the small number
of categories that actually matter, and no more.

**Budget.** Inventory query ≤100ms, local store.

**Floor** *(one category, one store, no framework)*
- [ ] 47.F1 A local inventory store with add/consume/query, seeded with **one** category the owner
      chooses. *gate:* new `test_inventory.py`
- [ ] 47.F2 Consumption is recorded with a date so depletion can be estimated at all.
      *gate:* `test_inventory.py` [history]
- [ ] 47.F3 Unknown is unknown — Afon never guesses stock he was not told about.
      *gate:* `test_inventory.py` [no invention]

**Raise**
- [ ] 47.R1 Depletion prediction and a reorder reminder into S16.
      *gate:* `test_inventory.py` [reorder]
- [ ] 47.R2 Location tracking ("where is the X").
      *gate:* `test_inventory.py` [location]
- [ ] 47.R3 A second and third category once the first is used for a month.
      *gate:* `test_inventory.py` [multi-category]

**Elite**
- [ ] 47.E1 Nothing in the tracked categories runs out unannounced for a quarter.
      *gate:* inventory log review.

---

## S48 · Collaborative Multi-Agent Coordination

**Status** built, thin — one-way delegation to an external router; no coordination layer.
**Surfaces** `src/afon/brain/fleet.py`, `src/afon/brain/worker.py`

**The bar.** When a job needs several workers, Afon briefs them, keeps them from colliding, merges
what comes back, and remains the single voice the owner hears.

**Budget.** Coordination overhead ≤10% of the total task time.

**Floor** *(depends on S39's tracking floor)*
- [ ] 48.F1 A shared work record: every delegated unit has an id, an owner, a state, and a result
      slot that both sides write to. *gate:* new `test_coordination.py`
- [ ] 48.F2 No two workers hold the same unit — claim-and-lock, asserted.
      *gate:* `test_coordination.py` [no double claim]
- [ ] 48.F3 Results merge deterministically, and disagreement is surfaced rather than averaged.
      *gate:* `test_coordination.py` [merge]

**Raise**
- [ ] 48.R1 Capability-based assignment rather than fixed routing.
      *gate:* `test_coordination.py` [assignment]
- [ ] 48.R2 Progress aggregation into one owner-facing status.
      *gate:* `test_coordination.py` [status]
- [ ] 48.R3 Failure isolation — one worker's failure does not sink the job.
      *gate:* `test_coordination.py` [isolation]

**Elite**
- [ ] 48.E1 One genuinely multi-worker job (research + draft + review) completed with a single
      coherent report. *gate:* recorded run.

---

## S49 · Fabrication & Manufacturing Control

**Status** **fully missing**, and correctly so today — there is no hardware.

**The bar.** If and when hardware exists, Afon prepares and queues jobs, and **never** starts a
machine without a physical-presence confirmation.

**Floor** *(do not build until hardware exists; these are the preconditions, recorded so the shape
is agreed in advance)*
- [ ] 49.F1 A decision record naming the first machine, its control surface, and its safety
      interlock, before any code. *gate:* the decision note exists in the vault and is cited here.
- [ ] 49.F2 Any machine-control tool enters the confirm tier at the highest grade — physical
      presence required, never voice-only, never autonomous.
      *gate:* `test_confirm_tier_documented.py` [physical-presence grade]
- [ ] 49.F3 A dry-run/simulation path exists before any live command.
      *gate:* new `test_fabrication_dryrun.py`

**Raise / Elite** deferred until 49.F1 is answered.

**Recommendation.** Keep this system explicitly parked. It is the only one of the fifty where the
right next step is a decision, not code.

---

## S50 · Legacy Continuity & Knowledge Preservation

**Status** half-built — the substrate exists, the continuity policy does not.
**Surfaces** the Obsidian vault (VPS-authoritative), `src/afon/brain/audit.py`,
`src/afon/protocols/backup.py`, `src/afon/protocols/checkpoint.py`

**The bar.** Everything worth keeping outlives any single machine, any single format, and any single
version of Afon — and can be read by a human without Afon's help.

**Budget.** Export runs monthly, ≤10min, verified.

**Floor**
- [ ] 50.F1 **A portable export**: memory, decisions, documents and audit summary written as plain
      markdown + JSON that a human can read without the codebase.
      *gate:* new `test_portable_export.py` — export produced and re-read by a fresh process.
- [ ] 50.F2 The export is verified by restoring it into an empty environment, on a schedule.
      *gate:* `test_backup_restore.py` [portable restore]
- [ ] 50.F3 The vault-write rule is enforced in code, not only in documentation: writes go to the
      authoritative host or fail loudly. *gate:* `test_vault_search.py` extended.

**Raise**
- [ ] 50.R1 Curation — what is worth preserving is decided by policy, not by keeping everything.
      *gate:* `test_portable_export.py` [curation]
- [ ] 50.R2 Provenance on preserved knowledge: when learned, from whom, how confident.
      *gate:* `test_portable_export.py` [provenance]
- [ ] 50.R3 A migration path documented and tested for the next format change.
      *gate:* `test_portable_export.py` [migration]

**Elite**
- [ ] 50.E1 A cold-start drill: a new machine, the export, and nothing else — rebuild a working Afon
      with the owner's history intact. *gate:* recorded drill.

---

# The scoreboard

One row per system. This table is the only place a status is allowed to change, and it may only
change when the named gates pass in a recorded suite run. `F` = floor tasks green, `R` = raise tasks
green, `E` = elite green.

| # | System | Today | F | R | E |
|---|---|---|---|---|---|
| S01 | Brain / Core Intelligence | structured badly | 1/3 | 0/4 | 0/2 |
| S02 | LLM Integration | complete for now | 2/3 | 0/3 | 0/1 |
| S03 | Tool Utilization | complete for now | 2/3 | 0/4 | 0/1 |
| S04 | Device Control | complete for now | 3/4 | 0/3 | 0/1 |
| S05 | Browser Control | complete for now | 2/3 | 0/3 | 0/1 |
| S06 | Document Creation | half-built | 0/3 | 0/3 | 0/1 |
| S07 | Session & Context | structured badly | 0/3 | 0/3 | 0/1 |
| S08 | Voice Enrollment | weak | 0/2 | 0/3 | 0/1 |
| S09 | Face Enrollment | not enrolled | 0/2 | 0/3 | 0/1 |
| S10 | Voice Recognition | structured badly | 2/3 | 0/3 | 0/1 |
| S11 | Face Recognition | structured badly | 2/3 | 0/3 | 0/1 |
| S12 | Multi-Device | structured badly | 2/3 | 0/3 | 0/1 |
| S13 | Notifications | structured badly | 2/3 | 0/3 | 0/1 |
| S14 | Proactivity | complete for now | 2/3 | 0/3 | 0/1 |
| S15 | Recommendations | missing | 0/3 | 0/3 | 0/1 |
| S16 | Task Queue | structured badly | 2/3 | 0/3 | 0/1 |
| S17 | Morning Brief | complete for now | 2/3 | 0/3 | 0/1 |
| S18 | Mic & Speaker | complete for now | 4/4 | 0/3 | 0/1 |
| S19 | Composio / MCP | structured badly | 2/3 | 0/3 | 0/1 |
| S20 | External APIs | complete for now | 2/3 | 0/3 | 0/1 |
| S21 | Personal Time Mgmt | structured badly | 0/3 | 0/3 | 0/1 |
| S22 | Recoverability | complete for now | 3/4 | 0/3 | 0/1 |
| S23 | 24/7 Reachability | complete, parked | 3/4 | 0/3 | 0/1 |
| S24 | Knowledge & World Model | structured badly | 0/2 | 0/3 | 0/1 |
| S25 | Personality | complete for now | 2/3 | 0/3 | 0/1 |
| S26 | Multi-Modal Perception | structured badly | 1/3 | 0/3 | 0/1 |
| S27 | Context Awareness | structured badly | 0/2 | 0/3 | 0/1 |
| S28 | IoT Orchestration | dark | 0/3 | 0/3 | 0/1 |
| S29 | Automation & Workflow | structured badly | 3/4 | 0/3 | 0/1 |
| S30 | Persistent Memory | structured badly | 0/3 | 0/4 | 0/1 |
| S31 | Self-Monitoring | complete for now | 3/4 | 0/3 | 0/1 |
| S32 | Redundancy & Failover | partly missing | 2/4 | 0/3 | 0/1 |
| S33 | Goal & Project Mgmt | structured badly | 1/3 | 0/3 | 0/1 |
| S34 | Health & Wellness | half-built | 0/3 | 0/3 | 0/1 |
| S35 | Crisis Response | half-built | 1/3 | 0/3 | 0/1 |
| S36 | Security & Access | complete for now | 4/5 | 0/3 | 0/1 |
| S37 | Privacy & Governance | half-built | 0/3 | 0/3 | 0/1 |
| S38 | Communication Hub | structured badly | 1/3 | 0/3 | 0/1 |
| S39 | Multi-Agent Delegation | thin | 1/3 | 0/3 | 0/1 |
| S40 | Financial & Asset Mgmt | missing | 0/3 | 0/3 | 0/1 |
| S41 | Research & Synthesis | half-built | 1/3 | 0/3 | 0/1 |
| S42 | Media Control | structured badly | 1/3 | 0/3 | 0/1 |
| S43 | Presence & Continuity | half-built | 1/3 | 0/3 | 0/1 |
| S44 | Ethics & Safety | complete for now | 3/4 | 0/3 | 0/1 |
| S45 | Explainability | complete for now | 2/3 | 0/3 | 0/1 |
| S46 | Predictive Analytics | missing | 0/3 | 0/3 | 0/1 |
| S47 | Inventory & Resources | missing | 0/3 | 0/3 | 0/1 |
| S48 | Multi-Agent Coordination | thin | 0/3 | 0/3 | 0/1 |
| S49 | Fabrication Control | parked by decision | 0/3 | 0/0 | 0/0 |
| S50 | Legacy Continuity | half-built | 0/3 | 0/3 | 0/1 |

**Totals at the moment of writing: 65 of 156 floor tasks green, 0 of 150 raise tasks, 0 of 50 elite
tasks — 65 of 356.** The floors are the furthest along because the last three weeks of work were
almost entirely floor work; that is the correct order and it should continue. These counts are
derived from the checkboxes in the sections above and must be re-derived, not edited by hand.

---

# What "done" means for the whole programme

The two documents together are finished when **all** of the following are true and each is
demonstrated by a recorded run, not asserted:

1. **Wired** — every system's floor tasks are green, and no capability exists that is unreachable
   from a real turn. Gate: `test_registry_complete.py`, `test_layering.py`, and each system's
   floor gates.
2. **Verified** — the hermetic suite is green and every claim in this document maps to a named test
   that exists and runs in it. Gate: `run_all_tests.py`, `test_run_all_tests_classifier.py`.
3. **Behaviourally elite** — the behavioural suite scores **≥95 overall with no category below 90**,
   as a median of five runs against the deployed brain. Gate: `behavioral_suite.py`.
4. **Fast** — turn latency inside the budgets stated per system, with the per-turn tool catalogue
   under its ceiling. Gate: `test_speed.py`, `efficiency_report.py`, `benchmarks.py`.
5. **No overlaps** — no two modules answer the same question independently; the health, memory,
   config and playback overlaps found in Part J are all resolved. Gate: `test_health_agreement.py`,
   `test_layering.py`, `test_localplay_routing.py`, `test_unified_recall.py`.
6. **No bugs of the known shapes** — prose-matching, silent degradation, phantom paths, dead
   scheduled tasks, duplicate delivery. Gate: the G-series standing gates.
7. **Production-proven** — thirty consecutive days unparked with self-detected incidents only.
   Gate: the uptime and incident logs.

---

# Working rules while executing this plan

- **One task at a time, in wave order.** A task is done when its gate passes in a full suite run,
  and the commit says which gate.
- **Nothing goes back into production until Wave 0 is green.** The park switch stays on. Unpark is
  itself a gated action (23.F4).
- **Every finding becomes a task here or in `TODO.md`, never a memory.** If it is not written down
  with a gate, it did not happen.
- **Owner-blocked items are listed as blocked and never silently skipped.** They are collected in
  one place below so a single sitting can clear most of them.
- **Do not add systems.** Fifty is the scope. New capabilities attach to an existing system or get
  a decision record first.

## Everything currently waiting on the owner

| Blocker | Blocks |
|---|---|
| Home Assistant long-lived token | S28 entirely |
| Twilio credentials | S35 calling ladder, S38 voice channel |
| Google Maps key; Fitness API enablement | S27 location detail, S34 |
| Voiceprint re-enrolment (read the script) | S08, and through it S10, S11 fusion |
| Sit for the ArcFace face capture | S09, S11 |
| `routines.json` real content | S21, S17 evening brief |
| Merge direction for laptop ↔ VPS learned state | S30, and S32 replication behind it |
| `.env` → password store; MiniMax + Vercel key rotation | S20.R3, S36.F5 |
| Elevated `Disable-ScheduledTask` for the remaining edge tasks | keeps the park honest |
| First inventory category; first fabrication decision | S47, S49 |
