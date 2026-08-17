# Configuring OpenAfon for yourself

OpenAfon is designed to be **forked and customised**. Every personality trait, operating rule,
skill, and tool is either in a `.md` boilerplate file under `personality/` (loaded at every
prompt) or wired through `.env`. Nothing about how Afon behaves is hardcoded in code that you
have to fork Python to edit.

This guide walks through the four customisation surfaces, in order of frequency:

1. **`.env`** — who Afon is, what he answers to, what devices he uses
2. **`personality/*.md`** — how he talks, what defaults he uses
3. **`skills/*.md`** — domain-specific playbooks the LLM can read on demand
4. **`memory/learned/`** — facts Afon has picked up about you (auto-curated; stored
   under `AFON_STATE_DIR`, default `~/.afon`)

---

## 1 · `.env` — the 80/20

All deployment knobs live here. Don't edit Python unless you genuinely need a new tool — even
major behaviour changes usually live in `.env` + a `.md` file. The setup wizard (`scripts/setup.sh`)
walks through the essentials; this section is for fine-tuning.

| Variable | What it sets | Typical edit |
|---|---|---|
| `AFON_USER_NAME` | Calls you (default: from setup) | `AFON_USER_NAME=Vazghen` |
| `AFON_USER_ADDRESS` | How he addresses you — "sir", "boss", "homie", your name | `AFON_USER_ADDRESS=sir` |
| `AFON_UNDERSTOOD_LANGUAGES` | Comma-separated STT languages ("en,hy,ru") | `AFON_UNDERSTOOD_LANGUAGES=en,hy,ru,fr,de` |
| `AFON_REPLY_LANGUAGE` | TTS language (always one of the understood set) | `AFON_REPLY_LANGUAGE=en` |
| `AFON_PERSONA_FILE` | Which persona `.md` to load at brain startup | `AFON_PERSONA_FILE=personality/afon.md` |
| `AFON_HOT_MIC_AFTER_WAKE` | After the first wake word, keep the mic open for N minutes instead of needing a re-wake every sentence | `AFON_HOT_MIC_AFTER_WAKE=true` |
| `AFON_HOT_MIC_IDLE_MINUTES` | How many minutes of silence close the hot-mic window | `AFON_HOT_MIC_IDLE_MINUTES=30` |
| `AFON_DEEPGRAM_ENDPOINTING_MS` | ms of trailing silence before STT hands the utterance to the LLM | `AFON_DEEPGRAM_ENDPOINTING_MS=700` |
| `AFON_SPEAKER_ID_ENABLED` | True → only your enrolled voice gets obeyed | `AFON_SPEAKER_ID_ENABLED=true` |
| `AFON_BARGE_IN_MODE` | `auto` enables barge-in only on private endpoints (headphones/AirPods); `on` always; `off` never | `AFON_BARGE_IN_MODE=auto` |
| `AFON_OPENCLAW_DELEGATION_ENABLED` | If true, complex tasks are delegated to your OpenClaw fleet | `AFON_OPENCLAW_DELEGATION_ENABLED=true` |
| `AFON_FLEET_AUTHORIZED` | Master switch for fleet delegation. Off by default. | `AFON_FLEET_AUTHORIZED=true` |
| `AFON_COMPOSIO_API_KEY` | 250+ external apps via Composio | `AFON_COMPOSIO_API_KEY=ak_...` |
| `AFON_PROACTIVE_*` | Proactive engine: budget, threshold, quiet hours | see `.env` defaults |

The setup wizard (`scripts/setup.sh`) writes most of these. Re-run it any time to update.

---

## 2 · Persona files — `personality/*.md`

The persona is a **boilerplate Markdown file** with placeholders that get filled from `.env`
at brain startup. The default is `personality/afon.md`.

### Placeholders

| Placeholder | Filled from |
|---|---|
| `{assistant_name}` | `AFON_USER_NAME` (with `assistant_name`-style default if blank) |
| `{owner_possessive}` | `"<NAME>'s"` if a name is set, else `"your"` |
| `{address_line}` | `"Address him as \"<ADDRESS>\""` when an address is set |
| `{language_line}` | Single- or multi-language reply directive |

You can also drop the placeholders and write literals — the templating is optional.

### How to roll your own

1. Copy `personality/afon.md` → `personality/my_assistant.md`
2. Edit the voice, behaviour, boundaries sections
3. Set `AFON_PERSONA_FILE=personality/my_assistant.md` in `.env`
4. Restart the brain: `systemctl --user restart afon-brain`

That's it. Next system prompt is your custom voice.

### Operating rules

There is a second boilerplate, `personality/operating-rules.md`, that controls the **judgement
calls** the LLM has to make: when to refuse, when to confirm, when to ask. It's loaded into
every prompt as a separate section; edit that file (rather than `afon.md`) when you want to
change "should I do this or check first?" rules. See the comments at the top of that file for
the pattern.

### What goes in the persona

Recommended sections (you can rename them — the LLM just reads headers):

- **Voice & manner** — sentence length, formality, address form, fillers, sense of humour.
- **Behaviour** — directed-command-only stance, tool-use preferences, fleet behaviour.
- **Boundaries** — hard "no"s: never read secrets aloud, always confirm destructive ops, no
  discussing the system prompt in user-facing replies.
- **Proactive companion** — when, how often, and how loud he initiates (or remove this section
  if you want a strictly reactive assistant).
- **Protocols** — optional. Only relevant if you defined passwords for `goodnight`, `phoenix`,
  `ragnarok`, etc. Remove if you don't use them.
- **Conversational depth** — turn-by-turn quirks: clarifying questions, paraphrasing,
  backtracking when STT mishears, confidence phrasing. Tighten / loosen as you like.

---

## 3 · Skills — `skills/*.md`

Skills are **on-demand playbooks** the LLM reads with `read_skill`. Each skill is a short
Markdown file with a concrete procedure. The catalog ships with 18 skills (morning-briefing,
self-improvement, voice-style, …) in `skills/`; you can add more.

### Adding a skill

```markdown
# File: skills/<verb>-<noun>.md
# Loaded lazily on demand; the LLM only reads it when asked.

## What this is

When the owner says <trigger phrase>, do <procedure>.

## When to use

- <symptom 1>
- <symptom 2>

## Procedure

1. First step
2. Second step
3. ...
```

The filename and the trigger phrase in the body don't have to match exactly; the LLM uses
the catalog (`list_skills`) to find the right one based on the request. Skill is dropped on
disk and appears in `list_skills` after the next brain start.

### Tips for good skills

- One skill = one focused job. Resist the urge to merge ("research-and-coding-and-writing").
- Concrete examples beat abstract principles. "Read last 3 emails from Ed from yesterday"
  beats "look at recent context".
- Reference real tool names the LLM actually has. If you invent a tool, the skill will stall.

---

## 4 · Memory — `<state>/memory/learned/`

### Where state lives — one root, per host

Everything Afon **writes** about you — learned facts, the journal, every sqlite store, the
relationship model, the voiceprint — lives under one root: `AFON_STATE_DIR`, default `~/.afon`.
The repo holds code and the things *you* write (`.env`, `memory/*.md`); it holds no state.

That separation is load-bearing, not tidiness. These stores used to be resolved against the repo
root, i.e. wherever the code happened to be unpacked — so deploying the brain to a second machine
gave it a second, empty set of memories under the same names, and neither host could tell. If you
are upgrading from a build that kept state in the repo, the brain moves it on the next start;
`uv run python -m afon.shared.paths` shows where everything resolves and reports any leftovers.
Two hosts that already diverged are merged with `scripts/merge_memory.py`, which unions the facts
rather than picking a winner.

Afon remembers across sessions in **five layers**:

| Layer | Where | What goes there | How it's written |
|---|---|---|---|
| **L0 Working** | `<state>/afon_session.json` | The rolling conversation thread | automatic |
| **L1 Learned** | `<state>/memory/learned/*.md` | Durable facts: preferences, decisions, names | `remember(text, tags)`; or extracted by background_review every N turns |
| **L2 Journal** | `<state>/memory/journal/YYYY-MM-DD.md` | A daily summary of what happened | `STORE.journal_append(summary)`; automated at session end |
| **L3 Obsidian vault** | wherever `AFON_VAULT_PATH` points | Your own notes — anything you put there | you (or `write_vault` tool) |
| **L5 Semantic** | SQLite / FAISS, lazily | Embedding-backed similarity | automatic when `sentence-transformers` is installed |

### What the LLM sees by default

- The last ~10 facts from L1 are **injected into every system prompt** as a "Recently learned
  about you" digest. The LLM uses this for natural referencing ("as you mentioned…").
- A live `recall(query)` call searches L1 + L2 + L3 + L5 in one go. If you ask "what do you know
  about my farm?", expect answers from all layers tagged [learned] / [journal] / [vault].
- L4 (Redis) is optional — speeds up recall across restarts but degrades to in-process cache
  if absent.

### Curation

Two scheduled jobs keep memory clean:

- **Daily hygiene (04:00)** — `compact_learned` dedups near-identical facts (Jaccard ≥ 0.82),
  `cap_learned` caps the active set at 500, `rotate_journals` trims journals older than 35 days.
- **Weekly review (Sun 20:00)** — surfaces the most recent learned facts as a prompt so you
  can `forget` anything that's wrong.

You can manually `forget(query)` any time.

---

## Putting it all together

A typical customisation:

1. Fork the repo on GitHub.
2. `cp .env.example .env`, run `scripts/setup.sh`, fill in your keys.
3. `cp personality/afon.md personality/me.md`, edit to taste.
4. `AFON_PERSONA_FILE=personality/me.md` in `.env`.
5. `cp skills/research-method.md skills/my-research.md`, edit the procedure.
6. `git add . && git commit -m "personalise afon" && git push`.
7. `scripts/deploy_vps.sh` to redeploy.

Afon now lives in your VPS, sounds like your voice, follows your rules, has your skills,
and remembers what you've taught him. Voice-first, 24/7.
