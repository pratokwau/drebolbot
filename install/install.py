#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
from pathlib import Path


ROOT = Path("/root/drebolbot")
ENV_FILE = ROOT / ".env"
SOURCE_ROOT = Path(__file__).resolve().parent.parent


def ask(prompt: str, default: str | None = None, required: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip()
        if not value and default is not None:
            return default
        if value:
            return value
        if not required:
            return ""
        print("Поле обязательно.")


def ensure_project_root() -> None:
    if ROOT.exists():
        return
    ROOT.mkdir(parents=True, exist_ok=True)
    for item in SOURCE_ROOT.iterdir():
        if item.name == "install":
            continue
        dest = ROOT / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)


def write_env(items: dict[str, str]) -> None:
    ENV_FILE.write_text(
        "\n".join(f"{key}={value}" for key, value in items.items()) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    print("Drebolbot setup")
    ensure_project_root()
    print(f"Project root: {ROOT}")
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
    print(f".env created: {ENV_FILE}")
    print(f"authorized.json path: {auth_path}")
    print("If you already have a backup authorized.json, replace the empty file at that path.")
    print("inventory.json created automatically.")
    print("To install dependencies: pip install -r requirements.txt")
    print("To start: python3 main.py")


if __name__ == "__main__":
    main()
