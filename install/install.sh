#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/drebolbot"
SERVICE_NAME="drebolbot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "Run this installer as root."
  exit 1
fi

mkdir -p "$ROOT"

if [[ ! -f "$ROOT/main.py" ]]; then
  rsync -a --delete \
    --exclude '.git/' \
    --exclude 'data/' \
    --exclude 'users/' \
    --exclude '__pycache__/' \
    --exclude '.DS_Store' \
    "$SOURCE_DIR"/ "$ROOT"/
fi

python3 "$ROOT/install/install.py"

if command -v python3 >/dev/null 2>&1; then
  if [[ ! -d "$ROOT/.venv" ]]; then
    python3 -m venv "$ROOT/.venv"
  fi
  "$ROOT/.venv/bin/pip" install -r "$ROOT/requirements.txt"
fi

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Drebolbot Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$ROOT/.venv/bin/python $ROOT/main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
ExecStartPre=/usr/bin/test -x $ROOT/.venv/bin/python

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo "Installed and started: $SERVICE_NAME"
