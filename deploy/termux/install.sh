#!/data/data/com.termux/files/usr/bin/bash
# Afon edge-lite installer for Android / Termux (TODO 8.5).
# Run inside Termux on the phone. Assumes the Termux:API app is also installed from F-Droid.
set -euo pipefail

echo "[1/4] Termux packages (python, git, termux-api, ffmpeg for wav)…"
pkg update -y
pkg install -y python git termux-api ffmpeg

echo "[2/4] uv (fast Python env manager)…"
pip install --upgrade uv || pip install uv

REPO="${AFON_REPO:-$HOME/OpenAfon}"
if [ ! -d "$REPO/.git" ]; then
  echo "[3/4] clone the repo to $REPO"
  git clone "${AFON_GIT_URL:?set AFON_GIT_URL to the private repo URL}" "$REPO"
fi
cd "$REPO"

echo "[4/4] minimal deps (no heavy audio/ML — the phone shells out to Termux:API + cloud STT)…"
uv sync --no-dev || uv pip install httpx pydantic websockets loguru pydantic-settings

cat <<'NOTE'

Done. Before first run, set these in the phone environment (or $HOME/.afon.env):
  AFON_BRAIN_WS_URL   = wss://<your-brain-host>:8765   (the 24/7 brain)
  AFON_API_AUTH_TOKEN = <same token the brain expects>
  AFON_GROQ_API_KEY   = <groq key for on-device speech-to-text>

Grant Termux the Microphone permission (Android Settings → Apps → Termux:API), then:
  bash deploy/termux/run.sh
NOTE
