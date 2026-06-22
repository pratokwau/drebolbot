# Drebolbot

## Install

Run the installer as root from the repository:

```bash
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

## After install

- `authorized.json` can be replaced later at the printed path.
- `XUI_URL` and `XUI_TOKEN` are set from the XUI menu in the bot.
- `GROQ_API_KEY` and `OPENROUTER_API_KEY` are set from the admin menu in the bot.
- `FP_TOKEN` is still configured inside the relevant handler later.
