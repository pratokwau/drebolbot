# handlers/admin.py

import json
import os
import re
from html import escape

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from loader import authorized_users, save_users, load_groups, save_groups
from handlers.utils import (no_access_reply, no_access_callback, load_requests, save_requests,
                             get_blocked_commands, block_user_command, unblock_user_command)
from config import ADMIN_ID
from states.states import GiveAccess, RevokeAccess, BlockCommand, SendMessage, Broadcast, AddGroup, AdminUserSettings, AdminUserNote
from aiogram.filters import Command
from aiogram.enums import ParseMode
from update_manager import get_update_status, update_from_git, restart_service

router = Router()
USER_NOTES_FILE = "data/user_notes.json"
USER_NOTE_MAX_LEN = 80


@router.callback_query(F.data == "noop")
async def cb_noop(callback: types.CallbackQuery):
    await callback.answer()


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def admin_menu() -> InlineKeyboardMarkup:
    requests = load_requests()
    pending = [r for r in requests if r.get("status") == "pending"]
    req_text = f"📨 Запросы доступа ({len(pending)})" if pending else "📨 Запросы доступа"
    update_label, has_update = get_update_status()
    update_text = f"🔄 Обновиться ({update_label})"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=update_text, callback_data="admin_update")
            ],
            [
                InlineKeyboardButton(text="✅ Выдать доступ", callback_data="give_access"),
                InlineKeyboardButton(text="❌ Забрать доступ", callback_data="revoke_access")
            ],
            [
                InlineKeyboardButton(text="📋 Список пользователей", callback_data="list_access")
            ],
            [
                InlineKeyboardButton(text=req_text, callback_data="list_requests")
            ],
            [
                InlineKeyboardButton(text="✉️ Отправить сообщение", callback_data="admin_send_msg")
            ],
            [
                InlineKeyboardButton(text="⚙️ Настройки пользователей", callback_data="admin_user_settings_0")
            ],
            [
                InlineKeyboardButton(text="🎫 Тикеты", callback_data="admin_tickets")
            ],
            [
                InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")
            ],
            [
                InlineKeyboardButton(text="📝 Редактировать /about", callback_data="admin_edit_about")
            ],
            [
                InlineKeyboardButton(text="👥 Группы", callback_data="admin_groups")
            ],
            [
                InlineKeyboardButton(text="🤖 AI ключи", callback_data="admin_ai_settings")
            ],
        ]
    )


def back_to_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад в меню", callback_data="back_to_admin")]
        ]
    )


def _normalize_command(text: str) -> str | None:
    cmd = (text or "").strip().lower().split()[0] if (text or "").strip() else ""
    cmd = cmd.split("@")[0]
    cmd = cmd if cmd.startswith("/") else f"/{cmd}"
    if not re.fullmatch(r"/[a-z0-9_]{2,32}", cmd):
        return None
    return cmd


def load_user_notes() -> dict:
    if not os.path.exists(USER_NOTES_FILE):
        return {}
    try:
        with open(USER_NOTES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_user_notes(notes: dict):
    os.makedirs("data", exist_ok=True)
    with open(USER_NOTES_FILE, "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=2)


def get_user_note(uid: int) -> str:
    return load_user_notes().get(str(uid), "")


def set_user_note(uid: int, note: str):
    notes = load_user_notes()
    key = str(uid)
    note = (note or "").strip()[:USER_NOTE_MAX_LEN]
    if note:
        notes[key] = note
    else:
        notes.pop(key, None)
    save_user_notes(notes)


async def show_user_card(message: types.Message, bot, uid: int):
    has_access = uid in authorized_users
    has_vpn = False
    try:
        from handlers.xui import get_vpn_user
        has_vpn = bool(get_vpn_user(uid))
    except Exception:
        pass

    try:
        user = await bot.get_chat(uid)
        name = user.full_name or "Без имени"
        nick = f"@{user.username}" if user.username else "нет ника"
    except Exception:
        name = "Неизвестно"
        nick = "нет ника"

    status = "✅ Есть доступ" if has_access else "🔐 Только VPN" if has_vpn else "🚫 Нет доступа"
    is_adm = "👑 Администратор" if uid == ADMIN_ID else ""
    blocked_cmds = get_blocked_commands(uid)
    note = get_user_note(uid)
    note_html = escape(note)

    blocked_text = ""
    if blocked_cmds:
        blocked_text = "\n🔒 <b>Заблокированные команды:</b> " + ", ".join(f"<code>{c}</code>" for c in blocked_cmds)

    text = (
        f"👤 <b>{name}</b>\n"
        f"🔗 {nick}\n"
        f"🆔 <code>{uid}</code>\n"
        f"📌 Статус: {status}"
        + (f"\n📝 Заметка: <i>{note_html}</i>" if note else "")
        + (f"\n{is_adm}" if is_adm else "")
        + blocked_text
    )

    buttons = []
    if uid != ADMIN_ID:
        if has_access:
            buttons.append([InlineKeyboardButton(text="🚫 Забрать доступ", callback_data=f"usr_revoke_{uid}")])
        else:
            buttons.append([InlineKeyboardButton(text="✅ Выдать доступ", callback_data=f"usr_give_{uid}")])
        buttons.append([InlineKeyboardButton(text="📝 Заметка", callback_data=f"usr_note_{uid}")])
        buttons.append([InlineKeyboardButton(text="🔒 Заблокировать команду", callback_data=f"usr_blkcmd_{uid}")])
        if blocked_cmds:
            buttons.append([InlineKeyboardButton(text="🔓 Разблокировать команду", callback_data=f"usr_unblkcmd_{uid}")])
    buttons.append([InlineKeyboardButton(text="↩️ К списку", callback_data="list_access")])

    await message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


def _on_off(val: bool) -> str:
    return "✅ Вкл" if val else "❌ Выкл"


def _admin_settings_targets() -> list[int]:
    targets = set(int(u) for u in authorized_users)
    try:
        from handlers.xui import load_vpn_users
        for tg_key in load_vpn_users().keys():
            if str(tg_key).startswith("anon_"):
                continue
            try:
                targets.add(int(tg_key))
            except ValueError:
                continue
    except Exception:
        pass
    return sorted(targets, key=lambda uid: (uid != ADMIN_ID, uid))


def _admin_user_targets() -> list[int]:
    return _admin_settings_targets()


async def _user_label(bot, uid: int) -> str:
    if uid == ADMIN_ID:
        return f"👑 {uid} — Admin"
    try:
        user = await bot.get_chat(uid)
        nick = f"@{user.username}" if user.username else user.full_name or str(uid)
    except Exception:
        nick = str(uid)
    note = get_user_note(uid)
    if note:
        nick = f"{nick} | {note[:24]}"
    if uid not in authorized_users:
        return f"🔐 {nick}"
    return f"👤 {nick}"


async def admin_user_settings_list_kb(bot, page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    targets = _admin_settings_targets()
    total_pages = max((len(targets) - 1) // per_page + 1, 1)
    page = max(0, min(page, total_pages - 1))
    current = targets[page * per_page:(page + 1) * per_page]

    buttons = []
    for uid in current:
        buttons.append([InlineKeyboardButton(
            text=await _user_label(bot, uid),
            callback_data=f"adm_stg_user_{uid}_{page}"
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"admin_user_settings_{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"admin_user_settings_{page + 1}"))
    buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Назад в меню", callback_data="back_to_admin")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _user_select_targets(mode: str) -> list[int]:
    if mode == "give":
        return [uid for uid in _admin_user_targets() if uid not in authorized_users]
    if mode == "revoke":
        return [uid for uid in _admin_user_targets() if uid != ADMIN_ID]
    if mode == "msg":
        return [uid for uid in _admin_user_targets() if uid != ADMIN_ID]
    return []


def _user_select_title(mode: str) -> str:
    titles = {
        "give": "✅ Выдача доступа",
        "revoke": "❌ Забор доступа",
        "msg": "✉️ Отправка сообщения",
    }
    return titles.get(mode, "Пользователи")


def _user_select_callback(mode: str, uid: int) -> str:
    if mode == "give":
        return f"adm_give_{uid}"
    if mode == "revoke":
        return f"adm_revoke_{uid}"
    return f"adm_msgto_{uid}"


async def user_select_kb(bot, mode: str, page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    targets = _user_select_targets(mode)
    total_pages = max((len(targets) - 1) // per_page + 1, 1)
    page = max(0, min(page, total_pages - 1))
    current = targets[page * per_page:(page + 1) * per_page]

    buttons = []
    for uid in current:
        buttons.append([InlineKeyboardButton(
            text=await _user_label(bot, uid),
            callback_data=_user_select_callback(mode, uid)
        )])

    if targets:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"adm_select_{mode}_{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"adm_select_{mode}_{page + 1}"))
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="↩️ Назад в меню", callback_data="back_to_admin")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def show_user_select(message: types.Message, bot, mode: str, page: int = 0):
    targets = _user_select_targets(mode)
    extra = ""
    if mode == "give":
        extra = "\n\nВведите ID человека или выберите VPN-пользователя из списка ниже:"
    elif mode == "revoke":
        extra = "\n\nВведите ID человека или выберите пользователя из списка ниже:"
    elif mode == "msg":
        extra = "\n\nВыберите получателя из списка ниже:"

    if not targets and mode == "msg":
        text = f"{_user_select_title(mode)}\n\nНет пользователей для отправки."
    elif not targets:
        text = f"{_user_select_title(mode)}{extra}\n\n<i>Подходящих пользователей в списке нет.</i>"
    else:
        text = f"{_user_select_title(mode)}{extra}"

    await message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=await user_select_kb(bot, mode, page)
    )


def admin_user_settings_kb(uid: int, page: int = 0) -> InlineKeyboardMarkup:
    from handlers.settings import get_user_settings, TTS_VOICES
    s = get_user_settings(uid)
    voice_label = TTS_VOICES.get(s["tts_voice"], s["tts_voice"])
    rows = [
        [InlineKeyboardButton(
            text=f"🔄 Перезагрузка бота: {_on_off(s['restart_notify'])}",
            callback_data=f"adm_stg_toggle_{uid}_{page}_restart_notify"
        )],
        [InlineKeyboardButton(
            text=f"📢 Рассылки: {_on_off(s['broadcast_notify'])}",
            callback_data=f"adm_stg_toggle_{uid}_{page}_broadcast_notify"
        )],
        [InlineKeyboardButton(
            text=f"📊 Ежедневный отчёт: {_on_off(s['saveprofit_notify'])}",
            callback_data=f"adm_stg_toggle_{uid}_{page}_saveprofit_notify"
        )],
        [InlineKeyboardButton(
            text=f"⏰ Время отчёта: {s['saveprofit_time']}",
            callback_data=f"adm_stg_time_{uid}_{page}"
        )],
    ]
    if int(uid) == int(ADMIN_ID):
        rows.extend([
            [InlineKeyboardButton(
                text=f"🌙 Админ-отчёт: {_on_off(s['admin_report_notify'])}",
                callback_data=f"adm_stg_toggle_{uid}_{page}_admin_report_notify"
            )],
            [InlineKeyboardButton(
                text=f"🕛 Время админ-отчёта: {s['admin_report_time']}",
                callback_data=f"adm_stg_admin_time_{uid}_{page}"
            )],
        ])
    rows.extend([
        [InlineKeyboardButton(
            text=f"🎙 Голос ИИ: {voice_label}",
            callback_data=f"adm_stg_voice_menu_{uid}_{page}"
        )],
        [InlineKeyboardButton(text="↩️ К пользователям", callback_data=f"admin_user_settings_{page}")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_admin_user_settings(message: types.Message, bot, uid: int, page: int = 0):
    label = await _user_label(bot, uid)
    await message.edit_text(
        f"⚙️ <b>Настройки пользователя</b>\n\n{label}\n🆔 <code>{uid}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_user_settings_kb(uid, page)
    )


# Главная команда /admin
@router.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        await no_access_reply(message)
        return

    update_label, _ = get_update_status()

    await message.answer(
        f"👨🏼‍💻 <b>Админ-Панель</b>\n\n"
        f"Обновление: <b>{update_label}</b>\n\n"
        f"Выберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu()
    )


# Выдать доступ — начало
@router.callback_query(F.data == "give_access")
async def cb_give_access(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await no_access_callback(callback)
        return

    await show_user_select(callback.message, callback.bot, "give", 0)
    await state.set_state(GiveAccess.waiting_for_user_id)
    await callback.answer()


# Выдать доступ — обработка
@router.message(GiveAccess.waiting_for_user_id)
async def process_give_access(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    try:
        user_id = int(message.text.strip())
        if user_id in authorized_users:
            text = f"⚠️ Пользователь <code>{user_id}</code> уже имеет доступ."
        else:
            authorized_users.append(user_id)
            save_users(authorized_users)
            text = f"✅ <b>Доступ выдан</b> пользователю <code>{user_id}</code>"
            try:
                await message.bot.send_message(
                    user_id,
                    "✅ <b>Вам выдан доступ к боту!</b>\n\nНапишите /start чтобы начать."
                )
            except Exception:
                pass
    except ValueError:
        text = "⚠️ <b>Ошибка ввода!</b> Введите числовой ID."

    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=back_to_admin_keyboard())
    await state.clear()


@router.callback_query(F.data.startswith("adm_select_"))
async def cb_admin_select_page(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await no_access_callback(callback)

    parts = callback.data.split("_")
    mode = parts[2]
    page = int(parts[3]) if len(parts) > 3 else 0
    if mode == "give":
        await state.set_state(GiveAccess.waiting_for_user_id)
    elif mode == "revoke":
        await state.set_state(RevokeAccess.waiting_for_user_id)
    else:
        await state.clear()
    await show_user_select(callback.message, callback.bot, mode, page)
    await callback.answer()


@router.callback_query(F.data.startswith("adm_give_"))
async def cb_admin_give_selected(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await no_access_callback(callback)

    uid = int(callback.data.split("_")[2])
    if uid not in authorized_users:
        authorized_users.append(uid)
        save_users(authorized_users)
        try:
            await callback.bot.send_message(
                uid,
                "✅ <b>Вам выдан доступ к боту!</b>\n\nНапишите /start чтобы начать."
            )
        except Exception:
            pass
        await callback.answer(f"✅ Доступ выдан {uid}")
    else:
        await callback.answer("У пользователя уже есть доступ", show_alert=True)

    await state.clear()
    await show_user_card(callback.message, callback.bot, uid)


# Забрать доступ — начало
@router.callback_query(F.data == "revoke_access")
async def cb_revoke_access(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await no_access_callback(callback)
        return

    await show_user_select(callback.message, callback.bot, "revoke", 0)
    await state.set_state(RevokeAccess.waiting_for_user_id)
    await callback.answer()


# Забрать доступ — обработка
@router.message(RevokeAccess.waiting_for_user_id)
async def process_revoke_access(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    try:
        user_id = int(message.text.strip())
        if user_id == ADMIN_ID:
            text = "⚠️ Нельзя забрать доступ у главного админа."
        elif user_id not in authorized_users:
            text = f"⚠️ У пользователя <code>{user_id}</code> нет доступа."
        else:
            authorized_users.remove(user_id)
            save_users(authorized_users)
            text = f"🚫 <b>Доступ забран</b> у <code>{user_id}</code>"
            try:
                await message.bot.send_message(
                    user_id,
                    "🚫 <b>Ваш доступ к боту был отозван.</b>\n\nОбратитесь к администратору."
                )
            except Exception:
                pass
    except ValueError:
        text = "⚠️ <b>Ошибка!</b> Введите числовой ID."

    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=back_to_admin_keyboard())
    await state.clear()


@router.callback_query(F.data.startswith("adm_revoke_"))
async def cb_admin_revoke_selected(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await no_access_callback(callback)

    uid = int(callback.data.split("_")[2])
    if uid == ADMIN_ID:
        await callback.answer("Нельзя забрать доступ у главного админа", show_alert=True)
        return

    if uid in authorized_users:
        authorized_users.remove(uid)
        save_users(authorized_users)
        try:
            await callback.bot.send_message(
                uid,
                "🚫 <b>Ваш доступ к боту был отозван.</b>\n\nОбратитесь к администратору."
            )
        except Exception:
            pass
        await callback.answer(f"🚫 Доступ забран у {uid}")
    else:
        await callback.answer("У пользователя нет полного доступа", show_alert=True)

    await state.clear()
    await show_user_card(callback.message, callback.bot, uid)


# Список пользователей — инлайн кнопки
@router.callback_query(F.data == "list_access")
@router.callback_query(F.data.startswith("list_access_"))
async def cb_list_access(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await no_access_callback(callback)
        return

    if callback.data.startswith("list_access_"):
        try:
            page = int(callback.data.split("_")[2])
        except ValueError:
            page = 0
    else:
        page = 0

    users = _admin_user_targets()
    if not users:
        await callback.message.edit_text(
            "📋 Список пуст.",
            reply_markup=back_to_admin_keyboard()
        )
        await callback.answer()
        return

    per_page = 10
    total_pages = max((len(users) - 1) // per_page + 1, 1)
    page = max(0, min(page, total_pages - 1))
    current_users = users[page * per_page:(page + 1) * per_page]

    buttons = []
    for uid in current_users:
        label = await _user_label(callback.bot, uid)
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"usr_info_{uid}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"list_access_{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"list_access_{page + 1}"))
    buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="↩️ Назад в меню", callback_data="back_to_admin")])
    await callback.message.edit_text(
        f"📋 <b>Пользователи ({len(users)} чел.)</b>\n\n"
        f"👤 — полный доступ\n"
        f"🔐 — только VPN\n\n"
        f"Нажмите на пользователя для управления.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


# Карточка пользователя
@router.callback_query(F.data.startswith("usr_info_"))
async def cb_usr_info(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await no_access_callback(callback)
        return

    await state.clear()
    uid = int(callback.data.split("_")[2])
    await show_user_card(callback.message, callback.bot, uid)
    await callback.answer()


@router.callback_query(F.data.startswith("usr_note_"))
async def cb_usr_note(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await no_access_callback(callback)
        return

    uid = int(callback.data.split("_")[2])
    note = get_user_note(uid)
    note_html = escape(note) if note else "не указана"
    await state.set_state(AdminUserNote.waiting_text)
    await state.update_data(note_target_uid=uid)
    await callback.message.edit_text(
        f"📝 <b>Заметка пользователя</b>\n\n"
        f"Пользователь: <code>{uid}</code>\n"
        f"Текущая заметка: <i>{note_html}</i>\n\n"
        f"Отправьте новую заметку до {USER_NOTE_MAX_LEN} символов.\n"
        f"Чтобы удалить заметку, отправьте <code>-</code>.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"usr_info_{uid}")]
        ])
    )
    await callback.answer()


@router.message(AdminUserNote.waiting_text)
async def proc_usr_note(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    uid = int(data.get("note_target_uid"))
    raw_note = (message.text or "").strip()
    note = "" if raw_note.lower() in {"-", "нет", "удалить", "delete"} else raw_note
    set_user_note(uid, note)
    await state.clear()

    await message.answer(
        "✅ Заметка сохранена." if note else "✅ Заметка удалена.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ К пользователю", callback_data=f"usr_info_{uid}")]
        ])
    )


# Выдать доступ из карточки
@router.callback_query(F.data.startswith("usr_give_"))
async def cb_usr_give(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await no_access_callback(callback)
        return

    uid = int(callback.data.split("_")[2])
    if uid not in authorized_users:
        authorized_users.append(uid)
        save_users(authorized_users)

    try:
        await callback.bot.send_message(
            uid,
            "✅ <b>Вам выдан доступ к боту!</b>\n\nНапишите /start чтобы начать."
        )
    except Exception:
        pass

    await callback.answer(f"✅ Доступ выдан {uid}")
    await show_user_card(callback.message, callback.bot, uid)


# Забрать доступ из карточки
@router.callback_query(F.data.startswith("usr_revoke_"))
async def cb_usr_revoke(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await no_access_callback(callback)
        return

    uid = int(callback.data.split("_")[2])
    if uid in authorized_users:
        authorized_users.remove(uid)
        save_users(authorized_users)

    try:
        await callback.bot.send_message(
            uid,
            "🚫 <b>Ваш доступ к боту был отозван.</b>\n\nОбратитесь к администратору."
        )
    except Exception:
        pass

    await callback.answer(f"🚫 Доступ забран у {uid}")
    # Возвращаемся к списку
    await callback.message.edit_text(
        "🚫 Доступ забран. Возвращаю к списку...",
        parse_mode=ParseMode.HTML
    )
    await cb_list_access(callback)


# Список запросов доступа
@router.callback_query(F.data == "list_requests")
async def cb_list_requests(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await no_access_callback(callback)
        return

    requests = load_requests()
    pending = [r for r in requests if r.get("status") == "pending"]

    if not pending:
        await callback.message.edit_text(
            "📨 <b>Запросы доступа</b>\n\n✅ Новых запросов нет.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_admin_keyboard()
        )
        await callback.answer()
        return

    text = f"📨 <b>Запросы доступа ({len(pending)} шт.)</b>\n\n"
    buttons = []
    for r in pending:
        uid = r["user_id"]
        name = r.get("full_name") or "Без имени"
        username = f"@{r['username']}" if r.get("username") else "нет ника"
        date = r.get("requested_at", "")
        text += f"👤 <b>{name}</b> ({username})\n🆔 <code>{uid}</code> · {date}\n\n"
        buttons.append([
            InlineKeyboardButton(text=f"✅ {name[:20]}", callback_data=f"req_allow_{uid}"),
            InlineKeyboardButton(text="❌", callback_data=f"req_deny_{uid}"),
        ])

    buttons.append([InlineKeyboardButton(text="↩️ Назад в меню", callback_data="back_to_admin")])
    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


# Разрешить доступ по запросу
@router.callback_query(F.data.startswith("req_allow_"))
async def cb_req_allow(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await no_access_callback(callback)
        return

    user_id = int(callback.data.split("_")[2])

    if user_id not in authorized_users:
        authorized_users.append(user_id)
        save_users(authorized_users)

    # Обновляем статус в запросах
    requests = load_requests()
    for r in requests:
        if r["user_id"] == user_id:
            r["status"] = "allowed"
    save_requests(requests)

    # Уведомляем пользователя
    try:
        await callback.bot.send_message(
            user_id,
            "✅ <b>Доступ предоставлен!</b>\n\nТеперь вы можете пользоваться ботом. Напишите /start"
        )
    except Exception:
        pass

    await callback.answer(f"✅ Доступ выдан пользователю {user_id}")

    # Обновляем список запросов
    requests = load_requests()
    pending = [r for r in requests if r.get("status") == "pending"]
    if not pending:
        await callback.message.edit_text(
            "📨 <b>Запросы доступа</b>\n\n✅ Все запросы обработаны.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_admin_keyboard()
        )
    else:
        # Перестраиваем список
        text = f"📨 <b>Запросы доступа ({len(pending)} шт.)</b>\n\n"
        buttons = []
        for r in pending:
            uid = r["user_id"]
            name = r.get("full_name") or "Без имени"
            username = f"@{r['username']}" if r.get("username") else "нет ника"
            date = r.get("requested_at", "")
            text += f"👤 <b>{name}</b> ({username})\n🆔 <code>{uid}</code> · {date}\n\n"
            buttons.append([
                InlineKeyboardButton(text=f"✅ {name[:20]}", callback_data=f"req_allow_{uid}"),
                InlineKeyboardButton(text="❌", callback_data=f"req_deny_{uid}"),
            ])
        buttons.append([InlineKeyboardButton(text="↩️ Назад в меню", callback_data="back_to_admin")])
        await callback.message.edit_text(
            text, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )


# Отклонить запрос
@router.callback_query(F.data.startswith("req_deny_"))
async def cb_req_deny(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await no_access_callback(callback)
        return

    user_id = int(callback.data.split("_")[2])

    requests = load_requests()
    for r in requests:
        if r["user_id"] == user_id:
            r["status"] = "denied"
    save_requests(requests)

    await callback.answer(f"❌ Запрос от {user_id} отклонён")

    # Обновляем список
    pending = [r for r in requests if r.get("status") == "pending"]
    if not pending:
        await callback.message.edit_text(
            "📨 <b>Запросы доступа</b>\n\n✅ Все запросы обработаны.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_admin_keyboard()
        )
    else:
        text = f"📨 <b>Запросы доступа ({len(pending)} шт.)</b>\n\n"
        buttons = []
        for r in pending:
            uid = r["user_id"]
            name = r.get("full_name") or "Без имени"
            username = f"@{r['username']}" if r.get("username") else "нет ника"
            date = r.get("requested_at", "")
            text += f"👤 <b>{name}</b> ({username})\n🆔 <code>{uid}</code> · {date}\n\n"
            buttons.append([
                InlineKeyboardButton(text=f"✅ {name[:20]}", callback_data=f"req_allow_{uid}"),
                InlineKeyboardButton(text="❌", callback_data=f"req_deny_{uid}"),
            ])
        buttons.append([InlineKeyboardButton(text="↩️ Назад в меню", callback_data="back_to_admin")])
        await callback.message.edit_text(
            text, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )


# Заблокировать команду — начало
@router.callback_query(F.data.startswith("usr_blkcmd_"))
async def cb_usr_blkcmd(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await no_access_callback(callback)
        return

    uid = int(callback.data.split("_")[2])
    await state.update_data(target_user_id=uid)
    await state.set_state(BlockCommand.waiting_for_command)
    await callback.message.edit_text(
        f"🔒 <b>Блокировка команды</b>\n\n"
        f"Для пользователя <code>{uid}</code>\n\n"
        f"Введите название команды <b>без слэша</b> (например: <code>start</code>, <code>rassstart</code>):",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"usr_info_{uid}")]
        ])
    )
    await callback.answer()


@router.message(BlockCommand.waiting_for_command)
async def proc_block_command(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    state_data = await state.get_data()
    uid = state_data.get("target_user_id")
    cmd = _normalize_command(message.text)
    if not cmd:
        return await message.answer(
            "⚠️ Введите команду в формате <code>start</code> или <code>/rassstart</code>.",
            parse_mode=ParseMode.HTML
        )

    block_user_command(uid, cmd)

    try:
        await message.bot.send_message(
            uid,
            f"⛔ Вам ограничен доступ к команде <code>{cmd}</code>.",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass

    await state.clear()
    await message.answer(
        f"🔒 Команда <code>{cmd}</code> заблокирована для <code>{uid}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ К пользователю", callback_data=f"usr_info_{uid}")]
        ])
    )


# ====================== НАСТРОЙКИ ПОЛЬЗОВАТЕЛЕЙ ======================

@router.callback_query(F.data.startswith("admin_user_settings_"))
async def cb_admin_user_settings(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await no_access_callback(call)

    await state.clear()
    try:
        page = int(call.data.split("_")[-1])
    except ValueError:
        page = 0

    targets = _admin_settings_targets()
    if not targets:
        await call.message.edit_text(
            "⚙️ <b>Настройки пользователей</b>\n\nПользователей пока нет.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_admin_keyboard()
        )
    else:
        await call.message.edit_text(
            f"⚙️ <b>Настройки пользователей</b>\n\nВыберите пользователя:",
            parse_mode=ParseMode.HTML,
            reply_markup=await admin_user_settings_list_kb(call.bot, page)
        )
    await call.answer()


@router.callback_query(F.data.startswith("adm_stg_user_"))
async def cb_admin_user_settings_open(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return await no_access_callback(call)

    parts = call.data.split("_")
    uid = int(parts[3])
    page = int(parts[4]) if len(parts) > 4 else 0
    await show_admin_user_settings(call.message, call.bot, uid, page)
    await call.answer()


@router.callback_query(F.data.startswith("adm_stg_toggle_"))
async def cb_admin_user_settings_toggle(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return await no_access_callback(call)

    rest = call.data[len("adm_stg_toggle_"):]
    uid_str, page_str, key = rest.split("_", 2)
    uid = int(uid_str)
    page = int(page_str)

    from handlers.settings import is_enabled, update_setting
    current = is_enabled(uid, key)
    update_setting(uid, key, not current)

    await call.message.edit_reply_markup(reply_markup=admin_user_settings_kb(uid, page))
    await call.answer("Включено" if not current else "Выключено")


@router.callback_query(F.data.startswith("adm_stg_time_"))
async def cb_admin_user_settings_time(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await no_access_callback(call)

    parts = call.data.split("_")
    uid = int(parts[3])
    page = int(parts[4]) if len(parts) > 4 else 0

    from handlers.settings import get_user_settings
    s = get_user_settings(uid)
    await state.set_state(AdminUserSettings.waiting_time)
    await state.update_data(settings_target_uid=uid, settings_page=page, settings_time_key="saveprofit_time")
    await call.message.edit_text(
        f"⏰ <b>Время отчёта</b>\n\n"
        f"Пользователь: <code>{uid}</code>\n"
        f"Текущее время: <b>{s['saveprofit_time']}</b>\n\n"
        f"Введите новое время в формате <code>ЧЧ:ММ</code>.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"adm_stg_user_{uid}_{page}")]
        ])
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm_stg_admin_time_"))
async def cb_admin_user_settings_admin_time(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await no_access_callback(call)

    parts = call.data.split("_")
    uid = int(parts[4])
    page = int(parts[5]) if len(parts) > 5 else 0
    if int(uid) != int(ADMIN_ID):
        return await call.answer("Доступно только для главного админа", show_alert=True)

    from handlers.settings import get_user_settings
    s = get_user_settings(uid)
    await state.set_state(AdminUserSettings.waiting_time)
    await state.update_data(settings_target_uid=uid, settings_page=page, settings_time_key="admin_report_time")
    await call.message.edit_text(
        f"🕛 <b>Время админ-отчёта</b>\n\n"
        f"Текущее время: <b>{s['admin_report_time']}</b>\n\n"
        f"Введите новое время в формате <code>ЧЧ:ММ</code>.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"adm_stg_user_{uid}_{page}")]
        ])
    )
    await call.answer()


@router.message(AdminUserSettings.waiting_time)
async def proc_admin_user_settings_time(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    uid = int(data.get("settings_target_uid"))
    page = int(data.get("settings_page", 0))
    time_key = data.get("settings_time_key", "saveprofit_time")

    text = (message.text or "").strip()
    if not re.match(r"^\d{1,2}:\d{2}$", text):
        return await message.answer(
            "⚠️ Неверный формат. Введите время как <code>ЧЧ:ММ</code>, например <code>22:00</code>.",
            parse_mode=ParseMode.HTML
        )

    h, m = map(int, text.split(":"))
    if h > 23 or m > 59:
        return await message.answer("⚠️ Некорректное время. Часы 0-23, минуты 0-59.")

    time_str = f"{h:02d}:{m:02d}"
    from handlers.settings import update_setting
    update_setting(uid, time_key, time_str)
    await state.clear()
    label = "Время админ-отчёта" if time_key == "admin_report_time" else "Время отчёта"
    await message.answer(
        f"✅ {label} для <code>{uid}</code> установлено: <b>{time_str}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_user_settings_kb(uid, page)
    )


@router.callback_query(F.data.startswith("adm_stg_voice_menu_"))
async def cb_admin_user_settings_voice_menu(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return await no_access_callback(call)

    parts = call.data.split("_")
    uid = int(parts[4])
    page = int(parts[5]) if len(parts) > 5 else 0

    from handlers.settings import get_user_settings, TTS_VOICES
    current = get_user_settings(uid)["tts_voice"]
    buttons = []
    for voice_id, label in TTS_VOICES.items():
        mark = "✅ " if voice_id == current else ""
        buttons.append([InlineKeyboardButton(
            text=f"{mark}{label}",
            callback_data=f"adm_stg_voice_{uid}_{page}_{voice_id}"
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Назад", callback_data=f"adm_stg_user_{uid}_{page}")])

    await call.message.edit_text(
        f"🎙 <b>Голос ИИ</b>\n\nПользователь: <code>{uid}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm_stg_voice_"))
async def cb_admin_user_settings_voice_set(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return await no_access_callback(call)

    rest = call.data[len("adm_stg_voice_"):]
    uid_str, page_str, voice_id = rest.split("_", 2)
    uid = int(uid_str)
    page = int(page_str)

    from handlers.settings import TTS_VOICES, update_setting
    if voice_id not in TTS_VOICES:
        return await call.answer("Неизвестный голос", show_alert=True)

    update_setting(uid, "tts_voice", voice_id)
    await call.answer(f"Голос изменён: {TTS_VOICES[voice_id]}")
    await show_admin_user_settings(call.message, call.bot, uid, page)


# Разблокировать команду — выбор
@router.callback_query(F.data.startswith("usr_unblkcmd_"))
async def cb_usr_unblkcmd(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await no_access_callback(callback)
        return

    uid = int(callback.data.split("_")[2])
    blocked = get_blocked_commands(uid)

    if not blocked:
        return await callback.answer("Нет заблокированных команд", show_alert=True)

    buttons = []
    for cmd in blocked:
        buttons.append([InlineKeyboardButton(
            text=f"🔓 {cmd}",
            callback_data=f"usr_docunblk_{uid}_{cmd.lstrip('/')}"
        )])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"usr_info_{uid}")])

    await callback.message.edit_text(
        f"🔓 <b>Выберите команду для разблокировки:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("usr_docunblk_"))
async def cb_do_unblock(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await no_access_callback(callback)
        return

    # usr_docunblk_{uid}_{cmd}
    # parts: ["usr", "docunblk", uid, cmd]
    parts = callback.data.split("_")
    uid = int(parts[2])
    cmd = f"/{'_'.join(parts[3:])}"

    unblock_user_command(uid, cmd)

    try:
        await callback.bot.send_message(
            uid,
            f"✅ Доступ к команде <code>{cmd}</code> восстановлен.",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass

    await callback.answer(f"🔓 {cmd} разблокирована")
    await show_user_card(callback.message, callback.bot, uid)


# ====================== ОТПРАВКА СООБЩЕНИЯ ПОЛЬЗОВАТЕЛЮ ======================

@router.callback_query(F.data == "admin_send_msg")
@router.callback_query(F.data.startswith("admin_send_msg_"))
async def cb_admin_send_msg(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await no_access_callback(callback)

    await state.clear()
    if callback.data.startswith("admin_send_msg_"):
        try:
            page = int(callback.data.split("_")[3])
        except ValueError:
            page = 0
    else:
        page = 0

    await show_user_select(callback.message, callback.bot, "msg", page)
    await callback.answer()


@router.callback_query(F.data.startswith("adm_msgto_"))
async def cb_adm_msgto(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await no_access_callback(callback)

    target_uid = int(callback.data.split("_")[2])
    try:
        user = await callback.bot.get_chat(target_uid)
        nick = f"@{user.username}" if user.username else user.full_name or str(target_uid)
    except Exception:
        nick = str(target_uid)

    await state.set_state(SendMessage.waiting_message)
    await state.update_data(send_target_uid=target_uid, send_target_nick=nick)
    await callback.message.edit_text(
        f"✉️ <b>Отправка сообщения → {nick}</b>\n\n"
        f"Отправьте текст, фото, видео, документ или голосовое — бот перешлёт получателю.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_send_msg")]
        ])
    )
    await callback.answer()


@router.message(SendMessage.waiting_message)
async def proc_send_message(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    target_uid = data.get("send_target_uid")
    nick = data.get("send_target_nick", str(target_uid))
    await state.clear()

    try:
        await message.copy_to(target_uid)
        await message.answer(
            f"✅ Сообщение доставлено → <b>{nick}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✉️ Ещё отправить", callback_data="admin_send_msg")],
                [InlineKeyboardButton(text="↩️ Назад в меню", callback_data="back_to_admin")],
            ])
        )
    except Exception as e:
        await message.answer(
            f"❌ Не удалось отправить: <code>{e}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_admin_keyboard()
        )


# ====================== РАССЫЛКА ======================

@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await no_access_callback(callback)

    await state.set_state(Broadcast.waiting_text)
    await callback.message.edit_text(
        "📢 <b>Рассылка</b>\n\n"
        "Отправьте текст или медиа для рассылки.\n"
        f"Получателей: <b>{len([u for u in authorized_users if u != ADMIN_ID])}</b> чел.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_admin")]
        ])
    )
    await callback.answer()


@router.message(Broadcast.waiting_text)
async def broadcast_preview(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    await state.set_state(Broadcast.waiting_confirm)
    await state.update_data(broadcast_msg_id=message.message_id)

    await message.answer("👁 <b>Предпросмотр рассылки:</b>", parse_mode=ParseMode.HTML)
    await message.copy_to(message.chat.id)
    await message.answer(
        f"Разослать <b>{len([u for u in authorized_users if u != ADMIN_ID])}</b> пользователям?",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Отправить", callback_data="broadcast_confirm"),
                InlineKeyboardButton(text="↩️ Назад", callback_data="admin_broadcast"),
            ]
        ])
    )


@router.callback_query(F.data == "broadcast_confirm")
async def cb_broadcast_confirm(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await no_access_callback(callback)

    data = await state.get_data()
    content_msg_id = data.get("broadcast_msg_id")
    await state.clear()

    from handlers.settings import is_enabled
    from handlers.xui import load_vpn_users

    # Авторизованные юзеры
    target_set = {u for u in authorized_users if u != ADMIN_ID}

    # + VPN-юзеры (с TG ID, кроме анонимных)
    for tg_key in load_vpn_users().keys():
        if tg_key.startswith("anon_"):
            continue
        try:
            tg_int = int(tg_key)
            if tg_int != ADMIN_ID:
                target_set.add(tg_int)
        except ValueError:
            continue

    targets = [u for u in target_set if is_enabled(u, "broadcast_notify")]

    await callback.message.edit_text(
        f"⏳ Отправляю {len(targets)} пользователям...",
        parse_mode=ParseMode.HTML
    )

    ok, fail = 0, 0
    for uid in targets:
        try:
            await callback.bot.copy_message(
                chat_id=uid,
                from_chat_id=callback.message.chat.id,
                message_id=content_msg_id
            )
            ok += 1
        except Exception:
            fail += 1

    await callback.message.edit_text(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"Доставлено: <b>{ok}</b>\n"
        f"Ошибок: <b>{fail}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад в меню", callback_data="back_to_admin")]
        ])
    )
    await callback.answer()


# Назад в меню
@router.callback_query(F.data == "back_to_admin")
async def cb_back_to_admin(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await no_access_callback(callback)
        return

    await state.clear()
    try:
        from handlers.tickets import set_admin_active_ticket
        set_admin_active_ticket(None)
    except Exception:
        pass
    await callback.message.edit_text(
        f"👨🏼‍💻 <b>Админ-панель</b>\n\n"
        f"Обновление: <b>{get_update_status()[0]}</b>\n\n"
        f"Выберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu()
    )
    await callback.answer()


@router.callback_query(F.data == "admin_update")
async def cb_admin_update(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await no_access_callback(callback)
        return

    status, has_update = get_update_status()
    if not has_update:
        await callback.answer("Новая версия не найдена", show_alert=True)
        return

    await callback.message.edit_text(
        "🔄 <b>Обновление запущено</b>\n\n"
        "Сейчас я подтяну код из репозитория, не трогая базы и данные.\n"
        "После этого сервис перезапустится сам.",
        parse_mode=ParseMode.HTML,
    )
    ok, msg = update_from_git()
    if not ok:
        await callback.message.edit_text(
            f"❌ <b>Ошибка обновления</b>\n\n<code>{msg}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_admin_keyboard()
        )
        return

    try:
        restart_service()
        await callback.message.edit_text(
            "✅ <b>Обновление установлено</b>\n\n"
            "Сервис перезапускается. Через несколько секунд бот поднимется с новой версией.",
            parse_mode=ParseMode.HTML,
        )
    finally:
        await callback.answer("Обновление выполнено", show_alert=False)


# ====================== ГРУППЫ ======================

def groups_kb(groups: list) -> InlineKeyboardMarkup:
    buttons = []
    for gid in groups:
        buttons.append([InlineKeyboardButton(
            text=f"🗑 {gid}",
            callback_data=f"admin_delgroup_{gid}"
        )])
    buttons.append([InlineKeyboardButton(text="➕ Добавить группу", callback_data="admin_addgroup")])
    buttons.append([InlineKeyboardButton(text="↩️ Назад в меню", callback_data="back_to_admin")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "admin_groups")
async def cb_admin_groups(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await no_access_callback(call)
    await state.clear()
    groups = load_groups()
    text = "👥 <b>Авторизованные группы</b>\n\n"
    if groups:
        text += "\n".join(f"• <code>{g}</code>" for g in groups)
    else:
        text += "<i>Групп пока нет.</i>"
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=groups_kb(groups))
    await call.answer()


@router.callback_query(F.data == "admin_addgroup")
async def cb_admin_addgroup(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await no_access_callback(call)
    await state.set_state(AddGroup.waiting_group_id)
    await call.message.answer(
        "👥 <b>Добавление группы</b>\n\n"
        "Отправьте ID группы (отрицательное число, например <code>-1001234567890</code>).\n\n"
        "<i>Чтобы узнать ID группы — добавьте бота в группу и перешлите любое сообщение из неё боту @userinfobot</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_groups")]
        ])
    )
    await call.answer()


@router.message(AddGroup.waiting_group_id)
async def proc_add_group(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()
    try:
        gid = int(message.text.strip())
        if gid >= 0:
            raise ValueError
    except ValueError:
        return await message.answer("⚠️ ID группы должен быть отрицательным числом, например <code>-1001234567890</code>", parse_mode=ParseMode.HTML)

    groups = load_groups()
    if gid in groups:
        await message.answer("ℹ️ Эта группа уже добавлена.")
    else:
        groups.append(gid)
        save_groups(groups)
        await message.answer(f"✅ Группа <code>{gid}</code> добавлена.", parse_mode=ParseMode.HTML)

    await state.clear()
    await message.answer(
        "👥 <b>Авторизованные группы</b>\n\n" + "\n".join(f"• <code>{g}</code>" for g in groups),
        parse_mode=ParseMode.HTML,
        reply_markup=groups_kb(groups)
    )


@router.callback_query(F.data.startswith("admin_delgroup_"))
async def cb_del_group(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return await no_access_callback(call)
    gid = int(call.data.replace("admin_delgroup_", ""))
    groups = load_groups()
    if gid in groups:
        groups.remove(gid)
        save_groups(groups)
        await call.answer(f"Группа {gid} удалена")
    else:
        await call.answer("Группа не найдена", show_alert=True)
    text = "👥 <b>Авторизованные группы</b>\n\n"
    text += "\n".join(f"• <code>{g}</code>" for g in groups) if groups else "<i>Групп пока нет.</i>"
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=groups_kb(groups))
