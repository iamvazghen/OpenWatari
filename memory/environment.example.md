# Environment & infrastructure — TEMPLATE (no secrets — those live in .env)

> Copy to `environment.md` (gitignored). Note where things run, so the assistant has context for its
> own deployment. Never put keys/tokens here — those belong in `.env`.

- **Local machine(s)** — OS, GPU or CPU-only, where the project lives, what runs there (the voice edge).
- **Brain host** — where `afon-brain` runs (a VPS or this machine) and how devices reach it
  (Tailnet IP / port). Restart policy if it's a 24/7 host.
- **LLM** — your provider/proxy and the primary model + fallback chain.
- **Obsidian vault** — the local mirror path and which side is authoritative (write there).
- **Home location** — lives in `runtime_prefs.json` (changeable by voice via `set_home_location`),
  not a fixed `.env` constant.
- **Hardware not owned yet** — list any integrations to leave parked until the device exists.

(Optional) **Fleet** — only if you run an external OpenClaw gateway; see `openclaw-fleet.md`.
