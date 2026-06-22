#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/drebolbot"
SERVICE_NAME="drebolbot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
REPO_URL="https://github.com/pratokwau/drebolbot.git"
APT_PACKAGES=(git python3 python3-pip)

if [[ $EUID -ne 0 ]]; then
  echo "Запусти установщик от root."
  exit 1
fi

cd /root

echo "== Drebolbot: установка =="
echo "Подготовка системы..."

ensure_apt_packages() {
  local missing=()
  for pkg in "${APT_PACKAGES[@]}"; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
      missing+=("$pkg")
    fi
  done

  if (( ${#missing[@]} > 0 )); then
    echo "Ставлю системные пакеты: ${missing[*]}"
    apt-get update
    apt-get install -y "${missing[@]}"
  fi
}

ensure_python_venv_package() {
  local py_ver py_pkg
  py_ver="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  py_pkg="python${py_ver}-venv"

  if ! dpkg -s "$py_pkg" >/dev/null 2>&1; then
    echo "Ставлю пакет $py_pkg для текущей версии Python..."
    apt-get update
    apt-get install -y "$py_pkg"
  fi
}

ensure_apt_packages
ensure_python_venv_package

echo "Старые файлы, если они есть, будут удалены."

if systemctl list-unit-files | grep -q "^${SERVICE_NAME}\.service"; then
  echo "Останавливаю старый сервис..."
  systemctl stop "$SERVICE_NAME" || true
  systemctl disable "$SERVICE_NAME" || true
fi

rm -f "$SERVICE_FILE"
systemctl daemon-reload || true
rm -rf "$ROOT"
echo "Клонирую свежую версию проекта..."
git clone "$REPO_URL" "$ROOT"

echo "Запускаю первичную настройку..."
python3 "$ROOT/install.py"

if [[ ! -d "$ROOT/.venv" ]]; then
  echo "Создаю виртуальное окружение..."
  python3 -m venv "$ROOT/.venv"
fi

echo "Обновляю pip и ставлю зависимости..."
"$ROOT/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt"

echo "Создаю systemd-сервис..."
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Drebolbot Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$ROOT/.venv/bin/python $ROOT/main.py
Restart=always
RestartSec=1
TimeoutStartSec=20
TimeoutStopSec=5
KillMode=mixed
Environment=PYTHONUNBUFFERED=1
ExecStartPre=/usr/bin/test -x $ROOT/.venv/bin/python

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo "Установка завершена."
echo "Сервис запущен: $SERVICE_NAME"
