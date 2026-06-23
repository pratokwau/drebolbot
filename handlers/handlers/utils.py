import os
import json
from datetime import datetime
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import INVENTORY_FILE, ADMIN_ID
from database import ProfitDatabase

REQUESTS_FILE = "data/access_requests.json"
RESTRICTIONS_FILE = "data/command_restrictions.json"
VPN_ONLY_FILE = "data/vpn_only_users.json"


def load_restrictions() -> dict:
    """Загружает ограничения команд {user_id: ["/start", "/rassstart"]}"""
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(RESTRICTIONS_FILE):
        return {}
    try:
        with open(RESTRICTIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_restrictions(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(RESTRICTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def block_user_command(user_id: int, command: str):
    """Блокирует команду для пользователя"""
    cmd = command if command.startswith("/") else f"/{command}"
    data = load_restrictions()
    key = str(user_id)
    if key not in data:
        data[key] = []
    if cmd not in data[key]:
        data[key].append(cmd)
    save_restrictions(data)


def unblock_user_command(user_id: int, command: str):
    """Разблокирует команду для пользователя"""
    cmd = command if command.startswith("/") else f"/{command}"
    data = load_restrictions()
    key = str(user_id)
    if key in data and cmd in data[key]:
        data[key].remove(cmd)
        if not data[key]:
            del data[key]
    save_restrictions(data)


def get_blocked_commands(user_id: int) -> list:
    """Возвращает список заблокированных команд пользователя"""
    data = load_restrictions()
    return data.get(str(user_id), [])


def is_command_allowed(user_id: int, command: str) -> bool:
    """Проверяет, разрешена ли команда пользователю"""
    cmd = command if command.startswith("/") else f"/{command}"
    blocked = get_blocked_commands(user_id)
    return cmd not in blocked


def load_requests() -> list:
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(REQUESTS_FILE):
        return []
    try:
        with open(REQUESTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_requests(requests: list):
    os.makedirs("data", exist_ok=True)
    with open(REQUESTS_FILE, "w", encoding="utf-8") as f:
        json.dump(requests, f, ensure_ascii=False, indent=2)


async def _notify_admin(user: types.User, bot):
    """Отправляет уведомление админу о новом запросе доступа"""
    username = f"@{user.username}" if user.username else "нет ника"
    name = user.full_name or "Без имени"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Разрешить", callback_data=f"req_allow_{user.id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"req_deny_{user.id}"),
        ]
    ])
    try:
        await bot.send_message(
            ADMIN_ID,
            f"🔔 <b>Запрос доступа</b>\n\n"
            f"👤 Имя: <b>{name}</b>\n"
            f"🔗 Ник: {username}\n"
            f"🆔 ID: <code>{user.id}</code>\n"
            f"🕐 Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            reply_markup=kb
        )
    except Exception:
        pass


async def no_access_reply(message: types.Message):
    user = message.from_user

    # Сохраняем запрос если ещё не сохранён
    requests = load_requests()
    ids = [r["user_id"] for r in requests]
    if user.id not in ids:
        requests.append({
            "user_id": user.id,
            "username": user.username or "",
            "full_name": user.full_name or "",
            "requested_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "status": "pending"
        })
        save_requests(requests)
        # Уведомляем админа только при первом запросе
        await _notify_admin(user, message.bot)

    await message.answer(
        "⛔ <b>У вас нет доступа</b> ⛔\n\n"
        "Обратитесь к администратору.\n"
        f"ID вашего аккаунта: <code>{user.id}</code>"
    )


async def no_access_callback(call: types.CallbackQuery):
    user = call.from_user

    # Сохраняем запрос если ещё не сохранён
    requests = load_requests()
    ids = [r["user_id"] for r in requests]
    if user.id not in ids:
        requests.append({
            "user_id": user.id,
            "username": user.username or "",
            "full_name": user.full_name or "",
            "requested_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "status": "pending"
        })
        save_requests(requests)
        await _notify_admin(user, call.bot)

    await call.answer(
        f"⛔ У вас нет доступа\nID: {user.id}",
        show_alert=True
    )

def get_user_dir(user_id: int):
    dir_path = f"base/{user_id}"
    os.makedirs(dir_path, exist_ok=True)
    return dir_path

def get_profit_file(user_id: int):
    return f"{get_user_dir(user_id)}/saveprofit.db"

# --- Функции для Базы Товаров (Inventory) ---

def load_inventory():
    """Загружает общую базу товаров {Название: Цена закупа}"""
    if os.path.exists(INVENTORY_FILE):
        try:
            with open(INVENTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Ошибка загрузки inventory.json: {e}")
            return {}
    return {}

def save_inventory(inventory: dict):
    """Сохраняет общую базу товаров"""
    # Создаем папку data, если её нет
    os.makedirs(os.path.dirname(INVENTORY_FILE), exist_ok=True)
    try:
        with open(INVENTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(inventory, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения inventory.json: {e}")

# --- Прибыль/продажи: SQLite в base/<tg_id>/saveprofit.db ---

def load_profits(user_id: int):
    db = ProfitDatabase(user_id)
    return db.load_profits()

def save_profits(user_id: int, profits: list):
    db = ProfitDatabase(user_id)
    db.save_profits(profits)

def format_date_now():
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


# ============ VPN-ONLY USERS (двухуровневая авторизация) ============

def load_vpn_only_users() -> list:
    """Загружает список пользователей, у которых есть VPN, но нет доступа к боту"""
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(VPN_ONLY_FILE):
        return []
    try:
        with open(VPN_ONLY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_vpn_only_users(users: list):
    """Сохраняет список VPN-пользователей"""
    os.makedirs("data", exist_ok=True)
    with open(VPN_ONLY_FILE, "w", encoding="utf-8") as f:
        json.dump([int(x) for x in users], f, ensure_ascii=False, indent=2)


def add_vpn_only_user(user_id: int):
    """Добавляет пользователя в список VPN-only"""
    users = load_vpn_only_users()
    if int(user_id) not in users:
        users.append(int(user_id))
        save_vpn_only_users(users)


def remove_vpn_only_user(user_id: int):
    """Удаляет пользователя из списка VPN-only"""
    users = load_vpn_only_users()
    users = [u for u in users if int(u) != int(user_id)]
    save_vpn_only_users(users)


def has_vpn_access(user_id: int) -> bool:
    """Проверяет, есть ли у пользователя привязанный VPN."""
    try:
        from handlers.xui import get_vpn_user
        return bool(get_vpn_user(user_id))
    except Exception:
        return False


def is_vpn_only_user(user_id: int) -> bool:
    """Пользователь с VPN без полного доступа к боту."""
    from loader import is_authorized
    return has_vpn_access(user_id) and not is_authorized(user_id)


# Разрешённые команды для VPN-only пользователей
VPN_ONLY_ALLOWED_COMMANDS = ["/start", "/status", "/myvpn", "/settings", "/help", "/cancel"]


def is_command_allowed_for_vpn_user(command: str) -> bool:
    """Проверяет, разрешена ли команда для VPN-only пользователей"""
    cmd = command if command.startswith("/") else f"/{command}"
    return cmd.lower() in [c.lower() for c in VPN_ONLY_ALLOWED_COMMANDS]
