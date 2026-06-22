import json
import os

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from aiogram.enums import ParseMode

from config import ADMIN_ID
from handlers.utils import no_access_reply, no_access_callback

router = Router()
SETTINGS_FILE = "data/ai_settings.json"


class AiSettings(StatesGroup):
    waiting_groq = State()
    waiting_openrouter = State()


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


@router.message(Command("aisettings"))
async def cmd_ai_settings(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await no_access_reply(message)

    data = load_ai_settings()
    text = (
        "🤖 <b>AI настройки</b>\n\n"
        f"GROQ: <code>{'set' if data['GROQ_API_KEY'] else 'empty'}</code>\n"
        f"OpenRouter: <code>{'set' if data['OPENROUTER_API_KEY'] else 'empty'}</code>\n\n"
        "Отправьте новый GROQ_API_KEY."
    )
    await state.set_state(AiSettings.waiting_groq)
    await message.answer(text, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "admin_ai_settings")
async def cb_ai_settings(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    await state.set_state(AiSettings.waiting_groq)
    data = load_ai_settings()
    await call.message.edit_text(
        "🤖 <b>AI настройки</b>\n\n"
        f"GROQ: <code>{'set' if data['GROQ_API_KEY'] else 'empty'}</code>\n"
        f"OpenRouter: <code>{'set' if data['OPENROUTER_API_KEY'] else 'empty'}</code>\n\n"
        "Отправьте новый GROQ_API_KEY.",
        parse_mode=ParseMode.HTML,
    )
    await call.answer()


@router.message(AiSettings.waiting_groq, F.text)
async def ai_settings_groq(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await no_access_reply(message)
    groq_key = message.text.strip()
    await state.update_data(groq_key=groq_key)
    await state.set_state(AiSettings.waiting_openrouter)
    await message.answer("Теперь отправьте OPENROUTER_API_KEY.")


@router.message(AiSettings.waiting_openrouter, F.text)
async def ai_settings_openrouter(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await no_access_reply(message)
    data = await state.get_data()
    groq_key = data.get("groq_key", "")
    openrouter_key = message.text.strip()
    save_ai_settings(groq_key, openrouter_key)
    await state.clear()
    await message.answer("✅ AI ключи сохранены.", parse_mode=ParseMode.HTML)
