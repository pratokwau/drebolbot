from __future__ import annotations

from aiogram import F, Router, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from handlers.admin import render_admin_menu
from handlers.tickets import open_support_flow
from sub.adminpaysub.paid_storage import has_paid_subscription
from sub.adminsub.storage import get_vpn_user, user_settings_ready
from sub.utils import is_admin
from sub.vpn import _render_vpn
from sub.adminsub.admin import render_inbounds
from sub.adminpaysub.paid_subscriptions import render_paid_subscriptions, render_paid_user_menu


router = Router()


def _start_kb(*, has_admin_sub: bool, has_paid_sub: bool, is_admin_user: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_admin_sub:
        rows.append([InlineKeyboardButton(text="🔐 Мой VPN", callback_data="start_vpn")])
    rows.append([InlineKeyboardButton(text="💳 Моя подписка", callback_data="start_sub")])
    rows.append([InlineKeyboardButton(text="🆘 Поддержка", callback_data="start_support")])
    if is_admin_user:
        rows.append([
            InlineKeyboardButton(text="⚙️ Админка", callback_data="start_admin"),
            InlineKeyboardButton(text="📡 Админская подписка", callback_data="start_adminsub"),
        ])
        rows.append([
            InlineKeyboardButton(text="💳 Платные подписки", callback_data="start_adminpaysub"),
            InlineKeyboardButton(text="🎫 Тикеты", callback_data="start_tickets"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_start(
    message: types.Message,
    *,
    user_id: int | None = None,
    edit: bool = False,
) -> None:
    user_id = int(user_id or message.from_user.id)
    vpn_user = get_vpn_user(user_id)
    subscription_type = str(vpn_user.get("subscription_type", "")).lower() if vpn_user else ""
    has_admin_sub = bool(
        vpn_user
        and subscription_type != "paid"
        and not vpn_user.get("admin_disabled")
    )
    has_paid_sub = has_paid_subscription(user_id)
    is_admin_user = is_admin(user_id)
    text = (
        "👋 <b>Добро пожаловать в Drebol VPN</b>\n\n"
        "Здесь можно открыть свой VPN или посмотреть подписку.\n\n"
        "Выберите действие кнопкой ниже:"
    )
    markup = _start_kb(
        has_admin_sub=has_admin_sub,
        has_paid_sub=has_paid_sub,
        is_admin_user=is_admin_user,
    )
    if edit:
        await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=markup)


@router.message(Command("start"))
async def cmd_start(message: types.Message):
    await _render_start(message)


@router.callback_query(F.data == "start_vpn")
async def cb_start_vpn(call: types.CallbackQuery):
    user_data = get_vpn_user(call.from_user.id)
    subscription_type = str(user_data.get("subscription_type", "")).lower() if user_data else ""
    if (
        not user_data
        or subscription_type == "paid"
    ):
        return await call.answer("⛔ У вас пока нет доступа к VPN", show_alert=True)
    if user_data.get("admin_disabled"):
        return await call.answer("⛔ Доступ временно отключён", show_alert=True)
    await call.answer()
    await _render_vpn(call.message, user_data)


@router.callback_query(F.data == "start_sub")
async def cb_start_sub(call: types.CallbackQuery):
    await call.answer()
    await render_paid_user_menu(
        call.message,
        user_id=call.from_user.id,
        username=call.from_user.username or "",
        first_name=call.from_user.first_name or "",
        last_name=call.from_user.last_name or "",
        edit=True,
    )


@router.callback_query(F.data == "start_support")
async def cb_start_support(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    await open_support_flow(call, state, edit=True)


@router.callback_query(F.data == "start_admin")
async def cb_start_admin(call: types.CallbackQuery):
    await call.answer()
    await render_admin_menu(call.message, call.from_user.id, edit=True)


@router.callback_query(F.data == "start_adminsub")
async def cb_start_adminsub(call: types.CallbackQuery):
    await call.answer()
    await render_inbounds(call.message, show_settings=False)


@router.callback_query(F.data == "start_adminpaysub")
async def cb_start_adminpaysub(call: types.CallbackQuery):
    await call.answer()
    await render_paid_subscriptions(call.message)


@router.callback_query(F.data == "start_tickets")
async def cb_start_tickets(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    from handlers.tickets import cb_admin_tickets

    await cb_admin_tickets(call, state)


@router.callback_query(F.data == "paiduser_to_main")
async def cb_paiduser_to_main(call: types.CallbackQuery):
    await call.answer()
    await _render_start(call.message, user_id=call.from_user.id, edit=True)
