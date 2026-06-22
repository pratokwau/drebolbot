# Drebolbot

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
