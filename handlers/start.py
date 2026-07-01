# handlers/start.py

from aiogram import Router, types
from aiogram.filters import Command
from aiogram.enums import ParseMode

from loader import is_authorized
from config import ADMIN_ID
from handlers.utils import no_access_reply

router = Router()


@router.message(Command("start"))
async def cmd_start(message: types.Message):
    if not is_authorized(message.from_user.id):
        await no_access_reply(message)
        return

    user_commands = (
        "<b>🪼 Drebol Bot</b>\n\n"
        "📋 <b>Доступные команды:</b>\n"
        "• /rassstart — Расчет прибыли FunPay\n"
        "• /playerokrass — Расчет прибыли PlayerOK\n"
        "• /saveprofit — Сохранение прибыли\n"
        "• /ai — AI-помощник\n"
        "• /settings — Настройки уведомлений\n"
        "• /status — Статус бота\n"
        "• /cancel — Отменить текущее действие"
    )

    if message.from_user.id == ADMIN_ID:
        admin_commands = (
            "\n\n👑 <b>Команды администратора:</b>\n"
            "• /admin — Панель администратора\n"
            "• /funpayauto — Автообновление FunPay\n"
        )
        text = user_commands + admin_commands
    else:
        text = user_commands

    await message.answer(text, parse_mode=ParseMode.HTML)
