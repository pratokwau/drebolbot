#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/drebolbot"
SERVICE_NAME="drebolbot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
REPO_URL="https://github.com/pratokwau/drebolbot.git"
APT_PACKAGES=(git python3 python3-pip)

if [[ $EUID -ne 0 ]]; then
  echo "Run this installer as root."
  exit 1
fi

ensure_apt_packages() {
  local missing=()
  for pkg in "${APT_PACKAGES[@]}"; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
      missing+=("$pkg")
    fi
  done

  if (( ${#missing[@]} > 0 )); then
    echo "Installing system packages: ${missing[*]}"
    apt-get update
    apt-get install -y "${missing[@]}"
  fi
}

ensure_python_venv_package() {
  local py_ver py_pkg
  py_ver="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  py_pkg="python${py_ver}-venv"

  if ! dpkg -s "$py_pkg" >/dev/null 2>&1; then
    echo "Installing $py_pkg for the current Python runtime..."
    apt-get update
    apt-get install -y "$py_pkg"
  fi
}

ensure_apt_packages
ensure_python_venv_package

if systemctl list-unit-files | grep -q "^${SERVICE_NAME}\.service"; then
  systemctl stop "$SERVICE_NAME" || true
  systemctl disable "$SERVICE_NAME" || true
fi

rm -f "$SERVICE_FILE"
systemctl daemon-reload || true
rm -rf "$ROOT"
git clone "$REPO_URL" "$ROOT"

python3 "$ROOT/install.py"

if [[ ! -d "$ROOT/.venv" ]]; then
  python3 -m venv "$ROOT/.venv"
fi

"$ROOT/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt"

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
