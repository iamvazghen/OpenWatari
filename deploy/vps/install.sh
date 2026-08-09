#!/usr/bin/env bash
# Deploy the Afon VPS ticker (Phase 4b true-24/7 recurring reminders).
# Run ON the VPS, from this directory:  bash install.sh
# Idempotent: re-running updates the code + restarts the service.
set -euo pipefail

DEST="${AFON_TICKER_DIR:-$HOME/afon-ticker}"
NTFY_TOPIC="${AFON_NTFY_TOPIC:-}"
NTFY_SERVER="${AFON_NTFY_SERVER:-https://ntfy.sh}"
PORT="${AFON_TICKER_PORT:-8770}"
HOST="${AFON_TICKER_HOST:-127.0.0.1}"
TOKEN="${AFON_TICKER_TOKEN:-}"

if [ -z "$NTFY_TOPIC" ]; then
  echo "Set AFON_NTFY_TOPIC to the SAME topic as your edge .env, e.g.:"
  echo "  AFON_NTFY_TOPIC=afon-vaz-619-7f3k9q2 bash install.sh"
  exit 1
fi

mkdir -p "$DEST"
cp -f afon_ticker.py "$DEST/afon_ticker.py"

# Isolated venv with just the two deps the ticker needs.
python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install --quiet --upgrade pip
"$DEST/.venv/bin/pip" install --quiet "apscheduler>=3.10" "httpx>=0.27"

# Environment file the systemd unit reads.
cat > "$DEST/ticker.env" <<EOF
AFON_NTFY_SERVER=$NTFY_SERVER
AFON_NTFY_TOPIC=$NTFY_TOPIC
AFON_TICKER_HOST=$HOST
AFON_TICKER_PORT=$PORT
AFON_TICKER_TOKEN=$TOKEN
AFON_TICKER_TZ=${AFON_TICKER_TZ:-Europe/Berlin}
AFON_TICKER_DB=$DEST/ticker_reminders.json
EOF

# Install + start the systemd service (rewrites paths/user for this account).
SVC=/etc/systemd/system/afon-ticker.service
sed -e "s#/home/openclaw/afon-ticker#$DEST#g" \
    -e "s#^User=.*#User=$(whoami)#" \
    afon-ticker.service | sudo tee "$SVC" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now afon-ticker.service
sleep 1
echo "--- health ---"
curl -fsS "http://$HOST:$PORT/health" || echo "(health check failed; check: journalctl -u afon-ticker -n 50)"
echo
echo "Deployed to $DEST. Point the edge at it with AFON_TICKER_URL=http://$HOST:$PORT in .env"
echo "(if HOST is 127.0.0.1, the edge must reach it over the same tunnel/host as the gateway)."
