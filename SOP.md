# Afon — Standard Operating Procedure

**Who this is for.** Anyone who clones this repo and needs to run Afon, and the owner who needs to
operate, diagnose and improve him over time. It answers: how do I start it, how do I know it's
working, where do I look when it isn't, what am I allowed to do to each subsystem, and how do I make
it better without breaking it.

**Companion document:** [`TESTING_GUIDE.md`](TESTING_GUIDE.md) — the live end-to-end test pass.
This document is *operations*; that one is *verification*.

**Standing principle.** Afon degrades politely by design: a missing key produces a fluent sentence,
not an error. That is good product behaviour and terrible diagnostic behaviour. **Never conclude a
subsystem works because Afon answered.** Check the log, the store, or the destination app.

---

## 1. Architecture in one page

```
LAPTOP (edge)                          VPS (brain, 24/7)
┌────────────────────────────┐         ┌──────────────────────────────┐
│ mic → wake word (openWW)   │         │ AfonAgent                    │
│  → VAD (Silero)            │  WS     │  ├ intent router / narrowing │
│  → STT (Deepgram/Whisper)  │◄───────►│  ├ tool belt (136 tools)     │
│  → speaker verification    │         │  ├ memory L1–L5b             │
│  → affect                  │         │  ├ proactive engine          │
│  → reflex gate (local Qs)  │         │  └ LLM chain + failover      │
│  ← TTS (ElevenLabs/Piper)  │         │ HTTP: /healthz /metrics      │
│ pc_agent (elevated)        │◄────────┤  /talk /hud.json /iphone     │
└────────────────────────────┘ PC_LINK └──────────────────────────────┘
```

**The rule that explains most confusion:** the brain runs on the VPS and has **no microphone, no
camera, no screen, and no speakers**. Anything physical — camera, screenshots, audio devices, app
launching, shutdown — must route back to the laptop over PC_LINK. When the brain moved to the VPS,
three protocols silently became no-ops *while still reporting success* because nobody had drawn this
line.

**Layout:** `src/afon/edge/` voice pipeline · `src/afon/brain/` agent, tools, memory, proactivity ·
`src/afon/protocols/` recovery scripts · `src/afon/shared/` errors, singleton, protocol ·
`bench/` tests + live benches · `scripts/` ops · `deploy/` install units.

---

## 2. First-time setup

### 2.1 Install

```bash
git clone https://github.com/iamvazghen/OpenAfon openafon && cd openafon
uv sync --extra edge --extra cloud-voice --extra brain --extra channels --extra identity --extra dev
```

`uv sync` **prunes extras you don't list** — install the full set in one command. For a fully local
stack, swap `cloud-voice` for `local-voice`.

### 2.2 Configure

```bash
uv run afon-setup                    # interactive wizard, writes .env
uv run python bench/check_config.py  # confirms settings load; prints no secrets
```

Every setting is `AFON_`-prefixed. **This prefix is load-bearing:** `config.py` declares
`env_prefix="AFON_"` and pydantic-settings uses `extra="ignore"`, so a `.env` full of vars under an
old prefix means the brain reads **zero configuration** — and starts healthy, passes its health
check, and behaves like a fresh install. `scripts/preflight.sh` exists largely to catch this.

### 2.3 Credential audit

```bash
uv run python bench/check_config.py
```

Tools whose credentials are absent report "not configured" rather than failing. To see the real
state at a glance, check which of these are set: ElevenLabs, Deepgram, Telegram, Notion, Google
OAuth, Home Assistant, Twilio, Google Maps, Composio, Tavily, Brave, Firecrawl, Jina, GitHub,
Browserbase, and the LLM providers (MiniMax, Groq, Cerebras, Vercel AI Gateway).

**Never commit secrets.** `bench/check_public_clean.py` scans every tracked file for keys, private
hosts and personal email; it runs in the gate and must stay green.

### 2.4 One-time logins

```bash
uv run python bench/telegram_login.py     # Telegram session
uv run python bench/google_login.py       # Google OAuth (Calendar + Gmail)
uv run python bench/enroll_voice.py       # owner voiceprint
# then, in conversation: "learn my face"  # owner face refs
```

---

## 3. Verifying an installation

Run these three, in order. They answer different questions and none substitutes for another.

```bash
bash scripts/preflight.sh              # 1. is this MACHINE set up correctly?
uv run python bench/run_all_tests.py   # 2. is the CODE internally consistent?
uv run python bench/efficiency_report.py  # 3. is it FAST enough?
```

**`preflight.sh`** checks environment invariants no unit test can see: the env prefix matches what
the code reads, state directories are where the code looks, enrolled biometrics load, shell scripts
and hooks are LF-only, Windows Scheduled Tasks point at code that still exists, the test registry is
complete, and how stale the code graph is. Add
`--remote` to also check the VPS (needs `AFON_VPS` in `scripts/deploy_vps.env`) — including the
prefix check on the target, which is the single most valuable assertion in the file.

**`run_all_tests.py`** is the single gate: ~130 hermetic tests, no network or keys required.
Network/fleet tests report SKIP (not FAIL) when unreachable, so an offline run still passes.

> **Capture the full output to a file.** The per-test result lines are inline, not in the summary —
> piping through `tail`/`Select-Object -Last N` will show you `129 passed, 1 failed` without telling
> you *which*.

**`efficiency_report.py`** grades hot paths against targets. Informational; never fails the build.

---

## 4. Running Afon

### 4.1 Brain (VPS, 24/7)

```bash
uv run python -m afon.brain.server                    # foreground, for debugging
systemctl --user status  afon-brain                   # normal operation
systemctl --user restart afon-brain
journalctl --user -u afon-brain -f                    # live logs
curl -s http://<brain-host>:8766/healthz              # → ok
```

**Two ports, and using the wrong one looks like an outage.** `brain_port` **8765** is
WebSocket-only (`/voice` for devices, `/control` for `pc_agent`) — an HTTP request there returns
**426 Upgrade Required**, which reads like a broken brain but means the opposite. The HTTP surface
(`/healthz`, `/metrics`, `/hud`, `/iphone`, `/talk`) is on `client_http_port` **8766**.

Uses **systemd `--user` with linger** so it survives logout. A daily refresh timer restarts it at
01:00 UTC — which is why anything that must accumulate across days has to be on disk, not in memory.

### 4.2 Edge (laptop)

```bash
uv run python -m afon.edge.assistant     # voice loop, foreground
uv run python -m afon.edge.pc_agent      # PC executor (needs elevation)
```

Normally both run as Scheduled Tasks (`AfonPcAgent` elevated, `AfonEdge`, plus `AfonEdgeRefresh` and
`AfonEdgeGuard`), installed by:

```powershell
.\scripts\install_edge_autostart.ps1     # AfonEdge + AfonPcAgent
.\scripts\install_edge_refresh.ps1       # AfonEdgeRefresh (daily restart)
.\scripts\install_edge_guard.ps1         # AfonEdgeGuard (30-min watchdog)
```

```powershell
Start-ScheduledTask AfonEdgeRefresh      # restart the edge WITHOUT a UAC prompt
.\scripts\restart_edge.ps1               # clean restart, force-kills stale instances
```

**`AfonEdgeGuard`** runs every 30 minutes and restarts the edge if it died — unless you stopped it
deliberately. Use the documented stop path so the guard doesn't fight you.

> ### ⚠️ Verify the tasks — this was broken in production and nothing said so
>
> **On 2026-08-09 this machine was in exactly that state.** The live tasks were still the pre-rename
> `JarvisEdge`, `WatariPcAgent` and `WatariEdgeRefresh`, all pointing into **`C:\Jarvis`, a directory
> that no longer exists**, while `AfonEdgeGuard` was **Disabled**. No edge process was running,
> nothing would have started one at logon, and the watchdog whose job is to notice was switched off.
> Every command in this section would have appeared to work while changing nothing.
>
> The rename moves the *code*. It does not move Scheduled Tasks, systemd units, or anything else
> holding an absolute path — and **a task whose executable is missing fails silently**.
>
> ```powershell
> Get-ScheduledTask | Where-Object { $_.TaskName -match 'Afon|Watari|Jarvis' } |
>   Select-Object TaskName, State
> (Get-ScheduledTask -TaskName <name>).Actions      # confirm the path still exists
> ```
>
> Match **all three** name generations. Filtering on `Afon|Watari` alone misses `JarvisEdge` — the
> task that runs the voice assistant itself.
>
> **Recovery (this is the script for it):**
>
> ```powershell
> powershell -ExecutionPolicy Bypass -File C:\Afon\scripts\finish_afon_rename.ps1   # approve UAC
> ```
>
> It **renames** rather than recreates: each task's XML is exported and its paths rewritten, so
> triggers, principal and RunLevel survive byte-for-byte — which matters because `AfonPcAgent` must
> stay `RunLevel Highest`. Do **not** reach for `install_edge_autostart.ps1` here; it only modifies
> tasks that already exist and silently skips missing ones.
>
> Verified working after the fix: all four tasks correct, `pc_agent` connected to the VPS brain, wake
> word firing, utterances routed. `scripts/preflight.sh` now asserts this permanently (§3).

### 4.3 The singleton rule

One process per role, enforced by `shared/singleton.py`. Two edges both hold the mic and you hear
Afon twice; two brains double-tick proactivity. If behaviour is doubled, **count processes first**.

---

## 5. Observability — where to look

### 5.1 Error journal (start here)

```bash
uv run python bench/show_errors.py                  # last hour
uv run python bench/show_errors.py --minutes 1440   # last day
uv run python bench/show_errors.py --summary        # counts, worst first
uv run python bench/show_errors.py --subsystem edge
uv run python bench/show_errors.py --turn a1b2c3d4  # one turn, edge + brain
```

Backed by `~/.afon/errors.jsonl`. Runs identically on laptop and VPS. Edge errors logged while the
brain link is down are **spooled and replayed on reconnect**, so an outage does not erase its own
evidence.

**Watch for `subsystem=swallowed/*`** — a caught exception that never surfaced. A quiet journal with
swallowed entries is worse than a noisy one.

### 5.2 Metrics

```bash
curl -sH "Authorization: Bearer $TOKEN" http://<brain-host>:8766/metrics
```

Counters (`turns`, `tool_calls`, `tool_errors`, `tool.<name>`) and rolling latency summaries.
**In-memory — reset by the nightly restart.** For anything that must accumulate, see 5.3.

### 5.3 Durable stores

| File (`~/.afon/`) | Holds | Read with |
|---|---|---|
| `errors.jsonl` | structured error journal | `bench/show_errors.py` |
| `tool_usage.json` | per-tool call counts, across restarts | `bench/tool_usage_report.py` |
| `proactive_state.json` | budget, suppression, `shown` + `held_*` per kind | read directly |
| `patterns.jsonl` | presence/activity observations | — |
| `relationship.json` | sensitivities, running jokes | — |
| `approvals.json` | pending approvals | `list_approvals` |
| `faces/owner.npy` | owner face refs | `preflight.sh` asserts presence |

### 5.4 HUD

`/hud` (live state) and `/hud.json`. Auth-gated. `/iphone` is the phone client — **`token_missing`
is the usual cause of "the dashboard won't open"**, not a server fault.

### 5.5 Turn latency

Each turn logs `turn latency: stt/brain/tts/total`. This is the number to tune against; total
matters less than **time-to-first-sound**, which is what the owner perceives.

---

## 6. Subsystems: what you may do to each

### 6.1 Tool belt — 136 tools

**54 core** (advertised every turn) + **lazy groups** activated by phrase:

`coding` `office` `home` `docs` `apps` `channels` `macros` `screen` `camera` `undo` `graph`
`activity` `coaching` `objectives` `approvals` `relationship` `phone` `places` `compute` `vitals`
`audioout`

```bash
uv run python bench/dump_tools.py            # full registry
uv run python bench/tool_usage_report.py     # what is actually used
```

**Adding a tool:** add the schema + handler in `src/afon/brain/tools/<module>.py`, put it in a lazy
group unless it is genuinely needed on most turns, and add a hermetic test. Then register that test
in `bench/run_all_tests.py:TESTS` — `bench/test_registry_complete.py` will fail the gate if you
forget, and will also catch a test function that is defined but never invoked.

**Group triggers are a token cost, not just a routing choice.** An over-broad trigger silently loads
its whole group onto unrelated turns: `"check my"` on the coaching group put five German-quiz tools
on "check my email", "check my calendar" and "check my tasks" — three of the most common utterances,
~565 wasted prompt tokens each, nothing visibly wrong.

### 6.2 Confirm gate — 32 tools

`browser` `complete_objective` `composio_run_tool` `create_event` `create_github_issue`
`delete_macro` `delete_task` `drop_objective` `file_op`(delete only) `forget` `git_commit`
`git_push` `git_revert` `ha_call` `invoke_skill` `notion_append` `notion_comment`
`notion_complete_task` `notion_create_page` `notion_delete_task` `notion_update_task` `place_call`
`process_op` `run_macro` `run_powershell` `run_protocol` `send_email` `send_push` `send_telegram`
`update_task` `write_source` `write_vault`

Reads are never gated. Composio slugs are classified by verb; **unknown verbs are gated**. A "yes"
must *immediately* follow the ask — any other utterance supersedes the pending confirmation.

**You may:** add tools to the tier. **Be careful removing any** — the gate is the last line before a
side effect in someone else's inbox.

### 6.3 Memory — L1…L5b

L1 learned facts · L2 journal · L3 vault search · L5 semantic re-rank (Jina) · L5b entity graph.
Default recall fuses L1, L2, L3, L5, L5b; the **per-turn** path is L1+L2 only, ~10ms, and that is
why turns stay fast. **Do not add a layer to the per-turn path** without measuring it.

```bash
uv run python bench/profile_memory_recall.py
```

### 6.4 Proactive engine

Gates in order: budget → behavioural mode → threshold (per kind, learned) → repeat-suppression →
quiet hours → busy context → emit. Every gate that holds a signal now increments a `held_*` counter
per kind in `proactive_state.json`.

**Tuning:** `proactive_daily_budget`, `proactive_quiet_hours`, `proactive_relevance_threshold`,
`proactive_repeat_suppress_minutes`, `proactive_quiet_override_urgency`,
`proactive_context_override_urgency`. Raise thresholds to quieten a kind; the engine also learns
from dismissals on its own.

**Diagnosing "capability X never fires":** read the `held_*` counters *before* touching thresholds. A
kind with `held_threshold: 47` is generating and being restrained (tune the threshold); a kind with
**no counters at all** never produced a signal (fix the source). These two look identical from the
outside and were confused for weeks.

### 6.5 Protocols

`goodnight` `phoenix` `ragnarok` (act on the **laptop**) · `backup` `ping` `diagnostics` `auditpack`
`checkpoint` (brain-side). The last three deliver a report to you.

All are password-protected and confirm-gated. `drill=True` exercises one without effect.
Report delivery has a freshness guard so a failed run cannot deliver last week's file as if it
were new.

### 6.6 LLM chain

Ordered failover across providers; a permanent error benches a provider for 6h. `bench/llm_bench.py`
and `bench/pick_model.py` compare candidates. **Prefer the latest, most capable Claude models when
adding a provider.**

### 6.7 Voice

STT Deepgram (cloud) / Whisper (local) · TTS ElevenLabs (cloud) / Piper / Kokoro (local). Health
tracking demotes an unhealthy cloud provider to local with a cooldown. **A transient idle-close must
not demote a healthy provider** — that regression has shipped twice.

`speaker_threshold` governs owner-vs-stranger. Raising it rejects strangers harder and rejects *you*
more often; tune from real recordings, not intuition.

---

## 7. Routine operations

### 7.1 Deploy to the VPS

```bash
bash scripts/preflight.sh --remote      # ALWAYS first
bash scripts/deploy_vps.sh              # tar src/afon + skills → VPS, restart the unit
bash scripts/verify_vps_sync.sh
```

**Known gap:** the deploy never deletes. A file removed locally lingers on the VPS. Orphans are
detected but not yet removed automatically — check after a rename or a file deletion.

### 7.2 Code graph

```bash
graphify query "<question>"        # ask before reading source
graphify explain "<symbol>"
graphify path "<A>" "<B>"
python scripts/graph_fresh.py      # is the graph behind the working tree?
```

Rebuilt automatically by `scripts/githooks/post-commit`, which logs to
`graphify-out/.last-update.log`. `bench/` is excluded via `.graphifyignore`, so the graph describes
the system rather than its scaffolding — the trade is that it can no longer answer "which test
covers X" (use the `bench/test_<topic>.py` naming instead).

**If the graph stops updating:** read that log. `graphify update` **refuses to shrink** a graph
without `--force`, which is a good guard against a half-scanned corpus — but it means an intentional
exclusion change needs one manual `graphify update . --force` to set the new baseline.

### 7.3 Vault writes

The canonical Obsidian vault lives **on the VPS**. The local copy is a one-way replica that gets
overwritten. **Write to the VPS or the note is lost.** If the VPS is unreachable, say so rather than
leaving a note that only looks saved.

### 7.4 Committing

`core.autocrlf=true` is set on this machine. `.gitattributes` pins `*.sh` and `scripts/githooks/*`
to LF — without it, checkout rewrites them to CRLF and they die under any non-Git-Bash invocation,
and a broken hook fails **silently**. Do not remove those rules.

---

## 8. Diagnosing — symptom to cause

| Symptom | Look at | Usual cause |
|---|---|---|
| "Hey Afon" does nothing | edge process; `audio_watchdog` | stale mic stream after a device change (AirPods) |
| Deaf after sleep/lid | edge log | resume detection; watchdog restart |
| Answers twice | process count | duplicate edge — singleton |
| Slow first word | `turn latency:`, prompt size | un-narrowed turn advertising ~80 tools |
| "Not configured" for a working tool | `check_config.py`, env prefix | `.env` under an old prefix → **zero** settings loaded |
| Owner not recognised | `preflight.sh` | face refs at a legacy path — answers "not enrolled" *without erroring* |
| Protocol reports success, nothing happens | which machine ran it | brain-side protocol trying to act on hardware |
| Report never arrives | `show_errors.py` | freshness guard; or Telegram unconfigured |
| Proactive capability silent | `proactive_state.json` `held_*` | restrained vs never generated — §6.4 |
| Dashboard won't open on iPhone | client console | `token_missing` |
| Graph answers about deleted code | `.last-update.log` | rebuild refused (shrink guard) |
| Test green alone, red in the suite | **believe the suite** | shared state, real hardware, or a genuine product bug |

> That last row is not a joke. Twice a "flaky" test was correct: once a hermetic test depended on
> the physical webcam, and once a freshness check failed ~10% of the time because a file written
> *after* a `time.time()` reading can carry an **earlier** mtime — which in production meant a
> brand-new report was judged stale and silently never delivered.

---

## 9. Improving Afon over time

### 9.1 The loop

1. **Measure** — `efficiency_report.py`, `turn latency:`, `tool_usage_report.py`, `show_errors.py --summary`.
2. **Find the real cost** — usually not where it feels like it is. Building the tool catalogue costs
   <1 ms; the same catalogue costs **~11,500 prompt tokens** on an un-narrowed turn. Optimising the
   CPU would have been a week wasted.
3. **Change one thing**, with a test that **fails before the fix**.
4. **Verify**, then run the whole gate.
5. **Record why** in `TODO.md` — including approaches you rejected and the measurement that killed
   them, so nobody re-derives a dead end.

### 9.2 Rules that have earned their place

- **Verify before optimising.** Three optimisations in this repo were declined on measurement:
  SQLite pooling (0.687 ms/call), a catalogue serialisation cache (0.66–0.89 ms), and prose trimming
  (unverifiable hermetically).
- **A new check must fail before you trust it.** Break the thing deliberately, watch the check go
  red, then restore. Checks written after a fix have passed while asserting nothing — including one
  where five of six assertions were inverted and the suite reported 6/6.
- **Read what writes a store before inferring from it being empty.**
- **A "VERIFIED" tag is not evidence.** Two items in `TODO.md` carried it and were wrong.
- **Verify a hook by running the hook**, not its body in your own shell — different environment,
  different output handling, different outcome.
- **A check that can never go green gets ignored** exactly like a false green.

### 9.3 Adding a capability

Tool → hermetic test → register in `TESTS` → add a live check to `TESTING_GUIDE.md` → decide whether
it belongs in a lazy group (default: yes) → run the gate → commit (the hook rebuilds the graph).

### 9.4 Backlog

`TODO.md` is the single backlog: done items keep their reasoning, open items state what blocks them.
Items blocked on the owner (credentials, a live listen-check, a hardware purchase) are marked as
such — don't burn time on them.

---

## 10. Safety and privacy

- Secrets in `.env` only, never committed; `check_public_clean.py` enforces this in the gate.
- Repo is **private**. Changes are **not pushed by default** — pushing requires an explicit ask.
- Confirm gate before any action with an external side effect (§6.2).
- Camera/mic capture is gated on owner presence; face and voice biometrics are **machine-local** and
  never leave the device.
- Audit values are scrubbed; a system-delete guard blocks the destructive edges.
- Protocol passwords are required for every protocol, including the non-destructive ones.

---

## 11. Quick reference

```bash
# health
bash scripts/preflight.sh
uv run python bench/run_all_tests.py
curl -s http://<brain-host>:8766/healthz

# what broke
uv run python bench/show_errors.py --summary
uv run python bench/show_errors.py --turn <id>

# what's it doing
curl -sH "Authorization: Bearer $TOKEN" http://<brain-host>:8766/metrics
uv run python bench/tool_usage_report.py
cat ~/.afon/proactive_state.json

# run
uv run python -m afon.brain.server
uv run python -m afon.edge.assistant
Start-ScheduledTask AfonEdgeRefresh

# deploy
bash scripts/preflight.sh --remote && bash scripts/deploy_vps.sh

# orient in the code
graphify query "<question>"
python scripts/graph_fresh.py
```
