#!/usr/bin/env bash
# Deploy afon-brain as an always-on systemd service ON THE VPS.
#
#   bash install-brain.sh
#
# Idempotent: re-run to update code + restart. Expects:
#   * the afon repo present at $AFON_DIR (default ~/afon) — git clone or rsync it first;
#   * a real .env at $AFON_DIR/.env (copy your edge .env, see notes below);
#   * uv installed (https://docs.astral.sh/uv/); falls back to python -m venv if absent.
set -euo pipefail

AFON_DIR="${AFON_DIR:-$HOME/afon}"
HOST="${AFON_BRAIN_HOST:-127.0.0.1}"
HEALTH_PORT="${AFON_CLIENT_HTTP_PORT:-8766}"

if [ ! -d "$AFON_DIR/src/afon" ]; then
  echo "No afon repo at $AFON_DIR. Clone/rsync it there first, then re-run."
  echo "  e.g.  git clone <repo> $AFON_DIR    (or rsync from your machine)"
  exit 1
fi
if [ ! -f "$AFON_DIR/.env" ]; then
  echo "No $AFON_DIR/.env. Copy your edge .env there (it holds the brain's keys), then re-run."
  echo "NOTE for a VPS-reachable brain: set AFON_BRAIN_HOST=0.0.0.0 AND a non-empty"
  echo "AFON_API_AUTH_TOKEN in that .env — the server enforces the bearer token when it's set."
  exit 1
fi

cd "$AFON_DIR"

# Sync the brain runtime (brain = LLM/FastAPI/scheduler/cache; channels = Telethon DM reading).
if command -v uv >/dev/null 2>&1; then
  UV_BIN="$(command -v uv)"
  echo "Syncing deps with uv ($UV_BIN)…"
  "$UV_BIN" sync --extra brain --extra channels --extra browse
  EXEC="$UV_BIN run --no-sync python -m afon.brain.server"
else
  echo "uv not found; using python venv…"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -e ".[brain,channels]"
  EXEC="$AFON_DIR/.venv/bin/python -m afon.brain.server"
  UV_BIN=""
fi

# Install the unit, rewriting user/paths/ExecStart for this account + chosen runtime.
SVC=/etc/systemd/system/afon-brain.service
sed -e "s#/home/openclaw/afon#$AFON_DIR#g" \
    -e "s#^User=.*#User=$(whoami)#" \
    -e "s#^ExecStart=.*#ExecStart=$EXEC#" \
    afon-brain.service | sudo tee "$SVC" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now afon-brain.service
sleep 2
echo "--- health ---"
curl -fsS "http://$HOST:$HEALTH_PORT/healthz" \
  && echo " (brain alive)" \
  || echo "(health check failed; inspect: journalctl -u afon-brain -n 60 --no-pager)"
echo
echo "Deployed. Logs: journalctl -u afon-brain -f"
echo "Restart proof: sudo systemctl kill afon-brain  ->  it should come back within ~5s."
