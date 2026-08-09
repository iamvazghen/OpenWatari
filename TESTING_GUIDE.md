# Afon — Live Production Testing Guide

**What this is.** A single ordered pass over every capability Afon has, tested *live* — real voice,
real keys, real network, real side effects. Not the hermetic suite. `bench/run_all_tests.py` proves
the code is internally consistent; this document proves the **system works in the world**.

**The claim it supports.** If every check here passes on the machines Afon actually runs on, Afon is
production-ready. Nothing else in this repo makes that claim, and the hermetic suite explicitly
cannot: it has passed 118/118 through a day in which three subsystems were silently broken.

**How to use it.** Work top to bottom. Each check has a **Say/Do**, an **Expect**, and a **Fail
means** naming the likely cause. Record the result in the tracking table at the end. Stop and fix
on any FAIL in Sections 0–3; later sections can be triaged.

**Time:** ~3–4 hours for a full pass. Sections 0–4 are the ~45-minute smoke subset.

---

## Read this before you start

Four rules, each of which exists because ignoring it produced a false green in this repo:

1. **A tool that answers is not a tool that worked.** Afon degrades politely. `not_configured()`
   returns a fluent sentence when a key is missing, so "he replied sensibly" proves nothing. For
   every integration check, verify the **side effect at the destination** — the message in Telegram,
   the row in Notion, the event in Calendar.
2. **Check the log, not just the ear.** After each section run
   `python bench/show_errors.py --minutes 15`. A turn can sound perfect and have swallowed an
   exception.
3. **An empty store is not evidence of a broken pipeline.** Several tables here are written only
   when *you* complete an interaction. Read §12 before concluding anything from a zero.
4. **Test the failure, not only the success.** Half the checks below deliberately break something.
   A recovery path that has never been exercised is not a recovery path.

### Preconditions

```bash
uv run python bench/check_config.py          # settings load; prints no secrets
bash scripts/preflight.sh                    # environment invariants (see SOP §3)
uv run python bench/run_all_tests.py         # hermetic gate must be green FIRST
```

Do not start live testing against a red hermetic suite — you will not know which layer failed.

**Known-unconfigured integrations** (as of this writing) — their checks are expected SKIP, not FAIL:
Home Assistant (`AFON_HA_TOKEN`), Twilio (`AFON_TWILIO_AUTH_TOKEN`), Google Maps
(`AFON_GOOGLE_MAPS_API_KEY`), Brave (`AFON_BRAVE_API_KEY`), Firecrawl, Cerebras. Confirm the current
list with the credential audit in SOP §2.3 rather than trusting this paragraph.

---

## Section 0 — Processes are up and singular

| # | Do | Expect | Fail means |
|---|---|---|---|
| 0.1 | `curl -s http://<brain-host>:<port>/healthz` | `ok` | brain down; see SOP §4.1 |
| 0.2 | `curl -sH "Authorization: Bearer $TOKEN" .../metrics` | JSON with `counters`, `latency_ms` | auth token wrong, or brain started before .env loaded |
| 0.3 | On the laptop: list running `pythonw` processes | Exactly **two** — `pc_agent` and `assistant` | see 0.4 |
| 0.4 | Start a *second* edge deliberately | It exits, logging that another holds the role | `shared/singleton.py` guard broken — two edges both hold the mic and you hear Afon twice |
| 0.5 | `uv run python bench/show_errors.py --summary` | No CRITICAL in the last 24h | read the entries before proceeding |
| 0.6 | `Get-ScheduledTask \| ? {$_.TaskName -like '*Afon*' -or $_.TaskName -like '*Watari*'}` | `AfonEdge`, `AfonPcAgent`, `AfonEdgeRefresh`, `AfonEdgeGuard` — all **Ready**, all pointing at paths that exist | see the box below |
| 0.7 | Reboot the laptop, wait 2 min | Edge comes back by itself | autostart is registered but broken — 0.6 |

> **Why 0.4 is not optional.** Duplicate processes are the most common production failure in this
> system and the hardest to diagnose from symptoms — it presents as "Afon answers twice", "the
> speaker gate fights itself", or doubled proactive messages.

> **Why 0.6 is not optional — it was already broken when this guide was written.** On 2026-08-09
> the live tasks were still the pre-rename `WatariPcAgent` and `WatariEdgeRefresh`, both pointing
> into **`C:\Jarvis`, a directory that no longer exists**, while `AfonEdgeGuard` was **Disabled**.
> No edge process was running, nothing would have started one at logon, and the watchdog whose job
> is to notice was off. A Scheduled Task whose executable is missing fails **silently** — there is
> no error anywhere, the edge is simply never there. `scripts/preflight.sh` now asserts this.

---

## Section 1 — Wake word and the ear

| # | Say/Do | Expect | Fail means |
|---|---|---|---|
| 1.1 | Say **"Hey Afon"**, then wait | Listening indicator/pulse; no response to the wake phrase alone | wake model not loaded — check `.wakewords/*.onnx` |
| 1.2 | "Hey Afon, what time is it?" | Answer in **under ~1s**, no network round trip | edge reflex not firing (`edge/reflexes.py`) — see 1.3 |
| 1.3 | Same, with the brain stopped | Still answers | reflexes are supposed to be zero-VPS; if it fails, the reflex gate is routing to the brain |
| 1.4 | Talk *near* Afon without the wake word for 2 min | No response at all | false-fire; retrain or raise the wake threshold |
| 1.5 | Say a phrase similar to the wake word ("hey a phone") | No trigger | threshold too permissive |
| 1.6 | Start speaking **while Afon is talking** | He stops promptly and listens | barge-in broken (`edge/vad_bargein.py`) |
| 1.7 | Connect AirPods mid-session, then say "Hey Afon" | Recovers within ~30s and answers | audio watchdog (`edge/audio_watchdog.py`) — this was the recurring "I say hey afon and nothing comes" bug |
| 1.8 | Disconnect them again, repeat | Recovers again | same |
| 1.9 | Close the laptop lid, wait 10 min, reopen, say "Hey Afon" | Answers | sleep/resume deafness — the wall-clock jump detector |

**Bench support:** `uv run python bench/verify_wakeword.py .wakewords/afon.onnx <clips...>` scores a
model on WAV clips. Use it to quantify 1.4/1.5 rather than guessing.

---

## Section 2 — Voice quality and latency

| # | Say/Do | Expect | Fail means |
|---|---|---|---|
| 2.1 | "Hey Afon, tell me a joke" | Speaks within ~2s; natural prosody | pure-chat fast path not engaging (should carry **zero** tools) |
| 2.2 | Watch the log for `turn latency:` | `stt/brain/tts/total` all present | latency tracer not wired |
| 2.3 | `uv run python bench/voice_live_bench.py` | 10-utterance battery; TTFW recorded per turn | — |
| 2.4 | `uv run python bench/edge_readiness.py` | Injects synthesized audio through the real pipeline; pass per stage | isolates edge software from mic hardware |
| 2.5 | Ask something emotional ("I had a rough day") | Warmer delivery, not just warmer words | affect→TTS is text-only today; **this is a known open item, judge by ear** |
| 2.6 | Pull the network, say "Hey Afon, what time is it?" | Local answer | — |
| 2.7 | `uv run python bench/voice_offline_check.py` | Fully-local turn viable (Whisper + Piper) | local models missing |

> **2.5 is the one check here with no automated backstop.** Prosody ships with starting values that
> have never been calibrated by ear. Record what you hear; do not mark it pass by default.

---

## Section 3 — Speaker identity and privacy gates

| # | Say/Do | Expect | Fail means |
|---|---|---|---|
| 3.1 | Owner says "Hey Afon, who am I?" | Recognises the owner | voiceprint stale — re-enroll with `bench/enroll_voice.py` |
| 3.2 | **Someone else** says the same | Does *not* claim they are the owner | `speaker_threshold` too permissive |
| 3.3 | Stranger asks for something confirm-gated (e.g. "send an email") | Refused or escalated, not executed | the identity gate is decorative |
| 3.4 | Owner: "learn my face" (seated, lit) | Enrolls; refs land in `~/.afon/faces/owner.npy` | camera path — on a VPS brain this must route through PC_LINK |
| 3.5 | "Look around" / "who's here" | Describes the room; owner matched | camera tool running headless on the VPS instead of the laptop |

> Face refs are **machine-local**. `scripts/preflight.sh` asserts they exist at the path the code
> reads — a rename once left them at the old path, where `_owner_refs()` returned `None` and every
> owner check answered "not enrolled" *without erroring*. That is the failure mode to watch for:
> a correct-looking answer.

---

## Section 4 — Core tools, one at a time

Say each as a natural sentence, wake word first. **Verify the value, not the fluency.**

### 4.1 Time, date, conversion, computation
| Say | Verify |
|---|---|
| "What time is it?" | matches your clock, correct timezone |
| "What's today's date?" | correct — date formatting has broken three times |
| "Convert 20 kilometres to miles" | ≈12.43 |
| "What's 17% of 4,200?" | 714 (`compute`) |

### 4.2 Weather — including the day you asked about
| Say | Verify |
|---|---|
| "What's the weather?" | current conditions, your city |
| "What's the weather **tomorrow**?" | **tomorrow's** forecast, not today's |
| "What's the weather **this week**?" | a range |
| Ask "today" then immediately "tomorrow" | **different answers** |

> The last row is the real check. A 900s response cache keyed only on location served today's
> sentence for a "tomorrow" question — a bug invisible to any test that asked one mode.

### 4.3 Knowledge and web
| Say | Verify |
|---|---|
| "What's the price of Bitcoin?" | plausible, current |
| "How's Apple stock doing?" | plausible |
| "What's the euro to dollar rate?" | plausible |
| "Look up the Roman Empire on Wikipedia" | real summary |
| "Define petrichor" | correct |
| "What's in the news?" | current headlines |
| "Search the web for X" | real results, cited |
| "Scrape <url> and summarise" | content actually from that page |

### 4.4 Memory (L1–L5b)
| Say | Verify |
|---|---|
| "Remember that I take my coffee black" | confirms |
| — restart the brain — | (state must survive) |
| "How do I take my coffee?" | recalls it |
| "What did we talk about earlier?" | journal (L2) |
| "Forget that I take my coffee black" | **confirm-gated**, then gone |
| "What's related to my rabbit farm?" | graph recall (L5b) |

### 4.5 Vault
"Search my vault for X" · "Read the note about Y" · "Write a note saying Z" (**confirm-gated** — verify
the file on the **VPS**, not the local replica; see SOP §7.3).

### 4.6 Tasks and reminders
Add / list / complete / delete a task. Set a reminder 2 minutes out — **wait for it to fire**. A
reminder that is stored but never speaks is the failure that matters.

### 4.7 Contacts
"Save Ann's number as …" then "What's Ann's number?" — `save_contact` was missing entirely until
recently, so contacts appeared to work while nothing persisted.

### 4.8 System and PC control
`open_app`, `open_url`, `file_op` (create/list), `run_powershell`, `media_pause`,
`switch_audio_output`, `list_audio_outputs`. From a **VPS brain** these must execute on the
**laptop** via PC_LINK — verify the effect is on the laptop, not the server.

### 4.9 Music
"Play <song>" · "What's playing?" · "Stop the music" · "Play it in the music room" (needs HA).

### 4.10 Screen and camera
`describe_screen`, `screenshot_screen`, `read_screen_text`, `look_around`. All must run where the
screen/camera physically is.

---

## Section 5 — Integrations, end to end

For every row: **check the destination app**, not Afon's reply.

| Integration | Say | Verify at the destination |
|---|---|---|
| Telegram | "Send myself a Telegram saying test 1" | message present in the chat |
| Telegram read | "Any new Telegram messages?" | matches the app |
| Notion | "Add a Notion task: test 2" | row exists in the database |
| Notion read | "What are my Notion tasks?" | matches Notion |
| Gmail | "Read my latest email" | matches the inbox |
| Gmail send | "Draft an email to me saying test 3" | draft/message exists |
| Calendar | "What's on my calendar today?" | matches Google Calendar |
| Calendar write | "Create an event tomorrow at 3pm" | event exists |
| Push | "Send a test push" | phone receives it |
| GitHub | "Create a GitHub issue titled test 4" | issue exists |
| Composio | "Find a tool for Slack" then run a read | real result |
| Home Assistant | "Turn on the lights" | device changes *(SKIP if unconfigured)* |
| Twilio | "Call/SMS me" | phone rings *(SKIP if unconfigured)* |
| Maps | "How long to drive to X?" | live traffic *(falls back to OSRM without a key)* |
| Fleet | "Ask ispir to …" | delegation reaches the fleet |

---

## Section 6 — Multi-step chains (the real test of the agent loop)

Single tools are the easy half. These force planning, sequencing, and multi-clause completion —
where the iteration budget, clause routing, and forced-tool logic actually get exercised.

| # | Say | Expect |
|---|---|---|
| 6.1 | "What's the weather tomorrow, **and** add a task to buy an umbrella if it rains" | both clauses satisfied |
| 6.2 | "Check my email **and** tell me if anything needs a reply today" | read → reason → summarise |
| 6.3 | "Look up X on the web, summarise it, **and remember** the summary" | 3 tools; the memory write actually lands |
| 6.4 | "What's on my calendar tomorrow, and send me a Telegram with it" | read → send; verify in Telegram |
| 6.5 | "Find my Notion tasks due today and turn the top one into a plan" | cross-tool |
| 6.6 | "Every weekday at 8am tell me the weather" (`if_then` / macro) | rule created; **verify the operator is honoured** |
| 6.7 | Define a macro, list it, run it, delete it | full lifecycle; run + delete are confirm-gated |
| 6.8 | "Plan my day" | uses real calendar + tasks, not a generic template |

> **6.3 is the canonical regression.** A completion loop that ran out of iterations would satisfy
> everything *except* the last clause — usually "…and remember it", the part you cared about most.
> Explicitly confirm the memory write, don't accept "I've noted that".

---

## Section 7 — Confirm gate and destructive actions

32 tools are confirm-gated (list in SOP §6.2). Test the *gate*, not the tool.

| # | Say | Expect |
|---|---|---|
| 7.1 | "Delete the file X" | asks first |
| 7.2 | Reply "no" | not deleted |
| 7.3 | Repeat, reply "yes" | deleted |
| 7.4 | Ask for a gated action, then say something **unrelated** | pending confirm is **dropped**, not applied to the new request |
| 7.5 | "Send an email to <someone>" | asks first, names recipient and content |
| 7.6 | "Run this PowerShell: …" | asks first |
| 7.7 | "Delete all my tasks" | asks; scope stated |
| 7.8 | `undo_last` after a write | reverses it |
| 7.9 | "What have you done recently?" | `list_recent_actions` |

> **7.4 is the security-relevant one.** A "yes" must immediately follow the ask. If an unrelated
> utterance can be answered *and* silently grant a pending confirmation, the gate is bypassable.

---

## Section 8 — Protocols

Eight protocols, three of which produce a report delivered to you.

| Protocol | Say | Expect |
|---|---|---|
| `ping` | "Run the ping protocol" | phone push arrives |
| `diagnostics` | "Run diagnostics" | **report reaches you in Telegram**, content inline |
| `backup` | "Back up your memory" | archive written |
| `checkpoint` | "Run a checkpoint" | `.zip` delivered as an attachment |
| `auditpack` | "Archive the audit logs" | `.zip` delivered |
| `goodnight` | "Goodnight protocol" | **stops Afon** — run last |
| `phoenix` | "Phoenix protocol" | restarts Afon |
| `ragnarok` | "Ragnarok protocol" | restarts the laptop — **destructive, schedule it** |

All require the protocol password and are confirm-gated.

> **Two traps.** (a) `goodnight`/`phoenix`/`ragnarok` act on the **laptop**; when the brain moved to
> the VPS all three silently became no-ops while still reporting success — verify the machine
> actually did the thing. (b) Report delivery has a freshness guard: run each report protocol
> **twice in a row** and confirm the second delivers the *new* report, not the first one again, and
> that a run producing nothing delivers **nothing** rather than last week's file.

---

## Section 9 — Error tracking and observability

| # | Do | Expect |
|---|---|---|
| 9.1 | `python bench/show_errors.py --minutes 60` | today's real entries |
| 9.2 | `--summary` | counts, worst first |
| 9.3 | `--subsystem edge` | filters |
| 9.4 | `--turn <id>` | one turn end to end across edge + brain |
| 9.5 | **Force an error** — ask for a tool whose key is removed | appears in the journal within seconds |
| 9.6 | Force an edge error with the brain unreachable | spooled, then **replayed on reconnect** |
| 9.7 | `/metrics` | `tool_calls`, `tool_errors`, `turns` climbing |
| 9.8 | `cat ~/.afon/tool_usage.json` | per-tool counts, surviving a restart |
| 9.9 | Check for a **swallowed** exception | `subsystem=swallowed/*` entries reviewed |

> 9.5 and 9.6 are the checks that matter. Error tracking that has only ever seen zero errors is
> untested. The offline→reconnect replay path (9.6) is what keeps a crash during a network outage
> from vanishing.

---

## Section 10 — Recoverability (deliberately break things)

| # | Do | Expect |
|---|---|---|
| 10.1 | `taskkill` the edge process | `AfonEdgeGuard` restarts it within ~30 min |
| 10.2 | Stop the edge **deliberately** (documented way) | guard does **not** fight you |
| 10.3 | Kill the brain | systemd restarts it; sessions recover |
| 10.4 | Revoke/blank the primary LLM key | fails over to the next provider in the chain, **out loud or in the log** |
| 10.5 | Break the primary TTS | falls back to local Piper — and does **not** demote a healthy provider on a transient idle-close |
| 10.6 | Unplug the network mid-turn | degrades, no hang; recovers |
| 10.7 | Corrupt `~/.afon/tool_usage.json` | starts fresh, does not crash the brain |
| 10.8 | Reboot the laptop | edge autostarts; `pc_agent` elevated |
| 10.9 | Reboot the VPS | brain autostarts (systemd + linger) |
| 10.10 | Fill the disk to >90% | degrades gracefully; alerts |

> **10.5 has bitten twice.** A transient Deepgram idle-close wrongly demoted a *healthy* ElevenLabs
> to Piper, and a teardown race made the failover flap. Confirm it fails over **and stays put**.

---

## Section 11 — Proactivity (patience is the test)

Proactivity cannot be rushed; that is the point. Verify **restraint** as carefully as action.

| # | Do | Expect |
|---|---|---|
| 11.1 | Use Afon normally for a day | at most `proactive_daily_budget` interjections |
| 11.2 | During quiet hours | silence, unless urgency ≥ override — then **push, never spoken** |
| 11.3 | While in a meeting / deep work | routine nudges **held**, not spoken |
| 11.4 | Dismiss a kind 3× | that kind backs off (learned penalty) |
| 11.5 | Act on a kind | it eases back |
| 11.6 | Have a calendar event in 15 min | prep nudge |
| 11.7 | After an autonomous action | **an action report** — what he did and why |
| 11.8 | `cat ~/.afon/proactive_state.json` | `shown` **and** `held_*` counters per kind |

> **11.8 is how you tell "correctly restrained" from "never generated".** Before the `held_*`
> counters existed, both looked like zero — which is how five companion capabilities were read as
> dormant for weeks. A kind with `held_threshold: 47` is working; a kind with no counters at all
> never produced a signal, and *that* is a broken source.

---

## Section 12 — Stores that only you can fill

Read this before concluding a store is broken.

| Store | Written when | An empty table means |
|---|---|---|
| coaching tables | **you finish a quiz/review** | you haven't done one — not a broken pipeline |
| `patterns.jsonl` | presence observations accumulate | fine early |
| `relationship.json` | sensitivities/jokes noted | fine early |
| `tool_usage.json` | any tool call | if empty after real use, the recorder is broken |
| `proactive_state.json` | any gate fires | see 11.8 |

> This distinction has been misread twice in this project: an empty table was reported as "the
> pipeline fails upstream" when the table is written by a *user* completing something. **Check what
> writes a table before inferring anything from it being empty.**

---

## Section 13 — Multilingual and edge cases

| # | Say | Expect |
|---|---|---|
| 13.1 | Ask in German, French, Spanish, Russian, Armenian | understands; replies per policy |
| 13.2 | `uv run python bench/verify_multilingual.py` | 6-language probe |
| 13.3 | Mumble / speak very quietly | asks you to repeat, doesn't invent |
| 13.4 | Ask something genuinely unanswerable | says so — **does not fabricate** |
| 13.5 | Ask for a tool that isn't configured | says it's not set up, names what's missing |
| 13.6 | Interrupt mid-sentence, change topic | follows |
| 13.7 | Very long rambling request | handles or asks to narrow |

> **13.4 is the anti-fabrication check.** In a sibling system a security agent invented a CVE. If
> Afon ever fills a gap with plausible fiction, stop and treat it as a P1.

---

## Section 14 — Devices

| Device | Check |
|---|---|
| Laptop edge | full loop (Sections 1–2) |
| iPhone (`/iphone`) | mic works over HTTPS; **`token_missing` is the usual cause of "it won't open"** |
| HUD (`/hud`) | live state |
| Termux / edge-lite | connects, basic turn |
| Glasses (MentraOS) | **unfinished scaffold — expected FAIL** |

---

## Section 15 — Self-improvement

| # | Do | Expect |
|---|---|---|
| 15.1 | "Review your recent conversations and learn" | facts into L1, relations into L5b |
| 15.2 | Check the digest after learning | new facts surface **without a restart** |
| 15.3 | "Improve your code" (coding group) | proposes a real diff; confirm-gated |
| 15.4 | `run_tests` via the coding group | actually runs the suite |
| 15.5 | Ask about something learned yesterday | recalled |

---

## Tracking table

Copy per run. `P` pass · `F` fail · `S` skip (unconfigured) · `N` not run.

| § | Area | Result | Notes |
|---|---|---|---|
| 0 | Processes | | |
| 1 | Wake word | | |
| 2 | Voice + latency | | |
| 3 | Identity | | |
| 4 | Core tools | | |
| 5 | Integrations | | |
| 6 | Multi-step chains | | |
| 7 | Confirm gate | | |
| 8 | Protocols | | |
| 9 | Error tracking | | |
| 10 | Recoverability | | |
| 11 | Proactivity | | |
| 12 | Stores | | |
| 13 | Multilingual/edges | | |
| 14 | Devices | | |
| 15 | Self-improvement | | |

**Production-ready** = Sections 0–11 all P or justified S, **and** the hermetic suite green, **and**
`bench/efficiency_report.py` within targets.

---

## Appendix — the live bench scripts

| Script | Answers |
|---|---|
| `bench/edge_readiness.py` | does the edge software path work, mic hardware aside |
| `bench/voice_live_bench.py` | TTFW / turn latency over 10 scripted utterances |
| `bench/voice_offline_check.py` | is a fully-local turn possible |
| `bench/verify_remote_brain.py` | does the VPS brain stream back over the tailnet |
| `bench/verify_wakeword.py` | does the wake model fire and stay quiet |
| `bench/demo_live_actions.py` | Afon performs every integration himself |
| `bench/behavioral_suite.py` | judges *how well*, not just that it works |
| `bench/efficiency_report.py` | hot paths vs targets |
| `bench/verify_multilingual.py` | 6-language comprehension |
| `bench/enroll_voice.py` | re-enroll the voiceprint |
| `bench/show_errors.py` | what broke |
| `bench/tool_usage_report.py` | which tools are actually used |
