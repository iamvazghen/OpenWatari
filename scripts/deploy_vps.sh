#!/usr/bin/env bash
# Deploy the brain to the VPS — but ONLY if the test suite is green. Codifies the safe deploy:
# no more scp-then-discover-it-was-broken. Run from the repo root: scripts/deploy_vps.sh
#
# REQUIRED: set AFON_VPS (e.g. "openclaw@<host>") in your shell or a personal gitignored
# `deploy_vps.env` next to this script. The framework never hardcodes a host — that would leak
# the deployment topology into a public repo.
set -euo pipefail

if [[ -f "$(dirname "$0")/deploy_vps.env" ]]; then
  # shellcheck disable=SC1091
  source "$(dirname "$0")/deploy_vps.env"
fi

: "${AFON_VPS:?Set AFON_VPS in your env (e.g. export AFON_VPS=openclaw@<your-host>) — see deploy_vps.env.example}"
VPS="$AFON_VPS"
REMOTE="${AFON_VPS_DIR:-/home/openclaw/afon}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# The brain's systemd unit. Overridable for the same reason REMOTE is: the rename left this
# script pointing at `afon-brain` while the VPS still ran `jarvis-brain`, and a deploy cannot
# succeed against a unit that does not exist.
SERVICE="${AFON_VPS_SERVICE:-afon-brain}"

echo "==> preflight: machine invariants (TODO K7)"
# Runs the standalone checker so the deploy and a manual `bash scripts/preflight.sh --remote`
# assert exactly the same things. The env-prefix check in particular is the one that matters:
# a mismatch there deploys a brain that starts, passes /healthz, and reads no configuration.
AFON_VPS_DIR="$REMOTE" AFON_VPS_SERVICE="$SERVICE" bash scripts/preflight.sh --remote || {
  echo "PREFLIGHT FAILED — not deploying."; exit 1; }

echo "==> preflight: does the target actually exist?"
# WHY THIS EXISTS: on 2026-08-08 both the remote directory and the service name were wrong
# after the Watari/Jarvis -> Afon rename, and the only symptom was `tar: Cannot open` from a
# remote shell three steps into the deploy. `set -e` did stop it, so nothing was half-deployed
# — but the message named neither the cause nor the fix, and the same failure looks identical
# to a network problem or a full disk. Checking first turns a confusing abort into a sentence
# that says what to change.
if ! ssh "$VPS" "test -d '$REMOTE'"; then
  echo "FATAL: $VPS:$REMOTE does not exist."
  echo "  Either create it, or point this deploy at the real directory:"
  echo "    echo 'AFON_VPS_DIR=/home/openclaw/<dir>' >> scripts/deploy_vps.env"
  ssh "$VPS" "ls -d ~/afon ~/jarvis 2>/dev/null" | sed 's/^/  candidate: /' || true
  exit 1
fi
if ! ssh "$VPS" "systemctl --user cat '$SERVICE' >/dev/null 2>&1"; then
  echo "FATAL: systemd --user unit '$SERVICE' does not exist on $VPS."
  echo "  Point this deploy at the real unit:"
  echo "    echo 'AFON_VPS_SERVICE=<name>' >> scripts/deploy_vps.env"
  ssh "$VPS" "systemctl --user list-units --type=service --no-pager 2>/dev/null | grep -iE 'afon|jarvis' | awk '{print \$1}'" | sed 's/^/  candidate: /' || true
  exit 1
fi

echo "==> running the full verification suite (deploy gate)"
# Canonical runner (not `pytest -q`, which reports these check()-based scripts green regardless and
# collects none of the main()-only ones). Subprocess exit codes + PASS/FAIL/SKIP classification.
.venv/Scripts/python.exe bench/run_all_tests.py || { echo "TESTS FAILED — not deploying."; exit 1; }

echo "==> syncing src/afon + skills + clients + personality -> $VPS:$REMOTE (excludes pycache; never touches .env/secrets)"
# scp the source tree + skill playbooks + web clients (iphone/hud served by the brain's HTTP sidecar)
# + the persona files. personality/ was missing here, so every persona edit stayed on the laptop and
# the VPS brain answered with a months-stale prompt — invisible while the edge ran its own local
# brain, and wrong the moment it routes to the VPS. .env, voiceprint, sessions live only on the target.
tar --exclude='__pycache__' -czf - src/afon skills clients personality | ssh "$VPS" "tar -xzf - -C '$REMOTE'"

echo "==> verifying the VPS matches local, by content (J4.1)"
# `verify_vps_sync.sh` existed for weeks and was called from NOWHERE. A verifier nobody runs is
# worse than no verifier, because its presence implies the check is happening — the orphaned
# `brain/tools/mynews.py` that H2.13 found had been live on the VPS the whole time it sat there.
#
# It runs AFTER the sync and BEFORE the restart on purpose. The tar above adds and overwrites but
# never deletes (H2.13), so a file removed locally is still importable on the brain: something
# that should fail loudly keeps working from a stale copy, while every local test passes. Catching
# that before the restart means the running process is never pointed at a tree we already know is
# wrong. Aborting here leaves the brain up on its current code — no worse than not deploying.
if ! REMOTE="$REMOTE" bash scripts/verify_vps_sync.sh; then
  echo
  echo "DRIFT — not restarting the brain."
  echo "  STALE files are the dangerous ones: deleted locally, still importable on the VPS."
  echo "  Remove them deliberately, then re-run:"
  echo "    ssh $VPS \"cd '$REMOTE' && rm <path>\""
  echo "  Automatic pruning is deliberately NOT done here: deleting files on a live"
  echo "  always-on brain is a decision, not a deploy step."
  exit 1
fi

echo "==> restarting the brain + health check"
ssh "$VPS" "systemctl --user restart '$SERVICE' && sleep 5 && systemctl --user is-active '$SERVICE' && curl -s localhost:8766/healthz"
echo
echo "==> deployed."
