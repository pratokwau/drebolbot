# handlers/playerokrass.py

import os
import json

from aiogram import Router, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from handlers.utils import no_access_reply

from loader import is_authorized
from states.playerokrass_states import PlayerOkStates

router = Router()

PLAYEROK_FILE = "data/playerok_commissions.json"
os.makedirs(os.path.dirname(PLAYEROK_FILE), exist_ok=True)

EXIT_HINT = "\n<i>Для выхода введите /cancel</i>"
DIVIDER = "──────────────────"


def load_commissions(key: str):
    try:
        with open(PLAYEROK_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get(key, [])
    except Exception:
        return []


def save_commission(key: str, value: float):
    value = round(value, 2)
    data = {}
    try:
        with open(PLAYEROK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        pass
    lst = data.get(key, [])
    if value in lst:
        lst.remove(value)
    lst.insert(0, value)
    data[key] = lst[:5]
    with open(PLAYEROK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def commission_keyboard(key: str, next_step: str) -> InlineKeyboardMarkup:
    lst = load_commissions(key)
    buttons = [InlineKeyboardButton(text=f"{v}%", callback_data=f"{next_step}_{v}") for v in lst]
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="playerok_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_only_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="playerok_cancel")]
    ])


@router.message(Command("playerokrass"))
async def cmd_playerokrass(message: types.Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        return await no_access_reply(message)

    await state.set_state(PlayerOkStates.waiting_sale_commission)
    await message.answer(
        "🧮 <b>Расчёт PlayerOK</b>\n\n"
        "💳 Комиссия на продажу (%):"
        f"{EXIT_HINT}",
        parse_mode=ParseMode.HTML,
        reply_markup=commission_keyboard("sale", "sale_commission")
    )


@router.callback_query(F.data == "playerok_cancel")
async def cb_playerok_cancel(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("❌ <b>Расчёт отменён.</b>", parse_mode=ParseMode.HTML)
    await call.answer()


# SALE COMMISSION — кнопкой
@router.callback_query(F.data.startswith("sale_commission_"))
async def cb_sale_commission(call: types.CallbackQuery, state: FSMContext):
    try:
        sale_val = float(call.data.split("_")[-1])
        if sale_val <= 0:
            raise ValueError
    except Exception:
        return await call.answer("⚠️ Некорректное значение", show_alert=True)

    save_commission("sale", sale_val)
    await state.update_data(sale_commission=sale_val)
    await state.set_state(PlayerOkStates.waiting_withdraw_commission)
    await call.message.edit_text(
        f"✅ Комиссия продажи: <b>{sale_val:.2f}%</b>\n\n"
        "💳 Комиссия на вывод (%):"
        f"{EXIT_HINT}",
        parse_mode=ParseMode.HTML,
        reply_markup=commission_keyboard("withdraw", "withdraw_commission")
    )
    await call.answer()


# SALE COMMISSION — вручную
@router.message(StateFilter(PlayerOkStates.waiting_sale_commission))
async def text_sale_commission(message: types.Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        await state.clear()
        return await no_access_reply(message)

    try:
        sale_val = float(message.text.replace(",", "."))
        if sale_val <= 0:
            raise ValueError
    except Exception:
        return await message.answer(
            "⚠️ Введите корректный процент\nПример: <code>10</code> или <code>7.5</code>",
            parse_mode=ParseMode.HTML
        )

    save_commission("sale", sale_val)
    await state.update_data(sale_commission=sale_val)
    await state.set_state(PlayerOkStates.waiting_withdraw_commission)
    await message.answer(
        f"✅ Комиссия продажи: <b>{sale_val:.2f}%</b>\n\n"
        "💳 Комиссия на вывод (%):"
        f"{EXIT_HINT}",
        parse_mode=ParseMode.HTML,
        reply_markup=commission_keyboard("withdraw", "withdraw_commission")
    )


# WITHDRAW COMMISSION — кнопкой
@router.callback_query(F.data.startswith("withdraw_commission_"))
async def cb_withdraw_commission(call: types.CallbackQuery, state: FSMContext):
    try:
        withdraw_val = float(call.data.split("_")[-1])
        if withdraw_val <= 0:
            raise ValueError
    except Exception:
        return await call.answer("⚠️ Некорректное значение", show_alert=True)

    save_commission("withdraw", withdraw_val)
    await state.update_data(withdraw_commission=withdraw_val)
    await state.set_state(PlayerOkStates.waiting_prices)
    await call.message.edit_text(
        f"✅ Комиссия вывода: <b>{withdraw_val:.2f}%</b>\n\n"
        "Введите закупку и продажу через пробел:\n"
        "Пример: <code>1500 2000</code>"
        f"{EXIT_HINT}",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_only_kb()
    )
    await call.answer()


# WITHDRAW COMMISSION — вручную
@router.message(StateFilter(PlayerOkStates.waiting_withdraw_commission))
async def text_withdraw_commission(message: types.Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        await state.clear()
        return await no_access_reply(message)

    try:
        withdraw_val = float(message.text.replace(",", "."))
        if withdraw_val <= 0:
            raise ValueError
    except Exception:
        return await message.answer(
            "⚠️ Введите корректный процент\nПример: <code>6</code> или <code>5.2</code>",
            parse_mode=ParseMode.HTML
        )

    save_commission("withdraw", withdraw_val)
    await state.update_data(withdraw_commission=withdraw_val)
    await state.set_state(PlayerOkStates.waiting_prices)
    await message.answer(
        f"✅ Комиссия вывода: <b>{withdraw_val:.2f}%</b>\n\n"
        "Введите закупку и продажу через пробел:\n"
        "Пример: <code>1500 2000</code>"
        f"{EXIT_HINT}",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_only_kb()
    )


# Ввод цен + расчёт
@router.message(StateFilter(PlayerOkStates.waiting_prices))
async def calc_prices(message: types.Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        await state.clear()
        return await no_access_reply(message)

    parts = message.text.replace(",", ".").split()
    if len(parts) != 2:
        return await message.answer(
            "⚠️ Нужно ввести две суммы через пробел\n"
            "Пример: <code>1500 2000</code>",
            parse_mode=ParseMode.HTML
        )

    try:
        buy_price = float(parts[0])
        sell_price = float(parts[1])
        if buy_price <= 0 or sell_price <= 0:
            raise ValueError
    except Exception:
        return await message.answer(
            "⚠️ Введите корректные положительные числа",
            parse_mode=ParseMode.HTML
        )

    data = await state.get_data()
    sale_c = data.get("sale_commission", 0.0)
    withdraw_c = data.get("withdraw_commission", 0.0)

    after_sale = sell_price - (sell_price * sale_c / 100)
    after_withdraw = after_sale - (after_sale * withdraw_c / 100)
    profit = after_withdraw - buy_price

    await message.answer(
        f"📊 <b>Результат PlayerOK</b>\n\n"
        f"🛒 Закупка:              <b>{buy_price:,.2f} ₽</b>\n"
        f"💰 Продажа:              <b>{sell_price:,.2f} ₽</b>\n"
        f"➖ Комиссия продажи ({sale_c:.2f}%): <b>{sell_price * sale_c / 100:,.2f} ₽</b>\n"
        f"➖ Комиссия вывода ({withdraw_c:.2f}%):  <b>{after_sale * withdraw_c / 100:,.2f} ₽</b>\n"
        f"{DIVIDER}\n"
        f"💎 <b>Чистая прибыль: {profit:,.2f} ₽</b>\n"
        f"{EXIT_HINT}",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_only_kb()
    )
