#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/drebolbot"
SERVICE_NAME="drebolbot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
REPO_URL="https://github.com/pratokwau/drebolbot.git"

if [[ $EUID -ne 0 ]]; then
  echo "Run this installer as root."
  exit 1
fi

mkdir -p "$ROOT"

if [[ ! -d "$ROOT/.git" ]]; then
  if [[ -n "$(ls -A "$ROOT" 2>/dev/null || true)" ]]; then
    echo "$ROOT exists and is not a git repo."
    echo "Remove it or clone the repository there manually, then rerun this script."
    exit 1
  fi
  git clone "$REPO_URL" "$ROOT"
fi

python3 "$ROOT/install/install.py"

ensure_python_venv() {
  if python3 -m venv "$ROOT/.venv" >/dev/null 2>&1; then
    return 0
  fi

  echo "python3-venv is missing, installing required packages..."
  apt-get update
  apt-get install -y python3-venv python3-pip
  python3 -m venv "$ROOT/.venv"
}

if command -v python3 >/dev/null 2>&1; then
  if [[ ! -d "$ROOT/.venv" ]]; then
    ensure_python_venv
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
