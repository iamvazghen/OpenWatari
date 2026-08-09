# Afon on the VPS — deployment runbook

Two always-on pieces can live on the VPS. They're independent — deploy either or both:

1. **`afon-brain`** — the full agent + the WebSocket voice endpoint + the reminder scheduler.
   This is the 24/7 brain that phone/glasses/laptop clients connect to. See **"The brain service"**
   below.
2. **`afon-ticker`** — a tiny standalone daemon that owns *recurring* (daily) ntfy pushes. Useful
   even without the brain. See **"The ticker"** further down.

---

## The brain service (`afon-brain`)

The brain is what makes Afon a 24/7 companion rather than a laptop app: it holds the agent loop,
memory, the proactive engine, and (now) the reminder scheduler, and it serves the edge/phone/glasses
over a WebSocket. When the brain runs here, reminders fire and proactive nudges happen **even with
the laptop off** (spoken to any connected client, else pushed to your phone via ntfy).

### What's here
- `afon-brain.service` — systemd unit (Restart=always, MemoryMax, EnvironmentFile=.env).
- `install-brain.sh` — one-command deploy (uv sync the `brain`+`channels` extras, install+enable,
  health check).

### Deploy (run ON the VPS)
```bash
# 1. get the repo onto the VPS (clone or rsync) at ~/afon
git clone <your-afon-repo> ~/afon        # or: rsync -a ./ vps:~/afon
# 2. put your .env there (it carries the brain's keys — copy your edge .env)
cp /path/to/your/.env ~/afon/.env
# 3. deploy
cd ~/afon/deploy/vps && bash install-brain.sh
```

### Reachability + auth
- **Loopback (default):** `AFON_BRAIN_HOST=127.0.0.1`. Clients reach it over the same tunnel you
  use for the gateway (Tailscale `<your-vps-host>` / an SSH tunnel). No auth needed on loopback.
- **Exposed:** set `AFON_BRAIN_HOST=0.0.0.0` **and** a non-empty `AFON_API_AUTH_TOKEN` in `.env`.
  The server rejects any client without `Authorization: Bearer <token>` (see `server._authorized`).

### Verify
```bash
curl -fsS http://127.0.0.1:8766/healthz      # -> ok   (liveness; the brain's HTTP side)
sudo systemctl kill afon-brain             # restart proof: it returns within ~5s
journalctl -u afon-brain -f                # watch turns / reminders / proactive ticks
```

> **One scheduler owner.** The brain and the *fully-local edge* (`afon.edge.assistant`) each start
> the reminder scheduler against the SQLite jobstore. Run **one** of them against a given DB, not
> both — otherwise a daily reminder could fire twice. In the standard split (brain on VPS, thin edge
> on the laptop) the brain owns it; the laptop edge connects as a client and does not.

---

# Afon VPS ticker — true 24/7 recurring reminders (Phase 4b)

Afon already delivers **one-shot / `at`** reminders to your phone even when the PC is off:
`set_reminder` hands those to **ntfy.sh**, which holds them server-side and pushes at the right
time (window: ~10s to 3 days out). No VPS needed for those.

**Recurring** reminders ("every day at 08:00") are the one case ntfy can't hold — a repeating
schedule needs a process that is itself always on. This tiny daemon is that process. It runs on
the VPS you already have (`openclaw@<your-vps-host>`), owns the daily jobs, and pushes them to the
**same ntfy topic** the edge uses. It's fully decoupled from the OpenClaw fleet/gateway — it only
knows ntfy + a local JSON file.

## What's here
- `afon_ticker.py` — the daemon (APScheduler + a tiny HTTP API; deps: `apscheduler`, `httpx`).
- `afon-ticker.service` — systemd unit template.
- `install.sh` — one-command deploy (venv + env file + service).

## Deploy (run ON the VPS)
```bash
# from this directory, copied to the VPS:
AFON_NTFY_TOPIC=afon-vaz-619-7f3k9q2 bash install.sh
```
Use the **same** `AFON_NTFY_TOPIC` as your edge `.env`. The script makes a venv, writes
`ticker.env`, installs+enables the systemd service, and prints a health check.

Optional env: `AFON_TICKER_PORT` (8770), `AFON_TICKER_HOST` (127.0.0.1),
`AFON_TICKER_TOKEN` (shared bearer secret), `AFON_TICKER_TZ` (Europe/Berlin).

## Point the edge at it
In the edge `.env`:
```
AFON_TICKER_URL=http://127.0.0.1:8770
AFON_TICKER_TOKEN=        # match the ticker if you set one
```
After that, `set_reminder(daily="08:00", ...)` on the PC **also** registers the reminder on the
ticker, so it fires from the VPS even with the laptop off. `cancel_reminder` removes it from both.
If `AFON_TICKER_URL` is unset/unreachable, nothing breaks — the local scheduler still speaks it
when the PC is on (graceful degradation).

> The ticker binds `127.0.0.1` by default. The edge must reach it over the same host/tunnel you
> use for the gateway (e.g. Tailscale `<your-vps-host>`, or an SSH tunnel). Bind `0.0.0.0` only
> behind a firewall, and set `AFON_TICKER_TOKEN` if you do.

## Verify
```bash
curl -s http://127.0.0.1:8770/health                # {"ok":true,"jobs":N}
curl -s -XPOST http://127.0.0.1:8770/reminders \
  -d '{"message":"test","daily":"09:00"}'           # schedules + persists
curl -s http://127.0.0.1:8770/reminders             # lists it
journalctl -u afon-ticker -f                      # watch it fire/push
```
