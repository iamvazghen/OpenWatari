#!/usr/bin/env bash
# Prove the VPS is running EXACTLY the local code — by content, not by timestamp.
#
# The normal deploy untars over the target, which updates and adds but never DELETES. A module
# removed locally therefore lingers on the VPS and can still be imported, so "I deployed" is not the
# same as "the VPS matches". This hashes every deployed file on both sides and reports any file that
# differs, is missing, or is stale (present on the VPS only).
#
#   bash scripts/verify_vps_sync.sh
# Exit 0 = identical. Exit 1 = drift (listed).
#
# REQUIRED: AFON_VPS (e.g. "openclaw@<host>"), from your shell or the gitignored
# `deploy_vps.env` next to this script — the same source deploy_vps.sh uses. The host is never
# hardcoded here; that would leak the deployment topology into the repo.
set -uo pipefail

if [[ -f "$(dirname "$0")/deploy_vps.env" ]]; then
  # shellcheck disable=SC1091
  source "$(dirname "$0")/deploy_vps.env"
fi
: "${AFON_VPS:?Set AFON_VPS (e.g. export AFON_VPS=openclaw@<your-host>) — see deploy_vps.env}"
VPS="${VPS:-$AFON_VPS}"
REMOTE="${REMOTE:-${AFON_VPS_DIR:-/home/openclaw/afon}}"
TREES="src/afon skills clients personality"

cd "$(dirname "$0")/.."

# Emit "<path> <hash>" with a SINGLE separator. sha256sum prints "<hash>  <path>" (two spaces), so
# naive field-splitting makes the join key an empty string and every file looks like it drifted.
# Also strips the leading '*' that sha256sum prefixes to paths in binary mode (Git Bash on Windows
# does this, the VPS does not) — otherwise every single path looks different between the two hosts.
normalise() { awk '{h=$1; $1=""; sub(/^  */,""); sub(/^\*/,""); print $0 "\t" h}'; }

hash_local() {
  find $TREES -type f ! -path '*__pycache__*' ! -name '*.pyc' -print0 2>/dev/null |
    xargs -0 sha256sum | normalise
}
hash_remote() {
  ssh "$VPS" "cd '$REMOTE' && find $TREES -type f ! -path '*__pycache__*' ! -name '*.pyc' -print0 2>/dev/null | xargs -0 sha256sum" | normalise
}

L=$(mktemp); R=$(mktemp)
hash_local  | LC_ALL=C sort > "$L"
hash_remote | LC_ALL=C sort > "$R"

echo "local files:  $(wc -l < "$L")"
echo "remote files: $(wc -l < "$R")"

drift=0
missing=$(LC_ALL=C comm -23 <(cut -f1 "$L") <(cut -f1 "$R"))
stale=$(LC_ALL=C comm -13 <(cut -f1 "$L") <(cut -f1 "$R"))
differing=$(LC_ALL=C join -t$'\t' -j 1 -o 0,1.2,2.2 "$L" "$R" | awk -F'\t' '$2 != $3 {print $1}')

[ -n "$missing"   ] && { echo "MISSING on VPS:";   echo "$missing"   | sed 's/^/  /'; drift=1; }
[ -n "$stale"     ] && { echo "STALE on VPS (deleted locally):"; echo "$stale" | sed 's/^/  /'; drift=1; }
[ -n "$differing" ] && { echo "CONTENT DIFFERS:";  echo "$differing" | sed 's/^/  /'; drift=1; }

rm -f "$L" "$R"
[ "$drift" -eq 0 ] && echo "=== VPS matches local exactly (content-verified) ===" || echo "=== DRIFT DETECTED ==="
exit "$drift"
