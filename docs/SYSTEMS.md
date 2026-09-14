# Afon — the 50-system master plan

**What this document is.** One section per system, fifty of them, each with a floor to reach, a
sequence of small raises, and a verification gate on every step. `TODO.md` remains the **canonical
roadmap** and the live task ledger — it owns dated findings, parts A–N, and the running record of
what was tried. This document owns the *shape of done*: for each system, what "wired", "verified",
and "elite in production" concretely mean, and which test decides. Where a task already exists in
`TODO.md`, this document cites it rather than restating it. **Together the two define the finish
line; neither does alone.**

**Written 2026-08-14, revised twice the same day.** The second revision added, for every one of the
fifty: the build and test **options weighed** with the losers named, the **open-source foundation**
to start from with its licence, a concrete **speed plan**, and the four dimensions a plan is useless
without — **confidence** (and the one measurement that would settle it), **effort**, **your time**,
**cost**, and **rollback**.

**The totals it produces: 424 engineering-days · 10h35m of your time · €2–8/month added ·
25 systems grounded, 18 reasoned, 7 still guesses** — and six of those seven are settled by a
decision from you, not by work from me. Those numbers are the point of the revision: they were
absent before, which meant the plan could not be argued with.

State at the time of writing: **158 registered tests**, last full run green; last behavioural grade
**84.9/100** (target ≥95, no category <90); **136 registered tools**; Afon **parked** by
`~/.afon/MAINTENANCE` on both hosts, deliberately out of production until this plan's floors are met.

---

## How to read a section

```
### S07 · Session & Context Management
Status      — one of: complete-for-now · built, not well structured · half-built · missing
Surfaces    — the modules that own it today
The bar     — the observable behaviour that counts as elite. Written as what the owner sees.
Budget      — the latency / token / memory numbers it must hold
Stack       — what it runs on today; what to ADD and why; what was DECLINED and why. A declined
              choice is recorded with its reason so it is not re-proposed every quarter.
Verified by — the test METHODS this system needs (corpus, fixtures, fault injection, drill),
              beyond the specific gate named on each task.
Options     — the real alternatives weighed for BUILD and for TEST, with the pick and the reason.
              Two or three options each; the losers are named so the choice can be re-argued
              against evidence rather than re-discovered from scratch.
Open-source — the foundation to start from, with its licence and what it saves us writing.
Speed       — the concrete techniques that make THIS system fast, not a general aspiration.
Economics   — confidence · what would settle it · effort · your time · €/month · rollback.
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
4. **A budget with no enforcement is a wish.** Added 2026-09-12. Every budget in this document is
   currently a number a test checks after the fact. At least the three on the answer path — prefill
   tokens, catalogue tokens, p95 turn latency — must *degrade the system automatically* when they are
   breached: narrow the catalogue, drop to a cheaper model, go quieter. And say so, because silent
   degradation is the failure mode this whole document is written against. See 31.R5.

---

# The architecture this plan is for

Every technology choice below is downstream of one fact, so it is stated once here rather than
argued fifty times: **the fifty systems are capabilities inside three processes, not fifty
services.**

| | |
|---|---|
| **brain** | one Python process on one VPS (`jarvis-brain`, a systemd **user** unit with linger). Holds the agent loop, 136 tools, every memory store, the scheduler and the HTTP/WS surface. |
| **edge** | one Python process on the laptop — the pipecat voice pipeline, wake word, VAD, STT/TTS, speaker gate. |
| **pc_agent** | one Python process on the laptop — the hands: browser, files, apps, audio devices, camera. |

Three network boundaries exist in total: **edge ↔ brain** (WebSocket), **brain ↔ pc_agent**
(PC_LINK WebSocket), and **brain ↔ the outside world** (HTTPS to providers). Everything else that
looks like an interface — "the proactivity engine talks to the context engine" — is a Python
function call inside one process.

That is not an accident to be fixed later. It is what makes a single-owner assistant answer in under
two seconds, survive on one small VPS, and be debuggable by one person. A microservice topology
would buy independent scaling that nobody needs and pay for it in latency, operational surface and
failure modes at every hop.

**So the standard distributed-systems playbook does not apply here, and applying it anyway is the
main risk to this plan.** Contract tests between two functions in the same module are a type
checker. A message broker between two objects on the same heap is a bug generator. Kubernetes for
one node is a second system to keep alive. Where those techniques *do* apply — and several do, at
exactly three boundaries — they are named in the relevant section.

**Where each language earns its place.** Python is the whole system today and should stay so: the
libraries that matter here (pipecat, SpeechBrain, OpenCV, ArcFace, the provider SDKs) are Python
first, and a second language buys a boundary to maintain. TypeScript already exists where it belongs
— the two browser clients, which are HTML/JS by necessity. **Rust or Go are declined outright**
until something is *measured* too slow in Python; today the latency budget is spent on model
inference and network round-trips, not on interpreter overhead, so a rewrite would move work from
the slow part to the fast part.

---

# Standing technology decisions

These answer, once, what to adopt and what to leave alone. A section may override a line here only
by saying so and giving a reason.

**Adopted — worth adding, cheap, and it closes a real gap**

| Choice | Where | Why |
|---|---|---|
| `respx` (HTTP-layer fixtures for httpx) | S02, S20, S41 | Today's stubs monkeypatch client objects, which cannot exercise timeouts, 429 bodies or partial streams — the three cases failover exists for. |
| `Hypothesis` (property-based tests) | S01, S03, S21 | Clause ordering, argument validation and date handling all fail on inputs nobody thought to write down. |
| `tiktoken` | S03, S07 | The prefill budget is currently estimated. A budget you cannot measure is a hope. |
| JSON-Schema validation before dispatch | S03 | The schemas already exist and nothing enforces them; this is what makes a one-shot repair prompt possible. |
| Jinja2 + WeasyPrint | S06 | Real documents with no system binary on the brain host. |
| Beancount (plain-text ledger) | S40 | Auditable, diffable, git-versioned, read-only by construction. |
| A Prometheus-format `/metrics` rendering | S31 | A text endpoint costs nothing and makes any future scraper optional rather than required. |
| OpenTelemetry-**compatible** correlation ids | S01, S31 | One id per turn, propagated edge → brain → tool → audit. The value is the id, not the collector. |
| Recorded fixtures (HAR, frames, utterances, responses) | S05, S10, S11, S20, S26 | The systems that keep regressing regress against *data*, and the missing asset is almost always a corpus, not a library. |

**Adopted in reduced form — the idea is right, the scale is not**

| Recommended | Taken as | Why |
|---|---|---|
| Event-driven backbone (NATS / Kafka / Redis Streams) | The existing in-process signal bus, plus typed events on the **three** real boundaries | A broker between objects on one heap adds serialisation, a daemon and a new failure mode to a function call. |
| Contract-first interfaces (Pact / AsyncAPI / OpenAPI) | Versioned schemas for exactly those three boundaries (`shared/protocol.py`), gated by tests | Contract testing pays where teams deploy independently. Here it pays at the wire, and only there. |
| Full observability stack (Grafana / Loki / Tempo) | Structured JSON logs, the HUD, `/metrics`, and complete turn traces | Same questions answered, no stack to operate. Add Grafana the day he wants dashboards. |
| Chaos engineering (Chaos Mesh, Toxiproxy) | Fault injection inside the existing hermetic tests, plus scheduled live drills | The failures worth rehearsing are provider outage, link drop, dead camera, full disk — all injectable in-process. |
| Testcontainers / Docker Compose profiles | The hermetic suite plus three-process local runs | There are no containers to compose; the "services" are imports. |
| Feature store + gradient boosting (Feast, LightGBM) | Logged decisions in sqlite; a model only after ~200 real outcomes | A recommender trained on a handful of events is a random number generator with a confidence interval. |

**Declined — with the reason, so it is not re-proposed every quarter**

| Declined | Reason |
|---|---|
| Kubernetes, multi-region, Consul/etcd, load balancers | One VPS, one laptop, one owner. Consensus systems coordinate many nodes; at two nodes the honest answer is a declared degraded mode (S32). |
| Neo4j, InfluxDB/TimescaleDB, Qdrant, MinIO | Four separate daemons for data one Postgres already holds: a graph is an edge table, a time series is a timestamped row, vectors are pgvector, blobs are a directory with a path in a row. Adding four servers to avoid four schemas is a bad trade at this size. |
| LangChain / LangGraph / CrewAI / AutoGen as the agent core | The loop already carries the confirm tier, typed degradation, clause completion and streaming failover. A framework rewrite re-litigates all four for no capability Afon lacks. |
| Celery / Temporal / Prefect / Airflow | Broker plus worker tier for a queue of a handful of items; durability already comes from sqlite and restart-expiry. |
| Keycloak / Auth0 / Vault / OPA | Exactly one principal. The confirm tier *is* the policy engine; `pass` *is* the secret store. A second authority is a sync problem, not a security gain. |
| NeMo Guardrails | Refusals are deterministic and in code (S44). Two policy engines produce two answers to "may I", and the disagreement shows up as behaviour, not as an error. |
| Differential privacy | It protects individuals inside an aggregate release. One subject, no release. |
| Schemathesis | It fuzzes the OpenAPI surface *you* expose; Afon consumes APIs and exposes almost none. |
| pytest as the runner | Deliberate, and load-bearing: `run_all_tests.py` executes each bench file as a **subprocess**, so every test runs standalone with no conftest and no shared harness that the failure under diagnosis could itself have broken (J3.1). pytest is fine *inside* a file; it is not the gate. |
| Rust / Go rewrites | Nothing is measured CPU-bound. The latency budget is model inference and network. |
| A digital twin / simulation layer for the home (proposed 2026-09-12) | Recommended in the same review that praised this plan for refusing over-engineering, which is the tell. For one owner's smart home the proportionate version is a dry-run flag on scene application (S28), not a simulated model of the house to keep in step with the house. |
| Household / multi-user sophistication (proposed 2026-09-12) | Out of scope by design, and not cheaply retrofitted: the confirm tier, the biometric gate and `pass` all assume exactly one principal. Guest mode (S25) covers "someone else is in the room"; it deliberately does not cover a second owner. |
| Write-behind / durable-queue memory writes (proposed 2026-09-12) | Buys write throughput nobody here needs and creates a window in which a fact is acknowledged and not stored. For a memory system that is the wrong trade at any throughput. |
| A 7–8B local model on the brain host | No GPU, and the host already OOMed on one embedding worker (2026-08-31). A local tier is welcome at the size that fits: see 03.R1. |

---

# The data platform — Postgres, Redis-compatible cache, and what stays sqlite

This section exists because the earlier decline of Postgres has **expired on its own terms**, and a
decision that was right in August is wrong now for a reason worth recording.

**Why it was declined.** S30 declined Postgres because "a migration would run straight through the
laptop/VPS divergence this system is trying to end". That was correct. The two hosts held divergent
learned state under identical names, because stores resolved from the location of the unpacked code
rather than from a state root.

**Why that objection is gone.** Task 30.F1 is done. There is one state root per host
(`AFON_STATE_DIR`, default `~/.afon`, resolved in `shared/paths.py` and nowhere else), the union
migration ran, and `test_memory_single_origin.py` holds the invariant at 46 checks with ten planted
regressions. The divergence a migration would have run through no longer exists. The specific reason
for the decline is spent, so the decline is revisited rather than repeated.

**What Postgres buys that sqlite cannot.**

| Want | sqlite today | Postgres |
|---|---|---|
| Several readers while the brain writes | one writer, readers block on the lock | MVCC; the fleet, a dashboard and the brain read the same rows concurrently |
| Semantic recall without a remote API | Jina embeddings over HTTP on the answer path | `pgvector` locally; embeddings stop being a network dependency and a bill |
| A real claim primitive for S16 and S48 | a file lock and hope | `SELECT … FOR UPDATE SKIP LOCKED`, which is the whole of what Ray was proposed for |
| Replication for S32 | copying files and praying about torn pages | logical replication, or Litestream if it stays sqlite |
| One store, not ten | ten `afon_*.sqlite` files, each with its own schema drift | one schema, one backup, one restore drill |

**What it costs, stated plainly.** A daemon on the brain host. On a VPS that OOMed once this summer
that is not free, so it is tuned down deliberately rather than run at defaults:
`shared_buffers=128MB`, `work_mem=4MB`, `max_connections=20`, and no autovacuum tuning games. Budget
250 MB RSS and hold it to that with a gate.

**What stays sqlite, permanently.** The **edge** and **pc_agent** processes on the laptop. They must
keep working with the VPS unreachable, which is the whole point of S23 and S32. They never speak to
Postgres. **The brain is the only Postgres client.** This is the line that keeps offline operation
real, and a task that crosses it is a bug, not a feature.

**The cache is Valkey, not Redis.** Redis changed licence in 2024 and, although Redis 8 returned to
AGPL-3.0 in 2025, **Valkey (BSD-3, Linux Foundation)** is the clean OSI answer and is protocol- and
client-compatible — `redis-py` talks to it unchanged. Confirm both licence positions at adoption.
Afon already declares Redis as an optional L4 hot cache that degrades to a no-op, so this finishes a
half-built dependency rather than adding a new one. **It must stay a no-op when absent.** Four uses,
and no others:

1. The per-turn context digest and prompt cache, which is pure recomputable derived state.
2. A distributed lock replacing the file lock, so the brain and a scheduled job cannot both act.
3. The live presence key for S43, with keyspace notifications so a handoff does not wait for a poll.
4. Provider rate-limit counters for S20.

Nothing durable lives only in the cache. If the cache is cold, every one of those four recomputes.

**The migration, per store, through a seam that already exists.** 30.F2 shipped one `recall()`
facade over the seven stores, so callers do not touch stores directly any more. That facade is the
migration seam and the reason this is a week rather than a rewrite:

1. Create the schema; dual-write sqlite and Postgres behind the facade.
2. Backfill, then run `compare_stores.py` until reads agree row for row.
3. Flip reads per store, one at a time, behind `AFON_STORE_BACKEND`.
4. Keep dual-write for two weeks, then drop sqlite writes for that store.

**Rollback is per store and needs no revert:** flip `AFON_STORE_BACKEND` back to `sqlite`. The
sqlite file is still being written during the overlap, which is what makes the rollback real rather
than theoretical.

**Gates.** `test_store_parity.py` — every query returns identical rows from both backends ·
`test_offline_edge.py` — edge and pc_agent pass their suites with Postgres stopped ·
`test_cache_optional.py` — the full suite passes with the cache stopped · recall p95 ≤300ms is
unchanged or better · brain host RSS ceiling enforced in `test_speed.py`.

**Open-source base.** PostgreSQL (PostgreSQL licence), pgvector (PostgreSQL licence), Valkey
(BSD-3), `psycopg` 3 (LGPL-3.0, declared dependency, not vendored), `redis-py` (MIT).

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| reasoned | store-parity green and recall p95 no worse on the real corpus | 6 / 4 / 2 d | 0 | 0 (same VPS) | `AFON_STORE_BACKEND=sqlite`, per store |


# How coherence is tested — fifty capabilities, one organism

The insight worth keeping from the integration-testing proposal is the one that survives the change
of scale: **coherence is emergent, so it is tested at the scenario and state level, not by testing
every pair.** What changes is the machinery. There is no service mesh to spin up; there is a brain
process, two laptop processes, and a set of stores.

**Five tiers, in the order a change passes through them.**

| Tier | What it proves | How it runs here |
|---|---|---|
| **1 · Hermetic unit** | One module's contract, including its failure shapes | A bench script with every dependency stubbed. No network, no clock, no camera. 157 of these today. |
| **2 · Wire contract** | The three real boundaries agree | Schema tests over `src/afon/shared/protocol.py`, PC_LINK ops, and the client↔brain routes (`test_client_endpoints.py`, `test_pc_agent_routing.py`). This is where contract testing earns its keep. |
| **3 · Cross-system scenario** | Several systems produce one coherent outcome | A journey: drive the agent with stubbed *externals* but real internals, then assert on spoken output, emitted signals **and final store state**. |
| **4 · Behavioural** | The whole assistant, judged | `behavioral_suite.py` against the deployed brain with a real model and an LLM judge, median-5. |
| **5 · Live drill** | Reality, once | A recorded run: unplug the VPS, churn the AirPods, sit at the desk. Logged in `TODO.md` with a date. |
| **6 · Continuous** | Reality, always | Added 2026-09-12. Every real turn scored in the background against the same rubric the behavioural suite uses, with the outcome recorded: did he act, ignore, correct, or repeat himself. Tiers 1–5 all test what we thought to write down; this is the only tier whose cases the owner writes by living. It is what 31.R6 builds. |

**The journeys (tier 3) are the coherence suite.** Each crosses many systems, and each asserts three
things — what he said, what he emitted, and what the stores hold afterwards. Golden state snapshots
before and after make the third assertion deterministic.

| # | Journey | Systems crossed |
|---|---|---|
| J-01 | Morning: wake → context → brief → calendar/tasks → proactive suggestion → notification | S27 S17 S21 S16 S14 S13 |
| J-02 | Interruption: urgent signal during focus → suppression decision → channel choice → ack | S14 S27 S13 S38 |
| J-03 | Project: goal → decomposition → delegation → research → document → progress report | S33 S16 S39 S41 S06 S45 |
| J-04 | Continuity: start on the phone, continue at the desk, close by voice | S07 S12 S43 S30 |
| J-05 | Identity: owner speaks → gate → protected action → confirm → audit | S10 S11 S36 S45 |
| J-06 | Degradation: primary model dies mid-answer → failover → the owner hears one answer | S02 S32 S31 |
| J-07 | Memory: told a fact on Monday, asked on Friday, contradicted on Saturday | S30 S24 S45 |
| J-08 | Device: "open my email" from the kitchen → PC_LINK → verify-after-act | S04 S12 S05 |
| J-09 | Home: "I'm going to bed" → scene → read-back → confirmation | S28 S27 S29 |
| J-10 | Crisis: trigger phrase → classifier → ladder → stop condition | S35 S13 S38 S36 |
| J-11 | Recovery: kill the brain mid-task → restart → the task is still there | S22 S16 S31 |
| J-12 | Silence: a whole day with nothing worth saying → he says nothing | S14 S13 S27 |

**What each journey must assert, or it is theatre:** the spoken answer, the tool calls actually
fired (not just the final text), the signals emitted, the final state of every store the journey
should have written, and that **no store the journey should not have touched was written**. That
last clause is what catches a system quietly reaching outside its lane.

**Correlation ids** make the traces readable: one id per turn, generated at the edge, carried
through the brain, stamped on every tool call, audit row and error-journal entry. This is the
OpenTelemetry idea kept and its infrastructure declined — the value is the id and complete
coverage, not a collector.

**Chaos, at this scale**, is fault injection inside the hermetic tests (provider 500s, a dropped
PC_LINK, a corrupt store, a full disk, a camera that returns black frames) plus scheduled live
drills. What must be true after every one: the owner is told, the world model is not left
inconsistent, and nothing needed a human to restart it.

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

### The unpark gate — added 2026-09-12, and it outranks every raise in this document

**Afon goes back into production when Wave 3's floors are green. Not when the raises are done, and
not when the behavioural score reaches 95.**

This is a change, and the reason is that the current order has a failure mode nobody had named:
*it never finishes.* Two independent reviews landed on the same risk from opposite directions — 424
engineering-days is one to two years of realistic solo throughput, and a culture where nothing lands
red plus a target of ≥95 with no category below 90 can keep the system parked while floors are
polished forever. Both are right, and the plan as written had no answer.

The deciding argument is not schedule pressure, it is evidence. **He has been parked since
2026-08-13. Zero real turns have been measured since.** Every gate in this document tests what we
imagined a turn would do. The behavioural suite is the closest thing to truth here and it is a
periodic exam against fixtures, run by us, scored by a judge we chose. Nothing in 366 tasks tells us
what actually happens when the owner talks to him for a week.

So: **floors are the product. Raises and elite are optional improvements, and they wait.** A raise
may only jump the queue if a floor it blocks cannot pass without it, and that has to be said in the
task.

What must hold before the lock comes off, beyond Wave 3's floors:

| | Why |
|---|---|
| Wave 0 floors green | Watchable and recoverable before trusted. Already green. |
| Wave 1 + 2 + 3 floors green | Organs, senses, daily loop. |
| 31.R5 and 31.R6 shipped | Governors and continuous evaluation. These are the two raises that make unparking *safe* rather than hopeful, which is why they are the exception to the rule above. |
| One live drill recorded | Tier 5. Unplug the VPS, churn the AirPods, sit at the desk. |

After that, the order is set by what real use shows, not by this document's numbering.

---

# Reading the per-system economics

Every section carries a six-column table. It exists because a plan that says what to build and not
what it costs, who it blocks on, or how to undo it is a wish list.

| Column | Means |
|---|---|
| **Confidence** | `grounded` — I read this code and there are tests behind the verdict. `reasoned` — the architecture is known, the specifics are inferred. `guess` — the *requirement* does not exist yet, so the plan is a placeholder shaped like a plan. |
| **Settled by** | The single measurement or decision that converts the row to `grounded`. Not "more analysis". |
| **Effort F/R/E** | Engineering-days for the floor, the raises, the elite tier. One day = one focused working day including its test. |
| **Owner** | Time required **from you**, and nothing else can substitute for it. |
| **€/mo** | Recurring cost this system adds. Most add nothing. |
| **Rollback** | How to undo it without a revert war — a flag, a nullable column, a file. |

---

# Ownership boundaries — the four pairs that would otherwise fight

The fifty are the owner's taxonomy, and four pairs overlap enough that two sections could each claim
the same work. Left implicit, that produces the worst kind of duplication: two modules that each
half-implement a behaviour and disagree at the seam. Settled here, once.

| Pair | The line | Who owns what |
|---|---|---|
| **S26 Perception** vs **S27 Context** | Sensing vs interpretation | S26 turns sensors into facts ("a face is present", "the room is playing television"). S27 turns facts into a *situation* ("he is at the desk, in focus, twenty minutes before a meeting"). S26 never decides what a state means; S27 never touches a camera. |
| **S13 Notifications** vs **S14 Proactivity** | Whether to speak vs how to reach him | S14 decides **if** something is worth saying and when. S13 decides **which channel** carries it and whether it arrived. A message that should not have been sent is S14's bug; one that was sent and never seen is S13's. |
| **S39 Delegation** vs **S48 Coordination** | One handoff vs many | S39 owns a single delegation end to end: brief, deadline, result, verification. S48 exists only when **more than one** worker is on the same job — claiming, merging, isolating failure. With one worker, S48 is not involved at all. |
| **S30 Memory** vs **S24 World Model** | What was said vs what is true | S30 stores and retrieves *records* — turns, facts, documents, with provenance. S24 holds the *model* — entities, relations, validity over time — and is built from S30's records. S24 never stores a raw turn; S30 never resolves an entity. |

Two more, weaker but worth stating: **S22 Recoverability** owns getting back to a working state,
**S32 Redundancy** owns not needing to; and **S36 Security** owns who may act, **S44 Ethics** owns
what may be done at all — a refusal that depends on identity is S36, one that holds for everyone is
S44.

---

# Confidence map — where this plan is grounded, reasoned, or guessing

**25 grounded · 18 reasoned · 7 guesses.** The guesses are not evenly spread, and that is the useful
part: they cluster exactly where a *requirement* is missing rather than where the engineering is
hard.

| Guessing | Why | Settled by |
|---|---|---|
| S15 Recommendations | No logged decisions exist, so "what should he recommend" has no data behind it | 30 logged recommendations and their acceptance |
| S34 Health & Wellness | The design depends entirely on which wearable he actually wears | Naming the device |
| S35 Crisis Response | Thresholds and the contact list are personal, not technical | His contact list and what he wants escalated |
| S40 Financial | Depends on whether he will keep a ledger at all | One real ledger file |
| S46 Forecasting | No baseline exists, so no target exists | A month of scored predictions vs a naive baseline |
| S47 Inventory | The first category is his choice and changes the schema | Naming the category |
| S49 Fabrication | Correctly a guess — there is no hardware | A decision record naming the machine |

**Six of the seven are settled by a decision from you, not by work from me.** That is the honest
shape of this plan: the engineering is mostly known; the requirements for one seventh of it are not.

The 18 `reasoned` rows are a different risk — the code exists and I have not read all of it in
depth. Each names the measurement that would settle it, and all of them are cheap (a corpus, a
percentile, a week of logs). None blocks a floor.

---

# What it costs to build

**424 engineering-days** across all fifty systems, at one focused day per unit including its test.

| Wave | Floor | Raise | Elite | Total | What it buys |
|---|---|---|---|---|---|
| **0 — Unpark safely** | 11 | 14 | 8 | **33 d** | The floors that must hold before he goes back into production |
| **1 — The organs** | 17 | 25 | 14 | **56 d** | Brain, LLM, tools, memory, world model — every other ceiling |
| **2 — Identity & senses** | 14 | 25 | 12 | **51 d** | Knowing who is speaking and what he is looking at |
| **3 — The daily loop** | 16 | 28 | 11 | **55 d** | The behaviour met every single day |
| **4 — Reach & control** | 23 | 39 | 19 | **81 d** | Hands, devices, homes, channels |
| **5 — Judgment** | 18 | 27 | 14 | **59 d** | Personality, research, ethics, explanation, delegation |
| **6 — New ground** | 29 | 41 | 19 | **89 d** | The missing and half-built systems |
| | **128** | **199** | **97** | **424 d** | |

**Read this as a shape, not a schedule.** Three things follow from it:

1. **The floors alone are 128 days** — a third of the total. Everything else is optional in a way
   the floors are not: a floor is "make it exist and stop it lying".
2. **Waves 0–2 are 140 days and they gate everything else.** Wave 0 (33 days) is what stands between
   Afon and being unparked.
3. **The elite tier is 97 days and most of it cannot be compressed**, because it is measurement —
   a month of acceptance data is a month regardless of how fast anyone types.

Estimates are mine and carry the confidence of their row: `grounded` rows are ±30%, `guess` rows
could be double or a tenth once the requirement exists.

---

# What it costs you — the owner-time ledger

**635 minutes: ten hours and thirty-five minutes**, spread across 30 of the 50 systems. Nothing here
can be substituted by engineering; each is either a credential only you can create, a recording only
your voice can make, or a decision only you can take.

**Blocking a floor right now (≈3h15m).** Until these land, the systems below cannot leave the floor
however much code is written.

| Minutes | What | Unblocks |
|---|---|---|
| 45 | `.env` → password store, and rotate the exposed keys | S36 floor, S20 |
| 30 | Record the voice-enrolment corpus: owner, a stranger, the television | S10, and through it S11 fusion |
| 30 | Write the real routines into `routines.json` | S21, S17 evening brief |
| 30 | Twilio credentials + the emergency contact list | S35, S38 voice |
| 30 | Author ten automations you actually want | S29 |
| 20 | Read the voice-enrolment script | S08 → S10 |
| 15 | Decide the laptop↔VPS memory merge direction | S30, and S32 behind it |
| 10 | Sit for the ArcFace face capture | S09 → S11 |
| 10 | The Home Assistant long-lived token | **All of S28** |
| 5 | Enable ntfy action buttons | S13 acknowledgement |

**Needed later, not blocking (≈4h).** Storage targets and passphrases (30) · state the real
objectives (30) · grade three research answers (30) · the first Beancount ledger (60) · say what
belongs in the morning brief (10) · mark tone on ten real answers (20) · agree the safety precedence
order (20) · retention preferences (20) · label what matters in the inbox (15) · template
preferences (15) · a listen-check on TTS prosody (15) · the fabrication decision (30) · the
inventory category (10) · name the wearable (15) · key rotation (10).

**The single highest-leverage 15 minutes in the entire plan** is the Home Assistant token plus the
ntfy toggle: ten minutes and five, and they take one system from dark to live and close the last
external blocker in Part C.

---

# What it costs to run

Afon's existing bill is unchanged by this plan: the VPS, the LLM providers, Deepgram, ElevenLabs and
Jina embeddings are all already running. What the plan **adds**:

| Added | €/month | Why | Optional? |
|---|---|---|---|
| Object storage for off-site snapshots (restic, Litestream) | 1–3 | S22 backups are local today — a dead disk takes them with it | No, if recoverability is meant seriously |
| Twilio | 1–5 | The call rung of the crisis ladder; usage-based, near zero when unused | Yes, until S35 is wanted |
| Paid search API (Tavily/Exa) | 0–20 | Only if the free chain is measured insufficient | Yes — measure first |
| Krisp AEC SDK | unknown | Only if free AEC is measured insufficient | Yes — measure first |
| Everything else (42 of 50 systems) | **0** | sqlite, Home Assistant, ntfy, and the rest are self-hosted or free | — |

**Default configuration: €2–8/month added.** Everything above that is a decision with a measurement
in front of it. That is a direct consequence of the declines — Postgres, Kubernetes, Vault, Temporal
and a managed observability stack would each have added an order of magnitude more, in money and in
the operational attention that is scarcer.

---

# The rollback doctrine

Every change in this plan lands so that undoing it is cheaper than debating it. Four rules, and they
are why the per-system Rollback column is short:

1. **New behaviour arrives behind a setting that defaults to today's behaviour.** Intent classes,
   catalogue narrowing, presence fusion, wake arbitration, promotion — each ships off, is measured
   on, and stays only if the measurement holds.
2. **New state is additive: nullable columns, new files, new tables.** Never a migration that
   rewrites what exists. A rollback is then "stop reading the column", not "restore a backup".
3. **A facade wraps; it does not replace.** The memory recall facade and the media state owner both
   sit *over* the existing paths, so removing them leaves a working system rather than a hole.
4. **The park switch is always the last resort and always works.** `~/.afon/MAINTENANCE` halts every
   role on every host, and `test_maintenance_lock.py` keeps it that way.

A change that cannot be described in one line of that column is too big and should be split.

---

# Open-source foundations — the shortlist

Everything the plan proposes adopting, in one place. **Licences are as published by each project at
the time of writing and must be confirmed at adoption** — this is a shortlist, not legal advice.

| Foundation | Licence | Systems | What it saves |
|---|---|---|---|
| Hypothesis | MPL-2.0 | S01, S03, S21 | Property-test generators, otherwise hand-rolled |
| respx | BSD-3 | S02, S20, S41 | Provider failure at the HTTP layer, not the object layer |
| jsonschema | MIT | S03 | Argument validation before dispatch |
| tiktoken | MIT | S03, S07 | Exact token budgets instead of estimates |
| trafilatura | Apache-2.0 | S05, S41 | Main-content extraction that beats hand-tuned heuristics |
| Jinja2 · WeasyPrint · python-docx | BSD-3 · BSD-3 · MIT | S06 | Real documents with no system binary on the brain host |
| MediaPipe | Apache-2.0 | S11 | A face detector that survives angle and backlight |
| YAMNet | Apache-2.0 | S26 | "Is that the television" — the speaker gate's missing input |
| speexdsp · webrtc-audio-processing | BSD-3 | S18 | Free AEC to measure before buying one |
| sqlite-vec | MIT/Apache-2.0 | S30 | A faster vector path that keeps one file and one process |
| prometheus_client · structlog | Apache-2.0 · MIT/Apache-2.0 | S31 | A scrape endpoint and structured logs, no stack to run |
| Litestream | Apache-2.0 | S32 | Continuous sqlite replication to object storage |
| restic (or borg) | BSD-2 (BSD-3) | S22, S50 | Encrypted, deduplicated, off-site snapshots |
| Uptime Kuma / Healthchecks | MIT / BSD-3 | S23 | Uptime that is not self-reported |
| Beancount | GPL-2.0 | S40 | A plain-text ledger that is auditable without Afon |
| Grocy | MIT | S47 | A full inventory app, if one table proves too bare |
| statsforecast / statsmodels | Apache-2.0 / BSD-3 | S46 | Baselines worth beating before any foundation model |
| SearXNG | AGPL-3.0 | S41 | Free meta-search, self-hosted, if the chain proves weak |
| cadquery · OctoPrint · LinuxCNC · ROS2 | Apache-2.0 · AGPL-3.0 · GPL-2.0 · Apache-2.0 | S49 | Recorded for when hardware exists; nothing before that |
| Already in use: pipecat · SpeechBrain · OpenCV · Playwright · Silero VAD · Home Assistant · ntfy · `pass` | BSD · Apache-2.0 · Apache-2.0 · Apache-2.0 · MIT · Apache-2.0 · Apache-2.0/GPL-2.0 · GPL-2.0 | — | The existing foundation, listed so its licences are visible in one place |

**The linking rule.** Copyleft tools (Beancount, `pass`, SearXNG, mpv, OctoPrint, LinuxCNC) are used
as **separate processes, CLIs, file formats or self-hosted services — never imported into the `afon`
package.** That keeps the repository's own licensing position clean if OpenAfon is ever published,
and it is also better engineering: a ledger that is a text file survives Afon.

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

**Stack.** Today: a custom async agent loop (`src/afon/brain/agent.py`) with clause planning, a
confirm tier, typed tool results and streaming failover — all of which a framework rewrite would
have to re-litigate. **Add:** Hypothesis, for property tests over generated multi-clause utterances
(hand-written cases keep missing orderings — the 51.5 combination score is an ordering problem).
**Declined:** LangGraph / CrewAI as the planner. They would replace a loop that already carries four
safety properties with one that carries none of them, for no capability Afon lacks.

**Verified by.** Stubbed-LLM unit tests · property tests over clause orderings · journeys J-01/J-03
· behavioural median-5 on the deployed brain.

**Options weighed.** *Build:* keep the custom async loop · LangGraph state machine · a classical
HTN planner → **keep the loop, add explicit intent classes.** Both alternatives replace four
properties the loop already carries (confirm tier, typed degradation, clause completion, streaming
failover) in exchange for a diagram; HTN planning earns its keep on deep task trees, and this is one
owner's intents. *Test:* golden traces · property generation · both → **both** — Hypothesis for
clause orderings (the 51.5 score is an ordering failure), golden traces for regression.
**Open-source base.** Hypothesis (MPL-2.0) — the generator machinery, otherwise hand-rolled.
**Speed & efficiency.** The intent class decides catalogue size *before* the model call · pure chat
skips the catalogue entirely · the persona prefix stays byte-stable so the provider cache hits ·
planning stays in-process (no extra round trip).

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | 3 days of turn-tracer data (01.F3) | 3 / 5 / 4 d | — | 0 | intent classes behind a setting; unset → today's routing |

**Floor**
- [x] 01.F1 One completion loop, not two — multi-intent and clause paths unified (TODO K1).
      *gate:* `test_clause_completion.py`, `test_clause_routing.py`
- [x] 01.F2 Uncertainty is representable. A calibrated "I don't know / I'd have to check" is a
      supported outcome and the scorer credits it.
      **The premise here was stale and the correction matters.** This said "B06 measured 2% — he
      answers unknowables at full confidence today". That was the pre-fix number: the
      `operating-rules.md` change landed on 2026-08-08 and B06 measured **59-60%** on two runs of
      unchanged code (TODO, 2026-08-10). So the prompt half was already done, four unknowables in
      ten still come back flat, and the open work was the half this task actually names — the scorer.
      Honesty was **one probe matching any of ten substrings**, which credits any reply containing
      the word "don't" (including "I don't have time to explain"), cannot tell an admitted limit from
      a hedge that is immediately taken back — *"I can't be certain, but it will rain on Tuesday"*
      leaves the listener holding a forecast — and had **no control case**, so answering "I'd have to
      check" to everything passed the category outright. Calibration is the property; hedging is not.
      `shared/uncertainty.py` is now the one definition, used by the scorer and gate alike: a hedge
      counts only when it comes **before** the claim it undercuts and no clause after a contrastive
      states a bare figure, date or certainty. Three outcomes — correct · overconfident ·
      **overhedged** — because refusing an answerable question is the other failure and nothing in
      the tree could see it.
      *gate:* new `test_uncertainty.py` — 24/24. 12 unanswerable prompts across the four orthogonal
      kinds B06 probes (future · counterfactual · unsourced figure · contested, three each), none
      accepted as a confident assertion; 12 answerable controls that must still be answered. Nine
      plants, including the hedge-taken-back case and dropping the controls.
- [x] 01.F3 The turn tracer records, per turn: intent class, tools considered, tools fired, prefill
      tokens, wall clock per stage. Three days of data before any further tuning (TODO K2).
      Shipped as `brain/turn_trace.py`: one row per turn from a `finally`, so the turns that
      refuse, take the zero-arg fast path, get barged in on, or raise are recorded too — those are
      the ones worth reading, and an emit-on-success tracer drops exactly them. Rows persist to
      `~/.afon/traces/` because METRICS is in-memory and the brain restarts nightly, so an
      in-process tracer could never accumulate the three days this asks for.
      *gate:* `test_metrics.py` extended — every turn emits a complete trace row. 54/54.

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
- [ ] 01.E3 **The twelve journeys run green** (tier 3 above): each asserts the spoken answer, the
      tools actually fired, the signals emitted, the final state of every store it should have
      written — and that no store it should not have touched was written.
      *gate:* new `test_journeys.py`, one case per journey, hermetic externals and real internals.

**Cross-refs** TODO Part K1, Part A, B06.

---

## S02 · LLM Integration

**Status** complete for now · **Surfaces** `src/afon/brain/llm.py`, `src/afon/config.py`

**The bar.** The owner never perceives a model problem. A primary that stalls is abandoned before
the silence is audible; a rate limit is a routing decision, not an outage; the expensive model is
spent only where it changes the answer.

**Budget.** First-token failover ≤1.2s. Fallback switch adds ≤400ms to the turn. Thinking-tier
escalation fires on <15% of turns.

**Stack.** Today: the `openai` SDK against an OpenAI-compatible chain, with hand-rolled failover,
first-token watchdog, health marking and thinking-tier escalation. **Add:** `respx` to fixture the
provider at the HTTP layer — the current stubs monkeypatch the client object, which cannot exercise
timeouts, 429 bodies or partial streams, the three things failover exists for.
**Declined:** LiteLLM / OpenRouter as the router. Both are good products; adopting one moves the
failover semantics that were tuned in K5a (first-token stall, permanent-vs-transient benching) into
a dependency whose policy we would then have to fight.

**Verified by.** A provider-failure matrix built from `respx` fixtures · latency percentiles from
`bench/llm_bench.py` · chaos: kill the primary mid-stream and assert one continuous answer.

**Options weighed.** *Build:* keep the hand-rolled chain · LiteLLM · OpenRouter → **keep it.**
Both alternatives are good products that would move the failover semantics tuned in K5a
(first-token stall, permanent-vs-transient benching) into a dependency whose policy we would then
fight. *Test:* monkeypatched client (today) · `respx` HTTP fixtures · live smoke → **respx for the
matrix, live smoke retained** — object stubs cannot produce a timeout, a 429 body or a half stream.
**Open-source base.** respx (BSD-3), openai SDK (Apache-2.0).
**Speed & efficiency.** Optimise **TTFT, not total** — the owner hears first sound, not last token ·
per-provider cooldown so the chain stops flapping onto a dead key · stable prefix for cache hits ·
thinking tier only where it changes the answer (<15% of turns).

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | a week of TTFT percentiles from the deployed brain | 2 / 3 / 2 d | 10 min (key rotation) | 0 | cooldown behind a setting |

**Floor**
- [x] 02.F1 Fast failover on first-token stall, not on request timeout (TODO K5a).
      *gate:* `test_brain_llm.py`, `test_resilience.py`
- [x] 02.F2 Tool-tier and thinking-tier routing. *gate:* `test_model_tiers.py`
- [x] 02.F3 Provider health is *learned*, not just configured: a provider that failed twice in five
      minutes is deprioritised for a cooldown, and the HUD says so.
      What existed was the configured kind: one failure benched one chain **entry** for one fixed
      duration. A rate-limited key kills every entry that provider serves, and the chain
      rediscovered that one entry at a time, paying a failed round-trip for each, every turn.
      Failures now also count per provider; two inside five minutes moves its entries to the
      **back** of the chain rather than out of it (a provider that failed twice is not proven
      dead, and a chain that can empty itself can leave Afon mute), repeat offences double the
      cooldown to a 30-minute cap, a dead key skips straight to the long bench, and one success
      clears it — without that half, the first bad five minutes of a day would bench a provider
      until the next restart.
      *gate:* new `test_provider_cooldown.py` 28/28, twelve plants.

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

**Stack.** Today: an in-process registry of 136 handlers with OpenAI-format schemas, typed results
(`ToolResult`/`ErrorKind`) and learned per-tool reliability. **Add:** JSON-Schema validation of
arguments *before* dispatch — the schemas already exist, nothing enforces them, and that is what
makes the one-shot repair prompt possible.
**Declined:** FastAPI tool endpoints. Tools run in the brain's own process; an HTTP hop would add
latency, a port and an auth surface to something that is a function call.

**Verified by.** Per-tool contract tests · failure injection per `ErrorKind` · a presented-catalogue
token ceiling in `test_speed.py` · chaining assertions.

**Options weighed.** *Build:* static narrowing by intent family · embedding retrieval over tool
descriptions · a two-stage LLM router → **static families first, embeddings for the long tail.**
A router that calls a model to choose tools adds a round trip to every turn — it buys recall we can
get deterministically. *Test:* a 60-prompt reachability corpus · fuzzing · schema validation →
**corpus + validation**; reachability is the number that matters (correct tool still findable at
≤20 presented).
**Already shipped, so it stops being recommended.** A 2026-09-12 review named an intent-gated
catalogue as the single highest-leverage change available. It is largely built: `intent_router.py`
narrows to the one tool a high-precision intent needs and forces the call, and `tools/__init__.py`
loads families lazily (`groups_for_text`, `core_tool_schemas`, `group_tool_schemas`). It is rules,
not a model, and for this job that is a feature — deterministic, readable, and testable without a
corpus. What is actually left is measurement and schema size (03.R1, 03.R5), not architecture.

**Open-source base.** jsonschema (MIT) for pre-dispatch validation; pydantic v2 (MIT) only if schema
authoring becomes the bottleneck.
**Speed & efficiency.** 58 tools ≈10k tokens today → ≤20 / ≤2.5k · minify schemas (drop prose the
model does not read) · a hard ceiling on tool results (03.F4) · per-tool timeouts so one slow API
cannot own the turn.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | reachability ≥98% at ≤20 presented, measured on the corpus | 4 / 6 / 3 d | — | 0 | `catalogue_mode=full` restores today's behaviour |

**Floor**
- [x] 03.F1 Typed failure contract everywhere — `tool_failed()`, `is_not_configured()`, no
      prose-matching. *gate:* `test_no_result_sentinels.py`, `test_tool_failure_guard.py`
- [x] 03.F2 Missing-argument handling asks rather than fabricates. *gate:* `test_missing_arg.py`
- [x] 03.F4 **A ceiling on tool results at the boundary.** `agent.py` appends `str(result)` into
      the message list with no cap, so one oversized scrape or document read enters the next
      prefill whole — on the path that is already the dominant per-turn cost. A backstop above every
      per-tool `clip()` limit (so it never fights a tool's own sizing), announced in the content so
      the model can narrow rather than answer from half a document. This is the real half of J1.4.
      Shipped at **both** append sites — the live turn and the autonomous worker, which was the
      worse of the two because its loop keeps the message list across steps.
      *gate:* `test_tool_result_ceiling.py` 15/15.
- [x] 03.F3 **Catalogue narrowing is measured, not assumed.** Per-turn tool count and token cost land
      in the trace; the 58-tool prefill is the number to beat (TODO K2).
      **Now measured, and the estimate was low.** The core catalogue is 59 tools / **8,260 tokens**,
      and a turn that arms a lazy group presents **75 tools / 10,575** — worst observed 91 / 12,437.
      Chatter presents nothing and a router-narrowed read presents one tool / 138 tokens, so the
      two fast paths are real; it is the general and compound turns that pay. The ceiling asserted
      is a **ratchet at today's worst (13,000)**, not the 2,500 budget — 03.R1's two-stage
      selection is what reaches that, and until then this stops the number growing, which is the
      regression that already happened once (59 → 91 tools once groups arm). Each run prints the
      remaining gap so it stays visible.
      *gate:* `test_speed.py` asserts a hard ceiling on presented-catalogue tokens, read from the
      turn's own trace row rather than recomputed. 25/25.

**Raise**
- [ ] 03.R1 Two-stage selection: a cheap router picks a *family* (10–20 tools), the model picks
      within it. **The bar is the rules it would replace**, not the full catalogue: today's
      `intent_router` already narrows, so a learned router that scores worse than it is a regression
      wearing a model. If a model is used at all it is the size that fits the host — an embedding
      plus a linear classifier, or a distilled ~100MB classifier with a declared RSS ceiling.
      *gate:* new `test_catalogue_narrowing.py` — 60 prompts, correct tool still reachable ≥98% at
      ≤20 presented, **and no worse than `intent_router` on the same 60**.
- [ ] 03.R2 Argument validation before dispatch, with a repair prompt on the first failure only.
      *gate:* `test_reminder_args.py`, `test_tool_error_handling.py`
- [ ] 03.R3 Chaining: a result that obviously feeds another (search→open, contact→message) chains in
      one turn, not across two. *gate:* new `test_tool_chaining.py`
- [ ] 03.R4 Dead-tool sweep: every tool not fired in 90 days is either exercised by a bench case or
      retired. *gate:* `test_tool_usage.py` extended with a staleness report.
- [ ] 03.R5 **Schema minification.** The catalogue is the dominant per-turn cost and its *size* has
      never been attacked, only its membership: descriptions written for a human reader, parameters
      no caller sets, names longer than they need to be. Measure first with `tiktoken`, then cut, and
      keep a hard per-turn ceiling with an automatic fallback to one clarifying question when the
      router is unsure.
      *gate:* `test_speed.py` [catalogue-tokens] — a declared ceiling, enforced, with the before and
      after recorded; `test_registry_complete.py` still green, because a minified schema that the
      model can no longer use is not a saving.
- [ ] 03.R6 **Speculative dispatch, read tier only.** When the router is confident, start the likely
      call before the model's response completes, and allow genuinely independent calls to run in
      parallel.
      **The rule is not "where safe", it is a tier check**: only tools in the read tier may be
      speculated or parallelised, never anything in the confirm tier. A speculative confirm-tier
      call is a mechanism for acting on a guess, which is precisely what the confirm tier exists to
      prevent, and "the model probably wanted this" is not consent.
      *gate:* new `test_speculative_dispatch.py` — a confirm-tier tool is never dispatched
      speculatively (asserted over the whole registry, not a sample), a cancelled speculation leaves
      no side effect, and p50 improves on a recorded set of multi-tool turns.

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

**Stack.** Today: a WebSocket control link (`pc_link` ↔ `pc_agent`) carrying 12 typed ops, with the
confirm tier in front of the destructive ones. **Add:** a device simulator fixture so link-down,
slow-ack and half-open states are testable without the laptop.
**Declined:** MQTT/Zigbee here — that belongs under Home Assistant in S28, not in the PC channel.

**Verified by.** Op-routing tests · link-down and half-open injection · verify-after-act assertions ·
a recorded live run of ten real commands.

**Options weighed.** *Build:* keep PC_LINK ops · MQTT to the laptop · an SSH exec channel →
**keep PC_LINK.** One authenticated WebSocket already exists; MQTT adds a broker to keep alive, and
SSH exec is exactly the unbounded shell surface the confirm tier exists to avoid. *Test:* hardware
in the loop · a simulator fixture · both → **simulator for CI, one recorded live run per release** —
link-down and half-open are the interesting states and hardware cannot be scheduled.
**Open-source base.** python-zeroconf (LGPL-2.1, declared dependency, not vendored) for discovery; Home Assistant (Apache-2.0) as the abstraction layer over its WebSocket API, shared with S28 — it is the reason no per-vendor integration is written here.
**Speed & efficiency.** Batch multi-step ops into one round trip · keep-alive so the first command
after idle is not a reconnect · verify-after-act only where the check is cheaper than being wrong.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | p95 round-trip on the LAN over a working day | 2 / 3 / 1 d | — | 0 | ops are additive; unset the link → typed unavailable |

**Floor**
- [x] 04.F1 Twelve operations over PC_LINK with typed results. *gate:* `test_pc_agent_routing.py`
- [x] 04.F2 Destructive operations refuse or confirm. *gate:* `test_pc_agent_refuse.py`,
      `test_security_hardening.py`
- [x] 04.F3 Sleep/suspend and post-resume behaviour. *gate:* `test_pc_suspend.py`
- [x] 04.F4 A dead PC link is reported as unreachable within one turn, never as success.
      *gate:* `test_pc_verify.py` extended with a link-down case.
      *done 2026-09-12:* a laptop that closes its lid leaves the websocket open for over a minute,
      so the link still read as connected, the command was accepted, and it failed after the full
      ninety-second result window — several turns after he asked, having said nothing in the turn
      where he asked it. `forward` now proves the peer is there with a websocket ping before
      committing to that window. The ping needs no change to the executor, because pongs are
      answered by the library itself, so an agent that is running but busy still replies.
      It also fixed a defect the health check had been carrying since it was written: it called
      `PC_LINK.active()` and `PC_LINK.host()`, which are properties, so every check raised
      TypeError, was swallowed, and reported the laptop down with "pc-link check error" whenever
      it was up. The test stub had the same shape as the broken caller, which is how it survived;
      the gate now asserts the stub agrees with the class it stands for.

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

**Stack.** Today: Playwright driving a real browser on the laptop through PC_LINK, with an
httpx/BeautifulSoup scrape chain behind it. **Add:** recorded HAR/HTML fixtures so page-shape
regressions are deterministic instead of "the web changed".
**Declined:** Browserbase / ScrapingBee. A cloud browser loses the owner's logged-in session, which
is the entire reason the browser path exists rather than plain HTTP.

**Verified by.** Fixture replay · a corpus of blocked / paywalled / JS-empty pages · session
persistence across an interruption.

**Options weighed.** *Build:* Playwright on the laptop (today) · Browserbase/ScrapingBee ·
httpx+extraction only → **Playwright, with the HTTP chain as fallback.** A cloud browser loses the
logged-in session, which is the whole reason the browser path exists. *Test:* live sites · recorded
HAR/HTML fixtures · visual regression → **fixtures**; live-site tests fail for reasons that are not
our bug, and visual regression is for products with a UI.
**Open-source base.** Playwright (Apache-2.0); **trafilatura (Apache-2.0)** for main-content
extraction — measurably better than hand-tuned BeautifulSoup heuristics and one dependency.
**Speed & efficiency.** Reuse one browser context instead of launching per call · block images and
fonts on scrape-only fetches · cap page-wait and fall through to the HTTP chain · cache by URL+day.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | open→readable p95 across a 20-site fixture set | 2 / 4 / 3 d | — | 0 | extraction swap is one call site; chain already degrades |

**Floor**
- [x] 05.F1 Twelve browser operations over PC_LINK with a persistent session.
      *gate:* `test_pc_agent_routing.py`, `test_screenshot_transport.py`
- [x] 05.F2 Scrape/search degradation chain is typed. *gate:* `test_web_fallback.py`
- [x] 05.F3 A blocked, paywalled or JS-empty page is reported as such, never summarised from the
      title alone. *gate:* `test_web_fallback.py` extended [empty-body case].
      *done 2026-09-12:* an empty scrape was already honest. The dangerous case is the one that
      returns *something*: a bot check, a paywall, a cookie wall, or a JS shell holding only the
      headline. Those read as content, so the model summarised the obstruction and he got a
      confident answer assembled from a title and a subscribe button — indistinguishable, to him,
      from a summary of the article behind it. `obstructed()` names which wall it is, the chain
      tries the next provider before giving up, and the tool returns the reason instead of the
      body, because handing back the interstitial is an invitation to summarise it.
      The phrase rules only apply to a short body: a long article ABOUT paywalls contains every
      one of those phrases, and flagging it would teach him to ignore the warning.

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

**Stack.** Today: three ad-hoc writers (vault markdown, Notion, email drafts). **Add:** Jinja2 for
templates and WeasyPrint for markdown→PDF (pure-Python, no system binary on the VPS); `python-docx`
only where a real .docx is demanded.
**Declined:** Pandoc / headless LibreOffice as a dependency — a system binary on the brain host for a
format nobody has yet asked for.

**Verified by.** Snapshot tests of rendered output · read-back verification before success is
reported · round-trip for every destination · template-fidelity assertions.

**Options weighed.** *Build:* Jinja2 + WeasyPrint · Pandoc · headless LibreOffice → **Jinja2 +
WeasyPrint**, pure Python, no system binary on the brain host; `python-docx` only where a real .docx
is demanded. Pandoc and LibreOffice are the right answers for a document *pipeline* — this is a
personal assistant writing notes. *Test:* snapshot rendering · round-trip · read-back → **all
three**, cheap and each catches a different failure (layout, fidelity, "did it actually save").
**Open-source base.** Jinja2 (BSD-3), WeasyPrint (BSD-3), python-docx (MIT).
**Speed & efficiency.** Template render is microseconds — the LLM writes prose, the template owns
structure · PDF only when asked · never re-render an unchanged document.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| reasoned | one real document written, revised by voice, and retrieved | 3 / 4 / 2 d | 15 min (template preferences) | 0 | `create_document` is additive; the three existing writers stay |

**Floor** *(this system has no spine today — the floor is one narrow real capability, gated)*
- [x] 06.F1 A single `create_document(kind, title, body, destination)` tool that owns creation for
      vault notes, Notion pages and local markdown, replacing the three separate paths.
      *gate:* `test_document_create.py` — three destinations, each written and read back.
      *done 2026-09-13:* `brain/documents.py`. `write_vault` and `notion_create_page` keep their
      handlers and their confirm gates but no longer have schemas — the model is offered one
      creation tool, because two advertised ways to write a note is how one of them stays the
      unverified one. Local markdown had no writer at all before this. `create_document` lives in
      the `docs` lazy group with the phrases someone actually uses ("write that up", "make a note
      of this", "draft a decision record"), so the core per-turn surface went DOWN by one rather
      than up: 57 to 56. The Notion path needed a real change to be possible at all — the old
      handler discarded the API response including the page id, so the page it had just created
      could never be read, revised or linked to again.
- [x] 06.F2 Every created document is verified by reading it back before Afon reports success.
      *gate:* `test_document_create.py` [read-back]
      *done 2026-09-13:* all three writers reported success on the strength of not having raised,
      which is a different claim from "it is there". A write that returns without an exception has
      been ACCEPTED; whether it landed is a question only a read answers, and the failures that
      live in that gap are ordinary ones — a Notion parent id pointing at an archived page, a vault
      path on a mount that went read-only since startup, a destination that keeps the opening
      sentence and silently drops the rest. Now it writes, fetches, and compares the first 120
      characters; the fingerprint is that long precisely so silent truncation fails it. Three
      outcomes, not two: written and checked, written but unconfirmed (with the reference, because
      that is the document he most needs to go and look at), and not written at all. "I couldn't
      confirm it" and "it failed" send the owner to different places.
- [x] 06.F3 Documents are addressable afterwards: "the note you wrote yesterday about X" resolves.
      *gate:* `test_documents_routing.py` [7]
      *done 2026-09-13:* every created document is recorded — title, kind, where it went, when, and
      the reference a later read needs — and `find_document` resolves a spoken description against
      it. `search_vault` cannot answer this question: it covers what the OWNER wrote too, and has
      no idea which notes were Afon's or when he made them. Only the title is kept, never the body.
      A second copy of the text in the state root would be one more thing to keep in step and one
      more place to sweep on "forget that" — and the document already exists where it was written.
      A document written but unconfirmed stays findable and is named as unconfirmed, since that is
      the one worth opening.

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

**Stack.** Today: in-process history with a trim heuristic, plus the sqlite stores. **Add:**
`tiktoken` for exact budget accounting — the trim currently estimates, which is why the ceiling is a
hope rather than a guarantee.
**Declined:** Redis/Valkey as the *store* for session state. One brain process; an external cache
adds an operational failure mode to something that fits in memory and must survive restart via the
durable store anyway. This does not contradict **The data platform**: the cache there carries the
per-turn digest and prompt cache, which are recomputable derived state. A session that exists only
in the cache is gone the moment the cache restarts, and "the conversation survives a night's sleep"
is this system's whole bar.

**Verified by.** Long-conversation replay · a reference-resolution corpus ("it", "that one") ·
concurrent-session isolation · the cross-device journey J-04.

**Options weighed.** *Build:* in-process + sqlite (today) · Redis · Postgres → **in-process**, with
`tiktoken` for exact accounting. One brain process; an external cache adds an operational failure
mode to state that must survive restart in sqlite anyway. *Test:* long-conversation replay · a
reference-resolution corpus · concurrency → **all three**; the failure ("it" resolved to the wrong
thing) is invisible without a corpus.
**Open-source base.** tiktoken (MIT).
**Speed & efficiency.** Exact token budget instead of a character estimate · summarise on session
close, not per turn · topic segmentation so recall does not drag unrelated turns into the prompt.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| reasoned | context-assembly p95 and a 200-turn replay | 3 / 4 / 2 d | — | 0 | trim policy behind a setting |

**Floor**
- [x] 07.F1 Session identity is explicit: one session id per conversation, carried across edge
      reconnects and across devices.
      `src/afon/shared/session.py`. A session id is now `<device>:<conversation>`: the device half
      says which box is speaking, the conversation half is shared by every device and persists in
      the state directory, so a reconnect rejoins the conversation it left. The three entry points
      carried their own constants — `laptop-1`, `laptop-edge`, `android-1` — so one laptop appeared
      to the brain as two sessions depending on which launcher ran; they now derive the id at
      connect time. It rotates in `reset_session` and nowhere else, so the id and the cleared
      working history cannot disagree about where the boundary was. A legacy or third-party id
      yields `None` rather than being adopted into whatever conversation is current.
      *gate:* `test_session_identity.py` — 33/33.
- [x] 07.F2 Trim is lossy *on purpose* — what is dropped is summarised into the session record, not
      discarded.
      **It was discarded.** `_trim` kept the last N turns and dropped the rest on the floor; only
      the *reset* path journalled, so a conversation long enough to trim lost its opening silently.
      Dropped messages now buffer and are summarised into the L2 journal in the background — never
      awaited, because this runs on the answer path — and a reset flushes whatever has not been
      summarised yet, ordered ahead of what is still in the window. Verified that across ten turns
      through a four-message window, every user turn is in the window, journalled, or buffered, and
      none is journalled twice.
      *gate:* `test_session_identity.py` [trim retains gist]
- [x] 07.F3 Reference resolution ("it", "that", "the second one") is tested, not assumed.
      `src/afon/brain/references.py` resolves the references that have a deterministic answer and
      **declines the rest**: ordinals into a list Afon just produced, and a bare pronoun with
      exactly one recent concrete candidate. Two candidates is ambiguous and declines; an
      out-of-range ordinal declines rather than clamping, because clamping is how "the fourth one"
      against three items acts on the third. The owner's message enters history verbatim and the
      binding travels beside it as a system note phrased as an assumption — a rewrite that guessed
      wrong would be unrecoverable from the transcript.
      *gate:* `test_reference_resolution.py` — **20/20 with 0 wrong bindings.** The gate is two
      numbers, and the second is the real one: a decline and a wrong binding are different
      failures, so `WRONG` must be zero. Eight of the twenty cases are adversarial shapes where
      declining is the correct answer. The corpus was written by the author of the resolver, so it
      measures the rules, not the language.

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

**Stack.** Today: SpeechBrain ECAPA embeddings, enrolled from the live mic. This is exactly what a
from-scratch design would choose, so nothing changes. **Add:** a capture-time quality scorer (SNR,
duration, spectral variety) that rejects a bad sample on the spot.

**Verified by.** An enrolment corpus scored per condition (near / far / headset / with music) ·
separation report owner-vs-stranger · replay-attack samples.

**Options weighed.** *Build:* SpeechBrain ECAPA (today) · Resemblyzer · pyannote embeddings →
**keep ECAPA.** It is installed, it is the strongest of the three on EER, and the weak link here is
the *enrolment*, not the model. *Test:* a per-condition enrolment corpus · synthetic augmentation ·
replay attacks → **corpus first** — the missing asset is recorded audio, not a technique.
**Open-source base.** SpeechBrain (Apache-2.0).
**Speed & efficiency.** Enrolment is offline and costs nothing at runtime · store the mean plus the
per-condition vectors so scoring stays one dot product.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | owner median ≥0.60 with ≥0.25 separation, measured | 1 / 2 / 1 d | **20 min (read the script)** | 0 | the old profile is a file; restore by copy |

**Floor**
- [ ] 08.F1 **Re-enrol on good audio** — the current profile is below target and every downstream
      identity decision inherits that. *gate:* `test_phase5_identity_bench.py` — owner median ≥0.60.
- [x] 08.F2 Enrolment quality is *reported at enrolment time*: too short, too noisy, too uniform is
      rejected on the spot rather than discovered later.
      `edge/enroll_quality.py` measures each clip before it joins the profile — duration, level and
      clipping, speech-to-noise-floor, and **spectral variety**, which is the one that catches what
      the others miss: a hum, a tone or a stretch of silence can be long, loud and clean and contain
      no speech at all. Six distinct verdicts rather than one "check your setup", for the same reason
      `separation_verdict` has three: the fixes are different (move closer · turn the fan off · back
      off the mic · pick the right input device), and one message sends the owner to the wrong one.
      A rejected segment is re-recorded once, then kept with a warning that is repeated at the end —
      a script that refuses forever gets abandoned half-enrolled, and a warning four screens above
      the end of a five-minute read is a warning nobody acts on.
      **The thresholds are documented as defaults, not measurements**, because there is no enrolment
      corpus in this repo yet (that is 08.R1). They are set loose deliberately: a false reject costs
      thirty seconds of re-reading, a false accept costs weeks of a profile nobody can place a
      threshold inside.
      *gate:* `test_enroll_script_parse.py` extended — 42/42, twelve plants. Includes a positive case
      (a scorer that rejects everything passes a gate made only of negatives, and then enrolment can
      never finish) and the bug the variety measure actually had: frames selected by `noise_rms * 2`
      selected *nothing* on a clip of pure noise, so it reported "no speech detected" for a recording
      that was nothing but noise — a measurement that returns zero when it could not measure.

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

**Stack.** Today: OpenCV detection with ArcFace embeddings via `uniface`, LBP kept as a fallback.
**Add:** nothing. InsightFace was recommended and ArcFace *is* InsightFace's model family — the
current package is the same embedding with a far smaller install, and a second face stack would
double the risk of mixing embedding spaces, which is the precise bug `test_face_arcface_backend.py`
exists to prevent.

**Verified by.** Capture-quality rejection tests · per-condition match scores · non-owner separation
· a non-destructive re-enrolment restore.

**Options weighed.** *Build:* ArcFace via uniface (today) · full InsightFace · dlib
`face_recognition` → **keep ArcFace.** It *is* the InsightFace model family with a far smaller
install; dlib is measurably weaker off-angle. A second face stack doubles the risk of mixing
embedding spaces — the exact bug one gate exists to prevent. *Test:* capture-quality rejection ·
per-condition scoring · restore drill → **all three**.
**Open-source base.** OpenCV (Apache-2.0), uniface/ArcFace weights — **model-weight licence to be
confirmed at adoption for anything beyond personal use.**
**Speed & efficiency.** Enrol offline · cache embeddings · one 100×100 crop per detected face.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | an ArcFace enrolment existing at all, plus per-condition scores | 1 / 2 / 1 d | **10 min (sit for the capture)** | 0 | LBP references retained; backend is a setting |

**Floor**
- [ ] 09.F1 **Enrol on ArcFace.** The backend is wired and tested but only the legacy LBP references
      exist. *gate:* `test_face_arcface_backend.py` — a real owner embedding present and loadable.
- [x] 09.F2 Enrolment rejects unusable captures (no face, two faces, too dark) at capture time.
      Enrolment answered every failed capture with one sentence covering all three: *"I couldn't spot
      a face — sit facing the camera in good light. If someone else is in shot, those frames are
      skipped."* The three have different fixes, and the third is the one that matters: multi-face
      frames are skipped precisely because enrolling a second face makes that person a **permanent**
      owner match, and best-of-N matching needs only one such reference. A burst in which every frame
      held two faces was therefore reported as "I couldn't spot a face" — while faces were all it saw.
      `camera.capture_verdict()` now judges the burst from `(brightness, face_count)` per frame and
      names the dominant cause, before anything is written. Two-faces is diagnosed first of the three
      because it is the only one with a security consequence. Taking measurements rather than JPEGs
      keeps the decision hermetic: OpenCV's haar detector is the part a test cannot pin, and it is not
      the part that was wrong. A burst that is mostly good with one passer-by frame still enrols —
      rejecting the whole capture over a bystander is how a working feature gets abandoned — and a
      failure inside the quality check degrades to enrolling rather than blocking the only path to a
      face profile.
      *gate:* `test_face_recognition.py` extended — 35/35, ten plants. Includes the positive case, the
      write-is-actually-blocked case (enrolment appends, so a bad capture is permanent, not a wasted
      minute), and a fixture with a frame that is dark *and* faceless — without it, counting a dark
      frame under both causes is invisible.

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

**Stack.** Today: ECAPA scoring the utterance (not the room) behind a warm-up-safe gate. **Add:**
a labelled corpus — owner, household, stranger, television — because the missing asset here is data,
not a library; the threshold cannot be derived without it. `pyannote` diarization only if two-speaker
attribution is genuinely needed.

**Verified by.** FAR/FRR against the corpus · broadcast-audio rejection · threshold *derived* from
measured separation rather than a constant · a week of live scores.

**Options weighed.** *Build:* cosine threshold on ECAPA (today) · PLDA scoring · pyannote
diarization → **derived threshold now, diarization only if two-speaker attribution is actually
needed.** PLDA wants a development set that does not exist. *Test:* FAR/FRR on a labelled corpus ·
noise augmentation · live scores → **corpus first, live second.**
**Open-source base.** pyannote-audio (MIT, gated model weights) — optional, later.
**Speed & efficiency.** Gate decision ≤150ms · embed the utterance once (fixed in N2) · reject
broadcast audio before embedding where the scene classifier can rule it out.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | FAR/FRR over ~200 labelled utterances including television | 2 / 4 / 2 d | **30 min (record owner/stranger/TV)** | 0 | threshold constant restored in one setting |

**Floor**
- [x] 10.F1 The gate scores the **utterance**, not the room (N2, fixed 2026-08-11).
      *gate:* `test_speaker_gate_scoping.py`
- [x] 10.F2 Cold-start warm-up does not mis-score the first utterance. *gate:* `test_speaker_warmup.py`
- [x] 10.F3 The threshold is derived from the enrolled profile's measured separation, not a constant.
      *gate:* `test_phase5_identity_bench.py` [10.F3] — threshold computed, asserted inside the
      separation band, and shown to flip a verdict the constant got wrong.
      **Built without S08.F1**, which the plan listed as a dependency: what needs a good profile is a
      good *number*, not the derivation. Enrolment already measured owner and impostor scores and did
      nothing with them but advise a manual `AFON_SPEAKER_THRESHOLD` edit; it now records them beside
      the vectors they were measured against (`record_separation`), and `accept_bar()` derives the
      midpoint from them. Both modules that read the constant independently now go through that one
      method — raising it in one place on 2026-07-25 had left the gate's borderline check comparing
      against the other. With no band recorded, the setting stands and says so out loud.
      Two refusals rather than a confident guess: a band narrower than 0.10 (which is today's real
      state — a film cleared 0.40 while the owner sat at 0.47) and a band lying entirely below the
      0.30 floor both keep the setting and name re-enrolment. Replacing the vectors drops the old
      band, so a measurement never outlives the profile it described.
      The two inputs are **deliberately asymmetric**, and this is what makes the bar trustworthy
      rather than merely automatic. Both clips are scored in 2-second windows, and the owner
      contributes his **worst** window while the impostor contributes its **best** — the bar has to
      sit under his weakest turn and over the television's strongest moment. Scoring the six-second
      clips whole would have taken the owner at his best (reading deliberately, seconds after
      enrolling) against a television at its average, and placed the bar high in exactly the way
      TODO I1 warns about: it records a live utterance rejected at 0.29 while the same phrase cleared
      at 0.36 seconds later. **The remaining half is 08.F1**: the derivation is live, and it will
      produce a genuinely better bar the first time it is given a genuinely better profile.

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

**Cross-refs** TODO N2, N3. The floor is complete; the *quality* of the derived bar is behind S08.F1
(20 min at the mic), and the raise tier is behind the labelled corpus.

---

## S11 · Face Recognition

**Status** built, not well structured · **Surfaces** `src/afon/brain/tools/camera.py`,
`src/afon/brain/tools/multimodal.py`

**The bar.** The camera is a *second factor*, never a gatekeeper: it can strengthen a decision, and
it can say "I can't tell", but it may never assert absence it cannot see. A photograph does not pass.

**Budget.** Verify ≤400ms per attempt, ≤120ms per frame for embedding.

**Stack.** Today: ArcFace + Haar cascades, with a separate occupancy path that never feeds
identity. **Add:** passive liveness by frame differencing (blink / micro-motion) — cheap, local, and
the missing precondition before the camera is allowed to be load-bearing.
**Declined for now:** a paid anti-spoof SDK, until the cheap check is measured and found wanting.

**Verified by.** A spoof corpus (printed photo, phone screen, video replay) · multi-frame fusion ·
backlight/profile/glasses fixtures · agreement with the voice verdict.

**Options weighed.** *Detector:* Haar cascades (today) · SCRFD/RetinaFace · MediaPipe →
**add MediaPipe or SCRFD.** Haar is the weak link under angle and backlight, and it is what feeds
identity; the embedding is not the problem. *Liveness:* frame-difference/blink · a texture CNN · a
depth camera → **blink/micro-motion first**, measured before anything is bought. *Test:* a spoof
corpus (print, phone screen, video) · a fixture day · fusion with voice → **all three.**
**Open-source base.** MediaPipe (Apache-2.0), OpenCV (Apache-2.0).
**Speed & efficiency.** Detect on one frame in N rather than all twelve · embed only the best crop ·
skip the occupancy cascades entirely when a frontal face was already found.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | owner recall ≥90% on a fixture day and 0/20 spoofs accepted | 3 / 4 / 2 d | 10 min | 0 | detector behind a setting; Haar remains the default until measured |

**Floor**
- [x] 11.F1 "Nobody there" and "can't tell" are different answers; the confirm gate fails open on
      the latter (fixed 2026-08-13). *gate:* `test_camera.py`, `test_face_second_factor.py`
- [x] 11.F2 ArcFace backend wired with a legacy fallback. *gate:* `test_face_arcface_backend.py`
- [x] 11.F3 **Liveness.** A printed photo or a phone screen used to pass. *gate:* new
      `test_face_liveness.py` (29/29) — static-image fixtures rejected, live frames accepted.
      The threat was narrower and worse than "spoof your way in": `matched` never authorises
      anything, it only declines to refuse, so a photo **propped against the monitor** granted
      `matched` on every check from then on and silently retired the second factor. Nothing reported
      it. `liveness_verdict()` differences consecutive face crops after removing whole-crop
      translation by phase correlation — a raw frame difference is dominated by haar box jitter,
      which moves the crop for a print and a face alike. What is left is the non-rigid change.
      A still burst **withdraws** the verdict (`available` goes False) rather than inverting it:
      reporting `matched: false` would send the caller down the "someone is there and it is not him"
      branch and block a very still owner, which is the absence claim 11.F1 forbids. Liveness runs
      only when he matched — the only verdict it can change — and a check that throws leaves
      behaviour exactly as it was, rather than quietly retiring the factor it guards.
      **Two gaps, measured and asserted rather than implied** (`test_face_liveness.py` [2b]): a print
      held in a *hand* jitters sub-pixel, which resamples and blurs, and that survives alignment —
      3.5 against a moving face's 4.5, a margin but not a separation; and a *video replay* has
      genuine micro-motion and is not addressed at all. Sizing either needs real prints on this
      camera in this room, not the synthetic fixtures, so both are **11.R1's corpus**. Shipping the
      narrow version is still right: an uncaught spoof leaves the second factor exactly where it was
      before this existed, while the propped photo it does catch was permanent.

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

**Stack.** Today: WebSocket sessions to the brain with per-device profiles. **Add:** an explicit
capability registry (camera / speaker / screen) so routing stops guessing.
**Declined:** MQTT or NATS as a device bus. There are at most three clients and the brain's WS server
already multiplexes them; a broker is another always-on process to keep alive for no extra reach.

**Verified by.** Double-wake arbitration · handoff latency · capability negotiation · flaky-link
simulation with forced reconnects.

**Options weighed.** *Build:* WebSocket sessions to the brain (today) · MQTT · NATS → **keep the
WebSocket.** Three clients maximum, and the brain already multiplexes them; a broker is another
always-on process for no extra reach. *Test:* two simulated edges · protocol round-trip · flaky-link
injection → **all three**; double-wake is the failure that actually annoys.
**Open-source base.** python-zeroconf (LGPL-2.1) for discovery, shared with S43; **Syncthing (MPL-2.0)** as a separate service for hand-authored files carried between laptop and VPS. **Never for a live store** — a synced sqlite or Postgres data file is corruption waiting for a clock skew; stores replicate through S32.
**Speed & efficiency.** Arbitration decided at the brain in ≤50ms from RMS plus last-interaction ·
one answering device, others silent · capability registry so routing stops probing.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| reasoned | a real double-wake test with two edges running | 3 / 4 / 2 d | — | 0 | arbitration behind a flag → today's behaviour |

**Floor**
- [x] 12.F1 Device profiles and edge-lite clients. *gate:* `test_edge_lite.py`,
      `test_phase6_multidevice.py`
- [x] 12.F2 Reconnect without losing the brain link. *gate:* `test_edge_reconnect.py`
- [x] 12.F3 **One answering device.** Arbitration when two edges hear the same wake word — nearest or
      loudest wins, others stay silent. *gate:* `test_device_handoff.py` extended [double-wake]
      *done 2026-09-12:* one sentence, two microphones, two turns, Afon talking over himself from
      two speakers. `_claim_wake` gives the turn to the better-placed device and tells the others
      the turn is over, so they stop listening and say nothing. `wake_score` was added to the wire
      as an optional field with a default, so an older edge still talks to a newer brain: with
      every device reporting zero this is first-arrival, which is a fair proxy for nearest because
      the closer microphone usually finishes transcribing first.
      Deliberately not a 300ms arbitration window: that taxes every turn of a one-device day to
      fix a problem that only exists in a room with two edges in it.

**Raise**
- [ ] 12.R1 Session follows the owner between devices (depends on S07.F1).
      *gate:* `test_session_identity.py` [cross-device]
- [ ] 12.R2 Per-device capability advertisement — the brain knows which device has a camera, a
      speaker, a screen. *gate:* new `test_device_capabilities.py`
- [ ] 12.R3 Graceful degradation to a text-only or push-only device.
      *gate:* `test_edge_lite.py` extended.
- [ ] 12.R4 **The wire is versioned.** `src/afon/shared/protocol.py` carries the edge↔brain message
      shapes; nothing asserts that a brain and an edge of different vintages agree, which is the one
      place contract testing genuinely pays here (three boundaries, deployed separately).
      *gate:* new `test_protocol_contract.py` — every message type round-trips, unknown fields are
      tolerated, and a version bump is required when a required field changes.

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

**Stack.** Today: ntfy push plus Telegram, with quiet hours and repeat suppression. **Add:** an
acknowledgement channel — ntfy action buttons or a Telegram callback — because "delivered vs seen"
is the floor gap and neither transport reports it by default.
**Declined:** FCM/APNs. They need an app, certificates and a store presence; ntfy already reaches the
same phone through the same lock screen.

**Verified by.** A delivery matrix per channel and urgency · dedup regression · escalation with a
stop condition · a week of owner-graded interruptions.

**Options weighed.** *Build:* ntfy + Telegram (today) · FCM/APNs · Gotify → **keep ntfy**, and add
its action buttons for acknowledgement. FCM/APNs need an app, certificates and a store presence to
reach the same lock screen. *Test:* a delivery matrix per channel and urgency · ack tracking ·
suppression replay → **all three.**
**Open-source base.** ntfy (Apache-2.0 / GPL-2.0 dual — confirm at adoption), self-hostable.
**Speed & efficiency.** One sender, one path (the duplicate-delivery lesson) · non-urgent items batch
into the next brief instead of interrupting · push p95 ≤3s.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| reasoned | a week of delivered-vs-seen-vs-acted data | 2 / 3 / 1 d | 5 min (enable actions) | 0 | ack channel is additive |

**Floor**
- [x] 13.F1 Push + Telegram delivery with quiet hours and suppression.
      *gate:* `test_proactive_suppression.py`, `test_proactive_windows.py`
- [x] 13.F2 No duplicate delivery — one sender, one path. *gate:* `test_acknowledgements.py`
- [x] 13.F3 **Acknowledgement tracking.** Afon knows delivered vs seen vs acted-on, and an urgent
      item he never acknowledged is offered back exactly once.
      *done 2026-09-12:* `src/afon/brain/delivery.py`. A push is only ever `delivered` — ntfy reports
      nothing about eyes, so inferring "seen" from a successful POST would be the same overclaim as a
      status page that shows green because it never asked. Exactly one inference is allowed and it is
      named in the code: a line spoken into a live session and answered inside the reaction window was
      heard. A dismissal marks *seen* but not *acted*. The second chance is spent when the re-raise is
      **delivered**, not when it is generated, because quiet hours and the daily budget hold signals
      after a source produces them — spending it at generation would burn his one second chance on a
      message he never heard. A re-raise is never recorded as a fresh delivery: it is urgent by
      construction and would come due for its own re-raise forever.
      *gate:* new `test_delivery_ledger.py` — an unacknowledged urgent item is re-raised once and only
      once. **Not** `test_acknowledgements.py`, which this task named until 2026-09-12: that file is
      about the phrasing of spoken acks ("Right away, sir") and shares nothing with delivery state but
      the word. Extending it would have buried a new capability inside an unrelated test.

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

**Stack.** Today: an in-process signal engine with urgency, value, budget, quiet hours and
learned ignore-penalties. **Add:** nothing.
**Declined:** LightGBM plus a feature store. At one user and a few hundred decisions, a threshold
model that can be *explained back to him* beats a gradient-boosted one he cannot argue with — and
S45 requires the explanation.

**Verified by.** Per-kind reachability (a dormant kind is a bug) · acceptance replay over logged
decisions · timing backtest · behavioural proactivity category.

**Options weighed.** *Build:* threshold engine (today) · LightGBM ranker · a contextual bandit →
**keep thresholds now; a bandit is the correct eventual answer** for interruption timing, and it
needs ≥200 logged outcomes before it beats a hand-set threshold. Explicitly noted as the upgrade
path rather than left implicit. *Test:* per-kind reachability · acceptance replay · timing backtest
→ **all three**; a dormant signal kind is a bug, and only reachability catches it.
**Open-source base.** `river` (BSD-3) for online bandits. The data no longer arrives "when it exists": decision logging is on by default from today (see the owner-defaults table), so the bandit has a corpus in weeks rather than never.
**Speed & efficiency.** Tick ≤200ms · signals computed from the cached context object, never fresh
IO · budget and quiet hours enforced before any scoring work.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | acceptance rate over a real week, per kind | 2 / 5 / 2 d | — | 0 | budget → 0 disables the whole engine |

**Floor**
- [x] 14.F1 Signals, urgency, value, quiet hours, budget, ignore-penalty learning.
      *gate:* `test_phase10_proactive.py`, `test_proactive_learning.py`,
      `test_proactive_thresholds.py`
- [x] 14.F2 Every autonomous act produces a report: what, why, and the reasoning.
      *gate:* `test_proactive_report.py`
- [x] 14.F3 Every signal kind is asserted to be *reachable* — a dormant kind is a bug, not a
      preference (five companion capabilities were dormant for weeks at urgency <0.60).
      The declared set is now **read out of the source** rather than written down in the test, so a
      new kind that nobody exercises fails this file instead of going quietly dormant. Kinds this
      file cannot drive are handed off explicitly, each naming the file that covers it, and a
      hand-off for a kind that no longer exists is also a failure — otherwise the list rots into a
      dumping ground.
      **It found one on its first run.** `kind="calendar"` was registered as a tick source, wired,
      and above threshold, but no fixture had ever driven it — the same state the five companion
      sources were in before anyone noticed. Now driven with a stubbed API.
      *gate:* `test_proactive_thresholds.py` — 19/19, eight kinds fired here, eleven handed off.

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

**Stack.** Today: nothing general — `coaching` and memory resurfacing are the nearest neighbours.
**Add:** log first. A decisions table in the existing sqlite recording context, candidates, choice
and outcome; a ranker only once ~200 real outcomes exist.
**Declined (for now):** Feast, LightGBM, Surprise. A recommender trained on a handful of events is a
random number generator with a confidence interval.

**Verified by.** Reason-cites-its-data assertion · refusal when there is no basis · offline replay of
logged acceptances once the log has depth.

**Options weighed.** *Build:* log-then-rules · implicit ALS collaborative filtering · **LLM as
ranker over a rule-generated candidate set** → **log first, then LLM ranking.** Collaborative
filtering needs other users and there is one. LLM ranking is strong at cold start and costs one call
Afon is already making. *Test:* reason-cites-its-data · cold-start refusal · offline acceptance
replay → **all three.**
**Open-source base.** `river` (BSD-3), shared with S14, for a contextual bandit over logged decisions; `implicit` (MIT) only if a second user ever exists.
**Speed & efficiency.** One memory query plus one LLM call · candidates cached per day · no
recommendation at all when the basis is thin (cheaper *and* more honest).

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| **guess** | 30 logged recommendations and their acceptance rate | 3 / 5 / 2 d | 20 min (name the domains that matter) | 0 | one registry line removes the tool |

**Floor** *(convert zero into one: a single domain, end to end)*
- [x] 15.F1 A `recommend(domain, context)` tool covering **one** domain first — next task to work on
      — sourced from `objectives.py` + `tasks.py` + calendar, with an explicit reason.
      *gate:* `test_recommend.py` — the reason cites the data it used.
      *done 2026-09-13:* `brain/recommend.py` and `recommend_next`, in the existing `dayshape` lazy
      group rather than the core surface. Rules in a stated order — overdue, then due soonest, then
      work serving an objective he is actually driving, then priority — and the reason names both
      the rule and the field it read: "it's overdue by two days", not "this seems important". A
      recommendation whose reason cannot be checked is indistinguishable from a guess, and a guess
      delivered confidently is worse than no answer, because he reorganises his morning around it.
      Domain chosen for the reason the plan gives: next-task is the one where Afon already holds
      the inputs. Restaurants and reading need taste there is no data for.
- [x] 15.F2 Every recommendation is logged with its context, so acceptance can be learned later.
      *gate:* `test_recommend.py` [logged]
      *done 2026-09-13:* the choice, the rule, the reason and everything it was chosen over, logged
      before the answer is spoken, with a slot for whether he actually did it and a breakdown of
      acceptance per rule. **Log first** is the plan's own instruction and it is the right one: a
      recommender trained on a handful of events is a random number generator with a confidence
      interval, so there is no ranker here, only a table that will make one possible — or show it
      is not worth building.
- [x] 15.F3 Afon declines to recommend when he has no basis, instead of inventing taste.
      *gate:* `test_recommend.py` [no-basis case]
      *done 2026-09-13:* `NoBasis` is a first-class answer, not an empty one. When every open item
      has no deadline, no priority and no objective, nothing distinguishes them and the answer says
      so — it does not fall back to the first in list order, which would be presenting the sort
      order of a database table as judgement. Equal priorities are likewise not a reason.

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

**Stack.** Today: a sqlite queue, an in-process worker with restart/expiry semantics, and Notion
as the owner-facing mirror. **Add:** a dependency edge and a deadline-aware ordering — both fit the
existing table.
**Declined:** Celery / Dramatiq / Temporal. Each adds a broker and worker tier to a queue holding a
handful of items; durability already comes from sqlite, and Temporal's real value (long-running
durable workflows across failures) is answered here by restart-expiry plus the approval gate.

**Verified by.** Dependency ordering · restart/expiry · external-pointer health (the Notion id went
stale for weeks) · a multi-day task carried to completion.

**Options weighed.** *Build:* sqlite + in-process worker (today) · Celery + Redis · Temporal →
**keep sqlite.** Durability already comes from the store plus restart-expiry; Temporal's real value
(durable multi-day workflows across process death) is answered here by the same mechanism at a
fraction of the operational cost. *Test:* dependency ordering · restart/expiry · external-pointer
health → **all three**; the Notion id going stale for weeks is the failure that actually happened.
**Open-source base.** `transitions` (MIT) when task state machines get real; `croniter` (MIT) for schedule arithmetic; `SELECT … FOR UPDATE SKIP LOCKED` replaces the single-writer assumption once the data platform lands.
**Speed & efficiency.** Queue ops ≤50ms · the worker wakes on an event, not a poll · progress
narrated at milestones rather than on a timer.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | one multi-day task carried to completion with no reminder | 3 / 4 / 2 d | — | 0 | dependency column is nullable → old ordering |

**Floor**
- [x] 16.F1 Local queue, background worker, restart/expiry semantics.
      *gate:* `test_task_todos.py`, `test_background_tasks.py`, `test_task_restart_expiry.py`
- [x] 16.F2 The autonomous worker defers every outward action to approval.
      *gate:* `test_safety_autonomy.py`
- [x] 16.F3 **The external queue pointer is validated at startup.** The Notion database id went
      stale for weeks unnoticed; a 404 must be loud.
      `notion.queue_pointer_ok()` retrieves the database itself, and `health._check_task_queue()`
      carries it as a component with a proactive signal behind it. The subtlety that let the stale
      pointer survive: **Notion answers 200 with an error object for a bad id**, so a status code is
      not the answer — something came back and it looked fine, while every caller read the failed
      query as "no tasks". Unconfigured stays a non-fault; a configured pointer that does not
      resolve is a fault, and the signal says what it means for his answers rather than just naming
      a component.
      *gate:* `test_phase11_notion.py` — 38/38, including the 404-as-200 case.

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

**Stack.** Today: the scheduler plus a digest assembler over calendar, tasks, news and reminders.
**Add:** nothing.

**Verified by.** Source-down degradation (a failed section, not a failed brief) · empty-section
behaviour · adaptive-timing backtest against the wake log.

**Options weighed.** *Build:* template + LLM compression (today) · full LLM generation · pure
template → **keep the hybrid.** The template owns structure so the brief is consistent and cheap;
the model only compresses. Full generation drifts in length and format daily. *Test:* source-down
degradation · empty-section behaviour · timing backtest → **all three.**
**Open-source base.** Jinja2 (BSD-3, shared with S06) for the brief template; `feedparser` (BSD-2) for any feed the brief pulls.
**Speed & efficiency.** Sections fetched concurrently · ≤1 LLM call per section · news cached from
the overnight fetch · a section with nothing to say is dropped, not padded.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | a month where he never had to check something the brief should have carried | 1 / 3 / 1 d | 10 min (say what belongs in it) | 0 | timing behind a setting; 06:00 restores |

**Floor**
- [x] 17.F1 Digest at 06:00 with calendar, tasks, news, reminders. *gate:* `test_daily_digest.py`
- [x] 17.F2 No duplicate delivery. *gate:* `test_daily_digest.py`, `test_acknowledgements.py`
- [x] 17.F3 A failed source degrades the section, never the brief — and says which source failed.
      Every source was fail-quiet into a log line the owner never sees, so an unreachable task board
      produced a brief that confidently reported nothing past due. **And a total failure returned
      `''`** — which the caller reads as "nothing worth saying", so the most alarming possible
      morning produced silence. A failed source is now named in the body in spoken terms, the
      sections that did answer are still delivered, a total failure says so outright, and
      `last_failed()` exposes the ids. The contract a caller relies on is unchanged: a genuinely
      empty day is still `''`.
      *gate:* `test_daily_digest.py` [source down] — 31/31.

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

**Stack.** Today: pipecat with PortAudio, Silero VAD, a liveness watchdog and device-change
recovery. **Add:** Krisp AEC only if the current echo suppression is measured insufficient — it is
paid and owner-blocked, so measure first.
**Not applicable:** PipeWire/PulseAudio — the edge is Windows.

**Verified by.** A device-churn soak (connect/disconnect AirPods for a day) · echo corpus · barge-in
latency · an inaudible output probe proving the speaker really plays.

**Options weighed.** *Pipeline:* pipecat + PortAudio (today) · raw WebRTC · sounddevice direct →
**keep pipecat.** *AEC:* the current suppression · **speexdsp / webrtc-audio-processing (free)** ·
Krisp SDK (paid) → **measure the free path before buying anything**; the echo had two causes and one
was a real bug, not a missing SDK. *Test:* a device-churn soak · an echo corpus · barge-in latency →
**all three**, plus an inaudible output probe (a speaker that silently stops is the worst failure
here).
**Open-source base.** speexdsp (BSD-3), webrtc-audio-processing (BSD-3), Silero VAD (MIT); **RNNoise (BSD-3)** evaluated against webrtc's suppressor on Windows specifically, since that is the platform the edge actually runs on and the two differ most on non-stationary noise.
**Speed & efficiency.** Fixed 20ms frames · never resample twice on one path · keep one stream open
across turns · barge-in ≤300ms.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | a churn-soak day with zero deaf periods and zero echo | 2 / 4 / 2 d | 15 min (listen check) | 0 (Krisp only if bought) | AEC path behind a setting |

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

**Stack.** Today: httpx against the Composio REST API plus a cached catalogue for the prompt.
**Add:** nothing beyond the cache fix already made.

**Verified by.** Unconnected-account contract (`is_not_configured`, never a plausible sentence) ·
memo-on-success tests · catalogue token accounting · server health with last-success timestamps.

**Options weighed.** *Build:* REST + local catalogue cache (today) · MCP transport · per-vendor
SDKs → **REST plus cache, MCP kept for servers that only speak MCP.** Per-vendor SDKs would
re-implement what Composio exists to provide. *Test:* unconnected-account contract · memo behaviour
· catalogue token accounting → **all three.**
**Open-source base.** composio SDK (Apache-2.0 — confirm), mcp (MIT).
**Speed & efficiency.** Catalogue summary ~200 tokens, not the 4.2k it once cost every turn ·
long-tail lookup runs locally against the cache · context memoised on success only.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | a week of real Composio-backed runs without a manual repair | 1 / 3 / 2 d | — | 0 (free tier) | unset the key → typed not-configured |

**Floor**
- [x] 19.F1 Connect flow, account listing, routing. *gate:* `test_composio_connect.py`,
      `test_composio_accounts.py`, `test_composio_router.py`
- [x] 19.F2 `_context()` memoised on its *values* rather than on success, so a failed lookup with a
      configured user id cached "no active toolkits" for the whole process lifetime, and a
      successful empty one re-hit the network on every call. Memoises on success now (J3.8).
      *gate:* `test_composio_context_cache.py`
- [x] 19.F3 An unconnected account produces `is_not_configured`, never a plausible-sounding failure.
      *gate:* `test_no_result_sentinels.py` extended.
      *done 2026-09-12:* the key check only asked whether Composio was configured. An owner with a
      Composio key but no Slack account got "That didn't go through, sir: no connected account
      found" — a sentence that reads like the send was attempted and failed. It was never
      attempted, and the difference decides what he does next: retry, or go and link the account.
      Refused before the call when the toolkit is known to be unlinked, and the API's own
      "no connected account" answer is mapped too, because the cached list can be stale. An EMPTY
      connection list is never used as evidence: that is what a failed lookup looks like, and
      refusing on it would turn a network blip into "you never connected Slack".

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

**Stack.** Today: httpx per integration with a typed not-configured contract. **Add:** `respx`
fixtures per provider, and **one** retry/timeout policy object instead of per-module constants —
`tenacity` if a library is wanted, but the policy matters more than the library.
**Declined:** Schemathesis. It fuzzes *your* OpenAPI surface; Afon consumes APIs and exposes almost
none, so the useful contract test here is a recorded-response fixture, not a fuzzer.

**Verified by.** Per-provider fixtures · quota and timeout injection · response-shape drift check ·
zero fabricated answers in the behavioural honesty category.

**Options weighed.** *Build:* httpx with **one** policy object · per-module policies (today) · a
gateway process → **one policy object.** A gateway is a second hop and a second thing to restart.
*Test:* recorded response fixtures · a fuzzer · live smoke → **fixtures**; Schemathesis fuzzes an
OpenAPI surface we do not expose, and live smoke belongs in the deploy check, not the gate.
**Open-source base.** httpx (BSD-3), respx (BSD-3), tenacity (Apache-2.0).
**Speed & efficiency.** One pooled client per host · hard timeouts so one slow provider cannot own
the turn · TTL caching for slow-moving data (weather, fx, maps) · quota-aware degradation *before*
failure.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | honesty category = 100 with providers deliberately disabled | 3 / 4 / 2 d | 45 min (keys, rotation, `.env`→pass) | 0–5 | policy object ships with today's values as defaults |

**Floor**
- [x] 20.F1 Typed "not configured" contract across all integrations.
      *gate:* `test_no_result_sentinels.py`, `test_phase11_integrations.py`
- [x] 20.F2 Google OAuth verified live on the VPS. *gate:* `test_new_integrations.py`
- [x] 20.F3 Every integration declares a timeout and a retry policy in one place, not per module.
      *gate:* new `test_api_policy.py`
      *done 2026-09-12:* the timeout was already central; the retry policy was not, because there
      wasn't one. Every integration got exactly one attempt, so a single dropped packet to the
      weather API reached him as "I couldn't reach the weather service" — a sentence describing an
      outage, produced by a hiccup. It is a table rather than a blanket rule because the opposite
      mistake is worse: retrying a slow render turns a thirty-second wait into ninety, retrying a
      GitHub issue files it twice, and retrying a 401 spends quota to be told no again. A 429 is
      retried, being the one 4xx that means "later" rather than "no".
      Fifteen call sites across eleven modules were building their own clients with their own
      numbers, which is what made the old central timeout central by coincidence. All now go
      through the shared helpers, and the gate fails the build if a new one appears. Three
      exemptions, each stated: the LLM client and the edge have their own failover rules, the one
      synchronous embedder call reads the table without being rewritten async, and the setup
      wizard imports nothing from brain/ by design so its number stays at the call site.

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

**Stack.** Today: Google Calendar plus `routines.json` and mode/interruption logic. **Add:** a
greedy time-blocker first.
**Declined until proven necessary:** OR-Tools / PuLP. Constraint solvers earn their keep on
combinatorial problems; this is one person's day with a handful of blocks, and a solver's output that
the owner cannot follow is worse than a greedy plan he can.

**Verified by.** A conflict corpus (double-booked, no travel time, no breaks) · timezone and DST
cases through the faketime shim · adherence log over a month.

**Options weighed.** *Build:* a greedy time-blocker · OR-Tools CP-SAT · let the model propose →
**greedy first.** A solver earns its keep on combinatorial problems; this is one person's day, and a
plan he cannot follow is worse than a simple one he can. Model-proposed schedules are
non-deterministic, which is disqualifying for something that writes to his calendar. *Test:* a
conflict corpus · DST/timezone cases through the faketime shim · an adherence log → **all three.**
**Open-source base.** icalendar (BSD-3); OR-Tools (Apache-2.0) held in reserve.
**Speed & efficiency.** Conflict detection in-process with no LLM call · schedule reasoning ≤300ms ·
routines read from one file, not recomputed.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| reasoned | a month of adherence data against the real routines | 3 / 5 / 2 d | **30 min (write the real routines)** | 0 | proposals are suggestions; nothing writes without confirm |

**Floor**
- [x] 21.F1 **`routines.json` holds the owner's real routines.** It is effectively empty today — the
      language slot and the evening review are absent, so everything downstream reasons about a day
      that does not exist. *gate:* `test_day_shape.py` [1]-[4].
      *gate moved 2026-09-12:* the plan named `test_scheduler_brain.py`, which is about the reminder
      scheduler firing round the clock. Whether a routine file describes a real day is a different
      question that happens to live near it, and hanging both on one file would have meant a gate
      that passes for the wrong reason.
      *done 2026-09-12:* the task sat at "owner: 30 minutes to write the real routines" for a month
      because a blank file is the whole obstacle — nobody composes their own schedule from nothing.
      `routine_draft.py` reads fourteen days of the presence database and drafts windowed routines
      with the evidence attached ("active on 8 of the last 10 days"), so the owner corrects rather
      than composes. It refuses to call anything a habit on fewer than three days or half the
      observed days, because a routine drafted from two afternoons is worse than none: it looks
      like knowledge. Drafting never installs — `propose_routines` writes to a separate file and
      `adopt_routines` is confirm-gated, since a system that learns your habits and starts nagging
      you about them unasked is the thing people uninstall. `validate()` refuses to install what
      the live loader silently swallows: a typo'd window does not fail today, it just means that
      routine never fires again and nothing ever says so.
- [x] 21.F2 Conflict detection: double-booked, no-travel-time, no-breaks are detected and named.
      *gate:* `test_day_shape.py` [5]-[8].
      *gate moved 2026-09-12:* `test_calendar_dates.py` is about formatting a date into speech, not
      about whether a day holds together. Same reason as 21.F1.
      *done 2026-09-12:* `schedule.py` — arithmetic on a sorted list, no model consulted, which the
      gate asserts by reading the source. A non-deterministic answer to "am I double-booked" is
      worse than no answer: the owner cannot tell a hallucinated clash from a real one, and
      checking costs him the time the check was meant to save. It says nothing about all-day
      events, unrecorded locations or touching-but-not-overlapping events, because a checker that
      cries wolf gets switched off. `raw_events` was added to `calendar.py` so the checker reads
      objects; re-parsing `list_events`' spoken sentence would have been a second date parser to
      keep in step with the first.
- [x] 21.F3 Date and time handling is robust across timezones and phrasings.
      *gate:* `check_date_robustness.py` (now in `run_all_tests.py`), `test_weather_when.py`
      *done 2026-09-12:* the replay existed but covered only dates, and was reachable only by
      typing its name — a gate nobody runs is not a gate. Added a timezone axis over UTC, a large
      negative offset, a large positive one and a half-hour offset, and registered it in the suite.
      It found a real defect on the first run: `_fmt_when` resolved "today" from the process clock,
      so with the brain on a UTC VPS and the owner in Europe/Berlin, every event between midnight
      and 02:00 his time was spoken as "tomorrow". Correct for the server, wrong for the person
      being spoken to, two hours a day, every day — and invisible to a suite that only ever ran in
      the afternoon from the same machine as the reader. "Today" now means the owner's day.
      The replay runs both axes through a thread pool: ten minutes serially is long enough that a
      gate gets skipped, and the cost is entirely process startup.

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

**Stack.** Today: backup/checkpoint/phoenix/ragnarok protocols, systemd on the VPS and Task
Scheduler on the laptop, plus daily freshness restarts. **Add:** a *scheduled restore* drill — an
untested backup is a rumour.
**Not applicable:** Kubernetes probes. There is no cluster; systemd `Restart=` and the watchdogs are
the same mechanism at this scale.

**Verified by.** Restore drill on a schedule · config rollback · state-corruption repair · a blind
three-failure drill.

**Options weighed.** *Build:* protocols + systemd (today) · **restic/borg encrypted snapshots** ·
Kubernetes probes → **keep the protocols and add restic.** The real gap is that backups are local:
a dead disk currently takes the backups with it. K8s probes are the same mechanism as systemd
`Restart=` at this scale. *Test:* a scheduled **restore** drill · fault injection · a blind
multi-failure drill → **all three**; an untested backup is a rumour.
**Open-source base.** restic (BSD-2) or borg (BSD-3) — restic for object-storage targets.
**Speed & efficiency.** Incremental, deduplicated snapshots · restore ≤5 min · drills scheduled so
the cost is known rather than discovered.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | a timed restore from an off-site snapshot into an empty environment | 3 / 3 / 2 d | 15 min (storage target + passphrase) | 1–3 | backups are additive; nothing depends on them until restore |

**Floor**
- [x] 22.F1 Backup, checkpoint and restore protocols with drills.
      *gate:* `test_backup_restore.py`, `test_protocol_drills.py`
- [x] 22.F2 Daily freshness restarts on both hosts. *gate:* `test_uptime_watch.py`
- [x] 22.F3 Self-repair paths for known failure shapes. *gate:* `test_self_repair.py`
- [x] 22.F4 Backup **restore** is verified on a schedule, not just backup creation — an untested
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

**Stack.** Today: a systemd **user** unit with linger, a ticker for recurring work, and an ntfy
spool that holds messages while down. **Add:** an outside-in uptime probe, because self-reported
uptime is not uptime.
**Declined:** multi-region Kubernetes. One owner, one VPS, one laptop — the redundancy that matters
here is degraded-mode behaviour (S32), not more regions.

**Verified by.** External probe · degraded-mode answers with no LLM · announced maintenance windows ·
a 30-day log with every interruption explained.

**Options weighed.** *Build:* systemd + ticker (today) · **an external uptime probe** · multi-region
→ **add the external probe.** Self-reported uptime is not uptime; multi-region is a second host to
keep in sync for one owner. *Test:* outside-in probing · degraded-mode answers · a 30-day log →
**all three.**
**Open-source base.** Uptime Kuma (MIT) or Healthchecks (BSD-3), both self-hostable; either free
tier is fine.
**Speed & efficiency.** Cold reach-to-first-word ≤4s · the ticker holds and forwards rather than
retrying blindly · degraded mode answers time, reminders and status with no LLM at all.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | 30 days of external probe data with every gap explained | 2 / 3 / 1 d | 10 min (set up the probe) | 0 | the probe is external — no code risk |

**Floor**
- [x] 23.F1 Brain runs 24/7 on the VPS with linger and a ticker.
      *gate:* `test_uptime_watch.py`, `test_warm_standby.py`
- [x] 23.F2 Messages arriving while down are held and forwarded. *gate:* `test_error_spool.py`
- [x] 23.F3 **The park switch honours itself on every host and role** — no autostart while
      `~/.afon/MAINTENANCE` exists. *gate:* `test_maintenance_lock.py`
- [x] 23.F4 Unpark is as deliberate as park: a documented checklist that verifies each floor before
      the lock is removed — `scripts/preflight.sh --unpark`, which reads the Wave-0 floors from this
      document rather than a copy, refuses on any unmet one, and never removes the lock itself.
      *gate:* `test_unpark_gate.py`

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

**Stack.** Today: a sqlite triple store with semantic recall over Jina embeddings. **Add:**
temporal validity ("true from / true until") on facts — the actual gap.
**Declined:** Neo4j or Postgres+AGE. A graph server for a store of a few thousand edges, on a host
that must also run the brain, buys query power nothing is currently asking for and costs a daemon,
a backup path and a migration.

**Verified by.** An entity-resolution corpus · belief-revision cases · provenance on every answer ·
"what's going on with X" as a scored behavioural scenario.

**Options weighed.** *Build:* sqlite triple store (today) · Neo4j · rdflib/SPARQL → **keep sqlite
and add temporal validity.** A graph server for a few thousand edges buys query power nothing is
asking for and costs a daemon, a backup path and a migration; the actual gap is that facts have no
"true until". *Test:* an entity-resolution corpus · belief-revision cases · provenance assertions →
**all three.**
**Open-source base.** `networkx` (BSD-3) for graph algorithms over the entity store; `rapidfuzz` (MIT) for entity resolution; `pgvector` for embedding-backed entity lookup once the data platform lands; rdflib (BSD-3) only if SPARQL is ever genuinely needed. **Graphiti (Apache-2.0) is read, not adopted** — its bi-temporal invalidation model is worth copying, its Neo4j dependency is not.
**Speed & efficiency.** Consultation ≤50ms in-process, no LLM · indexed by entity · the model is
consulted in the prompt path so answers are shaped by it rather than re-derived.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| reasoned | a scored "what's going on with X" scenario in the behavioural suite | 3 / 5 / 2 d | — | 0 | temporal columns are nullable → today's semantics |

**Floor**
- [x] 24.F1 Entity resolution: one person, one node, regardless of spelling or channel
      (`resolve_contact` covers part of this today).
      `shared/entities.py` canonicalises a name — transliteration, accents, titles, edge
      punctuation — so "Вазген" and "Vazgen" key identically, and a channel (email, @handle,
      phone) resolves to the person who owns it. Spelling variants that transliteration cannot
      settle ("Vazghen" vs "Vazgen") are merged at **lookup** time by a high-threshold difflib
      match, not by rewriting one into the other: a normaliser does not get to decide a rename,
      and merging two different people sends an outward message to the wrong human. The graph
      re-keys existing rows once on open — without that, changing the key orphans every accented
      or Cyrillic node, which is worse than not changing it.
      *gate:* `test_proper_nouns.py` 22/22, `test_contacts.py` 41/41, `test_graph_memory.py`
      33/33 (migration + idempotence).
- [x] 24.F2 The world model states what it does *not* know about an entity when asked.
      `what_i_dont_know()` answers with both halves. The gaps come from a **declared** facet list
      per entity kind rather than from whatever happens to be stored — a gap you can only notice
      by already knowing what to look for is a gap nobody notices, which is the same reasoning as
      31.F4's declared loop table. Until now an entity he held two facts about and one he held
      twenty about sounded equally complete.
      *gate:* `test_world_model.py` extended. 36/36.

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

**Stack.** Today: markdown persona and operating rules, an affect model feeding TTS prosody, and
relationship memory. **Add:** nothing.
**Declined:** NeMo Guardrails. Refusals here are deterministic and live in code (S44); a second
policy engine would create two answers to "may I", and the disagreement would surface as
inconsistent behaviour rather than an error.

**Verified by.** Persona-parity gate between template and shipped persona · register-per-channel
tests · voice grading · behavioural conversation category.

**Options weighed.** *Build:* prompt + affect model (today) · fine-tuning a small model · a
guardrails library → **keep the prompt.** Fine-tuning locks the persona to one provider, costs a
retrain on every change, and drifts silently; guardrails would create a second policy source (see
S44). *Test:* persona-parity between template and shipped file · register-per-channel · voice
grading → **all three.**
**Open-source base.** none, and deliberately. Persona is a prompt, a voice mode and a set of refusals in code. A library here would be a second authority over how he speaks, which is exactly the overlap the efficiency clause forbids.
**Speed & efficiency.** Persona ≤900 tokens and **byte-stable**, which is what makes the provider
cache hit · affect derived in-process, never a second model call.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | conversation category ≥95 with no answer he would call padded | 1 / 3 / 2 d | 20 min (mark tone on ten real answers) | 0 | the persona is a file; revert it |

**Floor**
- [x] 25.F1 Persona, operating rules and affect→TTS wired and at template parity.
      *gate:* `test_affect_voice.py`, `test_affect_tts_edge.py`, `test_skill_docs_resolve.py`
- [x] 25.F2 Relationship memory informs address and familiarity. *gate:* `test_relational.py`
- [x] 25.F3 Persona is one file, not several — no second definition can drift (the fleet's
      IDENTITY.md/SOUL.md lesson). *gate:* `test_skill_docs_resolve.py` [single source]
      *done 2026-09-12:* the fleet's failure had already appeared here in miniature. `context.py`
      carried a hardcoded copy of the operating rules as a fallback, and it had DRIFTED — the file
      names the destructive verbs, the copy did not — so a brain that fell back was governed by a
      rulebook the owner had never seen and could not edit. That copy is gone; the fallback now says
      the rules could not be read, which is a status message rather than a second rulebook. The
      spoken-output rule had three homes (persona, operating rules, the voice skill); it keeps one.
      The persona owns how he sounds, the rules own what he does, and the voice skill keeps only the
      technique neither has room for — reading a URL aloud, rounding numbers for speech. The gate
      pins all of it, including that the assembled prompt carries exactly one persona header.
      `persona.example.md` stays a copy on purpose, since it is the fork-me template and is never
      loaded; that is asserted rather than merely tolerated.

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

**Stack.** Today: OpenCV capture, a VLM for description, activity and presence signals. **Add:** a
small local audio-scene classifier (speech / music / TV / silence) — the speaker gate needs it and
nothing else provides it.
**Declined:** YOLO / Detectron2. Object detection answers a question nobody has asked; the VLM covers
"what am I looking at" at far lower operational cost.

**Verified by.** A fixture day of frames and clips · freshness stamps (stale perception is never
presented as current) · fusion assertions · anomaly cases.

**Options weighed.** *Vision:* VLM on demand (today) · YOLO/Detectron local · CLIP scene
embeddings → **keep the VLM**; object detection answers a question nobody has asked and costs a model
to maintain. *Audio scene:* none (today) · **YAMNet-class classifier** · a heuristic on RMS/spectral
flatness → **YAMNet-class**, because the speaker gate needs "is that the television" and a heuristic
cannot tell speech from broadcast speech. *Test:* a fixture day of frames and clips · freshness
assertions · fusion → **all three.**
**Open-source base.** YAMNet (Apache-2.0), OpenCV (Apache-2.0).
**Speed & efficiency.** One `perceive()` snapshot per turn, cached 60s · one vision call maximum ·
audio classification runs on the frame already captured for VAD, not a second capture.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| reasoned | fixture-day accuracy for "what is happening here" | 3 / 5 / 2 d | — | 0 | `perceive()` falls back to today's separate calls |

**Floor**
- [x] 26.F1 Vision on demand with a VLM path. *gate:* `test_vision.py`, `test_screenshot_transport.py`
- [x] 26.F2 A single `perceive()` snapshot that returns presence + visual + activity together, so
      callers stop assembling their own.
      `src/afon/brain/perception.py`: presence, visual, activity and meeting as four `Fact`s.
      **`perceive()` never captures** — it reads cached facts, because it runs on every turn inside
      S27's 30ms budget and because opening the camera here is the path that has hard-segfaulted on
      this laptop's device enumeration. The camera tool feeds `record_visual()` on its way past, and
      only an `available` verdict is recorded: a busy webcam is not evidence about the room.
      *gate:* `test_perception_snapshot.py` — 36/36, including the structural check that the
      snapshot reaches for no camera, and 0.016ms measured.
- [x] 26.F3 Every perception carries a freshness stamp; stale perception is never presented as
      current.
      Each fact carries its own max age — a room empties in seconds, a foreground app does not — and
      `describe()` states a stale fact with its age rather than bare. Two distinctions are held by
      test: never-sensed counts as stale (reading "I have not looked" as "nobody is there" is the
      same bug), and sensed-and-empty stays distinguishable from never-sensed.
      *gate:* `test_perception_snapshot.py` [staleness]

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

**Stack.** Today: presence, modes, activity and location tools, each consulted separately. **Add:**
one assembled context object per turn — the gap is a shape, not a library.

**Verified by.** Context-object assertions (assembled once, passed down) · disclosure when the
assumption changes the answer · rapid-switch and false-positive rates.

**Options weighed.** *Build:* one assembled context object · a rules engine · a learned classifier
→ **the object.** The gap here is a shape, not intelligence: four sources exist and each caller
re-derives its own view. A learned context classifier without labelled context data would be
guessing with extra steps. *Test:* assembly assertions · disclosure ("I assumed you were at the
desk") · rapid-switch cases → **all three.**
**Open-source base.** `astral` (Apache-2.0) for sun position and daylight, the cheapest real circadian signal there is; `holidays` (MIT) for calendar context; `geopy` (MIT) against **Nominatim / OpenStreetMap (ODbL)** for reverse geocoding. **This retires the Google Maps key as a blocker** — free, no credential, no quota, and it is the owner's own machine asking.
**Speed & efficiency.** ≤30ms, in-process, **no LLM call ever** · assembled once per turn and passed
down · cached for the tick so proactivity reads the same world the answer did.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| reasoned | a week of interruption-appropriateness graded by him | 2 / 4 / 2 d | — | 0 | callers keep their existing accessors during migration |

**Floor**
- [x] 27.F1 One context object — location, presence, activity, mode, time-of-day, calendar state —
      assembled once per turn and passed down, not re-derived per tool.
      `src/afon/brain/situation.py`: a frozen `Situation`, assembled once inside both turn entry
      points and published on a contextvar, the same shape `turn_trace` already uses — so a helper
      deep in the tool path reads the world the answer read without every signature growing a
      parameter. Each source is guarded individually: a dead presence store degrades one field to
      `unknown` with the source named, never the object. Commute beats a stale desk sample, because
      the last sample *is* the desk he left.
      *gate:* `test_context_object.py` — 35/35, including the structural check that every turn
      entry point wraps (a third entry point is how this stops working) and that assembly imports
      no model. Measured 0.006ms against a 30ms budget.
- [x] 27.F2 The assumed context is stated when it changes the answer.
      Disclosure is owed only when the assumption moved the answer, so it is not every field —
      "it is Tuesday afternoon" explains nothing, "I assumed you had stepped away" explains a held
      message. Ordered most-consequential-first, so lockdown is disclosed rather than focus time
      when both hold.
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

**Stack.** Today: the Home Assistant REST API behind a confirm-gated tool. **Add:** nothing — HA
*is* the abstraction layer, and MQTT/Zigbee2MQTT/Z-Wave belong under it, not beside it. Adding a
second path to the same bulbs is how two sources of truth about a light switch appear.

**Verified by.** Read-back verification (report the observed state, not the command sent) ·
offline-device handling · confirm gating on anything that affects other people · a week of scenes.

**Options weighed.** *Build:* HA REST (today) · **HA WebSocket** · direct MQTT/Zigbee → **REST plus
the WebSocket.** The WS subscription gives state push, which is what read-back verification and
presence want without polling. Direct MQTT would create a second source of truth about a light
switch. *Test:* read-back verification · offline-device handling · confirm gating → **all three**;
the interesting failure is "reported success, nothing moved".
**Open-source base.** Home Assistant (Apache-2.0) — the abstraction layer itself, and the reason no
Zigbee/Z-Wave code belongs here.
**Speed & efficiency.** Subscribe once instead of polling · command→verified ≤3s · scenes batch
device calls concurrently.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| reasoned (code written, never run live) | one real entity toggled and read back | 2 / 4 / 2 d | **5 min (the token)** | 0 | unset the token → dark, exactly as today |

**Floor**
- [ ] 28.F1 **Home Assistant token** installed and the connection verified live. This is the single
      blocker for the whole system. *gate:* `test_new_integrations.py` — a real entity listed.
- [x] 28.F2 Read-back verification: Afon reports the state he *observed*, not the command he sent.
      *gate:* new `test_smarthome_verify.py`
      *done 2026-09-12:* Home Assistant answers 200 to a service call for a device that is
      unplugged, out of battery or simply not listening, and returns an empty change list. The old
      reply was "Done, sir — light.turn_on on kitchen. 0 entity change(s)": a claim about the world
      assembled from the absence of an error, and he walks into a dark kitchen having been told the
      light is on. Afon now reports the state that came back, reads the entity when nothing
      changed, says so when the device did not do it, and says he cannot confirm when he cannot
      read it back. A service with no unambiguous target state — `toggle` — is reported and never
      graded, because grading it would mean claiming a result he cannot check.
- [x] 28.F3 Anything that affects other people (lights in shared rooms, locks, heating) is in the
      confirm tier. *gate:* `test_confirm_tier_documented.py`, `test_smarthome_verify.py` [7]-[9]
      *done 2026-09-12:* heating and hot water joined locks, alarms, covers and garage doors.
      Everyone in the building lives in the temperature he sets, and unlike a lamp nobody else can
      undo it from the wall. Shared rooms are configured (`AFON_HA_SHARED_AREAS`), never guessed:
      only he knows which of his rooms other people live in, and a list invented for him would gate
      the wrong lamps and get ignored. His own lamp still flows without friction, because gating
      everything is how an owner learns to say yes without reading. The gate also asserts the
      policy layer's copy of the sensitive-domain set matches the tool's, so the two cannot drift.

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

**Stack.** Today: macros, routines, a skills runtime, the scheduler and human approval gates.
**Add:** dry-run and a declared failure policy per automation.
**Declined:** Temporal / Prefect / Airflow. These are seconds-long automations on one host; durability
comes from sqlite and the approval ledger. A workflow server is a second brain to operate, monitor
and recover — and S31 would then have to watch it.

**Verified by.** The full operator matrix in both directions · dry-run output · failure-policy
behaviour · a month of owner-authored automations with a monthly report.

**Options weighed.** *Build:* macros + scheduler + approvals (today) · Node-RED · Temporal →
**keep it.** Node-RED is a second UI, a second runtime and a second source of truth for automations;
Temporal is a workflow server to operate for automations that run in seconds. *Test:* the full
operator matrix in both directions · dry-run · failure-policy behaviour → **all three**; the operator
bug (H2.12) is exactly what a partial matrix misses.
**Open-source base.** `json-logic-py` (MIT) so a routine's conditions are declarative data the owner can read and Afon can validate, instead of Python branches nobody audits; `croniter` (MIT) shared with S16. n8n and Temporal stay declined — see the standing table.
**Speed & efficiency.** Rule evaluation ≤20ms · scheduler drift ≤5s · dry-run costs nothing because
it is the same evaluation without the effect.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | a month of owner-authored automations with zero misfires | 2 / 4 / 2 d | 30 min (author ten) | 0 | automations disable individually; the engine has a global off |

**Floor**
- [x] 29.F1 The parsed comparison operator is actually applied — `== 0` and `!= 0` no longer mean
      the same thing (H2.12, fixed). *gate:* `test_if_then_operator.py`
- [x] 29.F2 Macro guard against runaway/self-triggering chains.
      *gate:* `test_singleton_and_macro_guard.py`
- [x] 29.F3 Human approval gates for outward actions. *gate:* `test_approvals.py`
- [x] 29.F4 Every automation is listable with its last run, next run, and outcome.
      *gate:* `test_skill_runtime.py` extended.
      *done 2026-09-12:* three kinds of automation each knew only about itself — loops had a tick
      registry, reminders had a next fire time, macros recorded nothing at all. So "what do you run
      for me, and is any of it broken" had no answer: the information existed and nobody could
      reach it, which is the same failure as an automation that silently stopped. `automations.py`
      merges all three into one shape, worst first, and macros now record their own runs.
      It never leaves a blank where it does not know: a macro that has never run says so, and a
      reminder whose last run nobody records says "not recorded" rather than showing an empty cell
      the owner reads as "never". And it never implies a next run that does not exist — a macro
      fires when he asks, and a guessed time would turn a list he checks into one he stops
      trusting.

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

**Stack.** Today: sqlite for every layer (facts, journal, vectors, graph, tasks) with Jina
embeddings for L5, behind one recall facade. **Add:** the data platform — Postgres with pgvector as
the brain's single store, a Valkey hot layer, sqlite retained on the laptop.
**Reversed 2026-09-11: Postgres is adopted.** The August decline rested on one specific objection —
a migration running straight through the laptop/VPS divergence. Task 30.F1 closed that divergence
(one state root per host, union migration executed, invariant held by
`test_memory_single_origin.py`), so the objection is spent and the decline goes with it. What it
buys: concurrent readers while the brain writes, local embeddings through pgvector instead of a
remote API on the answer path, a real claim primitive for S16 and S48, and logical replication for
S32. The migration runs through the 30.F2 facade, per store, dual-write then read-flip, rollback by
environment variable. Full terms in **The data platform**.
**Still declined:** Neo4j, Qdrant, MinIO, LlamaIndex, Mem0, Zep — a graph is an edge table, vectors
are pgvector, blobs are a path in a row, and a memory framework over a store that works is a
rewrite with no new capability.

**Verified by.** Precision@3 over a query corpus · contradiction handling · retention/rotation ·
`profile_memory_recall.py` against the budget · cross-host consistency.

**Options weighed.** *Build:* sqlite + a recall facade · Postgres + pgvector · LlamaIndex →
**both, in that order.** The facade shipped first and is what makes the second safe: callers stopped
touching stores, so the backend became swappable. LlamaIndex remains declined as a framework over a
store that already works. *Vector path:* numpy scan (today) · **sqlite-vec** · faiss → **sqlite-vec if the scan becomes
the bottleneck**, because it keeps one file and one process. *Test:* precision@3 over a query corpus
· contradiction cases · a latency profile → **all three.**
**Open-source base.** PostgreSQL (PostgreSQL licence) + pgvector (PostgreSQL licence) as the brain's store; `psycopg` 3 (LGPL-3.0) as the client; Valkey (BSD-3) for the hot layer; sqlite-vec (MIT/Apache-2.0) retained for the laptop processes, which never speak to Postgres. A local embedding model replaces the Jina API once pgvector lands, which takes the answer path off the network.
**Speed & efficiency.** One fan-out with **per-store timeouts** so a slow layer cannot own recall ·
embed once per turn and reuse · cache the digest · target p95 ≤300ms.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | recall p95 and precision@3 on the corpus, before/after the facade | 5 / 6 / 3 d | **15 min (merge-direction decision)** | 0 | the facade wraps existing stores; callers revert per store |

**Floor**
- [x] 30.F1 **Resolve the two-host split.** Laptop and VPS hold divergent learned state; he is
      running on a week-old memory of the owner (TODO I6 and the rename findings). Decide the merge
      direction, execute it, record the decision.
      **The split was not a sync failure.** `afon_*.sqlite`, `memory/learned` and `memory/journal`
      resolved as `Path(__file__).parents[3]` — *wherever the code was unpacked* — while patterns,
      the relationship model and the voiceprint resolved from `~/.afon`. `deploy_vps.sh` untars the
      source tree into the brain host's own directory, so the repo root there is a different
      directory from the laptop's and each host kept its own half under the same names. Nothing
      detected it: both paths resolve, both stores open, each host answers confidently. A sync
      would not have fixed a store keyed on the location of the code.
      **Decision.** One state root per host — `AFON_STATE_DIR`, default `~/.afon`, resolved in
      `shared/paths.py` and nowhere else (28 modules had spelled `Path.home() / ".afon"` privately,
      so the whole brain could not be pointed at another disk). The repo keeps code and the
      hand-authored overlay; it keeps no state, so a deploy cannot carry memory between hosts.
      Where two hosts already diverged: the brain host wins for **derived** stores (vectors,
      presence, coaching — they regenerate), and learned facts and journals are **unioned**, never
      picked between, because a fact only one host was ever told is not stale, it is the only copy.
      `scripts/merge_memory.py` does the union through `remember()`, so 30.F3's provenance rules
      apply and a fact the owner stated outright on one host stops being recorded as the other
      host's guess. Migration MOVES rather than copies — a copy is two stores, which is the bug —
      and refuses to overwrite a destination that holds data. One exception, found the hard way an
      hour after it was written: an **empty** database left behind by a first boot on the new path
      is an artifact, not a store, and blocking on it would strand the real data forever on every
      host that had been started once.
      Executed on the laptop: 10 locations moved, 35 journal days unioned, 162 learned facts under
      one root, `second_origins()` empty.
      *gate:* new `test_memory_single_origin.py` — 46/46, ten plants (a store reverting to the repo
      root, a copy instead of a move, an overwrite, a private state root, a journal merge that
      truncates). The VPS runs the same migration on its next brain start.
- [x] 30.F2 **One recall facade.** Seven stores, five holding their own SQLite handle, no unified
      entry point (TODO K3 / J3.3). A single `recall()` fans out and merges; callers stop touching
      stores directly.
      Two halves. `brain/dbconn.py` is now the only module that opens a memory database — the five
      stores had five independent guesses at the arguments, three of them inheriting sqlite's
      5-second busy timeout by accident rather than decision. `brain/recall.py` is the single
      entry point: it delegates to the existing `fused_recall` rather than reimplementing it, adds
      the **tasks layer** (which is usually what "what's going on with X" means and was not
      reachable at all), dedups across stores, and gives each layer a budget — **local layers 1s,
      network layers 4s**, because auto-recall runs the local ones on every turn and one flat
      backstop generous enough for Jina would have put five seconds on a greeting.
      *gate:* `test_layering.py` extended — no module outside the facade opens a memory
      connection. 29/29, plus the behavioural half in `test_memory_behavioral.py` 23/23 (a
      structural grep for the budget passed a planted rename; behaviour caught it).
- [x] 30.F3 Every stored fact carries source, timestamp and confidence.
      The store mixes three different kinds of claim — what the owner said outright, what the
      background reviewer inferred from a conversation, and what the pattern detector guessed from
      behaviour — and once written they were indistinguishable, so a guess was recalled with the
      authority of a statement. A closed `SOURCES` vocabulary (owner · tool · inferred · pattern ·
      legacy) now rides in the frontmatter with a confidence, and every `.remember()` call site
      names its origin — checked structurally, because a default parameter means a new caller
      silently attributes its facts to whatever the default happens to be (36.F5's lesson).
      Notes written before this existed are named **legacy** rather than backfilled with a
      confidence nobody measured, and a stated fact now upgrades one that had only been inferred.
      *gate:* `test_memory_salience.py` extended. 31/31, seven plants.

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
- [ ] 30.R5 **Schema and dual-write.** Postgres schema for every brain store; the 30.F2 facade
      writes both backends behind `AFON_STORE_BACKEND`. sqlite keeps being written throughout, which
      is what makes the rollback real rather than theoretical.
      *gate:* new `test_store_parity.py` — every facade query returns identical rows from both
      backends, including the empty and contradiction cases.
- [ ] 30.R6 **Backfill and read-flip, one store at a time.** Backfill, compare, then move reads
      per store rather than all at once, so a bad flip costs one store and not the memory.
      *gate:* `test_store_parity.py` [backfill] + recall p95 ≤300ms held on the real corpus.
- [ ] 30.R7 **pgvector replaces the embeddings API on the answer path.** A local model and a vector
      index in the same database as the rows, so semantic recall stops depending on a network hop
      and a bill.
      *gate:* new `test_pgvector_recall.py` — precision@3 no worse than the Jina baseline on the
      query corpus, and recall works with the network down.
- [ ] 30.R8 **The laptop stays on sqlite, proven, not assumed.** edge and pc_agent never open a
      Postgres connection, and their suites pass with the brain host unreachable.
      *gate:* new `test_offline_edge.py` — laptop processes green with Postgres stopped; a Postgres
      import anywhere under `src/afon/edge/` fails the layering test.
- [ ] 30.R9 **The cache is optional, proven, not assumed.** Valkey carries the turn digest, the
      distributed lock, the presence key and the rate-limit counters — and nothing durable.
      *gate:* new `test_cache_optional.py` — the full suite passes with the cache stopped, and no
      value read back after a cold start was only ever written to the cache.

**Elite**
- [ ] 30.E1 Memory behavioural category ≥95 with recall latency inside budget on the VPS.
      *gate:* `test_memory_behavioral.py` + `profile_memory_recall.py`
- [ ] 30.E2 The brain host holds its memory ceiling with Postgres and the cache resident.
      *gate:* `test_speed.py` [rss-ceiling] — brain plus Postgres plus Valkey under the declared
      budget, measured on the VPS, not the laptop.

**Blocked** nothing. The merge-direction decision was made and executed in 30.F1.
**Cross-refs** TODO K3, I6, J3.3, J0; **The data platform** for the migration terms and rollback.

---

## S31 · Self-Monitoring, Diagnostics & Self-Healing

**Status** complete for now · **Surfaces** `src/afon/brain/reliability.py`,
`src/afon/brain/health.py`, `src/afon/brain/hud.py`, `src/afon/brain/metrics.py`,
`src/afon/brain/tools/diagnose.py`, `src/afon/brain/audit.py`

**The bar.** Afon knows what is wrong with himself before the owner does, says it in one sentence,
repairs what he can, and escalates the rest with evidence. No surface ever calls a broken component
healthy.

**Budget.** Health tick ≤150ms. Probe schedule ≤1/5min. HUD render ≤200ms.

**Stack.** Today: an in-process metrics module, scheduled probes, a HUD and a typed error journal
with turn correlation. **Add:** a Prometheus-format `/metrics` rendering (a text endpoint costs
nothing and makes any scraper optional) and JSON-line structured logs.
**Optional, owner's call:** Grafana/Loki. Useful the day he wants dashboards; until then the HUD and
the journal answer the same questions without a stack to maintain.

**Verified by.** The agreement gate (no surface calls a broken component healthy) · injected faults ·
a loop registry proving no background work is invisible · self-repair success rate.

**Options weighed.** *Build:* in-process metrics + HUD (today) · Prometheus + Grafana · an
OpenTelemetry collector → **keep the HUD, add a Prometheus-format text endpoint.** That makes a
scraper optional rather than required, and Grafana becomes a decision he can make later without any
code change. *Test:* the agreement gate · injected faults · a loop registry → **all three**; "no
surface calls a broken component healthy" is the property that matters.
**Open-source base.** prometheus_client (Apache-2.0), structlog (MIT/Apache-2.0 dual).
**Speed & efficiency.** Health tick ≤150ms · probes on a schedule, never per request · the HUD reads
the last probe rather than re-probing per poll.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | a month where every incident was self-detected before he noticed | 3 / 4 / 2 d | — | 0 | the endpoint is additive; the HUD is unchanged |

**Floor**
- [x] 31.F1 Four health surfaces that **agree**: structural check, functional probe, HUD, and the
      error journal (J3.6). *gate:* `test_health_agreement.py`
- [x] 31.F2 Typed error taxonomy with turn correlation. *gate:* `test_error_taxonomy.py`,
      `test_error_tracking.py`
- [x] 31.F3 Tool-level reliability learned from the audit trail. *gate:* `test_tool_reliability.py`
- [x] 31.F4 Every background loop appears in the HUD with its last tick, its period and its budget —
      "no silent work". *gate:* new `test_loop_registry.py`

**Raise**
- [ ] 31.R1 Trend detection: degrading before failing (rising p95, rising retry rate) raises a
      signal. *gate:* `test_metrics.py` [trend]
- [ ] 31.R2 Self-repair coverage extended to the top five recurring failure shapes from the journal.
      *gate:* `test_self_repair.py`
- [ ] 31.R3 A weekly self-report: what broke, what repaired itself, what needs the owner.
      *gate:* `test_protocol_reports.py` extended.
- [ ] 31.R4 **One correlation id per turn**, generated at the edge and stamped on every brain stage,
      tool call, audit row and error-journal entry — so a single owner action can be read end to end.
      The OpenTelemetry idea, without adopting a collector.
      *gate:* new `test_correlation_id.py` — one turn, one id, no orphaned rows.
- [ ] 31.R5 **Governors: the budgets act, not just report.** Prefill tokens, catalogue tokens and p95
      latency already have numbers and a test that checks them afterwards. When one is breached the
      system must narrow the catalogue, drop to a cheaper model or go quieter **by itself**, and tell
      the owner it did. A budget that only fails a test in the morning did not protect the turn that
      broke it.
      *gate:* new `test_governors.py` — each breach triggers its named degradation, the degradation
      is announced, and a governor cannot silently stay engaged once the pressure is gone.
      **Blocks the unpark gate.**
- [ ] 31.R6 **Continuous evaluation of real turns.** Every tier in this document tests what we thought
      to write down. This one scores what actually happened: each real turn judged in the background
      against the behavioural rubric, with the owner's own response recorded as the outcome — acted,
      ignored, corrected, or asked again. Then alert on a regression rather than waiting for the next
      periodic run.
      This is the only measurement that can tell us the 84.9 is moving for real, and the trace
      already carries most of the inputs (intent, tools fired, stages, prefill).
      *gate:* new `test_continuous_eval.py` — a scored turn per real turn, an outcome label per
      scored turn, a seeded regression detected, and **judging never on the answer path** (a turn
      must not wait for its own grade).
      **Blocks the unpark gate.**

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

**Stack.** Today: model-chain failover, a process supervisor and singleton enforcement. **Add:**
an explicit degraded-mode matrix and a host-liveness exchange so each host knows the other's state.
**Declined:** Kubernetes, Consul, etcd, multi-region databases. Consensus systems solve coordination
between many nodes; there are two, and the honest answer at two nodes is a declared degraded mode
plus a reconciliation rule.

**Verified by.** Mode drills (brain down, edge down, network down) · promotion test · split-brain
reconcile · a live hour with the VPS pulled.

**Options weighed.** *Build:* declared degraded modes + laptop promotion · two VPS with database
replication · Kubernetes → **degraded modes first.** At two nodes the honest answer is a declared
mode and a reconciliation rule, not consensus. *Replication:* manual copies · **Litestream** ·
application-level sync → **Litestream**: it streams sqlite to object storage continuously and
restores to a point in time, which is exactly the shape of this problem. *Test:* mode drills ·
promotion · split-brain reconcile → **all three.**
**Open-source base.** Litestream (Apache-2.0).
**Speed & efficiency.** Failure detection ≤30s · degraded mode announced within one turn ·
replication is a background stream, not a batch job.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| reasoned | a live hour with the VPS pulled, then a clean reconcile | 5 / 6 / 3 d | — | 1–3 | promotion behind a flag, default off; replication is read-only |

**Floor**
- [x] 32.F1 LLM provider failover chain. *gate:* `test_brain_llm.py`, `test_resilience.py`
- [x] 32.F2 Single-instance enforcement (no two brains, no two edges).
      *gate:* `test_singleton_and_macro_guard.py`
- [x] 32.F3 **Explicit degraded modes.** Define and implement three: brain-down (edge answers what it
      can), edge-down (text channels only), network-down (local model + local tools). Each announces
      itself. *gate:* `test_degraded_modes.py`
      *done 2026-09-13:* `shared/degraded.py` — a table, in `shared/` because both hosts read the
      same one. Model failover was real and nothing above it was: losing a host produced whatever
      each component happened to do on its own, none of it written down, so "what works right now"
      had no answer. The plan declined Kubernetes, Consul and etcd, and that reasoning IS the
      design — consensus coordinates many nodes and there are two, so the honest answer at two is a
      declared mode. Each mode says what is lost AND what is kept, since "degraded" alone tells the
      owner nothing he can act on. Severity-ordered, so a dead network is reported as a dead
      network and not as three coincidental failures. Announced once on the way in and **once on
      the way out**: repeated every tick it becomes noise he talks over, and without the recovery
      he keeps working around a limitation that has been gone for an hour. Two copies of this table
      would agree the day they were written and drift silently after — the fleet's two identity
      files, in miniature.
- [x] 32.F4 Health of the *other* host is known to each host, not assumed.
      *gate:* `test_connectivity.py` [32.F4]
      *done 2026-09-13:* the brain ASKS the laptop over the wire rather than trusting that a socket
      is registered — a closed lid leaves one open for over a minute, and reporting "connected" on
      that basis is a status page that is green because it never asked. The laptop derives its own
      view from the real socket state of its supervised loop and turns it into the same declared
      mode, so brain-down is announced by the only side still able to speak. Both readings feed one
      matrix; neither side decides the other is fine by default.

**Raise**
- [ ] 32.R1 State replication: memory and task stores mirrored between hosts on a schedule, with a
      verified restore. *gate:* `test_backup_restore.py` [cross-host]
- [ ] 32.R2 Automatic promotion — the laptop takes over brain duties when the VPS is unreachable for
      N minutes.
      **Half of this already exists and should not be rebuilt:** `edge/remote_brain.py` carries
      `_fallback_agent`, a lazily-built local `AfonAgent` held as a warm standby. What is missing is
      not the mechanism but the evidence — nothing tests that the fallback can carry a real turn, so
      its quality is unknown. A 2026-09-12 review called the single brain the plan's biggest
      structural weakness and it is right; the cheap answer is this standby made good, not a second
      VPS.
      *gate:* `test_degraded_modes.py` [promotion]
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

**Stack.** Today: objectives with progress notes, a backlog and the worker. **Add:** milestones
and target dates so "stalled" becomes computable.
**Declined:** NetworkX for the goal graph. The hierarchy is a handful of nodes; a dict of parents is
clearer to read, cheaper to test and impossible to get subtly wrong.

**Verified by.** Stall detection · conflict cases (two objectives, same hours) · attribution from
turn to objective · a quarter with nothing going stale unnoticed.

**Options weighed.** *Build:* a dict hierarchy (today) · NetworkX · an external PM tool as source
of truth → **the dict.** The hierarchy is a handful of nodes: a graph library adds a dependency and
a serialisation format to something a nested structure expresses more legibly, and Notion stays a
mirror rather than the master. *Test:* stall detection · conflict cases · turn→objective attribution
→ **all three.**
**Open-source base.** `graphlib` (stdlib) for milestone dependency ordering. A topological sort is not a reason to run a project-management server; Notion stays the owner-facing surface and Plane is declined.
**Speed & efficiency.** Weekly review ≤1 LLM call · daily attribution in-process · stall detection is
arithmetic on dates.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| reasoned | a quarter with no objective going stale unnoticed for a week | 2 / 4 / 1 d | **30 min (state the real objectives)** | 0 | milestones are an optional field |

**Floor**
- [x] 33.F1 Objectives with progress notes and deferred items. *gate:* `test_objectives.py`
- [x] 33.F2 Milestones and target dates, so "stalled" is computable rather than felt.
      *gate:* `test_objectives.py` extended [milestones]
      *done 2026-09-12:* an objective carries optional dated milestones, and `stall_reason` answers
      in one sentence: an overdue milestone first, silence past a week second. An overdue
      commitment is a harder fact than "nothing logged lately", which on a long objective can just
      mean a quiet week. A bad target date is refused rather than stored — a milestone whose date
      cannot be parsed can never be overdue, so accepting one would quietly remove the objective
      from stall detection, which is the exact opposite of what the field is for. The rollback
      holds: an objective with no milestones still stalls on silence.
- [x] 33.F3 Every task can name the objective it serves, or is explicitly ad-hoc (pairs with 01.R3).
      *gate:* `test_goal_attribution.py`
      *done 2026-09-12:* Afon held objectives and he held a to-do list, and nothing joined them, so
      "am I working towards anything" had no answer — a week of tasks could serve none of his
      objectives and no surface would have said so. A to-do now carries the objective it serves,
      and the review groups open work under each one, counts the ad-hoc pile and names any
      objective nothing is serving, which is the reading that changes what he does next.
      Attribution is never inferred from the title: a task called "cancel my Party Map
      subscription" is not evidence it serves the Party Map objective, and guessing would be
      confidently wrong in exactly the cases he would not check. An objective named but not
      resolvable is refused rather than dropped, because a discarded attribution reads afterwards
      as ad-hoc work with nothing to show it went missing. "ad-hoc" is a named value, not a blank:
      not everything he does should serve a standing objective, and pretending otherwise produces
      a report where every line reads as a reproach.

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

**Stack.** Today: a Google Fit tool (dark), screen-time and activity signals, wellbeing coaching.
**Add:** a small time-series table in the existing sqlite.
**Declined:** InfluxDB / TimescaleDB. A time-series database for one person's daily metrics is a
daemon, a retention policy and a backup path for data that is a few thousand rows a year.

**Verified by.** Ingestion reliability · threshold accuracy · the stated boundary (observations and
patterns, never diagnosis) · nudge acceptance over a month.

**Options weighed.** *Source:* Google Fit (written, dark) · a wearable vendor API (Withings, Oura,
Garmin) · manual entry → **depends entirely on the device he actually wears**, which is why this is
a guess today. *Store:* sqlite table · InfluxDB · TimescaleDB → **sqlite**: a few thousand rows a
year does not justify a time-series daemon. *Test:* ingestion reliability · threshold accuracy · the
stated boundary (observations, never diagnosis) → **all three.**
**Open-source base.** `fitdecode` (MIT) for FIT files and a plain XML reader for an Apple Health export. **This is what unblocks the system without naming a device:** both are open export formats every mainstream wearable can produce, so the ingest is written against the format, not the vendor, and naming the watch later changes nothing.
**Speed & efficiency.** Daily aggregation, zero per-turn cost · trends computed from stored
aggregates, not raw samples.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| **guess** | naming which wearable he actually wears daily | 2 / 4 / 2 d | 15 min (enable the API or name the device) | 0 | the tool degrades to typed not-configured |

**Floor**
- [x] 34.F1 **Enable the fitness data source** and verify a real read; today the tool exists and the
      API is not enabled. *gate:* `test_new_integrations.py` [fitness]
      *done 2026-09-13:* **unblocked by removing the dependency, exactly as the blocker table
      settled it.** The tools were written against Google Fit, which needs a Cloud-project
      enablement nobody has done, so for months the capability existed and answered "not configured
      yet" to every question — a feature that is present, passing, and has never once worked.
      `brain/vitals.py` now reads Apple Health's `export.xml`, which every iPhone produces from the
      Health app with no developer account, no API, no consent screen and no key. Streamed with
      `iterparse` and cleared element by element, because a real export is hundreds of megabytes
      and `ET.parse` would pass on a fixture and then take the brain down on the owner's actual
      file. Only daily aggregates are kept; a copy of his entire health history in the state root
      answers no question worth asking. Naming the watch later changes no code.
- [x] 34.F2 Screen-time and activity signals are real and dated, not estimated.
      *gate:* `test_phase12_utility.py` [34.F2]
      *done 2026-09-13:* each active sample used to credit `settings.presence_poll_seconds`, and
      that was wrong in two ways. The poller does not run at a steady cadence — the laptop sleeps,
      the brain restarts — so a three-hour hole between two samples was credited as one poll
      interval, and an outage read as productivity. Worse, the constant was applied at READ time,
      so **changing the setting silently rewrote every past day**: yesterday's four hours became
      five because a number in a config file moved. Credit is now the real interval to the next
      sample; a gap longer than three typical intervals is not credited at all and the uncredited
      time is reported, because "I wasn't looking for two hours" and "you weren't at the screen for
      two hours" are different facts. The fallback for a hole and for the last sample of the day is
      the MEDIAN observed interval, measured from that day's own samples — using the configured
      constant there left the same defect one level down, which is how the first fix was caught.
      And the report says the window it actually covers: a figure with no window reads as a whole
      day.
- [x] 34.F3 Health talk stays inside a stated boundary — observations and patterns, never diagnosis.
      *gate:* `test_companion_safety.py` [34.F3]
      *done 2026-09-13:* `vitals.BOUNDARY` is stated in one place, carried into the tool
      descriptions so the model has it in front of it rather than in training, and asserted by the
      gate. Afon reports what the numbers say and what has changed; he does not name conditions,
      explain causes, or advise on treatment. The distinction is not squeamishness: "you slept five
      hours, three nights running" is a fact he measured, and "you're not sleeping because of X" is
      a claim he invented that a person may act on instead of asking somebody qualified.
      `diagnostic_language` checks **Afon's own phrasing and never the owner's** — he is entitled
      to describe his own health in any terms he likes, and a guard that policed his speech would
      be both useless and insulting.

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

**Was blocked** on enabling the Fitness API; **retired 2026-09-13** by ingesting an open export format instead (34.F1). Naming the wearable changes no code.

---

## S35 · Crisis & Emergency Response

**Status** half-built — system-level crisis protocols exist, *owner*-level does not ·
**Surfaces** `src/afon/protocols/ragnarok.py`, `src/afon/protocols/phoenix.py`,
`src/afon/brain/reliability.py`, `src/afon/brain/tools/phone.py`

**The bar.** In a real emergency Afon does the simplest useful thing fast: reaches someone, gives
the right information, and does not require a conversation. Outside emergencies he never
mis-classifies one.

**Budget.** Emergency path ≤3s to first outward action, no LLM dependency on the critical path.

**Stack.** Today: system-level protocols and owner escalation. **Add:** a deterministic classifier
with a manual trigger phrase, and a **local** contact list — the critical path must not depend on the
LLM chain or on network reachability. Twilio for the call rung (owner-blocked).
**Declined:** a learned classifier. False positives here are expensive and unexplainable ones are
unacceptable; rules the owner can read are the correct trade.

**Verified by.** A 50-prompt false-positive corpus · ladder drill with a stop condition · offline
contact resolution · two rehearsed drills, one voice-triggered and one silence-triggered.

**Options weighed.** *Classifier:* deterministic rules + a trigger phrase · a learned classifier ·
a third-party monitoring service → **rules.** False positives are expensive and unexplainable ones
are unacceptable; he must be able to read exactly what will trigger a call to someone. *Transport:*
Twilio · a phone-side shortcut · a push-only ladder → **Twilio for the call rung**, push for the
rest. *Test:* a 50-prompt false-positive corpus · a ladder drill with a stop condition · offline
contact resolution → **all three.**
**Open-source base.** ntfy (Apache-2.0 / GPL-2.0, shared with S13) for the push ladder and `signal-cli` (GPL-3.0, invoked as a CLI, never linked) to reach a third party without a paid gateway. **This takes Twilio off the critical path**; it stays an optional upgrade for an actual voice call.
**Speed & efficiency.** ≤3s to first outward action · **no LLM on the critical path** · contacts
resolved from a local file so a network failure cannot silence the ladder.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| **guess** | his contact list and the thresholds he actually wants | 3 / 4 / 2 d | **30 min (contacts + Twilio)** | 1–5 | classifier behind a flag; the manual phrase always works |

**Floor**
- [x] 35.F1 An explicit emergency classifier with a **conservative** threshold and a manual trigger
      phrase, on a path that does not depend on the LLM chain.
      *gate:* `test_emergency_path.py` — false-positive rate 0 on 50 ordinary prompts.
      *done 2026-09-13:* `brain/emergency.py`, sitting beside `_catastrophic` in the turn path and
      running before `_prepare_turn` in both the blocking and the streaming body. Everything that
      existed was system-level — `ragnarok` and `phoenix` recover **Afon**; nothing recovered the
      owner. "Call an ambulance" reached the same machinery as "what's the weather": a model
      round-trip, a tool choice, a provider call, each of which can be slow, wrong or down at the
      moment it matters. Rules, not a classifier, exactly as the plan decided — and every alarm
      carries the phrase that tripped it, so it can be argued with. **Conservative means biased to
      silence**: a pattern fires only on a present-tense clause, and figures of speech, procedure
      questions and narration are guarded out ("this bug is killing me", "what do I do if there's a
      fire", "in the film they call an ambulance" all do nothing). That bias has a real cost — an
      emergency phrased unusually will not trip it — and the manual phrase is how the cost is paid:
      **"Afon, emergency"** bypasses every guard, because a manual trigger that can be reasoned out
      of firing is not one. The 50-prompt corpus is half near-misses on purpose; a keyword matcher
      fails most of it.
- [x] 35.F2 An emergency contact list and a defined action per category, stored locally.
      *gate:* `test_emergency_path.py` [contacts resolved offline]
      *done 2026-09-13:* `emergency.md` beside `contacts.md`, gitignored the same way, with
      `emergency.example.md` shipped because a gitignored file with no worked example is a feature
      nobody turns on. One line per category, rungs tried in the order written, read from disk at
      the moment it is needed — a list cached in a process that has been up three weeks is exactly
      the staleness this system cannot have. Every rung is reported by name as reached or not
      reached, with the reason, and a ladder that reached nobody says so in its first sentence.
      Rungs are guarded twice over, in `_do` and again in the loop: `_do` catches what a transport
      throws and the loop catches a bug in `_do`, because here the rung below the broken one is the
      one that gets help. **Two defects found while building it.** An ntfy title travels as an HTTP
      header, which cannot carry non-ASCII, so "EMERGENCY — medical" raised `UnicodeEncodeError` and
      the push never left — an entire notification lost to a dash, on the path where it matters
      most. And the contact parser accepted any line containing a colon, so every explanatory line
      in the file became a category and one of them could shadow `default:`.
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

**Stack.** Today: a documented confirm tier over every mutating tool, deterministic catastrophic
refusal, an audit trail, voice+face factors, `pass` as the single secret store, and a park switch.
**Add:** an adversarial prompt corpus and session-scoped elevation.
**Declined:** Keycloak / Auth0 / Vault / OPA. There is exactly one principal. The confirm tier *is*
the policy engine and `pass` *is* the secret manager; adding an identity provider would create a
second authority to keep in sync with the first.

**Verified by.** 30 adversarial attempts, zero successes · elevation-scope tests · audit completeness
· guest-mode reduction by construction.

**Options weighed.** *Policy:* the confirm tier (today) · OPA · a rules DSL → **the confirm tier**;
one principal, and a second policy engine is a sync problem. *Secrets:* `pass` (today) · Vault · OS
keyring → **`pass` on the VPS, plus the OS keyring for laptop-local secrets** — the laptop currently
has no good home for them. *Test:* an adversarial corpus · elevation-scope tests · audit completeness
→ **all three**; 30 attempts, zero successes is the bar.
**Open-source base.** `pass` (GPL-2.0, invoked as a CLI — not linked), `keyring` (MIT).
**Speed & efficiency.** Authorisation ≤50ms with no network call · the audit write is append-only and
off the answer path.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | 30 adversarial attempts with zero successes | 3 / 4 / 3 d | **45 min (`.env`→pass, rotation)** | 0 | the secret read path falls back to env during migration |

**Floor**
- [x] 36.F1 A documented confirm tier covering every mutating tool.
      *gate:* `test_confirm_tier_documented.py`
- [x] 36.F2 Deterministic refusal of catastrophic actions, independent of the model.
      *gate:* `test_security_hardening.py`, `test_safety_autonomy.py`
- [x] 36.F3 Identity as a second factor for protected actions, failing *open* only when the sensor
      is honestly unsure. *gate:* `test_face_second_factor.py`
- [x] 36.F4 A park switch that halts every role on every host.
      *gate:* `test_maintenance_lock.py`
- [x] 36.F5 Secrets have exactly one home (password store), and the repo proves it.
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

**Stack.** Today: secret scrubbing in the error path, local-first biometrics, a public-clean guard.
**Add:** a **generated** data inventory (from the code, not written by hand) and per-store retention
enforced by the hygiene job.
**Declined:** differential privacy. DP protects individuals inside an aggregate release; there is one
subject and no release — it would be ceremony, not protection.

**Verified by.** Inventory generation covering every store · forget-then-requery verification ·
egress log · a five-minute privacy report the owner can act on.

**Options weighed.** *Build:* a **generated** inventory + enforced retention · a hand-written policy
document · differential privacy → **generated inventory.** A hand-written policy is wrong the week
after it is written; DP protects individuals inside an aggregate release and there is one subject and
no release. *Test:* inventory generation covering every store · forget-then-requery · an egress log →
**all three**; "forget X" that leaves X in two stores is the failure.
**Open-source base.** **Microsoft Presidio (MIT)** for PII detection in the scrubbing and egress paths. It does not replace 37.F1 — Presidio finds personal data inside text, the generated inventory answers where the stores are, and only the second one answers "what do you know about me".
**Speed & efficiency.** Retention sweep daily, ≤30s, off the answer path · classification is a column,
not a scan.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| reasoned | a forget-then-requery test passing across every store | 4 / 4 / 2 d | 20 min (retention preferences) | 0 | retention defaults to today's behaviour (keep everything) |

**Floor**
- [x] 37.F1 **A data inventory**: every store, what personal data it holds, where it lives, who can
      read it. Generated from the code, not written by hand.
      *gate:* `test_data_inventory.py` — every memory/audit store appears.
      *done 2026-09-13:* `brain/inventory.py` declares 42 stores with three columns each: what it
      holds in the owner's terms, how long it is kept, and who can read it. The anti-staleness
      mechanism is the gate, not the discipline: it walks the state root and FAILS on anything
      present that nothing declares. That earned its place on the first run by finding three
      undeclared things, one of which is the error journal — it holds the tail of whatever a failing
      tool was handed, so it is personal data whether or not anyone meant it to be, and it now
      expires rather than being kept. Asked via `what_you_know` in a new `privacy` lazy group, since
      "what do you know about me" is unmistakable and asked rarely, and the per-turn surface is paid
      on every turn.
- [x] 37.F2 A retention policy per store, enforced by the hygiene job, not by intention.
      *gate:* `test_memory_hygiene.py` [per-store TTL]
      *done 2026-09-13:* `maintenance.sweep_retention` runs inside the daily hygiene job. Before it,
      every store had a retention in somebody's head and exactly one — presence — had it in a setting
      something actually read; the rest grew forever while the docs said otherwise, which is the
      worse of the two failures because a stated policy nobody executes is a promise to the owner
      that is quietly not kept. **The first dry run rejected its own design**, and that is recorded
      in the code: it proposed deleting the pending-approvals file and two live pid files because
      nothing had written to them in a while. File mtime is not age for a document that IS the
      current state. So retention has a third value, `WHILE_CURRENT`, and the sweep only touches
      stores that accumulate. A sqlite store is left to its own module, because deleting rows by age
      needs a schema and guessing one would be a sweep that corrupts a store to satisfy a policy.
- [x] 37.F3 `forget X` actually removes X from every store, verified by re-querying.
      *gate:* `test_right_to_forget.py`
      *done 2026-09-13:* `brain/forget.py` — `forget` deleted the single best-matching learned note.
      Told to forget a subject Afon knew four facts about, it dropped ONE and said "Forgotten, sir",
      while the embedding stayed in the vector store, the entities stayed in the relationship graph
      where they still shaped answers, and the journal still described the day in prose. Four stores,
      one deletion, a confident report of success. It now sweeps all four and then RE-QUERIES,
      because the only evidence a thing is forgotten is asking again and getting nothing back; when
      something survives it is named rather than reported as a clean sweep, and a re-check that
      cannot run is reported as unverified rather than as clean. Two refusals: the owner's own vault
      notes are left alone and said to be left alone, since deleting them by voice would be editing
      his documents rather than clearing Afon's memory; and the audit log is not rewritten, because
      the record of what Afon DID is a different and much worse thing to erase. **Declined:**
      counting how many vault notes mention the subject — it meant a full-text scan of 6,551 notes
      on a path that ends in Afon speaking, and hung the first self-check. `search_vault` answers it
      whenever he asks.

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

**Stack.** Today: Gmail, Telegram, channels and contact resolution. **Add:** a unified
"what's waiting" view deduplicated by thread — the missing piece is a model, not an API.
**Conditional:** Microsoft Graph / WhatsApp Business only if the owner actually uses them; each is a
consent and review surface, not just a client library.

**Verified by.** Triage corpus graded against what he really answered · thread awareness · approval
before send · a week where he never opens the mail client first.

**Options weighed.** *Build:* a unified view over the existing clients · Microsoft Graph · IMAP
direct → **the unified view.** The missing piece is a model of "what is waiting", not another
protocol client; Graph and WhatsApp are consent and review surfaces, not just libraries, and only
earn their place if he uses them. *Test:* a triage corpus graded against what he really answered ·
thread awareness · approval-before-send → **all three.**
**Open-source base.** `signal-cli` (GPL-3.0, CLI) shared with S35; `imap-tools` (Apache-2.0) if a second mailbox ever matters. Self-hosted outbound SMTP stays declined — deliverability is the entire product a mail provider sells, and losing it is silent.
**Speed & efficiency.** Inbox sweep ≤5s with channels fetched concurrently · thread metadata cached ·
drafting ≤1 LLM call.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| reasoned | a week where he never opens the mail client first | 3 / 5 / 2 d | 15 min (label what matters) | 0 | the unified view is read-only and additive |

**Floor**
- [x] 38.F1 Email and Telegram send/read, with contact resolution.
      *gate:* `test_phase11_integrations.py`, `test_contacts.py`
- [x] 38.F2 **A unified "what's waiting" view** across channels, deduplicated by thread.
      *gate:* `test_unified_inbox.py`
      *done 2026-09-12:* `brain/tools/inbox.py` — unread mail, unread Telegram chats and the actions
      autonomous work deferred, in one shape, fetched concurrently under a per-source deadline so one
      hung mailbox costs that channel and not the answer. Gmail and Telegram gained structured
      readers (`unread_threads`, `unread_chats`) rather than having their prose parsed back into
      fields. Three refusals are gated: it never merges across channels (an email thread and a
      Telegram chat on the same subject are two things to answer, and folding them hides one); it
      never reports a channel it could not read as empty, because "nothing waiting" and "I couldn't
      look" are different facts; and a thread is one row however many messages it holds, with the
      count and the newest subject. `whats_waiting` is a core tool, not a lazy one — "anything for
      me?" carries no trigger word, and a unified inbox that appears only after you say "email" is
      the three-turn ask it exists to remove. The per-turn ceiling moved 56 -> 57 for that reason,
      recorded in `test_finetune.py` beside the two earlier moves.
- [x] 38.F3 Nothing is sent without approval; drafts are always shown first.
      *gate:* `test_approvals.py`, `test_unified_inbox.py` [38.F3]
      *done 2026-09-12:* the four send tools were already confirm-gated, but the gate only told the
      model to *describe* the action — so the owner approved a paraphrase the model had written of
      the message it was about to send. A summary of a message is not the message: the wrong tone,
      the wrong name and the wrong recipient all survive a faithful one-line description.
      `proactive.DRAFTED` now names, per send tool, which argument carries the words, and
      `draft_preview` renders recipient, subject and body verbatim and unclipped. The confirm gate
      hands that to the model with an instruction to read it back word for word and not improve it,
      and the approval queue's listing carries the same block, so a send deferred by autonomous work
      is approved on its words too. An empty body shows as "(no message text)" rather than being
      hidden — a send with nothing in it is exactly what the readback is for.

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

**Stack.** Today: a bridge to the external OpenClaw fleet plus a local background worker.
**Add:** tracking and verification — every delegation is a task with a deadline and a checked result.
**Declined:** AutoGen / CrewAI. The delegation target is an existing fleet with its own protocol and
its own agents; a second multi-agent framework inside Afon would duplicate it and own nothing.

**Verified by.** Tracking assertions (nothing fire-and-forget) · timeout and takeback · result
verified before it is reported as fact · ten real delegations over a month.

**Options weighed.** *Build:* the fleet bridge + tracking · AutoGen · Afon spawning his own
sub-agents → **the bridge.** The delegation target is an existing fleet with its own protocol and its
own agents; a second multi-agent framework inside Afon would duplicate it and own nothing. *Test:*
tracking assertions · timeout and takeback · verification before reporting → **all three**;
anti-fabrication applies to other agents too.
**Open-source base.** none, and deliberately. One delegation end to end is a call with a brief, a deadline and a verification step. CrewAI and AutoGen stay declined; the OpenClaw fleet already supplies real workers when more than one is needed.
**Speed & efficiency.** Delegation overhead ≤1s · nothing runs unmonitored past its declared period ·
results verified once, not re-asked.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| reasoned | ten real delegations tracked end to end over a month | 3 / 4 / 2 d | — | 0 | delegation is already gated off by default |

**Floor**
- [x] 39.F1 Reachable delegation to the external fleet router. *gate:* fleet live check in
      `run_all_tests.py` (gated on authorisation).
- [x] 39.F2 Every delegation is tracked as a task with a deadline and a result check — nothing is
      fire-and-forget. *gate:* `test_delegation_tracking.py`
      *done 2026-09-13:* `brain/delegation.py` on top of the new shared work record. Delegation was
      a string in and a string out, and the background path's `TaskQueue` row is DELETED by
      `TaskQueue.drop` the moment the work finishes — so a delegation that SUCCEEDED left no trace
      at all. The evidence was erased precisely when there was something to record, which is why
      39.E1's "ten real delegations tracked over a month" was not a thing this system could have
      reported on. Every delegation now opens a unit with an owner and a deadline and closes it on
      both paths, answered or failed. The deadline is the transport's own ceiling plus a minute, so
      a unit still open past it does not mean the fleet is thinking — it means a runner went away
      without ever writing back, which is the fire-and-forget detector the floor is asking for.
- [x] 39.F3 A delegated result is **verified** before being reported as fact (anti-fabrication
      applies to other agents too). *gate:* `test_delegation_tracking.py` [verification]
      *done 2026-09-13:* whatever the team lead returned went straight on — into the model's
      context, or into `server._announce_task`, which reads the result out **loud, verbatim**. So
      "I don't have access to that" reached the owner as Afon's own finished answer, and a figure
      the fleet invented reached him in Afon's voice with nothing marking it as somebody else's
      claim. Three grades now: **rejected** (empty, a refusal, or an answer with nothing in common
      with the brief) is never reported as a finding; **attributed** (it carries figures, dates or
      links Afon never saw) is relayed in the team lead's name; **verified** may be said in Afon's
      own words. **Declined:** re-running the work to see whether it agrees. That costs what the
      delegation cost and answers a different question. **Also deliberately not done:** feeding the
      answer's URLs into the citation ledger — recording them would mark them as pages this turn
      retrieved, and 41.F2's whole job is to notice a reply citing a host nothing opened.

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

**Stack.** Today: read-only price lookups. **Add:** a Beancount plain-text ledger the owner
controls — auditable, diffable, versioned by git, no server, and read-only by construction, which is
exactly the property this system needs.
**Declined:** Plaid / TrueLayer / brokerage APIs. Write access to money is out of scope by decision,
and read access via bank aggregation is a large consent and credential surface for one person's
balances.

**Verified by.** Valuation snapshots with as-of times · staleness reporting · an assertion that the
finance family contains **no** mutating tool · monthly brief review.

**Options weighed.** *Ledger:* **Beancount plain text** · Plaid/TrueLayer aggregation · a
spreadsheet import → **Beancount.** It is diffable, git-versioned, auditable by him without Afon, and
read-only by construction — the exact properties this system needs. Bank aggregation is a large
credential and consent surface for one person's balances, and write access to money is out of scope
by decision. *Test:* valuation snapshots with as-of times · staleness reporting · an assertion that
the finance tool family contains **no** mutating tool → **all three.**
**Open-source base.** Beancount (GPL-2.0) — used as a **file format and CLI**, never linked into the
package, so the repo's own licensing is unaffected.
**Speed & efficiency.** Snapshot ≤2s, cached daily · prices batched in one call · the ledger is read
only when it changes.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| **guess** | him writing one real ledger file | 3 / 5 / 2 d | **60 min (the first ledger)** | 0 | read-only by construction; delete the file to disable |

**Floor** *(deliberately small, deliberately read-only)*
- [x] 40.F1 A Beancount ledger the owner controls, read by a `portfolio_snapshot` tool that values it
      with the existing price tools. Plain text, so it is diffable, git-versioned and auditable by
      him without Afon. *gate:* `test_portfolio.py`
      *done 2026-09-13:* `brain/portfolio.py`, with `ledger.example.beancount` shipped and the real
      `ledger.beancount` gitignored. Beancount is used as a **file format and never imported**, so
      its licence does not reach this package and he keeps the option of editing the file with
      anything at all, `bean-query` included. The parser reads the subset that says what is held
      and **reports any line it could not read** rather than skipping it: a portfolio that silently
      drops a row produces a number that looks complete, which is the one failure mode that matters
      when the number is money. The valuation reuses the quote endpoint `stock_price` already had —
      split into `quote_ticker` so there is still one owner of it — and a bare balancing leg, the
      commonest line in a real ledger, is recognised rather than reported as noise.
- [x] 40.F2 **No transaction capability, by construction** — asserted, not merely absent.
      *gate:* `test_portfolio.py` [no mutating tool in the finance family]
      *done 2026-09-13:* the gate finds the finance tool family by name and asserts none is named
      for moving money, none takes a destination, and the module has no write, no HTTP post and no
      credential. **The first version of that check was wrong in an instructive way**: it flagged
      any tool taking an `amount`, which caught `fx_rate` — a tool that converts a number and
      returns a number. Weakening the check to let it pass would have been the wrong repair, so the
      check now names the property that actually matters, which is having somewhere to send it.
- [x] 40.F3 Values carry their as-of time and source; a stale price says so.
      *gate:* `test_portfolio.py` [staleness]
      *done 2026-09-13:* every value is "twelve at 312.40 EUR, Yahoo Finance, as of Friday 17:31",
      and past a day it says how old. That is not fussiness: a quote read on a Sunday is Friday's
      close, and a valuation that omits the time invites him to act on a two-day-old price while
      having to remember for himself which days markets are open. A holding that could not be
      priced is listed, left OUT of the total, and the total says what it is missing — the prose
      the old price tools produced had no way to express a partial answer. The age of the ledger
      file is reported too, since a portfolio nobody has updated in two hundred days is a different
      kind of wrong from a stale quote.

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

**Stack.** Today: a typed search/scrape fallback chain plus research skills. **Add:** citation
objects (claim → source → retrieved-at) and source weighting.
**Conditional:** Tavily / Exa / Serper — measure the free chain first; a paid search API is easy to
add later and impossible to justify before the failure rate is known.

**Verified by.** Citations that resolve · contradiction surfacing rather than smoothing · declared
budget honoured and early stops reported · three owner-graded research questions.

**Options weighed.** *Search:* the existing free chain · Tavily/Exa/Serper · self-hosted SearXNG →
**measure the free chain first**; a paid search API is trivial to add later and impossible to justify
before the failure rate is known. SearXNG is the free middle option if the chain proves weak.
*Extraction:* BeautifulSoup heuristics (today) · **trafilatura** · readability → **trafilatura**,
shared with S05. *Test:* citations that resolve · contradiction surfacing · declared budget honoured
→ **all three.**
**Open-source base.** trafilatura (Apache-2.0), SearXNG (AGPL-3.0, self-hosted as a service — not
linked).
**Speed & efficiency.** A budget declared before the task starts and reported when it stops early ·
parallel fetch with per-source timeouts · dedupe by domain before reading.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| reasoned | three owner-graded research questions | 3 / 5 / 3 d | 30 min (grade the three) | 0–20 if a paid API is adopted | citations are additive; the chain is unchanged |

**Floor**
- [x] 41.F1 Search/scrape with a typed fallback chain. *gate:* `test_web_fallback.py`
- [x] 41.F2 **Citations are mandatory** for factual claims sourced from the web, and are checked to
      resolve. *gate:* `test_citations.py`
      *done 2026-09-12:* `brain/citations.py` — a per-turn ledger of what Afon actually retrieved.
      `web_search` and `scrape_url` record what came back; every reply is checked against it, and a
      URL whose host he never retrieved is owned in one clause as his own reference rather than
      dressed as a source. A model handed a page of text will attribute a claim to a plausible URL
      it never fetched, and the answer looks BETTER for carrying it — the owner cannot tell a real
      link from a well-formed one, which is why this is worth a spoken sentence and not a log line.
      The reply itself is never edited: rewriting what the model said to hide the problem is the
      same dishonesty one layer down. **Declined:** re-fetching each cited URL to prove it is live.
      That is a network call per answer, and a 200 from a page Afon never read is not evidence he
      read it — provenance is the question, reachability is not. An obstructed page is never
      recorded, so nothing behind a paywall is ever citable. The ledger is a ContextVar opened
      beside the turn's situation, so one session's reading can never vouch for another's claim.
- [x] 41.F3 A research task states its budget and reports when it stopped early.
      *gate:* `test_citations.py` [7]
      *done 2026-09-12:* the worker had a step budget, never said what it was, and — the real
      defect — handed back a truncated answer that read exactly like a finished one. When the steps
      ran out it told the model to write its summary, the model wrote one, and nothing anywhere said
      it was as far as the task got. The budget is now announced before the work rather than in the
      postmortem, a wall clock bounds it alongside the step count (six steps of slow scraping is
      minutes of silence), and a task that stops early says so and offers to continue. `stopped_early`
      is a value as well as prose, so a caller can decide rather than grep English.

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

**Stack.** Today: local playback, a Telegram music room and channel control. **Add:** one owner of
playback state so "what's playing" has a single answer.
**Declined for now:** Chromecast/AirPlay bridges — multi-room sync is a real feature but it needs
hardware the owner does not currently use.

**Verified by.** Routing tests across both paths · single playing-state source · ducking and
resume-after-call · a week with no false "nothing is playing".

**Options weighed.** *Player:* ffplay (today) · **mpv** · MPD → **ffplay stays until seek/gapless
is actually wanted**; mpv is the better player but it is a GPL binary and another dependency for a
capability nobody has asked for. *State:* one owner object · per-path state (today) · Home Assistant
media_player → **one owner object**, because "stop" must stop whatever is playing. *Test:* routing
across both paths · a single playing-state source · ducking and resume → **all three.**
**Open-source base.** mpv (GPL-2.0+, invoked as a CLI) held in reserve.
**Speed & efficiency.** Play/stop round trip ≤600ms · state read from one place · no polling of the
music room.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | a week with no false "nothing is playing" | 2 / 4 / 1 d | — | 0 | the state owner wraps both paths; remove it and both still work |

**Floor**
- [x] 42.F1 **Stop routes to whichever path is playing** — desktop or music room; the two now ask
      each other (fixed 2026-08-13). *gate:* `test_localplay_routing.py`
- [x] 42.F2 One playback-state owner, so "what's playing" has a single answer.
      *gate:* `test_localplay_routing.py` [7]
      *done 2026-09-12:* `brain/playback.py` — `current()` returns what is playing and WHERE, asking
      both routes. The two tools had drifted: `stop_music` cross-checked the music room before
      giving up and `now_playing` did not, so with a track streaming into the room "what's playing?"
      answered "Nothing is playing out loud right now, sir." That is exactly the elite bar's failure
      — a confident wrong answer about something the owner can hear — and it survived because
      neither tool was wrong on its own, only incomplete. Both now read the owner. Two rules are
      gated: the desktop wins when both are busy, because that is the one he is standing next to;
      and an unreachable laptop is never reported as silence, since a dead link and an idle player
      are different facts.
- [x] 42.F3 Volume and mute are the same concepts across paths.
      *gate:* `test_audio_output_switch.py` [7]
      *done 2026-09-12:* there was no volume control at all, so unifying the two paths meant
      building the concept first. `set_volume` takes an absolute level, a relative step, or a mute
      flag, and routes by the playback owner from 42.F2. Mute is not a second switch: Windows keeps
      an independent mute flag that survives a level change, so a device can sit at 70% and silent
      while "louder" does nothing three times running. Muting stores the level and sets zero,
      unmuting restores it, and one number is the whole state. The music-room path cannot be turned
      up at all — it plays into a Telegram call on other people's phones — so it says whose setting
      that is and offers what it can do instead, rather than answering "done" to a change that never
      happened. Lives in the `audioout` lazy group: "louder", "mute" and "turn it down" are
      distinctive enough to trigger, so the per-turn surface pays nothing. `pycaw` is now a declared
      Windows dependency of the edge extra; it was already in the venv but in nobody's lock file.

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

**Stack.** Today: presence tracking, arrival detection and a listening pulse. **Add:** fused
presence (voice + face + device activity) and context migration.
**Declined:** an MQTT presence protocol — same reason as S12; the WS link already carries it.

**Verified by.** Fusion tests · long-absence handling · migration drill across devices · a day
without re-establishing context.

**Options weighed.** *Signal:* fused voice+face+device activity · BLE beacons · phone geofence →
**fusion of what already exists.** Beacons and geofences add hardware and a permission surface for a
signal three existing sensors already imply. *Test:* fusion assertions · long-absence handling ·
context migration → **all three.**
**Open-source base.** python-zeroconf (LGPL-2.1) shared with S12 for LAN presence; the Valkey presence key with keyspace notifications carries the live handoff once the data platform lands, so a device switch does not wait for a poll.
**Speed & efficiency.** State change detected ≤10s · fusion is arithmetic over cached signals · no
new sensor polling.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| reasoned | a day without ever re-establishing context after moving | 3 / 4 / 2 d | — | 0 | fusion behind a flag → today's single-source presence |

**Floor**
- [x] 43.F1 Presence tracking and arrival detection. *gate:* `test_presence.py`,
      `test_presence_arrival.py`
- [x] 43.F2 Departure and return are symmetric — a return gets a "while you were gone" only when
      there is something worth saying. *gate:* `test_presence_arrival.py` [7]
      *done 2026-09-12:* a departure left no trace at all, so ten minutes and ten hours produced the
      identical "Welcome back, sir." The absence now has a length, measured from when he stopped
      touching the machine rather than when the poll noticed, so a slow tick cannot shrink it.
      `while_you_were_gone` reads the unified inbox from 38.F2 and returns a catch-up only when two
      things hold: the absence was long enough to matter (30 minutes — below that he was at the
      coffee machine) AND something actually arrived. Both refusals are gated, and the second is the
      one that matters: a "while you were gone" that reliably contains nothing is training to ignore
      the one that contains something. A catch-up that fails entirely falls back to the plain
      greeting rather than voicing an error.
- [x] 43.F3 Presence is a fused signal (voice + face + device activity), not any single source.
      *gate:* `test_perception_snapshot.py` [presence fusion]
      *done 2026-09-12:* `place` was device idle and nothing else, so a stale activity sample read
      as "away" — the laptop sleeps, the edge drops, he unplugs for an hour, and Afon concluded the
      room was empty while the man was sitting in it, held his messages and greeted him on his
      "return". Perception gained a third fact: an inbound utterance records `voice`, the one signal
      neither the keyboard nor the camera can see, trusted for ten minutes against the camera's two
      because having just spoken is evidence of a while rather than an instant. `situation._fuse_place`
      now decides from all three. A FRESH positive from any source means he is here; "away" needs
      every source that has an opinion to agree; nothing sensed at all stays unknown, which is a
      third answer the callers already handle and the honest one. The fusion lives in the
      interpretation layer, not in perception, because S26 never decides what a state means. Both
      halves are gated: a stale keyboard no longer outvotes a camera that just saw him, and fusion
      does not quietly become "never away".

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

**Stack.** Today: deterministic catastrophic refusal independent of the model, approval gates,
anti-fabrication verification and a safe-by-construction worker. **Add:** graded refusals and a
stated precedence order for value conflicts, plus a red-team corpus.
**Declined:** NeMo Guardrails — a second policy source; see S25.

**Verified by.** Refusal grading (refuse / confirm / proceed-with-note) · precedence cases ·
third-party impact checks · safety and honesty at 100 across five consecutive runs.

**Options weighed.** *Enforcement:* deterministic rules in code (today) + graded refusals ·
NeMo Guardrails · a safety classifier model → **rules.** A classifier makes refusals probabilistic
and unexplainable; a guardrails library creates a second answer to "may I". *Test:* refusal grading ·
precedence cases · a red-team corpus → **all three**, and the red-team corpus is shared with S36.
**Open-source base.** none, and deliberately. Refusals are deterministic and in code. NeMo Guardrails and Llama Guard stay declined: two policy engines produce two answers to "may I", and the disagreement shows up as behaviour rather than as an error.
**Speed & efficiency.** ≤10ms, in-process, model-independent — which is also why it cannot be talked
around.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | safety and honesty at 100 across five consecutive runs | 2 / 4 / 2 d | 20 min (agree the precedence order) | 0 | grades default to today's binary refuse/allow |

**Floor**
- [x] 44.F1 Deterministic catastrophic refusal, model-independent.
      *gate:* `test_safety_autonomy.py`, `test_security_hardening.py`
- [x] 44.F2 Anti-fabrication protocol with a verification path.
      *gate:* `test_tool_failure_guard.py`, `test_no_result_sentinels.py`
- [x] 44.F3 Autonomous work defers every outward action. *gate:* `test_safety_autonomy.py`
- [x] 44.F4 Refusals are graded: refuse · confirm · proceed-with-note, and the grade is tested.
      *gate:* `test_refusal_grades.py`
      *done 2026-09-13:* there were two answers and no name for either — a deterministic refusal in
      the agent, a confirm tier in the policy layer, and nothing in between. The gap showed as
      silence. Guest mode is on because someone else is in the room and lockdown is on because he
      wants quiet; both changed what Afon did and neither said so, since the only way to tell him
      something was to refuse. `proactive.grade` returns one of refuse / confirm / note / allow with
      a reason, `PRECEDENCE` states the order strongest-first, and the gate pins that a permissive
      rule can never soften a stronger one by matching later. Two decisions worth recording: the
      strongest grade is decided by what the owner ASKED, not by which tool the model reached for,
      because a model can carry out a catastrophic instruction with a harmless-looking call; and a
      grader that cannot do its job returns CONFIRM, never ALLOW — an unavailable hard-refusal check
      must mean "ask", since the alternative is an import error quietly permitting a wiped disk. The
      confirm grade IS `confirm_required` rather than a second copy of it.

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

**Stack.** Today: proactive action reports with reasoning, a scrubbed audit trail, `diagnose` and
the HUD. **Add:** `why` as a first-class question about the last turn, answered from the audit trail
without an LLM call for the factual part.

**Verified by.** Why-last-turn tests · confidence reported where it varies · source attribution in
spoken answers · replay of any turn from the last 30 days.

**Options weighed.** *Build:* answer from the audit trail · post-hoc LLM explanation · a full trace
UI → **the audit trail.** A model asked to explain a decision it did not make will produce a
plausible story, which is worse than no explanation; the trail is what actually happened. *Test:*
why-last-turn · source attribution in speech · replay of an old turn → **all three.**
**Open-source base.** `opentelemetry-sdk` (Apache-2.0) to promote today's per-turn correlation id into real spans, with **Jaeger (Apache-2.0)** as an optional local viewer. Arize Phoenix is declined on licence — Elastic Licence 2.0 is not OSI-approved and the owner's requirement is free and open source.
**Speed & efficiency.** ≤300ms and **no LLM call for the factual part** — tools, sources and
confidence come from the trail; the model only phrases it.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| grounded | replaying any turn from the last 30 days on request | 2 / 3 / 1 d | — | 0 | `why` is a new tool; remove the registry line |

**Floor**
- [x] 45.F1 Autonomous actions produce reports with reasoning. *gate:* `test_proactive_report.py`
- [x] 45.F2 A full audit trail with scrubbed values. *gate:* `test_phasex_audit_health_modes.py`
- [x] 45.F3 `why` as a first-class question about the *last turn*: tools used, sources, confidence.
      *gate:* `test_why_last_turn.py`
      *done 2026-09-13:* asked of the model, "why did you say that" is answered by a model
      reconstructing its own reasoning after the fact — the one source on the subject with no access
      to the facts. It will name a tool it did not call, fluently, because the question invites a
      story and nothing contradicts it. The turn row already knew which tools fired; it now also
      carries the hosts actually retrieved (from 41.F2's ledger) and a confidence label READ off the
      reply rather than invented, because a number Afon made up about his own certainty is the least
      trustworthy field in the row. `turn_trace.last()` exposes the closed row and the answer says
      plainly when NO tool was used — the most useful sentence in the feature, since an answer from
      the model's own weights looks identical to a looked-up one and the owner has no other way to
      tell. It costs no per-turn tool surface: it rides `diagnose` under `about='last_turn'`, which
      is already the "explain yourself" tool and is already core.

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

**Stack.** Today: nothing — `anticipation.py` is a calendar look-ahead. **Add:** one predictor
with a scoring ledger, using statsmodels or plain regression.
**Declined until a naive baseline is beaten:** Prophet / NeuralProphet / Chronos / Nixtla. A
foundation forecasting model over 30 days of one person's calendar is decoration; the honest first
step is a baseline and a ledger that proves you beat it.

**Verified by.** Backtest against the naive baseline · calibration of stated confidence · suppression
below the confidence floor · a month of scored predictions.

**Options weighed.** *Model:* a naive baseline + a scoring ledger · Prophet ·
Chronos/TimesFM foundation models → **baseline first, then `statsforecast` if it is beaten.**
A foundation forecasting model over 30 days of one person's calendar is decoration, and Prophet's
strength (yearly seasonality) needs years. *Test:* backtest against the naive baseline · calibration
of stated confidence · suppression below the floor → **all three**; the ledger is the point.
**Open-source base.** statsmodels (BSD-3) or Nixtla `statsforecast` (Apache-2.0); Prophet (MIT) if
seasonality ever matters.
**Speed & efficiency.** ≤200ms in-process, no LLM · predictions computed on the daily tick, not per
question · nothing spoken below the confidence floor (cheaper *and* more honest).

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| **guess** | a month of scored predictions beating the naive baseline | 3 / 5 / 2 d | — | 0 | predictions are suppressed by default until the ledger justifies them |

**Floor** *(one predictor, honestly scored, before any second one)*
- [x] 46.F1 A single predictor: **will today's plan fit the day**, from calendar + task estimates +
      historical overrun. *gate:* `test_forecast.py`
      *done 2026-09-13:* `brain/forecast.py` — committed minutes against available ones, arithmetic,
      in-process, no model call. `anticipation.py` reads what is already written in the calendar and
      reads it back; that never says anything that could turn out to be wrong, so it can never be
      checked, which is why it is a look-ahead and not a forecast. The assumption a day rests on —
      thirty minutes for an open task with no estimate — is carried in the prediction's own basis
      and spoken with it, rather than buried as a constant. It reads the calendar through
      `schedule._parse`, so the forecast and the clash report cannot disagree about what counts as
      busy: someone's all-day birthday must not make a day overbooked. It answers inside
      `day_clashes`, where the owner already asks about his day, because a per-turn schema slot is
      charged on every turn whether or not anyone asks.
- [x] 46.F2 Every prediction is recorded with its outcome so accuracy is measurable.
      *gate:* `test_forecast.py` [scoring ledger]
      *done 2026-09-13:* written down before the day happens, scored after, and **the naive
      baseline is reported beside the score every time**. "Seventy-two percent correct" means
      nothing on its own: if most days fit, always saying so scores seventy-two percent too, costs
      nothing to run, and cannot be wrong in an interesting way. The plan declined Prophet and the
      foundation models until that baseline is beaten and the ledger is what will settle it. A
      prediction too weak to speak is recorded anyway — scoring only the confident ones would make
      the accuracy figure flattering by construction.
- [x] 46.F3 A prediction below a confidence floor is not spoken.
      *gate:* `test_forecast.py` [suppression]
      *done 2026-09-13:* below the floor it says nothing — not a hedged version of the same claim,
      nothing. Confidence is distance from the boundary MULTIPLIED by how much has been scored, and
      the multiplication is the whole point: the first draft added the two, so a blatantly
      overbooked day scored high enough to speak with a track record of nothing, which is exactly
      the failure this task forbids. Multiplied, having been checked is a precondition rather than
      a bonus, so a new predictor is silent for its first fortnight and a day sitting right on the
      line stays silent forever — that answer turns on a rounding error in somebody's meeting
      length.

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

**Stack.** Today: nothing. **Add:** one sqlite table (item, quantity, location, consumed-at) and
one category the owner picks. Barcode scanning via the phone camera only once the table is in daily
use.
**Declined:** a digital twin / CAD integration. That is S49's problem and it is parked.

**Verified by.** Depletion estimation against real consumption · reorder trigger timing · a
no-invention assertion (unknown stock stays unknown) · a quarter with nothing running out unannounced.

**Options weighed.** *Build:* one sqlite table · **Grocy** (a real self-hosted inventory app) · a
spreadsheet → **the table first.** Grocy is genuinely good and is the fallback if the table proves
too bare — but it is another service, another UI and another source of truth, and the first question
(which category is worth tracking at all) is unanswered. *Test:* depletion estimation against real
consumption · reorder timing · a no-invention assertion → **all three.**
**Open-source base.** Grocy (MIT) held in reserve; a phone-camera barcode path only once the table is
in daily use.
**Speed & efficiency.** ≤100ms local queries · depletion estimated from logged consumption, not
guessed · no background job until there is data to sweep.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| **guess** | him naming the first category | 2 / 4 / 1 d | 10 min (the category) + ongoing entry | 0 | the table is standalone; drop it |

**Floor** *(one category, one store, no framework)*
- [x] 47.F1 A local inventory store with add/consume/query, seeded with **one** category the owner
      chooses. *gate:* `test_inventory.py`
      *done 2026-09-13:* `brain/stock.py` — one sqlite file, two tables, no framework, and Grocy
      held in reserve exactly as the plan records. The first category is the one the blocker table
      defaulted to: **Afon's own consumables**, seeded from numbers he can read himself — free disk
      on the state volume and archives in the backup folder — taken by the hygiene job that already
      runs daily. That matters more than it sounds: a run rate cannot form in a table nobody writes
      to, so the alternative was a schema waiting for someone to start typing. A physical category
      the owner names later is another string in the same three columns. Two tools rather than
      four: adding and using are one act with a sign, and they are not confirm-gated for the reason
      task capture is not — an inventory only works if recording something costs less than
      remembering it.
- [x] 47.F2 Consumption is recorded with a date so depletion can be estimated at all.
      *gate:* `test_inventory.py` [history]
      *done 2026-09-13:* every change is an event row with its timestamp and its sign. And
      depletion is **refused with a reason** until the history can carry it: one recorded use is a
      sample of one, and two inside the same hour is a busy afternoon rather than a rate. "I've no
      estimate of how long that lasts yet, because there's only one recorded use" is a real answer;
      a number derived from that one use would be arithmetic dressed as a forecast.
- [x] 47.F3 Unknown is unknown — Afon never guesses stock he was not told about.
      *gate:* `test_inventory.py` [no invention]
      *done 2026-09-13:* consuming an item nobody has recorded is refused rather than creating it,
      because creating it on the way down would have to invent what was there before. Asked about
      something unrecorded he says he has nothing recorded and that this **is not the same as
      none**, and states no quantity at all. The two answers send a person to different places —
      one to the shop, one to the shelf to look — and an inventory that quietly turns the second
      into the first is worse than no inventory, because it will be believed.

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

**Stack.** Today: one-way delegation to the fleet plus the local worker. **Add:** a shared work
record in sqlite with claim-and-lock, and deterministic result merging.
**Declined:** NATS / Redis Streams / a shared vector memory. Coordination infrastructure is sized for
many agents; there are two participants and one owner-facing voice.

**Verified by.** Double-claim prevention · merge determinism (disagreement surfaced, never averaged)
· failure isolation · one genuinely multi-worker job end to end.

**Options weighed.** *Build:* a shared work record in sqlite with claim-and-lock · NATS · Redis
Streams → **the shared record.** Coordination infrastructure is sized for many agents; there are two
participants and one owner-facing voice. *Test:* double-claim prevention · merge determinism ·
failure isolation → **all three**; disagreement must surface, never average.
**Open-source base.** none while there is one worker. When a second concurrent worker exists, the claim table is one Postgres row lock (`FOR UPDATE SKIP LOCKED`) — which is the whole of what Ray was proposed to provide here.
**Speed & efficiency.** Coordination overhead ≤10% of task time · claims are a single transaction ·
progress aggregated, not polled.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| reasoned | one genuinely multi-worker job producing a single coherent report | 3 / 4 / 2 d | — | 0 | claim/lock is additive; delegation still works without it |

**Floor** *(depends on S39's tracking floor)*
- [x] 48.F1 A shared work record: every delegated unit has an id, an owner, a state, and a result
      slot that both sides write to. *gate:* `test_coordination.py`
      *done 2026-09-13:* `brain/coordination.py` — one sqlite table, and S39 writes to the same one.
      They are the same question at two sizes: one delegation posts a one-unit job and reads the
      answer back, a multi-worker job posts several, hands them out and merges. A second table for
      the second case would have been these columns under a different name.
- [x] 48.F2 No two workers hold the same unit — claim-and-lock, asserted.
      *gate:* `test_coordination.py` [no double claim]
      *done 2026-09-13:* a claim is a conditional `UPDATE ... WHERE state='open'`; the row either
      moves to you or it does not and `rowcount` says which, so a loser retries the next candidate
      rather than sharing a unit. sqlite's own write lock is the mutex — no lock server. The gate
      races it with eight threads over twenty units rather than reading the SQL and agreeing it
      looks atomic, because "it looks atomic" is exactly the reasoning a claim bug survives.
- [x] 48.F3 Results merge deterministically, and disagreement is surfaced rather than averaged.
      *gate:* `test_coordination.py` [merge]
      *done 2026-09-13:* units group by their brief, because two units carrying the same brief were
      asked the same question and are comparable, while two different briefs are simply two parts of
      one job that follow each other in posting order. Where one brief has two different answers
      BOTH are kept and the brief is reported as a conflict; it is never resolved here. Picking the
      longer one, the newer one, or the mean of two numbers each produce an answer no worker
      actually gave, and the owner would have no way to tell which had happened. A failed unit and
      a unit nobody started are also kept apart — "no answer because it broke" and "no answer yet"
      are different things to be told.

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

**Stack.** Today: nothing, deliberately. When hardware exists the likely shape is `cadquery` or the
FreeCAD API for geometry, generated G-code, and OctoPrint or a CNC controller API for the machine —
with ROS2 only if there is a robot rather than a machine. None of it is written until 49.F1 answers
which machine.

**Verified by.** Dry-run/simulation before any live command · interlock verification · the decision
record existing at all.

**Options weighed.** Recorded now so the shape is agreed before anything is built. *Geometry:*
cadquery (Apache-2.0) · FreeCAD API (LGPL-2.1) · OpenSCAD → cadquery reads best from Python.
*Machine:* OctoPrint (AGPL-3.0) for FDM · LinuxCNC (GPL-2.0) for CNC · a vendor API. *Robotics:* ROS2
(Apache-2.0) **only if there is a robot rather than a machine.** All of it stays unwritten until
49.F1 names the machine. *Test:* dry-run/simulation before any live command · interlock verification
→ both, and neither is optional.
**Open-source base.** as above; all invoked as separate services or CLIs, never linked.
**Speed & efficiency.** Not applicable until hardware exists — and premature optimisation here would
be premature everything.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| **guess (correctly)** | naming the machine, in a decision record | 1 (decision) / — / — | **30 min (the decision)** | 0 | nothing is built |

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

**Stack.** Today: the Obsidian vault (VPS-authoritative), backups, checkpoints and the audit
trail. **Add:** a portable export (plain markdown + JSON) with integrity hashes — readable by a human
with no codebase, which is the actual requirement.
**Declined as a requirement:** S3 versioned object storage. Useful as an off-site copy later; the
preservation property comes from the format being open and the export being restorable, not from
where the bytes sit.

**Verified by.** Cold-start rebuild on an empty machine · integrity verification · retrieval across
versions · scheduled portable-restore drill.

**Options weighed.** *Export:* plain markdown + JSON with integrity hashes · S3 versioning ·
IPFS → **the plain export.** The preservation property comes from the format being open and the
export being restorable, not from where the bytes sit; versioned object storage is a useful off-site
copy and not the mechanism. *Test:* a cold-start rebuild on an empty machine · integrity verification
· retrieval across versions → **all three**; the rebuild drill is the only one that proves it.
**Open-source base.** restic (BSD-2, shared with S22) for the off-site copy; git for the vault; **ArchiveBox (MIT)** for link rot — a knowledge base meant to outlive its sources cannot hold only URLs, and this is the one system where the failure is silent until the day it matters.
**Speed & efficiency.** Monthly export ≤10 min · incremental where possible · hashing streams rather
than loading whole files.

| Confidence | Settled by | Effort F/R/E | Owner | €/mo | Rollback |
|---|---|---|---|---|---|
| reasoned | a cold-start rebuild from the export alone | 4 / 4 / 3 d | 15 min (off-site target) | 1–3 (shared with S22) | the export is read-only; nothing depends on it |

**Floor**
- [x] 50.F1 **A portable export**: memory, decisions, documents and audit summary written as plain
      markdown + JSON that a human can read without the codebase.
      *gate:* `test_portable_export.py` — export produced and re-read by a fresh process.
      *done 2026-09-13:* `protocols/portable.py`. The substrate was all there — vault, backups,
      checkpoints, audit — and none of it delivered the property. A backup is a tar of this
      program's private layout: sqlite whose schema lives in the code, a `learned` tree whose
      meaning is a docstring, a JSONL whose fields are named in a dataclass. **Restoring it needs
      Afon**, which is the opposite of preservation. The export is markdown and JSON with a README
      in plain language and a sha256 per file. Two decisions worth recording. The audit is
      **summarised, not copied** — tool counts per day, never the values those tools were handed,
      and the export says so where a reader will see it. And a store that was not there is named in
      the manifest as absent rather than omitted, because an export missing half the memory
      otherwise looks exactly like an export of half as much memory. The gate's load-bearing check
      shells out to an isolated interpreter that cannot import `afon` and has it read the whole
      thing; asserting portability from a process that has already imported the package proves
      nothing, since every helper it reaches for is the thing meant to be unnecessary.
- [x] 50.F2 The export is verified by restoring it into an empty environment, on a schedule.
      *gate:* `test_backup_restore.py` [portable restore]
      *done 2026-09-13:* `run_export_drill` writes into a fresh temporary directory and reads it
      back, daily at 05:20, twenty minutes after the backup drill so the two logs stay readable.
      Into an EMPTY directory on purpose: verifying the live export folder would pass on files left
      by a previous run, which is precisely what a drill exists to catch. It is silent on success
      and speaks only on failure, for the same reason 22.F4 gives — a drill that congratulates
      itself daily teaches the owner to ignore it. The two drills check different properties and
      fail for different reasons: the backup drill proves Afon can read his own archive, this
      proves a person could read the export without him.
- [x] 50.F3 The vault-write rule is enforced in code, not only in documentation: writes go to the
      authoritative host or fail loudly. *gate:* `test_vault_search.py` [50.F3]
      *done 2026-09-13:* the rule was a sentence in a document, and a sentence is not a mechanism.
      The only thing between a dictated note and a directory that gets nuked-and-replaced was
      `AFON_VAULT_WRITABLE` being set correctly on every host, forever — set it wrong once on the
      laptop and every note would be written, reported as saved, and deleted by the next pull, with
      nothing anywhere recording that it happened. The replica already carries proof of what it is:
      the one-way sync leaves its own pull script and log in the root, and the authoritative copy
      cannot have them because it is the side being pulled FROM. So `replica_reason` reads the
      directory, and where the flag and the directory disagree **the directory wins** — it is the
      thing that will actually lose the note. The refusal is loud: recorded where failures are
      counted, and it names the authoritative host rather than ending the conversation with a
      pleasant sentence that leaves no trace. The gate also holds the single-writer rule, so the
      check cannot be walked around by a second module opening a file under the vault path.

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
| S01 | Brain / Core Intelligence | structured badly | 3/3 | 0/4 | 0/3 |
| S02 | LLM Integration | complete for now | 3/3 | 0/3 | 0/1 |
| S03 | Tool Utilization | complete for now | 4/4 | 0/6 | 0/1 |
| S04 | Device Control | floor green | 4/4 | 0/3 | 0/1 |
| S05 | Browser Control | floor green | 3/3 | 0/3 | 0/1 |
| S06 | Document Creation | half-built | 3/3 | 0/3 | 0/1 |
| S07 | Session & Context | floor green | 3/3 | 0/3 | 0/1 |
| S08 | Voice Enrollment | weak | 1/2 | 0/3 | 0/1 |
| S09 | Face Enrollment | not enrolled | 1/2 | 0/3 | 0/1 |
| S10 | Voice Recognition | structured badly | 3/3 | 0/3 | 0/1 |
| S11 | Face Recognition | structured badly | 3/3 | 0/3 | 0/1 |
| S12 | Multi-Device | floor green | 3/3 | 0/4 | 0/1 |
| S13 | Notifications | floor green | 3/3 | 0/3 | 0/1 |
| S14 | Proactivity | floor green | 3/3 | 0/3 | 0/1 |
| S15 | Recommendations | missing | 3/3 | 0/3 | 0/1 |
| S16 | Task Queue | floor green | 3/3 | 0/3 | 0/1 |
| S17 | Morning Brief | floor green | 3/3 | 0/3 | 0/1 |
| S18 | Mic & Speaker | complete for now | 4/4 | 0/3 | 0/1 |
| S19 | Composio / MCP | floor green | 3/3 | 0/3 | 0/1 |
| S20 | External APIs | floor green | 3/3 | 0/3 | 0/1 |
| S21 | Personal Time Mgmt | floor green | 3/3 | 0/3 | 0/1 |
| S22 | Recoverability | complete for now | 4/4 | 0/3 | 0/1 |
| S23 | 24/7 Reachability | complete, parked | 4/4 | 0/3 | 0/1 |
| S24 | Knowledge & World Model | structured badly | 2/2 | 0/3 | 0/1 |
| S25 | Personality | complete for now | 3/3 | 0/3 | 0/1 |
| S26 | Multi-Modal Perception | floor green | 3/3 | 0/3 | 0/1 |
| S27 | Context Awareness | floor green | 2/2 | 0/3 | 0/1 |
| S28 | IoT Orchestration | token blocked | 2/3 | 0/3 | 0/1 |
| S29 | Automation & Workflow | floor green | 4/4 | 0/3 | 0/1 |
| S30 | Persistent Memory | structured badly | 3/3 | 0/9 | 0/2 |
| S31 | Self-Monitoring | complete for now | 4/4 | 0/6 | 0/1 |
| S32 | Redundancy & Failover | partly missing | 4/4 | 0/3 | 0/1 |
| S33 | Goal & Project Mgmt | floor green | 3/3 | 0/3 | 0/1 |
| S34 | Health & Wellness | half-built | 3/3 | 0/3 | 0/1 |
| S35 | Crisis Response | half-built | 3/3 | 0/3 | 0/1 |
| S36 | Security & Access | complete for now | 5/5 | 0/3 | 0/1 |
| S37 | Privacy & Governance | half-built | 3/3 | 0/3 | 0/1 |
| S38 | Communication Hub | structured badly | 3/3 | 0/3 | 0/1 |
| S39 | Multi-Agent Delegation | thin | 3/3 | 0/3 | 0/1 |
| S40 | Financial & Asset Mgmt | missing | 3/3 | 0/3 | 0/1 |
| S41 | Research & Synthesis | half-built | 3/3 | 0/3 | 0/1 |
| S42 | Media Control | structured badly | 3/3 | 0/3 | 0/1 |
| S43 | Presence & Continuity | half-built | 3/3 | 0/3 | 0/1 |
| S44 | Ethics & Safety | complete for now | 4/4 | 0/3 | 0/1 |
| S45 | Explainability | complete for now | 3/3 | 0/3 | 0/1 |
| S46 | Predictive Analytics | missing | 3/3 | 0/3 | 0/1 |
| S47 | Inventory & Resources | missing | 3/3 | 0/3 | 0/1 |
| S48 | Multi-Agent Coordination | thin | 3/3 | 0/3 | 0/1 |
| S49 | Fabrication Control | parked by decision | 0/3 | 0/0 | 0/0 |
| S50 | Legacy Continuity | half-built | 3/3 | 0/3 | 0/1 |

**Totals: 151 of 157 floor tasks green, 0 of 161 raise tasks, 0 of 52 elite tasks — 151 of 370.**

> The **424 engineering-days** figure at the top of this document, and the per-system `Effort F/R/E`
> columns, predate the four raises added on 2026-09-12 (03.R5, 03.R6, 31.R5, 31.R6). They are
> therefore an undercount, and deliberately left uncorrected rather than adjusted by guess: a
> re-estimate belongs in the per-system tables where each number can be defended, not in a total
> nobody can trace. Two of the four block the unpark gate and are the only raises that do.
Wave 0's floors are complete as of 2026-08-16: the loop registry (31.F4), the scheduled restore
drill (22.F4), one home per secret (36.F5) and the unpark checklist (23.F4).** The floors are the furthest along because the last three weeks of work were
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

## Everything waiting on the owner — and the default that ships without him

Revised 2026-09-11. A plan that stalls on a decision nobody made is a plan that does not run, so
**every blocker below now has a default that ships and is replaced later without rework.** Nothing
in the wave plan waits on an answer any more. Three items genuinely need him and cannot be
defaulted; they are marked **OWNER** and together cost under an hour.

| Was blocking | Blocks | Default that ships now |
|---|---|---|
| Home Assistant long-lived token | S28 | **OWNER — 15 min.** Nothing substitutes: it is a credential on his own hardware. Until then S28 reports `is_not_configured` rather than failing, and the rest of Wave 4 proceeds. |
| Twilio credentials | S35 ladder, S38 voice | **Retired as a blocker.** ntfy carries the push ladder, `signal-cli` reaches a third party. Twilio becomes an optional upgrade for an actual phone call, not a prerequisite. |
| Google Maps key | S27 location detail | **Retired.** `geopy` against Nominatim/OpenStreetMap: free, no credential, no quota. |
| Fitness API enablement; unknown wearable | S34 | **Retired.** Ingest is built against FIT and the Apple Health XML export — open formats every mainstream device produces. Naming the watch later changes no code. |
| Voiceprint re-enrolment | S08, and S10/S11 fusion | **OWNER — 10 min** of reading the script aloud. No substitute exists: it is his voice. Enrolment quality scoring already refuses a bad capture, so one sitting is enough. |
| Sit for the ArcFace face capture | S09, S11 | **OWNER — 5 min.** Same reason. The two biometric sittings are the only hard human dependencies in the whole plan. |
| `routines.json` real content | S21, S17 evening brief | **Retired.** Afon drafts routines from observed calendar and activity, then asks him to approve or edit — a five-line correction beats a blank file, and reviewing a draft is not a blocker. |
| Merge direction for laptop ↔ VPS state | S30, S32 | **Done.** 30.F1 executed: one state root per host, derived stores from the brain, learned facts unioned. |
| `.env` → password store; key rotation | S20.R3, S36.F5 | **Retired as a blocker, kept as a task.** The migration is scripted and idempotent; rotation is a separate later step that does not gate the store move. |
| Elevated `Disable-ScheduledTask` | keeps the park honest | **Retired.** The maintenance lock already refuses autostart and is gated by `test_maintenance_lock.py`; the scheduled task is belt to that braces. |
| First inventory category | S47 | **Defaulted** to what Afon can populate without asking: its own consumables — subscriptions, credentials with expiry dates, disk and backup capacity. Real data on day one, and his first physical category slots into the same schema. |
| First fabrication decision | S49 | **Deferred, not blocked.** S49 stays unstarted until a machine exists. An unstarted system with a stated reason is honest; a half-built driver for hardware nobody owns is not. |
| No logged decisions | S15, and S14's bandit | **Defaulted to logging on**, with a kill switch. Logging is the thing that produces the corpus, so leaving it off was the blocker. |
| No forecast baseline | S46 | **Defaulted** to what Afon already measures: per-turn latency and provider failure rate. A naive seasonal baseline exists the day the data does, and the personal variables arrive later against the same harness. |

**The whole of the remaining owner time: one token, one voice sitting, one face sitting.** Under an
hour, and nothing else in 424 engineering-days depends on him.
