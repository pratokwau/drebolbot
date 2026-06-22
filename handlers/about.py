# handlers/about.py

import os
import json

from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode

from loader import is_authorized
from config import ADMIN_ID
from handlers.utils import no_access_reply, no_access_callback

router = Router()
EXIT_HINT = "\n\n<i>Для выхода введите /cancel</i>"

ABOUT_FILE = "data/about.json"
DEFAULT_TEXT = "ℹ️ <b>О боте</b>\n\nИнформация пока не заполнена."


class AboutStates(StatesGroup):
    waiting_text = State()


def load_about() -> str:
    if not os.path.exists(ABOUT_FILE):
        return DEFAULT_TEXT
    try:
        with open(ABOUT_FILE, encoding="utf-8") as f:
            return json.load(f).get("text", DEFAULT_TEXT)
    except Exception:
        return DEFAULT_TEXT


def save_about(text: str):
    os.makedirs("data", exist_ok=True)
    with open(ABOUT_FILE, "w", encoding="utf-8") as f:
        json.dump({"text": text}, f, ensure_ascii=False, indent=2)


# ====================== HANDLERS ======================

@router.message(Command("about"))
async def cmd_about(message: types.Message):
    if not is_authorized(message.from_user.id):
        return await no_access_reply(message)
    text = load_about()
    try:
        await message.answer(text, parse_mode=ParseMode.HTML)
    except Exception:
        await message.answer(text)


# ====================== ADMIN EDITING ======================

@router.callback_query(F.data == "admin_edit_about")
async def cb_admin_edit_about(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)

    current = load_about()
    await state.set_state(AboutStates.waiting_text)
    await call.message.edit_text(
        f"📝 <b>Редактирование /about</b>\n\n"
        f"<b>Текущий текст:</b>\n{current}\n\n"
        f"Отправьте новый текст.{EXIT_HINT}\n\n"
        f"<b>Поддерживаемые теги:</b>\n"
        f"<code>&lt;b&gt;жирный&lt;/b&gt;</code> → <b>жирный</b>\n"
        f"<code>&lt;i&gt;курсив&lt;/i&gt;</code> → <i>курсив</i>\n"
        f"<code>&lt;u&gt;подчёркнутый&lt;/u&gt;</code> → <u>подчёркнутый</u>\n"
        f"<code>&lt;s&gt;зачёркнутый&lt;/s&gt;</code> → <s>зачёркнутый</s>\n"
        f"<code>&lt;code&gt;моноширинный&lt;/code&gt;</code> → <code>моноширинный</code>\n"
        f"<code>&lt;a href=\"https://site.com\"&gt;ссылка&lt;/a&gt;</code> → <a href=\"https://t.me\">ссылка</a>\n"
        f"<code>&lt;blockquote&gt;цитата&lt;/blockquote&gt;</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_admin")]
        ])
    )
    await call.answer()


@router.message(AboutStates.waiting_text)
async def proc_about_text(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return

    # Проверяем что HTML валиден перед сохранением
    try:
        test = await message.answer(
            "👁 <b>Предпросмотр:</b>\n\n" + message.text,
            parse_mode=ParseMode.HTML
        )
    except Exception:
        return await message.answer(
            "⚠️ <b>Ошибка HTML-разметки!</b>\n\n"
            "Проверьте теги — каждый открывающий тег должен иметь закрывающий.\n"
            "Например: <code>&lt;b&gt;текст&lt;/b&gt;</code>\n\n"
            "Попробуйте ещё раз:",
            parse_mode=ParseMode.HTML
        )

    save_about(message.text)
    await state.clear()
    await message.answer(
        "✅ <b>Текст /about сохранён!</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Редактировать ещё", callback_data="admin_edit_about")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_admin")],
        ])
    )
