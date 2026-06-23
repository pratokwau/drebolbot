# handlers/ai_chat.py

import os
import re
import json
import base64
import secrets
import asyncio
from datetime import datetime
from io import BytesIO

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from groq import Groq

from loader import is_authorized, is_authorized_context
from handlers.utils import no_access_reply, no_access_callback
from handlers.ai_runtime import get_groq_api_key, get_openrouter_api_key

router = Router()
groq_client = None
openrouter_client = None
EXIT_HINT = "\n\n<i>Для выхода введите /cancel</i>"


def _get_groq_client():
    global groq_client
    key = get_groq_api_key()
    if not key:
        return None
    if groq_client is None or getattr(groq_client, "api_key", None) != key:
        groq_client = Groq(api_key=key)
    return groq_client


def _get_openrouter_client():
    global openrouter_client
    key = get_openrouter_api_key()
    if not key:
        return None
    if openrouter_client is None:
        try:
            from openai import OpenAI
            openrouter_client = OpenAI(
                api_key=key,
                base_url="https://openrouter.ai/api/v1",
            )
        except Exception:
            openrouter_client = None
    return openrouter_client


def _ai_not_configured_text() -> str:
    return (
        "🤖 <b>ИИ пока не настроен</b>\n\n"
        "Администратор ещё не указал ключ для AI.\n"
        "Попробуйте позже."
    )


def _ai_is_configured() -> bool:
    return bool(get_groq_api_key() or get_openrouter_api_key())


def clean_response(text: str) -> str:
    """Убирает Markdown, LaTeX и прочее форматирование из ответа AI"""
    # Убираем блоки LaTeX $$ ... $$
    text = re.sub(r'\$\$(.+?)\$\$', lambda m: m.group(1).strip(), text, flags=re.DOTALL)
    # Убираем инлайн LaTeX $ ... $
    text = re.sub(r'\$(.+?)\$', lambda m: m.group(1).strip(), text)
    # Убираем заголовки ## ### #
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Убираем жирный **текст**
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text, flags=re.DOTALL)
    # Убираем курсив *текст*
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    # Убираем LaTeX команды \frac \Rightarrow и т.д.
    text = re.sub(r'\\[a-zA-Z]+', '', text)
    # Убираем оставшиеся обратные слэши
    text = text.replace('\\', '')
    # Убираем лишние пустые строки
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

AI_MODEL = "llama-3.3-70b-versatile"
MAX_HISTORY = 20  # максимум сообщений в истории (10 пар вопрос/ответ)


class AiStates(StatesGroup):
    waiting_input = State()


# ====================== ХРАНИЛИЩЕ ======================

def get_ai_file(user_id: int) -> str:
    path = f"users/{user_id}"
    os.makedirs(path, exist_ok=True)
    return f"{path}/ai_chats.json"


def load_chats(user_id: int) -> dict:
    path = get_ai_file(user_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_chats(user_id: int, chats: dict):
    with open(get_ai_file(user_id), "w", encoding="utf-8") as f:
        json.dump(chats, f, ensure_ascii=False, indent=2)


# ====================== КЛАВИАТУРЫ ======================

def chats_list_kb(chats: dict) -> InlineKeyboardMarkup:
    buttons = []
    sorted_chats = sorted(
        chats.items(),
        key=lambda x: x[1].get("created_at", ""),
        reverse=True
    )
    for chat_id, chat in sorted_chats[:8]:
        name = chat.get("name", "Без названия")
        msg_count = len(chat.get("messages", [])) // 2
        buttons.append([InlineKeyboardButton(
            text=f"💬 {name[:30]} ({msg_count} сообщ.)",
            callback_data=f"ai_open_{chat_id}"
        )])
    buttons.append([
        InlineKeyboardButton(text="➕ Новый чат", callback_data="ai_new"),
        InlineKeyboardButton(text="🗑 Удалить", callback_data="ai_delmode"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def delete_chats_kb(chats: dict) -> InlineKeyboardMarkup:
    buttons = []
    sorted_chats = sorted(
        chats.items(),
        key=lambda x: x[1].get("created_at", ""),
        reverse=True
    )
    for chat_id, chat in sorted_chats[:8]:
        name = chat.get("name", "Без названия")
        buttons.append([InlineKeyboardButton(
            text=f"🗑 {name[:35]}",
            callback_data=f"ai_del_{chat_id}"
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="ai_list")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def chat_actions_kb(chat_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔊 Озвучить", callback_data=f"ai_tts_{chat_id}")],
        [InlineKeyboardButton(text="📋 К списку чатов", callback_data="ai_list")],
    ])


# ====================== ХЕНДЛЕРЫ ======================

@router.message(Command("ai"))
async def cmd_ai(message: types.Message, state: FSMContext):
    if not is_authorized_context(message.from_user.id, message.chat.id):
        await no_access_reply(message)
        return

    if not _ai_is_configured():
        await message.answer(_ai_not_configured_text(), parse_mode=ParseMode.HTML)
        return

    await state.clear()

    # В группе — сразу новый чат, без показа личных чатов
    if message.chat.type in ("group", "supergroup"):
        await _start_new_chat(message, state, message.from_user.id, is_call=False)
        return

    chats = load_chats(message.from_user.id)
    if not chats:
        await _start_new_chat(message, state, message.from_user.id, is_call=False)
        return

    await message.answer(
        "🤖 <b>AI Ассистент</b>\n\nВыберите чат или создайте новый:",
        parse_mode=ParseMode.HTML,
        reply_markup=chats_list_kb(chats)
    )


async def _start_new_chat(source, state: FSMContext, user_id: int, is_call: bool):
    if not _ai_is_configured():
        text = _ai_not_configured_text()
        if is_call:
            await source.message.edit_text(text, parse_mode=ParseMode.HTML)
            await source.answer()
        else:
            await source.answer(text, parse_mode=ParseMode.HTML)
        return

    chat_id = secrets.token_hex(4)
    await state.update_data(current_chat_id=chat_id, is_new_chat=True, chat_owner_id=user_id)
    await state.set_state(AiStates.waiting_input)

    text = (
        "🤖 <b>Новый чат с AI</b>\n\n"
        "Задайте любой вопрос." + EXIT_HINT
    )

    if is_call:
        await source.message.edit_text(text, parse_mode=ParseMode.HTML)
        await source.answer()
    else:
        await source.answer(text, parse_mode=ParseMode.HTML)


@router.callback_query(
    F.data.startswith("ai_")
    & ~F.data.startswith("ai_set_")
    & ~F.data.startswith("ai_settings_")
)
async def cb_ai(call: types.CallbackQuery, state: FSMContext):
    if not is_authorized_context(call.from_user.id, call.message.chat.id):
        await no_access_callback(call)
        return

    # В группе — кнопки доступны только тому кто их вызвал
    if call.message.chat.type in ("group", "supergroup"):
        state_data = await state.get_data()
        owner_id = state_data.get("chat_owner_id")
        if owner_id and call.from_user.id != owner_id:
            return await call.answer("❌ Это не ваш чат", show_alert=True)

    data = call.data
    user_id = call.from_user.id
    chats = load_chats(user_id)

    # --- Новый чат ---
    if data == "ai_new":
        await _start_new_chat(call, state, user_id, is_call=True)

    # --- Режим удаления ---
    elif data == "ai_delmode":
        if not chats:
            return await call.answer("Нет чатов для удаления", show_alert=True)
        await call.message.edit_text(
            "🗑 <b>Удалить чат</b>\n\nВыберите чат:",
            parse_mode=ParseMode.HTML,
            reply_markup=delete_chats_kb(chats)
        )
        await call.answer()

    # --- К списку ---
    elif data == "ai_list":
        await state.clear()
        if not chats:
            await call.message.edit_text(
                "🤖 <b>AI Ассистент</b>\n\nЧатов пока нет. Начните первый!",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="➕ Новый чат", callback_data="ai_new")]
                ])
            )
        else:
            await call.message.edit_text(
                "🤖 <b>AI Ассистент</b>\n\nВыберите чат или создайте новый:",
                parse_mode=ParseMode.HTML,
                reply_markup=chats_list_kb(chats)
            )
        await call.answer()

    # --- Открыть чат ---
    elif data.startswith("ai_open_"):
        chat_id = data[len("ai_open_"):]
        if chat_id not in chats:
            return await call.answer("Чат не найден", show_alert=True)

        chat = chats[chat_id]
        messages = chat.get("messages", [])

        # Превью последних сообщений
        preview = ""
        if messages:
            last = messages[-4:] if len(messages) >= 4 else messages
            for m in last:
                role_icon = "👤" if m["role"] == "user" else "🤖"
                content = m["content"]
                short = (content[:120] + "...") if len(content) > 120 else content
                preview += f"{role_icon} <i>{short}</i>\n\n"

        await state.update_data(current_chat_id=chat_id, is_new_chat=False)
        await state.set_state(AiStates.waiting_input)

        await call.message.edit_text(
            f"💬 <b>{chat.get('name', 'Чат')}</b>\n\n"
            f"{preview}"
            f"Продолжайте общение.{EXIT_HINT}",
            parse_mode=ParseMode.HTML,
            reply_markup=chat_actions_kb(chat_id)
        )
        await call.answer()

    # --- Озвучить ответ ---
    elif data.startswith("ai_tts_"):
        chat_id = data[len("ai_tts_"):]
        if chat_id not in chats:
            return await call.answer("Чат не найден", show_alert=True)
        messages = chats[chat_id].get("messages", [])
        last_ai = next((m["content"] for m in reversed(messages) if m["role"] == "assistant"), None)
        if not last_ai:
            return await call.answer("Нет ответа для озвучки", show_alert=True)
        await call.answer("🔊 Генерирую аудио...")
        try:
            import edge_tts
            import asyncio
            from io import BytesIO
            from aiogram.types import BufferedInputFile
            from handlers.settings import get_user_settings

            s = get_user_settings(call.from_user.id)
            voice = s.get("tts_voice", "ru-RU-SvetlanaNeural")

            buf = BytesIO()
            communicate = edge_tts.Communicate(last_ai[:3000], voice)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            buf.seek(0)

            await call.message.answer_voice(
                BufferedInputFile(buf.read(), filename="voice.mp3")
            )
        except Exception as e:
            await call.message.answer(f"❌ Ошибка озвучки: <code>{e}</code>", parse_mode=ParseMode.HTML)

    # --- Удалить чат ---
    elif data.startswith("ai_del_"):
        chat_id = data[len("ai_del_"):]
        if chat_id in chats:
            name = chats[chat_id].get("name", "чат")
            del chats[chat_id]
            save_chats(user_id, chats)
            await call.answer(f"🗑 «{name[:20]}» удалён")
        else:
            await call.answer("Чат не найден", show_alert=True)

        await state.clear()
        if not chats:
            await call.message.edit_text(
                "🤖 <b>AI Ассистент</b>\n\nЧатов нет. Начните новый!",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="➕ Новый чат", callback_data="ai_new")]
                ])
            )
        else:
            await call.message.edit_text(
                "🗑 <b>Удалить чат</b>\n\nВыберите чат:",
                parse_mode=ParseMode.HTML,
                reply_markup=delete_chats_kb(chats)
            )


# ====================== ОБРАБОТКА СООБЩЕНИЙ ======================

_VOICE_TRIGGERS = (
    "гс", "голосов", "озвуч", "скажи", "запиши гс", "отправь гс",
    "ответь голос", "войс", "voice"
)

def _wants_voice(text: str) -> bool:
    t = text.lower()
    return any(trigger in t for trigger in _VOICE_TRIGGERS)


async def _send_as_voice(message: types.Message, text: str):
    try:
        import edge_tts
        from aiogram.types import BufferedInputFile
        from io import BytesIO
        from handlers.settings import get_user_settings
        s = get_user_settings(message.from_user.id)
        voice = s.get("tts_voice", "ru-RU-SvetlanaNeural")
        buf = BytesIO()
        communicate = edge_tts.Communicate(text[:3000], voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        buf.seek(0)
        await message.answer_voice(BufferedInputFile(buf.read(), filename="voice.mp3"))
    except Exception as e:
        await message.answer(f"🤖 {text}{EXIT_HINT}")


async def _process_ai_message(message: types.Message, state: FSMContext, user_text: str):
    """Общая логика обработки запроса к AI (текст или голос)"""
    if not _ai_is_configured():
        await state.clear()
        await message.answer(_ai_not_configured_text(), parse_mode=ParseMode.HTML)
        return

    user_id = message.from_user.id

    state_data = await state.get_data()
    chat_id = state_data.get("current_chat_id")
    is_new_chat = state_data.get("is_new_chat", True)

    if not chat_id:
        return await message.answer("❌ Ошибка. Введите /ai заново.")

    chats = load_chats(user_id)

    # Создаём чат если новый
    if chat_id not in chats:
        chat_name = (user_text[:35] + "...") if len(user_text) > 35 else user_text
        chats[chat_id] = {
            "name": chat_name,
            "created_at": datetime.now().isoformat(),
            "messages": []
        }

    chat = chats[chat_id]
    messages = chat.get("messages", [])

    messages.append({"role": "user", "content": user_text})

    # Обрезаем историю
    if len(messages) > MAX_HISTORY:
        messages = messages[-MAX_HISTORY:]

    # Сохраняем сразу — чтобы чат не пропал даже если AI не ответит
    chat["messages"] = messages
    chats[chat_id] = chat
    save_chats(user_id, chats)

    if is_new_chat:
        await state.update_data(is_new_chat=False)

    thinking_msg = await message.answer("🤖 <i>Думаю...</i>", parse_mode=ParseMode.HTML)

    providers = [
        ("groq", AI_MODEL),
        ("openrouter", "meta-llama/llama-3.3-70b-instruct:free"),
        ("openrouter", "qwen/qwen3-next-80b-a3b-instruct:free"),
        ("openrouter", "openai/gpt-oss-120b:free"),
    ]

    system_prompt = (
        "Ты полезный ассистент. Отвечай чётко и по делу. "
        "Если вопрос про бизнес или торговлю — давай конкретные советы. "
        "ВАЖНО: не используй Markdown, LaTeX, звёздочки, решётки, знаки доллара и любое другое форматирование. "
        "Пиши только обычным текстом. Формулы пиши словами или простыми символами."
    )

    last_error = None
    response_text = None

    async def _run_completion(model_client, model_name, payload):
        return await asyncio.wait_for(
            asyncio.to_thread(
                lambda: model_client.chat.completions.create(
                    model=model_name,
                    messages=payload,
                    max_tokens=1024,
                    temperature=0.7,
                )
            ),
            timeout=45,
        )

    for provider, model_name in providers:
        try:
            if provider == "groq":
                model_client = _get_groq_client()
            else:
                model_client = _get_openrouter_client()

            if model_client is None:
                continue

            response = await _run_completion(
                model_client,
                model_name,
                [
                    {"role": "system", "content": system_prompt},
                ] + messages,
            )
            response_text = response.choices[0].message.content if response.choices else None
            if response_text:
                break
        except Exception as e:
            last_error = e
            continue

    await thinking_msg.delete()

    if not response_text:
        if last_error:
            await message.answer(f"❌ Ошибка AI: <code>{last_error}</code>", parse_mode=ParseMode.HTML)
        else:
            await message.answer("❌ Ошибка AI: не удалось получить ответ.")
        return

    ai_reply = clean_response(response_text)
    messages.append({"role": "assistant", "content": ai_reply})

    chat["messages"] = messages
    chats[chat_id] = chat
    save_chats(user_id, chats)

    if _wants_voice(user_text):
        await _send_as_voice(message, ai_reply)
    else:
        await message.answer(
            f"🤖 {ai_reply}{EXIT_HINT}",
            reply_markup=chat_actions_kb(chat_id)
        )


@router.message(AiStates.waiting_input, F.text)
async def proc_ai_input(message: types.Message, state: FSMContext):
    if not is_authorized_context(message.from_user.id, message.chat.id):
        return await state.clear()
    if not _ai_is_configured():
        await state.clear()
        return await message.answer(_ai_not_configured_text(), parse_mode=ParseMode.HTML)
    await _process_ai_message(message, state, message.text.strip())


@router.message(AiStates.waiting_input, F.voice)
async def proc_ai_voice(message: types.Message, state: FSMContext):
    if not is_authorized_context(message.from_user.id, message.chat.id):
        return await state.clear()
    if not _ai_is_configured():
        await state.clear()
        return await message.answer(_ai_not_configured_text(), parse_mode=ParseMode.HTML)

    thinking_msg = await message.answer("🎙 <i>Распознаю голосовое...</i>", parse_mode=ParseMode.HTML)
    try:
        buf = BytesIO()
        await message.bot.download(message.voice, buf)
        buf.seek(0)

        client = _get_groq_client()
        if client is None:
            await thinking_msg.delete()
            return await message.answer(_ai_not_configured_text(), parse_mode=ParseMode.HTML)

        transcription = await asyncio.wait_for(
            asyncio.to_thread(
                lambda: client.audio.transcriptions.create(
                    file=("voice.ogg", buf),
                    model="whisper-large-v3-turbo",
                    language="ru"
                )
            ),
            timeout=45,
        )
        user_text = transcription.text.strip()

        await thinking_msg.delete()

        if not user_text:
            return await message.answer("⚠️ Не удалось распознать голосовое.")

        # Показываем что распознали
        await message.answer(f"🎙 <i>Распознано: {user_text}</i>", parse_mode=ParseMode.HTML)
        await _process_ai_message(message, state, user_text)

    except Exception as e:
        await thinking_msg.delete()
        await message.answer(f"❌ Ошибка распознавания: <code>{e}</code>", parse_mode=ParseMode.HTML)


@router.message(AiStates.waiting_input, F.photo)
async def proc_ai_photo(message: types.Message, state: FSMContext):
    if not is_authorized_context(message.from_user.id, message.chat.id):
        return await state.clear()
    if not _ai_is_configured():
        await state.clear()
        return await message.answer(_ai_not_configured_text(), parse_mode=ParseMode.HTML)

    thinking_msg = await message.answer("🖼 <i>Анализирую фото...</i>", parse_mode=ParseMode.HTML)
    try:
        photo = message.photo[-1]
        file_info = await message.bot.get_file(photo.file_id)
        buf = BytesIO()
        await message.bot.download_file(file_info.file_path, buf)
        image_data = base64.b64encode(buf.getvalue()).decode("utf-8")

        # Текст от пользователя (caption к фото или дефолтный вопрос)
        user_question = message.caption.strip() if message.caption else "Что изображено на фото? Опиши подробно."

        client = _get_groq_client()
        if client is None:
            await thinking_msg.delete()
            return await message.answer(_ai_not_configured_text(), parse_mode=ParseMode.HTML)

        response = await asyncio.wait_for(
            asyncio.to_thread(
                lambda: client.chat.completions.create(
                    model="meta-llama/llama-4-scout-17b-16e-instruct",
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
                            {"type": "text", "text": user_question}
                        ]
                    }],
                    max_tokens=1024
                )
            ),
            timeout=45,
        )

        ai_reply = clean_response(response.choices[0].message.content)

        await thinking_msg.delete()

        state_data = await state.get_data()
        chat_id = state_data.get("current_chat_id")

        await message.answer(
            f"🤖 {ai_reply}{EXIT_HINT}",
            reply_markup=chat_actions_kb(chat_id) if chat_id else None
        )

    except Exception as e:
        await thinking_msg.delete()
        await message.answer(f"❌ Ошибка анализа фото: <code>{e}</code>", parse_mode=ParseMode.HTML)
