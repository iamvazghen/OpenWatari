# Auditing the OpenClaw fleet without letting it touch anything

These four scripts build `~/.openclaw-audit` on the fleet VPS: a clone of the live fleet config
that keeps what an audit measures — the eight agents, their models, the tool profile, the
governance shape — and removes every path by which a hostile probe could reach the real world.

**Why a clone and not the live fleet.** The live config runs `tools.profile: full`,
`exec.mode: full`, `fs.workspaceOnly: false`, `agentToAgent.allow: ["*"]`, elevated host actions
permitted from the CLI, Telegram bound to the owner's real chat, and the canonical Obsidian vault
mounted **rw**. An audit suite is ~450 deliberately hostile prompts per agent. Against that surface
it could execute commands, message real contacts, and write into the vault whose local copy is a
one-way replica and therefore cannot restore it.

Run them in order, on the VPS:

```bash
python3 mk_audit_profile.py   # clone config; drop secrets/auth/env, channels, bindings, cron
                              # disable elevated + agentToAgent; repoint the vault bind at scratch
python3 strip_refs2.py        # neutralise secret references -- BOTH the string and OBJECT shapes
python3 model_keys_only.py    # filtered pass store: the 2 model keys, of 93 entries
python3 audit_resolver.py     # audit copy of the pass resolver, pointed at the filtered store
openclaw --profile audit config validate
```

Then, for a run:

```bash
openclaw --profile audit gateway --bind loopback   # loopback:19555, NOT the live gateway's port
openclaw --profile audit agent --agent finance --json --session-key audit-1 -m "..."
```

**Stop the gateway when the suite finishes.** `memory-core` creates a managed *dreaming* cron job
at startup that will otherwise burn model credits unattended.

## Things that cost hours, recorded so they cost minutes

- **A secret reference is usually an OBJECT**, not a string:
  `{"source":"exec","provider":"pass","id":"apis/minimax/key"}`. A regex over string values reports
  "0 neutralised" while the reference sits in plain sight, and the gateway keeps failing to start
  on a ref you believe you removed.
- **The pass resolver hard-codes `~/.password-store`** and ignores `PASSWORD_STORE_DIR`. Pointing
  the env var at a filtered store silently resolves nothing; the clone needs its own copy of the
  resolver with the constant rewritten.
- **Removing `secrets` wholesale kills the models too**, so no agent turn can run at all. The two
  model keys have to come back — hence the filtered store, which is the narrowest cut that leaves
  the LLM working and all 91 other credentials absent.
- **`--local` still needs a gateway** for the profile; it is not an embedded-only path.
- **A failed gateway start leaves a migration lock** for ~4 minutes. Wait it out rather than
  clearing it by hand.
- **`--deliver` defaults to false**, so a probe's reply does not reach a channel unless asked. Good
  property, but it is a default and not a guarantee — the channel config is removed as well.
- **Agent DEFINITIONS must be copied in** (`agents/<id>/agent`); `--profile` gives the clone its own
  empty state dir. Copy the definition, never `sessions/` — the audit must start each agent on a
  clean thread rather than inherit the owner's real conversations.

## Verifying the boundary rather than assuming it

The check that matters is that the resolver the audit profile uses can decrypt the model keys and
nothing else:

```bash
echo '{"protocolVersion":1,"provider":"pass","ids":["apis/minimax/key","apis/notion/key"]}' \
  | python3 ~/.openclaw-audit/secrets/pass-resolver.py
# apis/minimax/key -> a value      apis/notion/key -> "no pass entry"
```

The live config must also be provably untouched: it still carries its `bindings`, and its vault
bind is still the real `obsidian-vault`.
