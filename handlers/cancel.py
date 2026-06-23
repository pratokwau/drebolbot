# handlers/cancel.py

from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

router = Router()


@router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("❌ <b>Нет активного действия для отмены.</b>")
        return

    # Если админ выходит из тикета — снимаем active_ticket
    from config import ADMIN_ID
    if message.from_user.id == ADMIN_ID and current_state and "TicketAdmin" in current_state:
        from handlers.tickets import set_admin_active_ticket
        set_admin_active_ticket(None)

    if current_state and current_state.startswith("AiSettings:"):
        await state.clear()
        from handlers.ai_settings import _ai_settings_text, ai_settings_menu_kb
        await message.answer(
            _ai_settings_text(),
            parse_mode="HTML",
            reply_markup=ai_settings_menu_kb(),
        )
        return

    await state.clear()
    await message.answer("❌ <b>Действие отменено.</b>")
