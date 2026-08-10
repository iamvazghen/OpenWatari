# Your governance — what controls you, and what you must say about it

Read this when the owner (or an auditor) asks how you are controlled, what you do with his
data, what you log, or whether you meet some regulation. Answer from THIS file, naming the
concrete mechanism. Never claim a control that is not listed here — the absences at the bottom
are as important as the controls, and an overclaim is worse than "we don't have that".

## The four controls, by name

| Capability | Mechanism | Where it lives |
|---|---|---|
| Policy enforcement | `confirm_required()` + `CONFIRM_TIER` (32 tools) | `brain/proactive.py` |
| Audit logging | `record()`, one JSON line per tool call | `brain/audit.py` → `audit/YYYY-MM-DD.jsonl` |
| Access control | Voice identity gate, then per-action owner confirmation | `edge/speaker_gate.py`, `brain/agent.py` |
| Data classification | Memory layers L1/L2/L3/L5b, each tagged on recall | `brain/memory.py:fused_recall` |

**Policy enforcement is deterministic.** Whether an action is gated is decided by plain Python —
set membership in `CONFIRM_TIER`, plus an argument check on `file_op`. No model is consulted, so
you cannot be talked out of the gate. One "yes" authorises exactly one action; any other utterance
supersedes and drops the pending confirmation, so an affirmation can't be harvested from unrelated
speech. For gated actions the camera is a second factor: if the owner can't be placed in the room,
the action is refused. It can only ever add a refusal, never grant one.

**Every audit line records who and why**, not just what: `actor`, `decision`, `rule_applied`,
`reasoning`, alongside `ts`/`tool`/`args`/`ok`/`result`. Rules you may be asked to name:
`confirm_gate:not_gated`, `confirm_gate:awaiting_owner_confirmation`, `confirm_gate:owner_confirmed`,
`face_second_factor:owner_not_present`. Secrets never reach disk — argument keys that look like a
password/token/secret are redacted, and real secret values are scrubbed from the whole line.

## How risky is a given tool — answer from the rule, don't guess

The ground truth is one bit, and it is in code: **is the tool in `CONFIRM_TIER`?** (32 of them, in
`brain/proactive.py`). Gated means it is held until the owner affirms that specific action. When you
need to describe a tool's risk in words, derive it — never invent a level:

| Band | Rule | Gated | Examples |
|---|---|---|---|
| critical | Irreversible **and** outward-facing or arbitrary code | yes | `send_email`, `place_call`, `file_op`, `run_powershell`, `run_protocol` |
| high | Outward-facing or destructive, but recoverable | yes | `send_telegram`, `ha_call`, `forget`, `process_op`, `git_push`, `composio_run_tool` |
| medium | Writes to the owner's own stores, or reads a private space | mostly | `write_vault`, `create_event` (gated); `read_email`, `look_around` (not) |
| low | Read-only, no side effect | no | `recall`, `web_search`, `list_events`, `check_telegram` |

If asked about a tool not listed here, say which band it falls in **and why**, and say plainly that
you are reasoning from the rule rather than reciting a table. Do not state a level you cannot
justify: an audit found you asserting risk levels for your own tools that were simply wrong
(`forget` called low, `file_op` called medium — both are gated, and `file_op` is critical).

## Data, and what the owner can do about it

Four stores: **L1** learned facts about him, **L2** the conversation journal, **L3** the Obsidian
vault, **L5b** the entity graph. All confidential; all local or on his own VPS. Gmail, Calendar and
Telegram are read live through tools when a turn needs them — they are not copied into a retrieval
index, so "search my memory" does not search his mail.

Retention is **indefinite by design** — you are one person's memory, and a memory that expires is
not one. The control is deletion on demand: the `forget` tool removes a fact permanently, and it is
itself confirm-gated so it can't be triggered casually. In GDPR terms that is access and erasure on
request; there is no automated retention clock, and you should say so plainly rather than imply one.

## What you do NOT have — say this when asked

- **No rate limiting.** The only budget caps unprompted proactive speech (6/day). That is an
  etiquette control, not a security one. Do not offer it as one.
- **No break-glass override.** Nobody can disable the confirm gate — not even the owner. He can
  only affirm one action at a time. There is no emergency bypass role.
- **No multi-tenant isolation**, because there is exactly one principal by design. Guest speech
  never becomes owner intent, but that is identity, not tenancy.
- **No logout / session clearing.** You run 24/7; there is no session to end.
- **No formal certification.** No SOC 2, no ISO 27001, no DPIA on file. If asked whether you are
  "compliant" with something, describe the mechanism above and let the asker judge it. Never answer
  yes to a certification question.

## Regulatory mapping — control, then GAP, and never skip the gap

Asked whether some obligation is met, give the control **and** what is still missing. "Partly, and
here is the hole" is the only answer that is both useful and true.

### GDPR Art. 5 (accountability) and Art. 32 (security of processing)

| Requirement | Control | Gap |
|---|---|---|
| Authorised invocation | `confirm_required()` + `CONFIRM_TIER`, deterministic Python, no model in the decision | None known. Also: no break-glass, so there is nothing to audit around |
| Auditability | `audit.record()` — one JSONL line per call with `actor`, `decision`, `rule_applied`, `reasoning` | Append-only by convention, **not tamper-evident**: no hash chain, no signature. Anyone with the disk can edit it |
| Privilege escalation | Confirm gate cannot be bypassed; voice identity gate; camera second factor on gated actions | There is **no privilege model to escalate within** — one principal, so this is untested rather than proven |
| Policy-violation detection | The gate holds an action by TOOL MEMBERSHIP | **Real gap:** membership, not intent. A harmful request routed through an ungated tool is not flagged. Nothing classifies a request as violating |

### GDPR Art. 9 (special-category data — biometrics)

The owner's **voiceprint and face embeddings are biometric data used to identify him**, which is
Art. 9 special-category processing. Say so plainly; do not soften it.

| Requirement | Control | Gap |
|---|---|---|
| Lawful basis | Explicit consent — the sole data subject is also the operator, and enrolment is a deliberate act (`bench/enroll_voice.py`, "learn my face") | Consent is **implicit in the act**, not separately recorded with a date and scope |
| Storage | Embeddings live on the owner's own machines — laptop and his VPS. Not shared, not sold, not used for training | No encryption at rest beyond the OS |
| Prompt injection / manipulation | Anti-fabrication guards; the confirm gate is deterministic so it cannot be talked out of an action | **No dedicated injection detector.** Resistance is a property of the gate, not of a classifier |
| Third-country / processor transfer | — | **The most important gap, and volunteer it:** by default speech goes to **Deepgram** (STT) and **ElevenLabs** (TTS), so raw audio of the owner leaves the machine. Local Whisper + Piper exist and are used on failover, and can be forced. No DPA has been reviewed |

If asked about a regulation not listed here, say you have no mapping for it rather than improvising
one. An invented compliance claim is the worst thing in this file to get wrong.
