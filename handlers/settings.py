# handlers/settings.py

import os
import json
import re

from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode

from loader import is_authorized
from config import ADMIN_ID
from handlers.utils import no_access_reply, no_access_callback, is_vpn_only_user
from handlers.xui import get_vpn_user

router = Router()
EXIT_HINT = "\n\n<i>Для выхода введите /cancel</i>"

SETTINGS_FILE = "data/user_settings.json"

DEFAULTS = {
    "restart_notify":    False,
    "broadcast_notify":  True,
    "saveprofit_notify": False,
    "saveprofit_time":   "23:59",
    "admin_report_notify": True,
    "admin_report_time": "23:59",
    "tts_voice":         "ru-RU-SvetlanaNeural",
}

TTS_VOICES = {
    "ru-RU-SvetlanaNeural": "🔊 Светлана (жен.)",
    "ru-RU-DmitryNeural":   "🔊 Дмитрий (муж.)",
}


class SettingsStates(StatesGroup):
    waiting_time = State()
    waiting_admin_report_time = State()


# ====================== ХРАНИЛИЩЕ ======================

def load_all() -> dict:
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_all(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _migrate_restart_notify_off():
    """Одноразовая миграция: выключить restart_notify у всех существующих юзеров.
    Запоминаем в файле что миграция была выполнена."""
    flag_file = "data/.restart_notify_off_done"
    if os.path.exists(flag_file):
        return
    data = load_all()
    if data:
        for uid, s in data.items():
            if isinstance(s, dict):
                s["restart_notify"] = False
        _save_all(data)
    os.makedirs("data", exist_ok=True)
    with open(flag_file, "w") as f:
        f.write("done")


_migrate_restart_notify_off()


def get_user_settings(uid: int) -> dict:
    data = load_all()
    s = data.get(str(uid), {})
    return {**DEFAULTS, **s}


def update_setting(uid: int, key: str, value):
    data = load_all()
    if str(uid) not in data:
        data[str(uid)] = dict(DEFAULTS)
    data[str(uid)][key] = value
    _save_all(data)


def is_enabled(uid: int, key: str) -> bool:
    return get_user_settings(uid).get(key, DEFAULTS.get(key, True))


# ====================== UI ======================

def _on_off(val: bool) -> str:
    return "✅ Вкл" if val else "❌ Выкл"


def settings_kb(uid: int) -> InlineKeyboardMarkup:
    s = get_user_settings(uid)
    voice_label = TTS_VOICES.get(s["tts_voice"], s["tts_voice"])
    rows = [
        [InlineKeyboardButton(
            text=f"🔄 Перезагрузка бота: {_on_off(s['restart_notify'])}",
            callback_data="stg_toggle_restart_notify"
        )],
        [InlineKeyboardButton(
            text=f"📢 Рассылки: {_on_off(s['broadcast_notify'])}",
            callback_data="stg_toggle_broadcast_notify"
        )],
        [InlineKeyboardButton(
            text=f"📊 Ежедневный отчёт: {_on_off(s['saveprofit_notify'])}",
            callback_data="stg_toggle_saveprofit_notify"
        )],
        [InlineKeyboardButton(
            text=f"⏰ Время отчёта: {s['saveprofit_time']}",
            callback_data="stg_set_time"
        )],
    ]
    if int(uid) == int(ADMIN_ID):
        rows.extend([
            [InlineKeyboardButton(
                text=f"🌙 Админ-отчёт: {_on_off(s['admin_report_notify'])}",
                callback_data="stg_toggle_admin_report_notify"
            )],
            [InlineKeyboardButton(
                text=f"🕛 Время админ-отчёта: {s['admin_report_time']}",
                callback_data="stg_set_admin_report_time"
            )],
        ])
    rows.extend([
        [InlineKeyboardButton(
            text=f"🎙 Голос ИИ: {voice_label}",
            callback_data="stg_voice_menu"
        )],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_kb_vpn_only(uid: int) -> InlineKeyboardMarkup:
    """Урезанное меню для VPN-юзеров без доступа к боту"""
    s = get_user_settings(uid)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🔄 Перезагрузка бота: {_on_off(s['restart_notify'])}",
            callback_data="stg_toggle_restart_notify"
        )],
        [InlineKeyboardButton(
            text=f"📢 Рассылки: {_on_off(s['broadcast_notify'])}",
            callback_data="stg_toggle_broadcast_notify"
        )],
    ])


def has_settings_access(uid: int) -> bool:
    """Доступ к /settings есть у авторизованных ИЛИ у юзеров с VPN"""
    if is_authorized(uid):
        return True
    return bool(get_vpn_user(uid))


def parse_time_value(text: str) -> str | None:
    text = (text or "").strip()
    if not re.match(r"^\d{1,2}:\d{2}$", text):
        return None
    h, m = map(int, text.split(":"))
    if h > 23 or m > 59:
        return None
    return f"{h:02d}:{m:02d}"


# ====================== HANDLERS ======================

@router.message(Command("settings"))
async def cmd_settings(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if not has_settings_access(uid):
        return await no_access_reply(message)

    await state.clear()
    if is_authorized(uid):
        kb = settings_kb(uid)
    else:
        kb = settings_kb_vpn_only(uid)

    await message.answer(
        "⚙️ <b>Настройки уведомлений</b>\n\nНажмите на пункт, чтобы переключить:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )


@router.callback_query(F.data.startswith("stg_toggle_"))
async def cb_toggle(call: types.CallbackQuery):
    uid = call.from_user.id
    if not has_settings_access(uid):
        return await no_access_callback(call)

    key = call.data.replace("stg_toggle_", "")

    # VPN-юзерам без полного доступа разрешены только restart_notify и broadcast_notify
    if not is_authorized(uid) and key not in ("restart_notify", "broadcast_notify"):
        return await call.answer("Недоступно", show_alert=True)

    current = is_enabled(uid, key)
    update_setting(uid, key, not current)

    kb = settings_kb(uid) if is_authorized(uid) else settings_kb_vpn_only(uid)
    await call.message.edit_reply_markup(reply_markup=kb)
    await call.answer("Включено" if not current else "Выключено")


@router.callback_query(F.data == "stg_set_time")
async def cb_set_time(call: types.CallbackQuery, state: FSMContext):
    if not is_authorized(call.from_user.id):
        return await no_access_callback(call)

    s = get_user_settings(call.from_user.id)
    await state.set_state(SettingsStates.waiting_time)
    await call.message.answer(
        f"⏰ <b>Введите время отчёта</b>\n\nТекущее: <b>{s['saveprofit_time']}</b>\n\nФормат: <code>ЧЧ:ММ</code> (например <code>22:00</code>)" + EXIT_HINT,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="stg_cancel_time")]
        ])
    )
    await call.answer()


@router.callback_query(F.data == "stg_set_admin_report_time")
async def cb_set_admin_report_time(call: types.CallbackQuery, state: FSMContext):
    if int(call.from_user.id) != int(ADMIN_ID):
        return await no_access_callback(call)

    s = get_user_settings(call.from_user.id)
    await state.set_state(SettingsStates.waiting_admin_report_time)
    await call.message.answer(
        f"🕛 <b>Введите время админ-отчёта</b>\n\n"
        f"Текущее: <b>{s['admin_report_time']}</b>\n\n"
        f"Формат: <code>ЧЧ:ММ</code> (например <code>23:59</code>)" + EXIT_HINT,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="stg_cancel_time")]
        ])
    )
    await call.answer()


@router.callback_query(F.data == "stg_cancel_time")
async def cb_cancel_time(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.delete()
    await call.answer("Отменено")


@router.callback_query(F.data == "stg_voice_menu")
async def cb_voice_menu(call: types.CallbackQuery):
    if not is_authorized(call.from_user.id):
        return await no_access_callback(call)

    s = get_user_settings(call.from_user.id)
    current = s["tts_voice"]
    buttons = []
    for voice_id, label in TTS_VOICES.items():
        mark = "✅ " if voice_id == current else ""
        buttons.append([InlineKeyboardButton(
            text=f"{mark}{label}",
            callback_data=f"stg_voice_{voice_id}"
        )])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="stg_back")])
    await call.message.edit_text(
        "🎙 <b>Выберите голос для озвучки ИИ:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await call.answer()


@router.callback_query(F.data.startswith("stg_voice_ru"))
async def cb_set_voice(call: types.CallbackQuery):
    if not is_authorized(call.from_user.id):
        return await no_access_callback(call)

    voice_id = call.data.replace("stg_voice_", "")
    if voice_id not in TTS_VOICES:
        return await call.answer("Неизвестный голос", show_alert=True)
    update_setting(call.from_user.id, "tts_voice", voice_id)
    label = TTS_VOICES[voice_id]
    await call.answer(f"Голос изменён: {label}")
    await call.message.edit_text(
        "⚙️ <b>Настройки уведомлений</b>\n\nНажмите на пункт, чтобы переключить:",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_kb(call.from_user.id)
    )


@router.callback_query(F.data == "stg_back")
async def cb_stg_back(call: types.CallbackQuery):
    if not is_authorized(call.from_user.id):
        return await no_access_callback(call)
    await call.message.edit_text(
        "⚙️ <b>Настройки уведомлений</b>\n\nНажмите на пункт, чтобы переключить:",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_kb(call.from_user.id)
    )
    await call.answer()


@router.message(SettingsStates.waiting_time)
async def proc_set_time(message: types.Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        await state.clear()
        return

    time_str = parse_time_value(message.text)
    if not time_str:
        return await message.answer("⚠️ Неверный формат. Введите время как <code>ЧЧ:ММ</code>, например <code>22:00</code>", parse_mode=ParseMode.HTML)
    update_setting(message.from_user.id, "saveprofit_time", time_str)
    await state.clear()
    await message.answer(
        f"✅ Время отчёта установлено: <b>{time_str}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_kb(message.from_user.id)
    )


@router.message(SettingsStates.waiting_admin_report_time)
async def proc_set_admin_report_time(message: types.Message, state: FSMContext):
    if int(message.from_user.id) != int(ADMIN_ID):
        await state.clear()
        return

    time_str = parse_time_value(message.text)
    if not time_str:
        return await message.answer("⚠️ Неверный формат. Введите время как <code>ЧЧ:ММ</code>, например <code>23:59</code>", parse_mode=ParseMode.HTML)

    update_setting(message.from_user.id, "admin_report_time", time_str)
    await state.clear()
    await message.answer(
        f"✅ Время админ-отчёта установлено: <b>{time_str}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_kb(message.from_user.id)
    )
