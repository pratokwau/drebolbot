# handlers/xui/storage.py

import json
import os
import secrets

VPN_USERS_FILE = "data/vpn_users.json"
CLIENT_NOTES_FILE = "data/client_notes.json"
NOTE_MAX_LEN = 50
DEFAULT_MAX_DEVICES = 10


def load_vpn_users() -> dict:
    import os as _os
    if not _os.path.exists(VPN_USERS_FILE):
        return {}
    try:
        with open(VPN_USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _migrate_vpn_users(data)
    except Exception:
        return {}


def _migrate_vpn_users(data: dict) -> dict:
    """Конвертация старого формата {uuid, email, ib_id} в новый {devices: [...]}"""
    # Чистим неправильные username (когда сохранился username бота)
    BAD_USERNAMES = {"drebolwork_bot"}
    changed = False
    for tg_id, info in data.items():
        if isinstance(info, dict) and info.get("username", "").lower() in {b.lower() for b in BAD_USERNAMES}:
            info["username"] = ""
            changed = True

    for tg_id, info in data.items():
        if "devices" not in info:
            old_uuid = info.get("uuid")
            old_email = info.get("email")
            old_ib = info.get("ib_id")
            devices = []
            if old_uuid and old_email and old_ib:
                devices.append({"ib_id": old_ib, "uuid": old_uuid, "email": old_email})
            info["devices"] = devices
            info.pop("uuid", None)
            info.pop("email", None)
            info.pop("ib_id", None)
            info.setdefault("max_devices", DEFAULT_MAX_DEVICES)
            info.setdefault("note", "")
            info.setdefault("username", "")
            changed = True
        else:
            info.setdefault("max_devices", DEFAULT_MAX_DEVICES)
            info.setdefault("note", "")
            info.setdefault("username", "")
    if changed:
        _save_vpn_users_raw(data)
    return data


def _save_vpn_users_raw(data: dict):
    import os as _os
    _os.makedirs("data", exist_ok=True)
    with open(VPN_USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_vpn_users(data: dict):
    _save_vpn_users_raw(data)


def get_vpn_user(tg_id: int) -> dict | None:
    data = load_vpn_users()
    return data.get(str(tg_id))


def get_tg_id_by_client(ib_id: int, email: str) -> int | None:
    """Ищет TG ID юзера которому принадлежит клиент по ib_id+email"""
    data = load_vpn_users()
    for tg_id, info in data.items():
        for d in info.get("devices", []):
            if d.get("ib_id") == ib_id and d.get("email") == email:
                try:
                    return int(tg_id)
                except ValueError:
                    return None
    return None


def add_device_to_user(tg_id: int, ib_id: int, uuid: str, email: str):
    data = load_vpn_users()
    key = str(tg_id)
    if key not in data:
        data[key] = {
            "username": "",
            "note": "",
            "max_devices": DEFAULT_MAX_DEVICES,
            "admin_disabled": False,
            "devices": []
        }
    # Проверка на дубликат: если устройство (ib_id+email) уже есть — не добавляем
    for d in data[key]["devices"]:
        if d.get("ib_id") == ib_id and d.get("email") == email:
            d["uuid"] = uuid  # обновляем UUID на всякий случай
            save_vpn_users(data)
            return
    data[key]["devices"].append({"ib_id": ib_id, "uuid": uuid, "email": email})
    save_vpn_users(data)


def remove_device_from_user(tg_id: int, ib_id: int, email: str):
    data = load_vpn_users()
    key = str(tg_id)
    if key in data:
        data[key]["devices"] = [
            d for d in data[key].get("devices", [])
            if not (d.get("ib_id") == ib_id and d.get("email") == email)
        ]
        save_vpn_users(data)


def create_empty_user(tg_id: int, max_devices: int = DEFAULT_MAX_DEVICES, username: str = "", note: str = ""):
    """Создаёт пустого юзера без устройств"""
    data = load_vpn_users()
    key = str(tg_id)
    if key not in data:
        data[key] = {
            "username": username,
            "note": note,
            "max_devices": max_devices,
            "admin_disabled": False,
            "devices": []
        }
        save_vpn_users(data)


def unbind_vpn_user(tg_id: int):
    """Удаляет привязку юзера (но клиенты остаются в 3xUI)"""
    data = load_vpn_users()
    data.pop(str(tg_id), None)
    save_vpn_users(data)


def _new_anon_key() -> str:
    """Генерирует уникальный ключ для анонимного юзера"""
    return f"anon_{secrets.token_hex(4)}"


def convert_to_anon(tg_id: int) -> str:
    """Конвертирует TG-юзера в анонимного, сохраняя его устройства"""
    data = load_vpn_users()
    key = str(tg_id)
    if key not in data:
        return ""
    user_data = data.pop(key)
    user_data["username"] = ""
    anon_key = _new_anon_key()
    data[anon_key] = user_data
    save_vpn_users(data)
    return anon_key


def bind_tg_to_anon(anon_key: str, tg_id: int):
    """Привязывает TG ID к анонимному юзеру"""
    data = load_vpn_users()
    if anon_key not in data:
        return False
    if str(tg_id) in data:
        return False  # такой TG уже есть
    user_data = data.pop(anon_key)
    data[str(tg_id)] = user_data
    save_vpn_users(data)
    return True


def create_user(tg_id: int | None, max_devices: int = DEFAULT_MAX_DEVICES, note: str = "") -> str:
    """Создаёт пустого юзера. Если tg_id=None — анонимный. Возвращает ключ."""
    data = load_vpn_users()
    if tg_id:
        key = str(tg_id)
        if key in data:
            return key
    else:
        key = _new_anon_key()
    data[key] = {
        "username": "",
        "note": note,
        "max_devices": max_devices,
        "admin_disabled": False,
        "devices": []
    }
    save_vpn_users(data)
    return key


def delete_user_completely(user_key: str):
    """Удаляет юзера и помечает что устройства тоже нужно удалить (в API отдельно)"""
    data = load_vpn_users()
    user = data.pop(user_key, None)
    save_vpn_users(data)
    return user


def set_admin_disabled(tg_id: int, value: bool):
    data = load_vpn_users()
    if str(tg_id) in data:
        data[str(tg_id)]["admin_disabled"] = value
        save_vpn_users(data)


def set_user_note(tg_id: int, note: str):
    data = load_vpn_users()
    if str(tg_id) in data:
        data[str(tg_id)]["note"] = note[:NOTE_MAX_LEN]
        save_vpn_users(data)


def set_user_username(tg_id: int, username: str):
    data = load_vpn_users()
    if str(tg_id) in data:
        data[str(tg_id)]["username"] = username or ""
        save_vpn_users(data)


async def refresh_username(tg_id: int) -> str:
    """Пытается получить username юзера через bot.get_chat и кэширует"""
    try:
        from loader import bot as _bot
        chat = await _bot.get_chat(tg_id)
        username = chat.username or ""
        set_user_username(tg_id, username)
        return username
    except Exception as e:
        print(f"[XUI USERNAME] Не удалось получить username для {tg_id}: {e}")
        return ""


async def refresh_all_usernames():
    """Обновляет username для всех TG-привязанных юзеров"""
    data = load_vpn_users()
    for tg_id_str in list(data.keys()):
        if tg_id_str.startswith("anon_"):
            continue
        try:
            tg_id = int(tg_id_str)
        except ValueError:
            continue
        await refresh_username(tg_id)


# ===== Заметки одиночных клиентов =====

def load_client_notes() -> dict:
    import os as _os
    if not _os.path.exists(CLIENT_NOTES_FILE):
        return {}
    try:
        with open(CLIENT_NOTES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_client_notes(data: dict):
    import os as _os
    _os.makedirs("data", exist_ok=True)
    with open(CLIENT_NOTES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_client_note(ib_id: int, email: str) -> str:
    data = load_client_notes()
    return data.get(f"{ib_id}_{email}", "")


def set_client_note(ib_id: int, email: str, note: str):
    data = load_client_notes()
    key = f"{ib_id}_{email}"
    if note:
        data[key] = note[:NOTE_MAX_LEN]
    else:
        data.pop(key, None)
    save_client_notes(data)


def remove_client_note(ib_id: int, email: str):
    data = load_client_notes()
    data.pop(f"{ib_id}_{email}", None)
    save_client_notes(data)