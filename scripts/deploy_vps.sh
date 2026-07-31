#!/usr/bin/env bash
# Deploy the brain to the VPS — but ONLY if the test suite is green. Codifies the safe deploy:
# no more scp-then-discover-it-was-broken. Run from the repo root: scripts/deploy_vps.sh
#
# REQUIRED: set JARVIS_VPS (e.g. "openclaw@<host>") in your shell or a personal gitignored
# `deploy_vps.env` next to this script. The framework never hardcodes a host — that would leak
# the deployment topology into a public repo.
set -euo pipefail

if [[ -f "$(dirname "$0")/deploy_vps.env" ]]; then
  # shellcheck disable=SC1091
  source "$(dirname "$0")/deploy_vps.env"
fi

: "${JARVIS_VPS:?Set JARVIS_VPS in your env (e.g. export JARVIS_VPS=openclaw@<your-host>) — see deploy_vps.env.example}"
VPS="$JARVIS_VPS"
REMOTE="${JARVIS_VPS_DIR:-/home/openclaw/jarvis}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> running the full verification suite (deploy gate)"
# Canonical runner (not `pytest -q`, which reports these check()-based scripts green regardless and
# collects none of the main()-only ones). Subprocess exit codes + PASS/FAIL/SKIP classification.
.venv/Scripts/python.exe bench/run_all_tests.py || { echo "TESTS FAILED — not deploying."; exit 1; }

echo "==> syncing src/jarvis + skills + clients + personality -> $VPS:$REMOTE (excludes pycache; never touches .env/secrets)"
# scp the source tree + skill playbooks + web clients (iphone/hud served by the brain's HTTP sidecar)
# + the persona files. personality/ was missing here, so every persona edit stayed on the laptop and
# the VPS brain answered with a months-stale prompt — invisible while the edge ran its own local
# brain, and wrong the moment it routes to the VPS. .env, voiceprint, sessions live only on the target.
tar --exclude='__pycache__' -czf - src/jarvis skills clients personality | ssh "$VPS" "tar -xzf - -C '$REMOTE'"

echo "==> restarting the brain + health check"
ssh "$VPS" "systemctl --user restart jarvis-brain && sleep 5 && systemctl --user is-active jarvis-brain && curl -s localhost:8766/healthz"
echo
echo "==> deployed."
