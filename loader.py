import json
from typing import List
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from config import AUTH_FILE, ADMIN_ID, TOKEN

GROUPS_FILE = "data/authorized_groups.json"

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher(storage=MemoryStorage())


def load_users() -> List[int]:
    try:
        with open(AUTH_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return [int(x) for x in data]
    except Exception:
        return [ADMIN_ID]


def save_users(users):
    with open(AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump([int(x) for x in users], f, ensure_ascii=False, indent=2)


def load_groups() -> List[int]:
    try:
        with open(GROUPS_FILE, "r", encoding="utf-8") as f:
            return [int(x) for x in json.load(f)]
    except Exception:
        return []


def save_groups(groups: List[int]):
    import os
    os.makedirs("data", exist_ok=True)
    with open(GROUPS_FILE, "w", encoding="utf-8") as f:
        json.dump([int(x) for x in groups], f, ensure_ascii=False, indent=2)


authorized_users = load_users()


def is_authorized(user_id: int) -> bool:
    return int(user_id) in authorized_users


def is_authorized_group(chat_id: int) -> bool:
    return int(chat_id) in load_groups()


def is_authorized_context(user_id: int, chat_id: int) -> bool:
    """Авторизован если пользователь в списке ИЛИ чат — авторизованная группа"""
    return is_authorized(user_id) or is_authorized_group(chat_id)