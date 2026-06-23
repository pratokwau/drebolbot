# handlers/xui/views.py

import html

from aiogram import types
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from handlers.xui.api.client import xui_get
from handlers.xui.api.inbounds import api_get_inbounds
from handlers.xui.api.clients import api_get_client
from handlers.xui.api.helpers import parse_clients, get_client_stats_map
from handlers.xui.storage import (
    load_vpn_users, save_vpn_users, get_vpn_user, set_user_username,
    get_client_note, DEFAULT_MAX_DEVICES, refresh_username,
)
from handlers.xui.keyboards import user_menu_kb, client_actions_kb
from handlers.xui.utils import cache, _cache, format_bytes

async def _show_user_menu(call_or_msg, user_key: str, ib_id_default: int = 0, edit: bool = True):
    """Показывает меню юзера админу. user_key — TG ID или anon_xxx"""
    data = load_vpn_users()
    info = data.get(user_key)
    if not info:
        text = "❌ Пользователь не найден"
        if edit:
            return await call_or_msg.edit_text(text)
        return await call_or_msg.answer(text)

    devices = info.get("devices", [])

    # Синхронизация: дедупликация + проверка панели
    if devices:
        # Шаг 1: убираем дубли по email прямо в базе бота
        _seen = set()
        _deduped = []
        for _d in devices:
            _em = _d.get("email")
            if _em not in _seen:
                _seen.add(_em)
                _deduped.append(_d)
        if len(_deduped) != len(devices):
            data[user_key]["devices"] = _deduped
            save_vpn_users(data)
            info = data[user_key]
            devices = _deduped

        # Шаг 2: убираем устройства которых нет в панели (через paged API)
        try:
            _panel_emails = set()
            _page = 1
            while True:
                _r = await xui_get(f"/panel/api/clients/list/paged?page={_page}&pageSize=200")
                if not _r.get("success"):
                    break
                _obj = _r.get("obj", {})
                for _c in _obj.get("items", []):
                    _panel_emails.add(_c.get("email"))
                if _page * 200 >= _obj.get("total", 0):
                    break
                _page += 1
            if _panel_emails:
                _clean = [_d for _d in devices if _d.get("email") in _panel_emails]
                if len(_clean) != len(devices):
                    data[user_key]["devices"] = _clean
                    save_vpn_users(data)
                    info = data[user_key]
                    devices = _clean
        except Exception:
            pass

    # Если ib_id_default не передан - берём из первого устройства
    if not ib_id_default and devices:
        ib_id_default = devices[0]["ib_id"]

    # Обновим username если есть TG ID
    username = info.get("username", "")
    if not user_key.startswith("anon_") and not username:
        try:
            username = await refresh_username(int(user_key))
        except Exception:
            pass

    note = info.get("note", "")
    max_devs = info.get("max_devices", DEFAULT_MAX_DEVICES)
    admin_disabled = info.get("admin_disabled", False)

    inbounds, _ = await api_get_inbounds()
    total_up = 0
    total_down = 0
    active_count = 0
    devs_in_ib = [d for d in devices if d.get("ib_id") == ib_id_default]
    for d in devs_in_ib:
        ib = next((i for i in inbounds if i.get("id") == d.get("ib_id")), None)
        if not ib:
            continue
        clients = parse_clients(ib)
        cl = next((c for c in clients if c.get("email") == d.get("email")), None)
        if cl and cl.get("enable", True):
            active_count += 1
        stats_map = get_client_stats_map(ib)
        s = stats_map.get(d.get("email"), {})
        total_up += s.get("up", 0)
        total_down += s.get("down", 0)

    if admin_disabled:
        status = "🚫 Заблокировано"
    elif devs_in_ib:
        status = f"✅ Активен ({active_count}/{len(devs_in_ib)})"
    else:
        status = "📭 Нет устройств"

    if user_key.startswith("anon_"):
        header = "👤 <b>Пользователь без TG ID</b>"
    else:
        username_str = f" (@{username})" if username else ""
        header = f"👤 <b>TG: {user_key}</b>{username_str}"

    note_str = f"\n📝 Заметка: <i>{__import__('html').escape(note)}</i>" if note else ""

    text = (
        f"{header}{note_str}\n\n"
        f"📌 Статус: {status}\n"
        f"📱 Устройств: <b>{len(devs_in_ib)} / {max_devs}</b>\n"
        f"📤 Общий: <b>{format_bytes(total_up)}</b>\n"
        f"📥 Общий: <b>{format_bytes(total_down)}</b>"
    )
    if devs_in_ib:
        text += "\n\nВыберите устройство или действие:"
    else:
        text += "\n\n<i>Устройств нет. Пользователь может добавить их через /myvpn</i>"

    kb = user_menu_kb(user_key, admin_disabled, devices, ib_id_default)
    if edit:
        await call_or_msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await call_or_msg.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _refresh_client_view(call: types.CallbackQuery, cl_h: str):
    """Перерисовывает экран клиента после действия (без рекурсии в cb_xui)"""
    info = _cache.get(cl_h, {})
    email = info.get("email", "?")
    ib_id = info.get("ib_id")

    inbounds, _ = await api_get_inbounds()
    inbound = next((ib for ib in inbounds if ib.get("id") == ib_id), None)
    if not inbound:
        return

    # v3: получаем клиента через API, не через parse_clients
    client = await api_get_client(email)
    if not client:
        return

    stats_map = get_client_stats_map(inbound)
    stats = stats_map.get(email, {})
    enabled = client.get("enable", True)
    up = format_bytes(stats.get("up", 0))
    down = format_bytes(stats.get("down", 0))
    total = stats.get("total", 0)
    total_str = format_bytes(total) if total > 0 else "∞"
    expiry = client.get("expiryTime", 0)
    expiry_str = "∞" if not expiry or expiry == 0 else f"{expiry}"
    status = "✅ Активен" if enabled else "❌ Отключён"
    is_vless = inbound.get("protocol", "").lower() == "vless"

    owner_user_key = info.get("owner_uk", "")
    if not owner_user_key:
        for uk, uinfo in load_vpn_users().items():
            for d in uinfo.get("devices", []):
                if d.get("ib_id") == ib_id and d.get("email") == email:
                    owner_user_key = uk
                    break
            if owner_user_key:
                break

    note = get_client_note(ib_id, email) if not owner_user_key else ""

    text = f"👤 <b>{email}</b>\n\n"
    if owner_user_key:
        if owner_user_key.startswith("anon_"):
            text += "👥 Владелец: <i>без TG</i>\n"
        else:
            text += f"👥 Владелец: TG <code>{owner_user_key}</code>\n"
    text += (
        f"📌 Статус: {status}\n"
        f"📤 Отправлено: <b>{up}</b>\n"
        f"📥 Получено: <b>{down}</b>\n"
        f"💾 Лимит: <b>{total_str}</b>\n"
        f"⏳ Срок: <b>{expiry_str}</b>"
    )
    if note:
        text += f"\n📝 Заметка: <i>{__import__('html').escape(note)}</i>"

    try:
        await call.message.edit_text(
            text, parse_mode=ParseMode.HTML,
            reply_markup=client_actions_kb(cl_h, enabled, is_vless, owner_user_key)
        )
    except Exception:
        pass


def myvpn_main_kb(devices: list, max_devices: int, admin_disabled: bool, can_add: bool) -> InlineKeyboardMarkup:
    rows = []
    for d in devices:
        ib_id = d.get("ib_id")
        email = d.get("email", "?")
        uuid_val = d.get("uuid", "")
        h = cache(f"mvd_{ib_id}_{email}", {"email": email, "uuid": uuid_val, "ib_id": ib_id})
        rows.append([InlineKeyboardButton(text=f"📱 {email}", callback_data=f"myvpn_dev_{h}")])
    if can_add:
        rows.append([InlineKeyboardButton(text="➕ Добавить устройство", callback_data="myvpn_add")])
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="myvpn_refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def myvpn_device_kb(dev_hash: str, enabled: bool, admin_disabled: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔗 Получить VPN ссылку", callback_data=f"myvpn_link_{dev_hash}")],
        [InlineKeyboardButton(text="📖 Инструкция", callback_data=f"myvpn_inst_{dev_hash}")],
    ]
    if not admin_disabled:
        toggle_text = "⏸ Отключить" if enabled else "▶️ Включить"
        rows.append([InlineKeyboardButton(text=toggle_text, callback_data=f"myvpn_tog_{dev_hash}")])
    rows.append([InlineKeyboardButton(text="🗑 Удалить устройство", callback_data=f"myvpn_del_{dev_hash}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="myvpn_refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def show_myvpn(target, user_id: int, edit: bool = False, real_username: str = ""):
    """Показывает главное меню /myvpn со списком устройств.
    real_username — username юзера который вызвал команду (НЕ из call.message.from_user!)."""

    binding = get_vpn_user(user_id)
    if not binding:
        text = "❌ <b>У вас нет привязанного VPN.</b>\n\nОбратитесь к администратору."
        if edit:
            return await target.edit_text(text, parse_mode=ParseMode.HTML)
        return await target.answer(text, parse_mode=ParseMode.HTML)

    # Обновляем username только если он реально от юзера и отличается от сохранённого
    if real_username and binding.get("username") != real_username:
        set_user_username(user_id, real_username)
        binding["username"] = real_username

    devices = binding.get("devices", [])
    max_devices = binding.get("max_devices", DEFAULT_MAX_DEVICES)
    admin_disabled = binding.get("admin_disabled", False)

    # Считаем общий трафик и активные устройства
    inbounds, _ = await api_get_inbounds()
    total_up = 0
    total_down = 0
    for d in devices:
        ib = next((i for i in inbounds if i.get("id") == d.get("ib_id")), None)
        if not ib:
            continue
        stats_map = get_client_stats_map(ib)
        s = stats_map.get(d.get("email"), {})
        total_up += s.get("up", 0)
        total_down += s.get("down", 0)

    status = "🚫 Заблокировано админом" if admin_disabled else "✅ Активен"

    text = (
        f"🔐 <b>Ваш VPN</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"📌 Статус: {status}\n"
        f"📱 Устройств: <b>{len(devices)} / {max_devices}</b>\n"
        f"📤 Отправлено: <b>{format_bytes(total_up)}</b>\n"
        f"📥 Получено: <b>{format_bytes(total_down)}</b>"
    )

    if not devices:
        text += "\n\n<i>Нет устройств. Нажмите «Добавить устройство».</i>"

    can_add = len(devices) < max_devices and not admin_disabled
    kb = myvpn_main_kb(devices, max_devices, admin_disabled, can_add)

    if edit:
        await target.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await target.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def show_myvpn_device(call: types.CallbackQuery, dev_hash: str):
    info = _cache.get(dev_hash, {})
    email = info.get("email")
    uuid_val = info.get("uuid")
    ib_id = info.get("ib_id")
    user_id = call.from_user.id

    binding = get_vpn_user(user_id)
    if not binding:
        return await call.answer("Нет привязки", show_alert=True)
    admin_disabled = binding.get("admin_disabled", False)

    inbounds, _ = await api_get_inbounds()
    inbound = next((ib for ib in inbounds if ib.get("id") == ib_id), None)
    if not inbound:
        return await call.answer("Сервер недоступен", show_alert=True)

    client = await api_get_client(email)
    if not client:
        return await call.answer("Устройство не найдено", show_alert=True)

    stats_map = get_client_stats_map(inbound)
    stats = stats_map.get(email, {})
    enabled = client.get("enable", True)
    up = format_bytes(stats.get("up", 0))
    down = format_bytes(stats.get("down", 0))

    if admin_disabled:
        status = "🚫 Заблокировано админом"
    elif enabled:
        status = "✅ Активен"
    else:
        status = "⏸ Выключен"

    text = (
        f"📱 <b>{html.escape(str(email))}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"📌 Статус: {status}\n"
        f"📤 Отправлено: <b>{up}</b>\n"
        f"📥 Получено: <b>{down}</b>"
    )

    await call.message.edit_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=myvpn_device_kb(dev_hash, enabled, admin_disabled)
    )
