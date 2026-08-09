#!/usr/bin/env bash
# Install the weekly live-integration check (systemd user timer). Run ON the VPS, once.
set -euo pipefail
mkdir -p ~/.config/systemd/user
cp "$(dirname "$0")/live-check.sh" "$HOME/afon/live-check.sh"
chmod +x "$HOME/afon/live-check.sh"

cat > ~/.config/systemd/user/afon-live-check.service <<'EOF'
[Unit]
Description=Afon weekly live-integration check (credential rot)

[Service]
Type=oneshot
ExecStart=%h/afon/live-check.sh
EOF

cat > ~/.config/systemd/user/afon-live-check.timer <<'EOF'
[Unit]
Description=Run the Afon live-integration check weekly

[Timer]
OnCalendar=Mon 07:30
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now afon-live-check.timer
systemctl --user list-timers afon-live-check.timer --no-pager
