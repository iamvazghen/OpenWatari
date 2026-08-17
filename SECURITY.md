# Security

OpenAfon is a framework for a high-trust agent (the assistant, **Afon**): a deployed instance
runs shell commands, drives a real browser, sends messages and email, controls smart-home devices,
and edits and commits its own source code. This document describes the safety model that makes that
trustworthy, what is kept out of version control, and how to report a problem. **If you fork and
deploy this, you are the operator — read §11.** (*Afon* is only the blueprint/inspiration; the
internal Python package is named `afon` for stability.)

> **Design principle: least surprise, full reversibility, deny-by-default.** Every consequential
> action is either confirmed first, reversible, scoped, or all three. A missing credential degrades
> to a spoken "not configured" note — never a crash and never a silent failure.

> **Supported version.** This is a single-tree project; security fixes land on the default branch.
> Run a recent checkout. There is no separate LTS line.

---

## 1. Secrets & credentials

**Secrets never live in code and never enter version control.**

- All secrets are read from environment variables (prefix `AFON_`) via a local **`.env`** file,
  which is **git-ignored**. Only `.env.example` (no values) is committed.
- The following are explicitly excluded from git (see [`.gitignore`](.gitignore)):
  `.env` and any `.env.*` (except the example), `*.session` / `*.session-journal` (Telegram/Telethon
  logins), `voiceprint.json` (your biometric template), `afon_jobs.sqlite`, `audit/`, `backups/`,
  `.afon-browser/` (the persistent browser profile, which holds login cookies).
- **Afon's learned memory is not in the repository at all.** Everything he writes about you — the
  learned facts, the daily journal, every sqlite store, the relationship model — lives under
  `AFON_STATE_DIR` (default `~/.afon`), per host, outside the tree. There is nothing to
  accidentally commit and nothing for a deploy to copy to another machine (SYSTEMS.md 30.F1).
- The **audit log redacts secrets two ways**: (1) any tool argument whose *key* looks like a
  password / token / secret / api_key / auth / credential is written as `***redacted***`; and (2) a
  value-level scrub removes any actual secret *value* from `.env` (API keys, tokens, protocol
  passwords) from the whole line — so a secret that leaks into a tool *result* (e.g. PowerShell
  echoing an env var) never reaches disk. Protocol passwords are never logged or spoken.
- If you publish this repo, make it **private** — the committed context (`memory/*.md`,
  `personality/afon.md`) is personal, and host/service details may be sensitive.

**Token scopes.** Use the narrowest credential that works:
- GitHub: a **fine-grained PAT** limited to the one repo, **Contents: read/write** only.
- Notion: an **internal integration**, and **share only the specific pages** you want Afon to
  touch (Notion is deny-by-default — the integration sees nothing else).
- Google: the OAuth app is yours; keep yourself as the only test user.

---

## 2. Confirmation tier (outward-facing & destructive actions)

Afon **confirms before acting** on anything outward-facing or hard to undo. This is **enforced in
code**, not merely prompted: the agent's tool-execution path (`brain/agent.py::_execute_calls`) calls
`brain/proactive.py::confirm_required` and, for a confirm-tier tool, **blocks the call**, hands the
model a `CONFIRM_REQUIRED` sentinel so it reads the action back, and runs it only after the next user
turn affirms it (`_is_affirmation`). One "yes" authorises exactly one action (the grant is consumed).
So even a weak model that ignores the prompt physically cannot fire a consequential tool unprompted.
The tier (`CONFIRM_TIER`) covers all 32 of these. `bench/test_confirm_tier_documented.py` fails if
this list and the code ever disagree — security prose that drifts from the enforcing code is worse
than no prose, because it is the thing an auditor reads instead of the code.

- Messaging/outbound: `send_telegram`, `send_email`, `send_push`, `place_call` (Twilio — a wrong
  number or wrong words cannot be recalled).
- Machine: `file_op` (deletes), `process_op` (kill/start), `run_powershell`, `browser`.
- Calendar/home/Notion: `create_event`, `ha_call` (locks/alarms/covers especially),
  `notion_append`, `notion_comment`, `notion_create_page`.
- Self-improvement: `write_source`, `git_commit`, `git_push`, `git_revert`.
- Outward-facing filing: `create_github_issue` (posts to a shared repo).
- Protocols and stored sequences: `run_protocol`; `run_macro` and `invoke_skill` are gated
  **dynamically** — only when their own steps contain a confirm-tier tool.
- Destroying owner state (no undo): `delete_task`, `notion_delete_task`, `delete_macro`, `forget`,
  `drop_objective`, `complete_objective`, `write_vault` (a wrong note in the durable knowledge base
  is worse than no note, because it gets trusted later).
- Gated **dynamically**, so the frictionless case stays frictionless:
  - `update_task` / `notion_update_task` — adding a note or nudging a deadline is free; overwriting
    a title or blanking a field is not.
  - `notion_complete_task` — completing one task by name is free; "mark everything done" sweeps the
    whole open list in one call and is gated.
  - `composio_run_tool` — gated when the resolved slug is a WRITE.

Creating and updating tasks is deliberately **not** gated: capture-by-voice has to be frictionless
or it does not get used.

Reads and lookups (recall, web/vault search, weather, `read_email`, `read_chat`, `git_status`, …)
need no confirmation — they have no side effects.

**Clarify before guessing.** An ambiguous request ("do it", a bare pronoun) triggers a clarifying
question instead of a risky guess (`needs_clarification`).

---

## 3. Filesystem & system guardrails

- **Destructive file ops refuse protected paths.** `file_op` deletes are blocked under
  `AFON_SYSTEM_PROTECTED_PATHS` (default `C:\Windows`, `C:\Program Files`,
  `C:\Program Files (x86)`) and drive roots.
- **System file ops also refuse Afon's own secrets/state.** `file_op` delete *and* overwrite
  refuse `.env`/`.env.*`, `*.session`, `voiceprint.json`, and the repo's `.git/`, `audit/`,
  `backups/`, `.afon-browser/` — the same set the coding tool blocks — so "delete the .env" or
  clobbering the voiceprint can never land through the system tools either.
- **Elevation is explicit.** `run_powershell(as_admin=true)` raises a Windows UAC prompt you must
  accept; Afon cannot silently elevate.
- **The browser is visible by default** (`AFON_BROWSER_HEADLESS=false`) so you can watch what it
  does and complete logins yourself.

---

## 4. Self-improvement guardrails (Phase 13)

Letting an agent edit its own code is safe here because the capability is deliberately constrained:

- **Repo-scoped.** `read_source` / `write_source` / `list_source` resolve paths and **refuse
  anything outside the repository** (no path traversal) and **refuse secrets** (`.env`, `*.session`,
  `voiceprint.json`, `audit/`, `backups/`, `.git/`).
- **Reversible-only git.** The git tools are `status`, `diff`, `log`, `new_branch`, `commit`,
  `push`, `revert`. There is **no** `reset`, `force-push`, `rebase`, `amend`, `clean`, or
  branch-delete tool — by design. History can be added to, never rewritten or destroyed; a revert is
  always a new commit.
- **Verify-then-commit.** He has `run_tests` and `lint`, and the self-improvement playbook instructs
  him to commit only green code, on a branch, after your confirmation.
- **Confirm-gated.** Writes, commits, and pushes are all in the confirmation tier.

Net effect: the worst case is an uncommitted bad edit (caught by tests) or a committed change you can
`git_revert`. Your work cannot be lost.

---

## 5. Protocols (privileged routines)

`goodnight` (stop), `phoenix` (restart Afon), `ragnarok` (restart the machine), `backup`
(archive memory), `ping` (phone push test), `diagnostics` (local health report), `auditpack`
(audit archive), and `checkpoint` (non-secret context archive) are
**password-gated**. Afon asks for the password first; it's compared in constant time
(`hmac.compare_digest`); a wrong or missing password runs nothing. **Change the default passwords**
in `.env` (`AFON_PROTOCOL_*_PASSWORD`) before deploying — the **`afon-setup`** wizard generates a
strong, unique password for all eight automatically, so a fork is never shipped on the placeholders.
Passwords are never spoken or logged.

The non-privileged routines (`briefing`, `focus`, `lockdown`, `guest`, `commute`, `panic`, `backup`,
`normal`) change *behaviour*, not the system, and need no password.

---

## 6. Speaker biometrics (optional)

With `AFON_SPEAKER_ID_ENABLED=true` and an enrolled voiceprint, Afon obeys **only your voice**
and ignores the TV or a guest. It's **off until you enroll** — and deliberately fails *open* (accepts
all) rather than risk locking you out if no profile exists. The voiceprint (`voiceprint.json`) is
git-ignored.

---

## 7. The OpenClaw fleet is gated

Afon can *consult* an external multi-agent fleet, but it touches shared infrastructure, so it is
**off by default** (`fleet_authorized=False`). Arm it deliberately per session, or for a deployment
via `AFON_FLEET_AUTHORIZED=true`. Until armed, `delegate_to_fleet` returns a safe sentinel and
performs no network action. Afon always re-voices any fleet result as himself.

---

## 8. Audit trail

Every tool call is appended (best-effort, fail-quiet) to `audit/<date>.jsonl` with a timestamp, the
tool name, **redacted** arguments, success flag, and a clipped result. This is the trust-and-debug
record: you can see exactly what Afon did. The audit directory is git-ignored (it can contain
personal context).

---

## 9. Network & privacy posture

- **Local-first.** Audio capture, wake-word, VAD, and (optionally) STT/TTS run on your machine.
  Cloud STT/TTS (Deepgram/ElevenLabs) are opt-in quality choices behind provider flags.
- **Loopback by default.** The brain WebSocket (`/voice`, `/control`) and the HTTP sidecar
  (`/talk` for the iPhone Siri path) bind `127.0.0.1`. To reach them from a phone/glasses, bind
  `0.0.0.0` **and set `AFON_API_AUTH_TOKEN`** — remote clients must then present the token as
  `Authorization: Bearer <token>` (native) or `?token=<token>` (browser/Shortcut). Requests without
  it are refused. The `afon-setup` wizard generates this token for you when you choose the VPS
  deployment shape. **Don't expose any port without the token.**
- **Prefer a private overlay.** Put the brain host on a private network (e.g. Tailscale/WireGuard)
  rather than the public internet; the bearer token guards the port, the overlay keeps it unroutable
  to the world in the first place.
- **Graceful degradation** means an unconfigured integration makes **no** network calls at all.

---

## 10. Reporting a vulnerability

This is a personal project, not a service. If you find a security issue in the code (e.g. a way to
bypass the path/secret guards, the confirmation tier, or the protocol password check), please open a
**private** report (a private issue or direct contact with the repository owner) rather than a public
issue. Include reproduction steps and the affected file/function.

---

## 11. Operator responsibilities (you, if you fork & deploy)

- **Run `afon-setup`** (or set them by hand): it generates the brain auth token and the eight
  protocol passwords so you never ship on defaults.
- **Keep your repository private** if it carries personal data — `memory/*.md` and
  `personality/afon.md` describe a real person and their accounts. A clean framework fork (no
  personal `memory/`) can be public; a configured instance should not be.
- **Rotate any key ever shared in plaintext** (chat, screenshare, a committed mistake) and use the
  narrowest scope that works (see §1).
- **Don't expose the brain without the bearer token**, and prefer a private overlay network (§9).
- **Review `TODO-NOW.md`** before enabling outward-facing integrations, and enroll a voiceprint (§6)
  if others share your space.
- **Treat the host running the brain as trusted** — it holds the tokens that let the assistant act on
  your behalf. Anyone with shell on that host can read `.env`.
- **Arm the fleet deliberately** (`AFON_FLEET_AUTHORIZED`) — it reaches shared infrastructure.

## 12. Licensing, conduct & attribution

This project is **OpenAfon** (assistant persona **Afon**); *Afon* is referenced only as the
blueprint/inspiration, never as this project's brand. Governance and licensing live in these
root files:

- [`LICENSE`](LICENSE) — MIT (the code grant). Copyright "Vazghen and the OpenAfon contributors".
- [`NOTICE`](NOTICE) — attribution + the naming notice.
- [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) — dependency licenses (incl. the single LGPL-3.0
  optional dependency, `py-tgcalls`) and the cloud-service Terms you accept by using your own keys.
- [`ACCEPTABLE_USE.md`](ACCEPTABLE_USE.md) — responsible-use expectations for an autonomous,
  tool-using AI agent (operator duties, prohibited uses, high-risk integrations).
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — dev setup, the test gate, how to add a tool safely.
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — Contributor Covenant v2.1.

MIT covers copyright, not trademark — see the naming note in `THIRD_PARTY_NOTICES.md` before
distributing a fork widely. Full docs: **https://openafon.vercel.app**.
