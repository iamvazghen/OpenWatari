#!/data/data/com.termux/files/usr/bin/bash
# Launch Afon edge-lite (push-to-talk) on Android/Termux.
set -euo pipefail

REPO="${AFON_REPO:-$HOME/OpenAfon}"
cd "$REPO"

# Load local secrets if present (AFON_BRAIN_WS_URL, AFON_API_AUTH_TOKEN, AFON_GROQ_API_KEY).
[ -f "$HOME/.afon.env" ] && set -a && . "$HOME/.afon.env" && set +a

# Keep the CPU awake while the loop runs so the mic/link don't sleep (needs termux-api).
termux-wake-lock 2>/dev/null || true
trap 'termux-wake-unlock 2>/dev/null || true' EXIT

exec uv run python -m afon.edge.edge_lite
