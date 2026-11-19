#!/usr/bin/env bash
# Installerer IoT-enheden på Raspberry Pi'en som en systemd-service, der starter
# automatisk, når Pi'en tændes. Kør på Pi'en i mappen med pi_sensor.py:
#     bash install.sh
# Kræver ~/.alarm-pi.env med API_URL, DEVICE_API_KEY og TURBINE_ID (se README.md).
set -euo pipefail

USER_NAME="$(whoami)"
APP_DIR="$HOME/alarm-pi"
ENV_FILE="$HOME/.alarm-pi.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "Mangler $ENV_FILE. Opret den først (se README.md)." >&2
  exit 1
fi
chmod 600 "$ENV_FILE"

echo "== Pakker (Sense HAT)"
sudo apt-get update -qq
sudo apt-get install -y -qq sense-hat

echo "== Program til $APP_DIR"
mkdir -p "$APP_DIR"
cp pi_sensor.py "$APP_DIR/"

echo "== systemd-service alarm-pi"
sudo tee /etc/systemd/system/alarm-pi.service > /dev/null <<EOF
[Unit]
Description=Alarm-service IoT-enhed (Sense HAT)
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/python3 $APP_DIR/pi_sensor.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now alarm-pi
sleep 3
systemctl --no-pager --lines=5 status alarm-pi || true
echo
echo "Færdig. Se loggen med:  journalctl -u alarm-pi -f"
