# Drebolbot

## Установка

Клонируй репозиторий на Ubuntu и запусти установщик от root:

```bash
git clone https://github.com/pratokwau/drebolbot.git
cd drebolbot
chmod +x install/install.sh
./install/install.sh
```

Установщик:

1. Копирует проект в `/root/drebolbot`.
2. Спрашивает `TOKEN` и `ADMIN_ID`.
3. Сам создаёт пустой `authorized.json` и показывает его путь.
4. Сам создаёт `data/inventory.json`.
5. Ставит зависимости в `.venv`.
6. Регистрирует `systemd`-сервис, чтобы бот поднимался после перезагрузки.
7. Оставляет `XUI_URL`, `XUI_TOKEN`, `GROQ_API_KEY` и `OPENROUTER_API_KEY` для настройки уже в меню бота.

## После установки

- `authorized.json` потом можно заменить по показанному пути.
- `XUI_URL` и `XUI_TOKEN` задаются в меню XUI в боте.
- `GROQ_API_KEY` и `OPENROUTER_API_KEY` задаются в админ-меню в боте.
- `FP_TOKEN` по-прежнему настраивается внутри соответствующего хендлера.

## Обновление

Если в репозитории появилась новая версия, в админ-панели будет кнопка обновления:

- `🔄 Обновиться (есть новая версия)`
- `🔄 Обновиться (нет новой версии)`

Обновление подтягивает код из Git и перезапускает сервис, не трогая:

- `data/`
- `users/`
- локальные базы
- `.env`

---

## Install

Clone the repository on Ubuntu and run the installer as root:

```bash
git clone https://github.com/pratokwau/drebolbot.git
cd drebolbot
chmod +x install/install.sh
./install/install.sh
```

The installer:

1. Copies the project to `/root/drebolbot`.
2. Asks for `TOKEN` and `ADMIN_ID`.
3. Creates an empty `authorized.json` and prints its path.
4. Creates `data/inventory.json` automatically.
5. Installs dependencies into `.venv`.
6. Registers a `systemd` service so the bot restarts after reboot.
7. Leaves `XUI_URL`, `XUI_TOKEN`, `GROQ_API_KEY`, and `OPENROUTER_API_KEY` to be configured later from the bot menus.

## After install

- `authorized.json` can be replaced later at the printed path.
- `XUI_URL` and `XUI_TOKEN` are set from the XUI menu in the bot.
- `GROQ_API_KEY` and `OPENROUTER_API_KEY` are set from the admin menu in the bot.
- `FP_TOKEN` is still configured inside the relevant handler later.

## Update

If a new version appears in the repository, the admin panel shows an update button:

- `🔄 Обновиться (есть новая версия)`
- `🔄 Обновиться (нет новой версии)`

The update pulls code from Git and restarts the service without touching:

- `data/`
- `users/`
- local databases
- `.env`
