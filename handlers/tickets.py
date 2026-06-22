# handlers/tickets.py

import os
import json
from datetime import datetime
from html import escape

from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode

from loader import bot, is_authorized
from config import ADMIN_ID
from handlers.utils import no_access_reply, no_access_callback, has_vpn_access

router = Router()

TICKETS_FILE = "data/tickets.json"

EXIT_HINT = "\n\n<i>Для выхода введите /cancel</i>"

# Какой тикет админ сейчас просматривает (in-memory, обнуляется при рестарте)
_admin_active_ticket: int | None = None


def set_admin_active_ticket(ticket_id: int | None):
    global _admin_active_ticket
    _admin_active_ticket = ticket_id


def get_admin_active_ticket() -> int | None:
    return _admin_active_ticket


class TicketUser(StatesGroup):
    chatting = State()


class TicketAdmin(StatesGroup):
    chatting = State()


# ====================== ХРАНИЛИЩЕ ======================

def _load() -> dict:
    if not os.path.exists(TICKETS_FILE):
        return {"next_id": 1, "tickets": {}}
    try:
        with open(TICKETS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"next_id": 1, "tickets": {}}


def _save(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(TICKETS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_user_ticket(user_id: int) -> dict | None:
    for t in _load()["tickets"].values():
        if t["user_id"] == user_id:
            return t
    return None


def get_or_create_ticket(user_id: int) -> tuple[dict, bool]:
    existing = get_user_ticket(user_id)
    if existing:
        return existing, False
    data = _load()
    tid = data["next_id"]
    ticket = {
        "id": tid,
        "user_id": user_id,
        "created_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "has_unread": False,
        "messages": []
    }
    data["tickets"][str(tid)] = ticket
    data["next_id"] = tid + 1
    _save(data)
    return ticket, True


def get_ticket(ticket_id: int) -> dict | None:
    return _load()["tickets"].get(str(ticket_id))


def set_unread(ticket_id: int, value: bool):
    data = _load()
    t = data["tickets"].get(str(ticket_id))
    if t:
        t["has_unread"] = value
        _save(data)


def add_message(ticket_id: int, from_type: str, text: str = None, media_type: str = None,
                from_chat_id: int = None, message_id: int = None):
    data = _load()
    t = data["tickets"].get(str(ticket_id))
    if not t:
        return
    entry = {"from": from_type, "timestamp": datetime.now().strftime("%d.%m %H:%M")}
    if text:
        entry["text"] = text[:300]
    if media_type:
        entry["media"] = media_type
    if from_chat_id and message_id:
        entry["from_chat_id"] = from_chat_id
        entry["message_id"] = message_id
    t["messages"].append(entry)
    _save(data)


def format_history(ticket: dict, last_n: int = 20) -> str:
    msgs = ticket["messages"][-last_n:]
    if not msgs:
        return "<i>Сообщений пока нет</i>"
    lines = []
    for m in msgs:
        who = "👤 Пользователь" if m["from"] == "user" else "👨‍💻 Вы"
        time = m.get("timestamp", "")
        content = escape(m.get("text") or f"[{m.get('media', 'медиа')}]")
        lines.append(f"<b>{who}</b> <i>{time}</i>\n{content}")
    return "\n\n".join(lines)


def _format_msg_preview(m: dict, limit: int = 160) -> str:
    who = "👤 Пользователь" if m.get("from") == "user" else "👨‍💻 Админ"
    time = m.get("timestamp", "")
    content = m.get("text") or f"[{m.get('media', 'медиа')}]"
    content = escape(content)
    if len(content) > limit:
        content = content[:limit - 3] + "..."
    return f"<b>{who}</b> <i>{time}</i>\n{content}"


def format_history_compact(ticket: dict, last_n: int = 8) -> str:
    msgs = ticket["messages"][-last_n:]
    if not msgs:
        return "<i>Сообщений пока нет</i>"
    return "\n\n".join(_format_msg_preview(m, limit=220) for m in msgs)


async def _ticket_user_label(bot_obj, user_id: int) -> str:
    try:
        user = await bot_obj.get_chat(user_id)
        return f"@{user.username}" if user.username else user.full_name or str(user_id)
    except Exception:
        return str(user_id)


def ticket_panel_kb(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✍️ Ответить", callback_data=f"adm_ticket_reply_{ticket_id}"),
            InlineKeyboardButton(text="📜 История", callback_data=f"adm_ticket_history_{ticket_id}"),
        ],
        [
            InlineKeyboardButton(text="📎 Медиа", callback_data=f"adm_ticket_media_{ticket_id}"),
            InlineKeyboardButton(text="↩️ К тикетам", callback_data="admin_tickets"),
        ],
    ])


async def show_ticket_panel(message: types.Message, ticket: dict, edit: bool = True):
    user_id = ticket["user_id"]
    nick = await _ticket_user_label(message.bot, user_id)
    count = len(ticket.get("messages", []))
    last_msg = ticket["messages"][-1] if ticket.get("messages") else None
    last_text = _format_msg_preview(last_msg, limit=180) if last_msg else "<i>Сообщений пока нет</i>"
    unread = "🔴 Есть новые" if ticket.get("has_unread") else "✅ Прочитано"

    text = (
        f"🎫 <b>Тикет #{ticket['id']}</b>\n"
        f"👤 {escape(nick)} · <code>{user_id}</code>\n"
        f"📅 Создан: <b>{ticket['created_at']}</b>\n"
        f"📨 Сообщений: <b>{count}</b>\n"
        f"📌 Статус: {unread}\n\n"
        f"<b>Последнее сообщение:</b>\n{last_text}"
    )
    if edit:
        await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=ticket_panel_kb(ticket["id"]))
    else:
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=ticket_panel_kb(ticket["id"]))


def _media_type(message: types.Message) -> str | None:
    if message.photo:      return "фото"
    if message.video:      return "видео"
    if message.document:   return "документ"
    if message.voice:      return "голосовое"
    if message.audio:      return "аудио"
    if message.sticker:    return "стикер"
    if message.video_note: return "видео-кружок"
    return None


async def _forward_to(message: types.Message, target_uid: int, label: str):
    media = _media_type(message)
    if message.text:
        await bot.send_message(target_uid, f"{label}\n{message.text}", parse_mode=ParseMode.HTML)
    elif media:
        await bot.send_message(target_uid, f"{label} [{media}]", parse_mode=ParseMode.HTML)
        await message.copy_to(target_uid)


# ====================== USER HANDLERS ======================

@router.message(Command("help"))
async def cmd_help(message: types.Message, state: FSMContext):
    if not is_authorized(message.from_user.id) and not has_vpn_access(message.from_user.id):
        return await no_access_reply(message)

    ticket, is_new = get_or_create_ticket(message.from_user.id)
    await state.set_state(TicketUser.chatting)
    await state.update_data(ticket_id=ticket["id"])

    if is_new:
        await message.answer(
            "💬 <b>Чат с администратором открыт!</b>\n\n"
            "Пишите — можно отправлять текст, фото, видео и файлы."
            f"{EXIT_HINT}",
            parse_mode=ParseMode.HTML
        )
        try:
            user = await message.bot.get_chat(message.from_user.id)
            nick = f"@{user.username}" if user.username else user.full_name or str(message.from_user.id)
            await bot.send_message(
                ADMIN_ID,
                f"🎫 <b>Новый тикет #{ticket['id']}</b>\n"
                f"👤 {nick} (<code>{message.from_user.id}</code>)",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=f"💬 Открыть тикет #{ticket['id']}", callback_data=f"adm_ticket_{ticket['id']}")]
                ])
            )
        except Exception:
            pass
    else:
        history = format_history(ticket)
        await message.answer(
            f"💬 <b>Ваш чат с администратором</b>\n\n{history}"
            f"{EXIT_HINT}",
            parse_mode=ParseMode.HTML
        )


@router.message(TicketUser.chatting)
async def user_ticket_message(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    if not ticket_id:
        await state.clear()
        return

    text = message.text or message.caption
    media = _media_type(message)
    add_message(
        ticket_id, "user", text=text, media_type=media,
        from_chat_id=message.chat.id, message_id=message.message_id
    )

    ticket = get_ticket(ticket_id)
    user = message.from_user
    nick = f"@{user.username}" if user.username else user.full_name or str(user.id)
    now = datetime.now().strftime("%d.%m %H:%M")
    label = f"📩 Тикет #{ticket_id} · {nick} · {now}"

    try:
        # Если админ сейчас в этом тикете — пересылаем сразу С МЕДИА
        if get_admin_active_ticket() == ticket_id:
            if message.text:
                await bot.send_message(ADMIN_ID, f"{label}\n\n{message.text}", parse_mode=ParseMode.HTML)
            else:
                existing_caption = message.caption or ""
                caption = f"{label}\n{existing_caption}".strip()
                await message.copy_to(ADMIN_ID, caption=caption)
        else:
            # Шлём баннер только при ПЕРВОМ непрочитанном
            if not ticket.get("has_unread"):
                set_unread(ticket_id, True)
                await bot.send_message(
                    ADMIN_ID,
                    f"📩 <b>Тикет #{ticket_id} · {nick}</b>\nЕсть новые сообщения",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=f"💬 Открыть тикет #{ticket_id}", callback_data=f"adm_ticket_{ticket_id}")]
                    ])
                )
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки: {e}")
        return

    await message.answer(f"✅ Отправлено{EXIT_HINT}", parse_mode=ParseMode.HTML)


# ====================== ADMIN HANDLERS ======================

@router.callback_query(F.data == "admin_tickets")
async def cb_admin_tickets(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)

    await state.clear()
    set_admin_active_ticket(None)
    data = _load()
    tickets = sorted(data["tickets"].values(), key=lambda x: x["id"], reverse=True)

    buttons = []
    for t in tickets:
        nick = await _ticket_user_label(call.bot, t["user_id"])
        unread = " 🔴" if t.get("has_unread") else ""
        last = t["messages"][-1] if t.get("messages") else {}
        last_from = "👤" if last.get("from") == "user" else "👨‍💻" if last else "·"
        buttons.append([InlineKeyboardButton(
            text=f"#{t['id']} {last_from} {nick} · {len(t['messages'])}{unread}",
            callback_data=f"adm_ticket_{t['id']}"
        )])

    buttons.append([InlineKeyboardButton(text="↩️ Назад в меню", callback_data="back_to_admin")])
    text = (
        f"🎫 <b>Тикеты ({len(tickets)})</b>\n\n"
        f"🔴 — есть непрочитанные\n"
        f"👤/👨‍💻 — кто написал последним"
    ) if tickets else "🎫 <b>Тикеты</b>\n\n<i>Пока нет тикетов.</i>"
    await call.message.edit_text(text, parse_mode=ParseMode.HTML,
                                 reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()


@router.callback_query(F.data.regexp(r"^adm_ticket_\d+$"))
async def cb_adm_open_ticket(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)

    ticket_id = int(call.data.split("_")[2])
    ticket = get_ticket(ticket_id)
    if not ticket:
        return await call.answer("Тикет не найден", show_alert=True)

    set_unread(ticket_id, False)
    set_admin_active_ticket(None)
    await state.clear()
    await show_ticket_panel(call.message, ticket, edit=True)
    await call.answer()


@router.callback_query(F.data.startswith("adm_ticket_history_"))
async def cb_adm_ticket_history(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)

    ticket_id = int(call.data.split("_")[3])
    ticket = get_ticket(ticket_id)
    if not ticket:
        return await call.answer("Тикет не найден", show_alert=True)

    history = format_history_compact(ticket, last_n=8)
    await call.message.edit_text(
        f"📜 <b>История тикета #{ticket_id}</b>\n\n"
        f"{history}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ К тикету", callback_data=f"adm_ticket_{ticket_id}")],
            [InlineKeyboardButton(text="🏠 К списку", callback_data="admin_tickets")],
        ])
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm_ticket_media_"))
async def cb_adm_ticket_media(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)

    ticket_id = int(call.data.split("_")[3])
    ticket = get_ticket(ticket_id)
    if not ticket:
        return await call.answer("Тикет не найден", show_alert=True)

    media_messages = [
        m for m in ticket["messages"]
        if m.get("from") == "user"
        and m.get("media")
        and m.get("from_chat_id")
        and m.get("message_id")
    ][-10:]
    if media_messages:
        for m in media_messages:
            try:
                await bot.copy_message(
                    chat_id=ADMIN_ID,
                    from_chat_id=m["from_chat_id"],
                    message_id=m["message_id"]
                )
            except Exception:
                pass  # сообщение могло быть удалено
        await call.answer(f"Отправлено медиа: {len(media_messages)}")
    else:
        await call.answer("В тикете нет медиа", show_alert=True)


@router.callback_query(F.data.startswith("adm_ticket_reply_"))
async def cb_adm_ticket_reply(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)

    ticket_id = int(call.data.split("_")[3])
    ticket = get_ticket(ticket_id)
    if not ticket:
        return await call.answer("Тикет не найден", show_alert=True)

    nick = await _ticket_user_label(call.bot, ticket["user_id"])
    set_admin_active_ticket(ticket_id)
    await state.set_state(TicketAdmin.chatting)
    await state.update_data(ticket_id=ticket_id, ticket_user_id=ticket["user_id"], ticket_nick=nick)
    await call.message.edit_text(
        f"✍️ <b>Ответ в тикет #{ticket_id}</b>\n\n"
        f"Получатель: {escape(nick)} · <code>{ticket['user_id']}</code>\n\n"
        f"Отправьте текст, фото, видео или файл.\n"
        f"{EXIT_HINT}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"adm_ticket_{ticket_id}")]
        ])
    )
    await call.answer()


@router.message(TicketAdmin.chatting)
async def admin_ticket_message(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    target_uid = data.get("ticket_user_id")
    nick = data.get("ticket_nick", "пользователь")

    if not ticket_id or not target_uid:
        await state.clear()
        return

    text = message.text or message.caption
    media = _media_type(message)
    add_message(ticket_id, "admin", text=text, media_type=media)

    try:
        await _forward_to(message, target_uid, "💬 <b>Ответ администратора:</b>")
        await message.answer(f"✅ Доставлено → {nick}{EXIT_HINT}", parse_mode=ParseMode.HTML)
    except Exception as e:
        await message.answer(f"❌ Не удалось доставить: {e}")
