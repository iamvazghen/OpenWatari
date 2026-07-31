# Watari (OpenWatari) — repo pointer

## Persistent memory (Obsidian vault)

Durable project memory (state, architecture traps, decisions) lives in the vault, NOT here:
`C:\Users\iamva\Documents\Obsidian Vault\30-Projects\Active\openwatari.md`.

**Context order — cheapest first:**
1. `graphify query "<question>"` — the code graph (`graphify-out/graph.json`). Never grep to orient.
2. The vault note above for intent / why.
3. Raw source only to edit, or when 1 and 2 don't answer.

`/resume` to load state, `/save` to write conclusions back to the vault (VPS-authoritative).

## Non-negotiables (see the vault note for the full list)
- Repo `iamvazghen/OpenWatari` is PRIVATE — do NOT push without an explicit ask.
- git hooks are redirected: `core.hooksPath=scripts/githooks`. Pre-push runs the full suite; never `--no-verify`.
- Deploy brain-only: `scripts/deploy_vps.sh` (never touches `.env`/secrets). Edge: `scripts\restart_edge.ps1`.
- Secrets / biometrics (`.env`, `*.session`, `owner.npy`, …) are gitignored and never leave the laptop.
