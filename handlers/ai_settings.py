import json
import os

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMIN_ID
from handlers.utils import no_access_reply, no_access_callback

router = Router()
SETTINGS_FILE = "data/ai_settings.json"
EXIT_HINT = "\n\n<i>Для выхода введите /cancel</i>"


class AiSettings(StatesGroup):
    waiting_groq = State()
    waiting_openrouter = State()


def ai_settings_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="GROQ_API_KEY", callback_data="ai_set_groq")],
        [InlineKeyboardButton(text="OPENROUTER_API_KEY", callback_data="ai_set_openrouter")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_admin")],
    ])


def ai_settings_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="ai_settings_back"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="ai_settings_cancel"),
        ]
    ])


def load_ai_settings() -> dict:
    if not os.path.exists(SETTINGS_FILE):
        return {"GROQ_API_KEY": "", "OPENROUTER_API_KEY": ""}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "GROQ_API_KEY": data.get("GROQ_API_KEY", ""),
            "OPENROUTER_API_KEY": data.get("OPENROUTER_API_KEY", ""),
        }
    except Exception:
        return {"GROQ_API_KEY": "", "OPENROUTER_API_KEY": ""}


def save_ai_settings(groq_key: str, openrouter_key: str) -> None:
    os.makedirs("data", exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "GROQ_API_KEY": groq_key.strip(),
                "OPENROUTER_API_KEY": openrouter_key.strip(),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )


def _ai_settings_text() -> str:
    data = load_ai_settings()
    return (
        "🤖 <b>AI ключи</b>\n\n"
        f"GROQ: <code>{'set' if data['GROQ_API_KEY'] else 'empty'}</code>\n"
        f"OpenRouter: <code>{'set' if data['OPENROUTER_API_KEY'] else 'empty'}</code>\n\n"
        "Выберите, какой ключ настроить."
    )


def _key_prompt_text(key_name: str, current_value: str) -> str:
    return (
        f"🔐 <b>{key_name}</b>\n\n"
        f"Текущий статус: <code>{'set' if current_value else 'empty'}</code>\n\n"
        f"Введите новый {key_name}.{EXIT_HINT}"
    )


@router.message(Command("aisettings"))
async def cmd_ai_settings(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await no_access_reply(message)

    await state.clear()
    await message.answer(_ai_settings_text(), parse_mode=ParseMode.HTML, reply_markup=ai_settings_menu_kb())


@router.callback_query(F.data == "admin_ai_settings")
async def cb_ai_settings(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    await state.clear()
    await call.message.edit_text(
        _ai_settings_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=ai_settings_menu_kb(),
    )
    await call.answer()


@router.callback_query(F.data == "ai_set_groq")
async def cb_ai_set_groq(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    data = load_ai_settings()
    await state.set_state(AiSettings.waiting_groq)
    await call.message.edit_text(
        _key_prompt_text("GROQ_API_KEY", data.get("GROQ_API_KEY", "")),
        parse_mode=ParseMode.HTML,
        reply_markup=ai_settings_back_kb(),
    )
    await call.answer()


@router.callback_query(F.data == "ai_set_openrouter")
async def cb_ai_set_openrouter(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    data = load_ai_settings()
    await state.set_state(AiSettings.waiting_openrouter)
    await call.message.edit_text(
        _key_prompt_text("OPENROUTER_API_KEY", data.get("OPENROUTER_API_KEY", "")),
        parse_mode=ParseMode.HTML,
        reply_markup=ai_settings_back_kb(),
    )
    await call.answer()


@router.callback_query(F.data == "ai_settings_back")
async def cb_ai_settings_back(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    await state.clear()
    await call.message.edit_text(
        _ai_settings_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=ai_settings_menu_kb(),
    )
    await call.answer()


@router.callback_query(F.data == "ai_settings_cancel")
async def cb_ai_settings_cancel(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    await state.clear()
    await call.message.edit_text(
        _ai_settings_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=ai_settings_menu_kb(),
    )
    await call.answer("Действие отменено")


@router.message(AiSettings.waiting_groq, F.text)
async def ai_settings_groq(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await no_access_reply(message)
    text = message.text.strip()
    if text == "/cancel":
        await state.clear()
        await message.answer(_ai_settings_text(), parse_mode=ParseMode.HTML, reply_markup=ai_settings_menu_kb())
        return
    if text in {"⬅️ Назад", "назад", "back"}:
        await state.clear()
        await message.answer(_ai_settings_text(), parse_mode=ParseMode.HTML, reply_markup=ai_settings_menu_kb())
        return
    current = load_ai_settings()
    save_ai_settings(text, current.get("OPENROUTER_API_KEY", ""))
    await state.clear()
    data = load_ai_settings()
    await message.answer(
        "✅ GROQ_API_KEY сохранён.",
        parse_mode=ParseMode.HTML,
        reply_markup=ai_settings_menu_kb(),
    )


@router.message(AiSettings.waiting_openrouter, F.text)
async def ai_settings_openrouter(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await no_access_reply(message)
    text = message.text.strip()
    if text == "/cancel":
        await state.clear()
        await message.answer(_ai_settings_text(), parse_mode=ParseMode.HTML, reply_markup=ai_settings_menu_kb())
        return
    if text in {"⬅️ Назад", "назад", "back"}:
        await state.clear()
        await message.answer(_ai_settings_text(), parse_mode=ParseMode.HTML, reply_markup=ai_settings_menu_kb())
        return
    current = load_ai_settings()
    save_ai_settings(current.get("GROQ_API_KEY", ""), text)
    await state.clear()
    await message.answer(
        "✅ OPENROUTER_API_KEY сохранён.",
        parse_mode=ParseMode.HTML,
        reply_markup=ai_settings_menu_kb(),
    )
