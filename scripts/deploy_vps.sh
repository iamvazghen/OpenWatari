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

# Opt-in, and off by default: see the drift block below for what it will and will not delete.
PRUNE=0
for arg in "$@"; do
  case "$arg" in
    --prune) PRUNE=1 ;;
    *) echo "unknown argument: $arg (only --prune is accepted)"; exit 2 ;;
  esac
done

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
DRIFT="$(mktemp)"
trap 'rm -f "$DRIFT"' EXIT
if ! REMOTE="$REMOTE" DRIFT_OUT="$DRIFT" bash scripts/verify_vps_sync.sh; then
  stale_n=$(grep -c '^STALE' "$DRIFT" || true)
  other_n=$(grep -cE '^(MISSING|DIFFERS)' "$DRIFT" || true)
  remote_n=$(awk -F'\t' '$1=="REMOTE_FILES"{print $2}' "$DRIFT")

  # H2.13, resolved. Deleting on a live always-on brain stays a DECISION — it just no longer has to
  # be a manual `ssh … rm` and a second full deploy. `--prune` is opt-in, and it can only ever
  # remove files the verifier classified as STALE: present on the VPS, absent locally, therefore
  # importable code that no longer exists in the repo. That is the actual hazard — not the wasted
  # bytes, but a deleted module still working on the host while every local test passes.
  #
  # It refuses in the two cases where deleting would be wrong rather than merely bold:
  #   * ANY missing/differing file means the sync itself did not land, and the remote tree is not
  #     something to start removing files from — abort, unchanged.
  #   * An implausibly large stale list is the signature of a broken LOCAL enumeration (a bad
  #     REMOTE dir, a failed `find`), where "everything is stale" and pruning wipes the deployment.
  #     A real refactor deletes a handful of files; a quarter of the tree is a bug in this script.
  # The mechanism swap the TODO proposed (`rsync --delete`, or extract-and-swap) is deliberately
  # NOT taken: both replace a transfer that cannot half-apply with one that can, to solve a problem
  # the existing verifier already detects exactly. Reusing its answer costs no new failure mode.
  if [[ "$PRUNE" -eq 1 && "$other_n" -eq 0 && "$stale_n" -gt 0 ]] &&
     ! awk -F'\t' '$1=="STALE"{print $2}' "$DRIFT" | grep -qE '(^/|\.\.)'; then
    if (( stale_n > 25 || (remote_n > 0 && stale_n * 4 > remote_n) )); then
      echo
      echo "REFUSING TO PRUNE: $stale_n of $remote_n remote files look stale."
      echo "  That is too much of the tree to be a refactor. Check AFON_VPS_DIR points at the"
      echo "  brain's directory and that the local trees enumerated correctly, then prune by hand."
      exit 1
    fi
    echo
    echo "==> --prune: removing $stale_n stale file(s) of $remote_n on the VPS"
    awk -F'\t' '$1=="STALE"{print $2}' "$DRIFT" | sed 's/^/    rm /'
    awk -F'\t' '$1=="STALE"{print $2}' "$DRIFT" |
      ssh "$VPS" "cd '$REMOTE' && xargs -d '\n' -r rm -f --"
    echo "==> re-verifying after prune (a prune that did not converge is not a deploy)"
    if ! REMOTE="$REMOTE" bash scripts/verify_vps_sync.sh; then
      echo "STILL DRIFTING after prune — not restarting the brain."
      exit 1
    fi
  else
    echo
    echo "DRIFT — not restarting the brain."
    echo "  STALE files are the dangerous ones: deleted locally, still importable on the VPS."
    if [[ "$other_n" -eq 0 && "$stale_n" -gt 0 ]]; then
      echo "  All of it is stale-only, so this run can clean it up for you:"
      echo "    scripts/deploy_vps.sh --prune"
    fi
    echo "  Or remove them deliberately, then re-run:"
    echo "    ssh $VPS \"cd '$REMOTE' && rm <path>\""
    exit 1
  fi
fi

echo "==> restarting the brain + health check"
ssh "$VPS" "systemctl --user restart '$SERVICE' && sleep 5 && systemctl --user is-active '$SERVICE' && curl -s localhost:8766/healthz"
echo
echo "==> deployed."
