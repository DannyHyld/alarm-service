#!/usr/bin/env bash
# Installerer vindmølle-dashboardet på Raspberry Pi'en som en systemd-service på port 8080.
# Kør på Pi'en i mappen dashboard/:
#     bash install.sh
# Kræver ~/.alarm-pi.env med API_URL og OPERATOR_API_KEY (se README.md).
set -euo pipefail

USER_NAME="$(whoami)"
APP_DIR="$HOME/alarm-pi/dashboard"
ENV_FILE="$HOME/.alarm-pi.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "Mangler $ENV_FILE. Opret den først (se README.md)." >&2
  exit 1
fi
if ! grep -q '^OPERATOR_API_KEY=' "$ENV_FILE"; then
  echo "Advarsel: OPERATOR_API_KEY mangler i $ENV_FILE, så alarmcentralen bliver tom." >&2
fi
chmod 600 "$ENV_FILE"

echo "== Program til $APP_DIR"
mkdir -p "$APP_DIR/static"
cp server.py "$APP_DIR/"
cp static/* "$APP_DIR/static/"

echo "== systemd-service alarm-dashboard"
sudo tee /etc/systemd/system/alarm-dashboard.service > /dev/null <<EOF
[Unit]
Description=Vindmølle-dashboard for Horns Rev 1
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/python3 -u $APP_DIR/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now alarm-dashboard
sudo systemctl restart alarm-dashboard
sleep 2
systemctl --no-pager --lines=3 status alarm-dashboard || true
IP="$(hostname -I | awk '{print $1}')"
echo
echo "Færdig. Åbn http://$IP:8080 i en browser på samme netværk."
