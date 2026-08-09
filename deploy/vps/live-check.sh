#!/usr/bin/env bash
# Weekly credential-rot check: run the live integration suite on the VPS; alert Telegram on fail.
# Installed by a systemd user timer (see install-live-check.sh). Safe to run by hand.
set -u
cd "$HOME/afon" || exit 1

OUT=$(.venv/bin/python bench/test_live_integrations.py 2>&1)
STATUS=$?
echo "$OUT" | tail -20

if [ $STATUS -ne 0 ]; then
  # All Afon->owner messages are VOICE. Speak the failure; text only if synthesis itself fails.
  TALLY=$(echo "$OUT" | tail -1 | head -c 120)
  SPOKEN="Sir, the weekly integration check failed. The tally: ${TALLY}. Details are in the VPS journal."
  if ! .venv/bin/python -m afon.brain.voice_io "$SPOKEN"; then
    TOKEN=$(grep -oP '^AFON_TELEGRAM_BOT_TOKEN=\K.+' .env || true)
    CHAT=$(grep -oP '^AFON_TELEGRAM_DEFAULT_CHAT=\K.+' .env || true)
    if [ -n "$TOKEN" ] && [ -n "$CHAT" ]; then
      SUMMARY=$(echo "$OUT" | tail -6 | head -c 800)
      curl -s "https://api.telegram.org/bot${TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${CHAT}" \
        --data-urlencode "text=⚠️ Afon weekly live-integration check FAILED (voice synth also down):
${SUMMARY}" > /dev/null
    fi
  fi
fi
exit $STATUS
