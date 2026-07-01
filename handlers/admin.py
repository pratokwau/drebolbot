# handlers/admin.py

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.enums import ParseMode

from handlers.utils import no_access_reply, no_access_callback
from config import ADMIN_ID
from update_manager import (
    get_update_status,
    update_from_git,
    restart_service,
    save_admin_chat_id,
    save_restart_notice,
)

router = Router()


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def admin_menu() -> InlineKeyboardMarkup:
    update_label, _ = get_update_status()
    update_text = f"🔄 Обновиться ({update_label})"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=update_text, callback_data="admin_update")
            ],
            [
                InlineKeyboardButton(text="🤖 AI ключи", callback_data="admin_ai_settings")
            ],
            [
                InlineKeyboardButton(text="🚀 Миграция на новый сервер", callback_data="admin_migrate")
            ],
        ]
    )


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


@router.callback_query(F.data == "back_to_admin")
async def cb_back_to_admin(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await no_access_callback(callback)
        return

    await state.clear()
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
        save_admin_chat_id(callback.message.chat.id)
        save_restart_notice(
            callback.message.chat.id,
            "✅ <b>Бот успешно перезапустился.</b>\n\nОбновление применено успешно."
        )
        restart_service()
        await callback.message.edit_text(
            "✅ <b>Обновление установлено</b>\n\n"
            "Сервис перезапускается, это может занять до минуты.\n"
            "После старта я пришлю подтверждение отдельным сообщением.",
            parse_mode=ParseMode.HTML,
        )
    finally:
        await callback.answer("Обновление выполнено", show_alert=False)


def back_to_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад в меню", callback_data="back_to_admin")]
        ]
    )
