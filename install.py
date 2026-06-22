#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path("/root/drebolbot")
ENV_FILE = ROOT / ".env"


def ask(prompt: str, default: str | None = None, required: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"Введите {prompt}{suffix}: ").strip()
        if not value and default is not None:
            return default
        if value:
            return value
        if not required:
            return ""
        print("Поле обязательно.")


def ensure_project_root() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)


def write_env(items: dict[str, str]) -> None:
    ENV_FILE.write_text(
        "\n".join(f"{key}={value}" for key, value in items.items()) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    print("Drebolbot: первичная настройка")
    ensure_project_root()
    print(f"Корневая папка: {ROOT}")
    print("Сейчас нужно ввести данные для запуска бота.")
    token = ask("TOKEN", required=True)
    admin_id = ask("ADMIN_ID", required=True)
    auth_file = "authorized.json"

    env = {
        "TOKEN": token,
        "ADMIN_ID": admin_id,
        "AUTH_FILE": "authorized.json",
        "FP_TOKEN": "",
        "DATA_DIR": "data",
        "INVENTORY_FILE": "data/inventory.json",
        "GROQ_API_KEY": "",
        "OPENROUTER_API_KEY": "",
    }
    write_env(env)

    inv = ROOT / "data" / "inventory.json"
    inv.parent.mkdir(parents=True, exist_ok=True)
    if not inv.exists():
        inv.write_text(json.dumps({}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    auth_path = ROOT / auth_file
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    if not auth_path.exists():
        auth_path.write_text("[]\n", encoding="utf-8")

    print()
    print(f".env создан: {ENV_FILE}")
    print(f"Путь к authorized.json: {auth_path}")
    print("Если у тебя есть свой authorized.json, можешь заменить пустой файл по этому пути.")
    print("inventory.json создан автоматически.")
    print("Первичная настройка завершена.")
    print("Дальше запусти: python3 main.py")


if __name__ == "__main__":
    main()
