# handlers/start.py
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.enums import ParseMode

from loader import is_authorized
from config import ADMIN_ID
from handlers.utils import no_access_reply
from handlers.xui import get_vpn_user

router = Router()


@router.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    has_vpn = bool(get_vpn_user(user_id))

    if not is_authorized(user_id):
        if has_vpn:
            # Пользователь без доступа к боту, но с привязанным VPN
            await message.answer(
                f"🔐 <b>drebol</b> 🪼\n\n"
                f"У вас подключён VPN-сервис.\n\n"
                f"📱 <b>Доступные команды:</b>\n"
                f"• /myvpn — Управление вашим VPN\n"
                f"• /status — Просмотр работы бота и VPN\n"
                f"• /settings — Настройки уведомлений\n"
                f"• /help — Связаться с администратором\n"
                f"• /cancel — Для выхода из текущего действия\n\n"
                f"<i>Если нужен доступ к остальным функциям бота — обратитесь к админу.\n"
                f"Ваш ID: <code>{user_id}</code></i>",
                parse_mode=ParseMode.HTML
            )
            return
        await no_access_reply(message)
        return

    is_admin = user_id == ADMIN_ID

    user_commands = (
        "<b>drebol</b> 🪼 приветствует вас!\n\n"
        "🛒 <b>FunPay / PlayerOK:</b>\n"
        "• /rassstart — Запуск расчёта\n"
        "• /playerokrass — Запуск PlayerOK расчёта\n"
        "• /saveprofit — Запись чистой прибыли\n\n"
        "🤖 <b>ИИ:</b>\n"
        "• /ai — AI-ассистент\n\n"
        "⚙️ <b>Прочее:</b>\n"
        "• /start — Главное меню\n"
        "• /about — О боте\n"
        "• /status — Просмотр работы бота и VPN\n"
        "• /help — Связаться с администратором\n"
        "• /settings — Настройки уведомлений\n"
        "• /cancel — Для выхода из текущего действия"
    )

    vpn_commands = ""
    if has_vpn:
        vpn_commands = "\n\n🔐 <b>VPN:</b>\n• /myvpn — Управление вашим VPN"

    admin_commands = (
        "\n\n👨🏼‍💻 <b>Команды администратора:</b>\n"
        "• /admin — Админ-панель\n"
        "• /funpayauto — FunPay Auto\n"
        "• /xui — 3X-UI панель"
    )

    text_welcome = user_commands + vpn_commands + (admin_commands if is_admin else "")

    await message.answer(
        text_welcome,
        parse_mode=ParseMode.HTML
    )
