# handlers/saveprofit.py

import os
import json
from datetime import datetime, timedelta
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, StateFilter
from aiogram.enums import ParseMode
from html import escape
from database import db

from loader import is_authorized
from states.states import SaveProfitStates, ProfitStatsStates
from handlers.utils import load_profits, save_profits, format_date_now as format_date, no_access_reply
from handlers.wallet import update_wallet

router = Router()
EXIT_HINT = "\n<i>Для выхода введите /cancel</i>"


def cancel_only_kb(callback_data: str = "saveprofit_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=callback_data)]
    ])


def parse_date(date_str: str) -> datetime:
    for fmt in ["%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"]:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Неверный формат даты: {date_str}")


def get_sorted_profits(profits: list):
    return sorted(profits, key=lambda x: parse_date(x["date"]), reverse=True)


def _money(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _short(text: str, limit: int = 54) -> str:
    text = str(text or "").strip()
    return (text[:limit - 1] + "...") if len(text) > limit else text


def _profit_source(raw_type: str) -> tuple[str, str]:
    raw_type = str(raw_type or "Неизвестно")
    low = raw_type.lower()
    if "fp #" in low or "funpay" in low:
        return "FunPay", "🟦"
    if "po #" in low or "playerok" in low:
        return "PlayerOK", "🟩"
    return raw_type, "▫️"


def _total_pages(total: int, per_page: int = 10) -> int:
    return max(1, (total - 1) // per_page + 1)


def saveprofit_menu_text() -> str:
    return (
        "💼 <b>Прибыль</b>\n"
        "━━━━━━━━━━━━━━\n"
        "Журнал продаж, ручные записи и статистика."
    )


def saveprofit_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить прибыль", callback_data="save_add_profit")],
        [
            InlineKeyboardButton(text="📒 Просмотр", callback_data="save_view_profit"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="save_stats"),
        ],
        [
            InlineKeyboardButton(text="✏️ Редактировать", callback_data="save_edit_profit"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data="save_delete_profit"),
        ],
    ])


def format_profit_list(profits: list, page: int = 0, per_page: int = 10):
    sorted_profits = get_sorted_profits(profits)
    if not sorted_profits:
        return (
            "📒 <b>Журнал прибыли</b>\n"
            "━━━━━━━━━━━━━━\n"
            "Записей пока нет."
        ), {}

    start = page * per_page
    end = start + per_page
    page_profits = sorted_profits[start:end]

    total_profit = sum(_money(p.get("profit", 0)) for p in sorted_profits)
    total_sell = sum(_money(p.get("sell_price", 0)) for p in sorted_profits)
    total_pages = _total_pages(len(sorted_profits), per_page)

    text = (
        f"📒 <b>Журнал прибыли</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"🧾 Записей: <b>{len(sorted_profits)}</b>\n"
        f"💵 Продажи: <b>{total_sell:.2f} ₽</b>\n"
        f"💰 Прибыль: <b>{total_profit:.2f} ₽</b>\n"
        f"📄 Страница: <b>{page + 1}/{total_pages}</b>\n\n"
    )
    index_map = {} 

    for display_i, p in enumerate(page_profits, start=start + 1):
        # Важно: находим индекс в ОРИГИНАЛЬНОМ списке profits для удаления
        try:
            real_index = profits.index(p)
        except ValueError:
            continue
            
        raw_type = p.get("type", "Неизвестно")
        _, source_icon = _profit_source(raw_type)
        type_str = escape(_short(raw_type, 58))

        buy = _money(p.get("buy_price", 0.0))
        sell = _money(p.get("sell_price", 0.0))
        profit = _money(p.get("profit", 0.0))
        date_str = escape(str(p.get("date", "")))

        profit_icon = "✅" if profit >= 0 else "⚠️"
        text += (
            f"<b>{display_i}. {source_icon} {type_str}</b>\n"
            f"📅 <i>{date_str}</i>\n"
            f"💸 Закуп: <code>{buy:.2f} ₽</code>  |  💵 Продажа: <code>{sell:.2f} ₽</code>\n"
            f"{profit_icon} Чистыми: <b>{profit:.2f} ₽</b>\n\n"
        )
        index_map[display_i] = real_index

    return text, index_map


def pagination_keyboard(page: int, total_pages: int, prefix: str):
    total_pages = max(1, total_pages)
    buttons = []
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="◀️", callback_data=f"{prefix}_page_{page-1}"))
    row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="none"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton(text="▶️", callback_data=f"{prefix}_page_{page+1}"))
    buttons.append(row)
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="saveprofit_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def stats_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="День", callback_data="stats_day"),
            InlineKeyboardButton(text="Неделя", callback_data="stats_week"),
        ],
        [
            InlineKeyboardButton(text="Месяц", callback_data="stats_month"),
            InlineKeyboardButton(text="Свой период", callback_data="stats_custom"),
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="saveprofit_menu")]
    ])


def calculate_profit_sum(profits: list, start_date: datetime, end_date: datetime):
    total = 0.0
    day_start = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    for p in profits:
        try:
            p_date = parse_date(p["date"])
            if day_start <= p_date <= day_end:
                total += p.get("profit", 0.0)
        except Exception:
            continue
    return total


def calculate_profit_stats(profits: list, start_date: datetime, end_date: datetime):
    total_profit = 0.0
    total_sell = 0.0
    count = 0
    day_start = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    for p in profits:
        try:
            p_date = parse_date(p["date"])
            if day_start <= p_date <= day_end:
                count += 1
                total_profit += _money(p.get("profit", 0.0))
                total_sell += _money(p.get("sell_price", 0.0))
        except Exception:
            continue
    return count, total_sell, total_profit


# /saveprofit
@router.message(Command("saveprofit"))
async def cmd_saveprofit(message: types.Message):
    if not is_authorized(message.from_user.id):
        await no_access_reply(message)
        return

    await message.answer(saveprofit_menu_text(), parse_mode=ParseMode.HTML, reply_markup=saveprofit_menu_keyboard())


# Назад в меню
@router.callback_query(F.data == "saveprofit_menu")
async def cb_saveprofit_menu(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    await call.message.edit_text(saveprofit_menu_text(), parse_mode=ParseMode.HTML, reply_markup=saveprofit_menu_keyboard())


# Ввод прибыли — выбор типа
@router.callback_query(F.data == "save_add_profit")
async def cb_save_add_profit(call: types.CallbackQuery, state: FSMContext):
    await call.answer()

    text = (
        "➕ <b>Добавление прибыли</b>\n"
        "━━━━━━━━━━━━━━\n"
        "Выберите площадку:"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="FunPay", callback_data="save_type_funpay"),
            InlineKeyboardButton(text="PlayerOK", callback_data="save_type_playerok")
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="saveprofit_menu")]
    ])

    await call.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


# Выбор типа для добавления
@router.callback_query(F.data.startswith("save_type_"))
async def cb_save_type(call: types.CallbackQuery, state: FSMContext):
    await call.answer()

    type_str = call.data.split("_")[-1]
    await state.update_data(profit_type=type_str)

    if type_str == "funpay":
        text = (
            "🟦 <b>FunPay</b>\n"
            "━━━━━━━━━━━━━━\n"
            "Введите закуп и продажу через пробел.\n\n"
            "Пример: <code>100 200</code>"
        )
        await call.message.answer(text + EXIT_HINT, parse_mode=ParseMode.HTML, reply_markup=cancel_only_kb())
        await state.set_state(SaveProfitStates.waiting_funpay_prices)
    else:
        text = (
            "🟩 <b>PlayerOK</b>\n"
            "━━━━━━━━━━━━━━\n"
            "Введите комиссию на продажу (%):"
        )
        await call.message.answer(text + EXIT_HINT, parse_mode=ParseMode.HTML, reply_markup=cancel_only_kb())
        await state.set_state(SaveProfitStates.waiting_sale_c)


# FunPay — ввод цен
@router.message(StateFilter(SaveProfitStates.waiting_funpay_prices))
async def process_funpay_prices(message: types.Message, state: FSMContext):
    parts = message.text.replace(",", ".").split()
    if len(parts) != 2:
        await message.answer("⚠️ <b>Введите две суммы через пробел</b>", parse_mode=ParseMode.HTML)
        return

    try:
        buy_price = float(parts[0])
        sell_price = float(parts[1])
        # Твоя комиссия 3%
        commission = 0.03 
        profit = sell_price - buy_price - (sell_price * commission)
    except Exception:
        await message.answer("⚠️ <b>Введите корректные числа</b>", parse_mode=ParseMode.HTML)
        return

    profits = load_profits(message.from_user.id)
    profits.append({
        "type": "FunPay",           # Метка для отчета в wallet.py
        "buy_price": buy_price,
        "sell_price": sell_price,   # Обязательно сохраняем для суммы заказов
        "profit": profit,
        "date": format_date()
    })
    save_profits(message.from_user.id, profits)
    update_wallet(message.from_user.id, profit) 

    text = (
        f"✅ <b>Прибыль FunPay сохранена</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"💸 Закуп: <code>{buy_price:.2f} ₽</code>\n"
        f"💵 Продажа: <code>{sell_price:.2f} ₽</code>\n"
        f"💰 Чистыми: <b>{profit:.2f} ₽</b>"
    )
    await message.answer(text, parse_mode=ParseMode.HTML)
    await state.clear()


# PlayerOK — комиссия продажи (вручную)
@router.message(StateFilter(SaveProfitStates.waiting_sale_c))
async def text_save_sale_c(message: types.Message, state: FSMContext):
    try:
        sale_c = float(message.text.replace(",", "."))
        if sale_c <= 0:
            raise ValueError("Комиссия должна быть положительной")
    except Exception:
        text = "⚠️ <b>Введите корректное положительное число</b>"
        await message.answer(text, parse_mode=ParseMode.HTML)
        return

    await state.update_data(sale_commission=sale_c)

    text = (
        f"✅ <b>Комиссия продажи: {sale_c}%</b>\n\n"
        "Введите комиссию на вывод (%):"
        f"{EXIT_HINT}"
    )

    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=cancel_only_kb())
    await state.set_state(SaveProfitStates.waiting_withdraw_c)


# PlayerOK — комиссия вывода (вручную)
@router.message(StateFilter(SaveProfitStates.waiting_withdraw_c))
async def text_save_withdraw_c(message: types.Message, state: FSMContext):
    try:
        withdraw_c = float(message.text.replace(",", "."))
        if withdraw_c <= 0:
            raise ValueError("Комиссия должна быть положительной")
    except Exception:
        text = "⚠️ <b>Введите корректное положительное число</b>"
        await message.answer(text, parse_mode=ParseMode.HTML)
        return

    await state.update_data(withdraw_commission=withdraw_c)

    text = (
        f"✅ <b>Комиссия вывода: {withdraw_c}%</b>\n\n"
        "Введите цену товара и цену продажи через пробел:\n"
        "<code>пример: 1500 2000</code>"
        f"{EXIT_HINT}"
    )

    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=cancel_only_kb())
    await state.set_state(SaveProfitStates.waiting_playerok_prices)


# PlayerOK — ввод цен
@router.message(StateFilter(SaveProfitStates.waiting_playerok_prices))
async def process_playerok_prices(message: types.Message, state: FSMContext):
    parts = message.text.replace(",", ".").split()
    if len(parts) != 2:
        text = "⚠️ <b>Введите две суммы через пробел</b>"
        await message.answer(text, parse_mode=ParseMode.HTML)
        return

    try:
        buy_price = float(parts[0])
        sell_price = float(parts[1])
        data = await state.get_data()
        sale_c = data.get("sale_commission", 0.0)
        withdraw_c = data.get("withdraw_commission", 0.0)
        after_sale = sell_price - sell_price * (sale_c / 100)
        after_withdraw = after_sale - after_sale * (withdraw_c / 100)
        profit = after_withdraw - buy_price
    except Exception:
        text = "⚠️ <b>Введите корректные числа</b>"
        await message.answer(text, parse_mode=ParseMode.HTML)
        return

    profits = load_profits(message.from_user.id)
    profits.append({
        "type": "PlayerOK",
        "buy_price": buy_price,
        "sell_price": sell_price,
        "sale_c": sale_c,
        "withdraw_c": withdraw_c,
        "profit": profit,
        "date": format_date()
    })
    save_profits(message.from_user.id, profits)
    update_wallet(message.from_user.id, profit) 

    text = (
        f"✅ <b>Прибыль PlayerOK сохранена</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"💸 Закуп: <code>{buy_price:.2f} ₽</code>\n"
        f"💵 Продажа: <code>{sell_price:.2f} ₽</code>\n"
        f"💰 Чистыми: <b>{profit:.2f} ₽</b>"
    )
    await message.answer(text, parse_mode=ParseMode.HTML)
    await state.clear()


# Редактирование — список
@router.callback_query(F.data == "save_edit_profit")
async def cb_save_edit_profit(call: types.CallbackQuery, state: FSMContext):
    await call.answer()

    profits = load_profits(call.from_user.id)
    text, index_map = format_profit_list(profits)
    await state.update_data(index_map=index_map)

    keyboard = pagination_keyboard(0, _total_pages(len(profits)), "edit")

    await call.message.answer(
        f"{text}\n<i>Отправьте номер записи для редактирования.</i>{EXIT_HINT}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            *keyboard.inline_keyboard[:-1],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="saveprofit_menu")]
        ])
    )
    await state.set_state(SaveProfitStates.choose_edit)


# Страницы редактирования
@router.callback_query(F.data.startswith("edit_page_"))
async def cb_edit_page(call: types.CallbackQuery, state: FSMContext):
    await call.answer()

    page = int(call.data.split("_")[-1])
    profits = load_profits(call.from_user.id)
    text, index_map = format_profit_list(profits, page)
    await state.update_data(index_map=index_map)

    keyboard = pagination_keyboard(page, _total_pages(len(profits)), "edit")

    await call.message.edit_text(
        f"{text}\n<i>Отправьте номер записи для редактирования.</i>{EXIT_HINT}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            *keyboard.inline_keyboard[:-1],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="saveprofit_menu")]
        ])
    )


# Выбор номера для редактирования
@router.message(StateFilter(SaveProfitStates.choose_edit))
async def process_choose_edit(message: types.Message, state: FSMContext):
    try:
        display_num = int(message.text.strip())
        data = await state.get_data()
        index_map = data.get("index_map", {})

        if display_num in index_map:
            real_index = index_map[display_num]
            profits = load_profits(message.from_user.id)
            if 0 <= real_index < len(profits):
                await state.update_data(
                    edit_id=real_index,
                    display_num=display_num  # сохраняем номер, который видит пользователь
                )
                text = (
                    f"➕ <b>Редактирование записи №{display_num}</b>\n\n"
                    "Выберите тип:"
                )
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="FunPay", callback_data="edit_type_funpay"),
                        InlineKeyboardButton(text="PlayerOK", callback_data="edit_type_playerok")
                    ],
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="saveprofit_menu")]
                ])
                await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
                await state.set_state(SaveProfitStates.edit_choose_type)
            else:
                text = "⚠️ <b>Ошибка: запись не найдена.</b>"
                await message.answer(text, parse_mode=ParseMode.HTML)
        else:
            text = "⚠️ <b>Неверный номер записи.</b>"
            await message.answer(text, parse_mode=ParseMode.HTML)
    except ValueError:
        text = "⚠️ <b>Введите номер записи (число).</b>"
        await message.answer(text, parse_mode=ParseMode.HTML)


# Выбор типа для редактирования
@router.callback_query(F.data.startswith("edit_type_"))
async def cb_edit_type(call: types.CallbackQuery, state: FSMContext):
    await call.answer()

    type_str = call.data.split("_")[-1]
    await state.update_data(profit_type=type_str)

    data = await state.get_data()
    display_num = data.get("display_num", "неизвестно")

    if type_str == "funpay":
        text = (
            f"➕ <b>FunPay (редактирование записи №{display_num})</b>\n\n"
            "Введите цену товара и цену продажи через пробел:\n"
            "<code>пример: 100 200</code>"
            f"{EXIT_HINT}"
        )
        await call.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=cancel_only_kb())
        await state.set_state(SaveProfitStates.edit_waiting_funpay_prices)
    else:
        text = (
            f"➕ <b>PlayerOK (редактирование записи №{display_num})</b>\n\n"
            "Введите комиссию на продажу (%):"
            f"{EXIT_HINT}"
        )
        await call.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=cancel_only_kb())
        await state.set_state(SaveProfitStates.edit_waiting_sale_c)


# Редактирование FunPay — цены
@router.message(StateFilter(SaveProfitStates.edit_waiting_funpay_prices))
async def process_edit_funpay_prices(message: types.Message, state: FSMContext):
    parts = message.text.replace(",", ".").split()
    if len(parts) != 2:
        text = "⚠️ <b>Введите две суммы через пробел</b>"
        await message.answer(text, parse_mode=ParseMode.HTML)
        return

    try:
        buy_price = float(parts[0])
        sell_price = float(parts[1])
        profit = sell_price - buy_price - (sell_price * 0.03)
    except Exception:
        text = "⚠️ <b>Введите корректные числа</b>"
        await message.answer(text, parse_mode=ParseMode.HTML)
        return

    data = await state.get_data()
    edit_id = data.get("edit_id")
    display_num = data.get("display_num", "неизвестно")
    profits = load_profits(message.from_user.id)
    if edit_id is not None and 0 <= edit_id < len(profits):
        old_date = profits[edit_id].get("date", format_date())
        profits[edit_id] = {
            "type": "FunPay",
            "buy_price": buy_price,
            "sell_price": sell_price,
            "profit": profit,
            "date": old_date
        }
        save_profits(message.from_user.id, profits)
        text = f"✅ <b>Запись №{display_num} обновлена: {profit:.2f} ₽</b>"
        await message.answer(text, parse_mode=ParseMode.HTML)
    else:
        text = "⚠️ <b>Ошибка редактирования.</b>"
        await message.answer(text, parse_mode=ParseMode.HTML)

    await state.clear()


# Аналогично исправляем остальные хендлеры редактирования PlayerOK (добавляем display_num)
@router.message(StateFilter(SaveProfitStates.edit_waiting_sale_c))
async def text_edit_sale_c(message: types.Message, state: FSMContext):
    try:
        sale_c = float(message.text.replace(",", "."))
        if sale_c <= 0:
            raise ValueError("Комиссия должна быть положительной")
    except Exception:
        text = "⚠️ <b>Введите корректное положительное число</b>"
        await message.answer(text, parse_mode=ParseMode.HTML)
        return

    await state.update_data(sale_commission=sale_c)

    data = await state.get_data()
    display_num = data.get("display_num", "неизвестно")

    text = (
        f"✅ <b>Комиссия продажи для записи №{display_num}: {sale_c}%</b>\n\n"
        "♟️ Введите комиссию на вывод (%):"
        f"{EXIT_HINT}"
    )

    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=cancel_only_kb())
    await state.set_state(SaveProfitStates.edit_waiting_withdraw_c)


@router.message(StateFilter(SaveProfitStates.edit_waiting_withdraw_c))
async def text_edit_withdraw_c(message: types.Message, state: FSMContext):
    try:
        withdraw_c = float(message.text.replace(",", "."))
        if withdraw_c <= 0:
            raise ValueError("Комиссия должна быть положительной")
    except Exception:
        text = "⚠️ <b>Введите корректное положительное число</b>"
        await message.answer(text, parse_mode=ParseMode.HTML)
        return

    await state.update_data(withdraw_commission=withdraw_c)

    data = await state.get_data()
    display_num = data.get("display_num", "неизвестно")

    text = (
        f"✅ <b>Комиссия вывода для записи №{display_num}: {withdraw_c}%</b>\n\n"
        "♟️ Введите цену товара и цену продажи через пробел:\n"
        "<code>пример: 1500 2000</code>"
        f"{EXIT_HINT}"
    )

    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=cancel_only_kb())
    await state.set_state(SaveProfitStates.edit_waiting_playerok_prices)


@router.message(StateFilter(SaveProfitStates.edit_waiting_playerok_prices))
async def process_edit_playerok_prices(message: types.Message, state: FSMContext):
    parts = message.text.replace(",", ".").split()
    if len(parts) != 2:
        text = "⚠️ <b>Введите две суммы через пробел</b>"
        await message.answer(text, parse_mode=ParseMode.HTML)
        return

    try:
        buy_price = float(parts[0])
        sell_price = float(parts[1])
        data = await state.get_data()
        sale_c = data.get("sale_commission", 0.0)
        withdraw_c = data.get("withdraw_commission", 0.0)
        after_sale = sell_price - sell_price * (sale_c / 100)
        after_withdraw = after_sale - after_sale * (withdraw_c / 100)
        profit = after_withdraw - buy_price
    except Exception:
        text = "⚠️ <b>Введите корректные числа</b>"
        await message.answer(text, parse_mode=ParseMode.HTML)
        return

    data = await state.get_data()
    edit_id = data.get("edit_id")
    display_num = data.get("display_num", "неизвестно")
    profits = load_profits(message.from_user.id)
    if edit_id is not None and 0 <= edit_id < len(profits):
        old_date = profits[edit_id].get("date", format_date())
        profits[edit_id] = {
            "type": "PlayerOK",
            "buy_price": buy_price,
            "sell_price": sell_price,
            "sale_c": sale_c,
            "withdraw_c": withdraw_c,
            "profit": profit,
            "date": old_date
        }
        save_profits(message.from_user.id, profits)
        text = f"✅ <b>Запись №{display_num} обновлена: {profit:.2f} ₽</b>"
        await message.answer(text, parse_mode=ParseMode.HTML)
    else:
        text = "⚠️ <b>Ошибка редактирования.</b>"
        await message.answer(text, parse_mode=ParseMode.HTML)

    await state.clear()


# Обработка удаления
# Обработка удаления (вход в меню)
@router.callback_query(F.data == "save_delete_profit")
async def cb_save_delete_profit(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    profits = load_profits(call.from_user.id)
    text, index_map = format_profit_list(profits, page=0)
    await state.update_data(index_map=index_map)
    total_pages = _total_pages(len(profits))
    keyboard = pagination_keyboard(0, total_pages, "delete")
    
    await call.message.edit_text(
        f"{text}\n<i>Отправьте номер записи для удаления.</i>{EXIT_HINT}", 
        parse_mode=ParseMode.HTML, 
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            *keyboard.inline_keyboard[:-1],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="saveprofit_menu")]
        ])
    )
    await state.set_state(SaveProfitStates.choose_delete)

@router.message(StateFilter(SaveProfitStates.choose_delete))
async def process_delete_profit(message: types.Message, state: FSMContext):
    try:
        display_num = int(message.text.strip())
        data = await state.get_data()
        index_map = data.get("index_map", {})
        
        if display_num in index_map:
            real_index = index_map[display_num]
            profits = load_profits(message.from_user.id)
            item = profits[real_index]
            
            # СИНХРОНИЗАЦИЯ С FUNPAY ADMIN (п. 2)
            # Ищем ID заказа в названии типа (например "FP #12345")
            if "FP #" in item['type']:
                try:
                    order_id = item['type'].split("#")[1].split()[0]
                    db.set_prime_cost(order_id, None) # Удаляем из БД бота
                except Exception: pass

            removed_profit = profits.pop(real_index)
            save_profits(message.from_user.id, profits)
            
            # Возвращаем деньги в кошелек (отнимаем прибыль)
            update_wallet(message.from_user.id, -removed_profit['profit'])
            
            await message.answer(
                f"✅ <b>Запись удалена</b>\n"
                f"━━━━━━━━━━━━━━\n"
                f"№{display_num} удалена, данные синхронизированы.",
                parse_mode=ParseMode.HTML
            )
            await state.clear()
    except Exception:
        await message.answer("Ошибка. Введите число.")


# Страницы удаления
@router.callback_query(F.data.startswith("delete_page_"))
async def cb_delete_page(call: types.CallbackQuery, state: FSMContext):
    await call.answer()

    page = int(call.data.split("_")[-1])
    profits = load_profits(call.from_user.id)
    text, index_map = format_profit_list(profits, page)
    await state.update_data(index_map=index_map)

    keyboard = pagination_keyboard(page, _total_pages(len(profits)), "delete")

    await call.message.edit_text(
        f"{text}\n<i>Отправьте номер записи для удаления.</i>{EXIT_HINT}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            *keyboard.inline_keyboard[:-1],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="saveprofit_menu")]
        ])
    )




# Просмотр данных — список (теперь работает!)
@router.callback_query(F.data == "save_view_profit")
async def cb_save_view_profit(call: types.CallbackQuery):
    await call.answer()

    profits = load_profits(call.from_user.id)
    text, _ = format_profit_list(profits)
    keyboard = pagination_keyboard(0, _total_pages(len(profits)), "view")

    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="📊 Статистика", callback_data="save_stats")
    ])

    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


# Страницы просмотра
@router.callback_query(F.data.startswith("view_page_"))
async def cb_view_page(call: types.CallbackQuery):
    await call.answer()

    page = int(call.data.split("_")[-1])
    profits = load_profits(call.from_user.id)
    text, _ = format_profit_list(profits, page)
    keyboard = pagination_keyboard(page, _total_pages(len(profits)), "view")

    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="📊 Статистика", callback_data="save_stats")
    ])

    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


# Статистика — меню
@router.callback_query(F.data == "save_stats")
async def cb_save_stats(call: types.CallbackQuery):
    await call.answer()

    text = (
        "📊 <b>Статистика прибыли</b>\n"
        "━━━━━━━━━━━━━━\n"
        "Выберите период:"
    )

    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=stats_keyboard())


# Статистика по периоду
@router.callback_query(F.data.startswith("stats_"))
async def cb_stats_period(call: types.CallbackQuery, state: FSMContext):
    await call.answer()

    period = call.data.split("_")[-1]
    profits = load_profits(call.from_user.id)
    now = datetime.now()
    if period == "day":
        start_date = now
        end_date = now
        period_str = "за день"
    elif period == "week":
        start_date = now - timedelta(days=7)
        end_date = now
        period_str = "за неделю"
    elif period == "month":
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = now
        period_str = "за месяц"
    elif period == "custom":
        text = (
            "📆 <b>Свой период</b>\n"
            "━━━━━━━━━━━━━━\n"
            "Введите даты в формате ДД.ММ.ГГГГ по ДД.ММ.ГГГГ:\n"
            "<code>пример: 01.01.2026 по 31.01.2026</code>"
            f"{EXIT_HINT}"
        )
        await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=cancel_only_kb())
        await state.set_state(ProfitStatsStates.waiting_custom_period)
        return

    count, total_sell, total = calculate_profit_stats(profits, start_date, end_date)
    text = (
        f"📊 <b>Статистика {period_str}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"🧾 Записей: <b>{count}</b>\n"
        f"💵 Продажи: <b>{total_sell:.2f} ₽</b>\n"
        f"💰 Чистая прибыль: <b>{total:.2f} ₽</b>"
    )

    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=stats_keyboard())


# Свой период — ввод дат
@router.message(StateFilter(ProfitStatsStates.waiting_custom_period))
async def process_custom_period(message: types.Message, state: FSMContext):
    input_text = message.text.strip().lower()
    if "по" not in input_text:
        text = "⚠️ <b>Формат: ДД.ММ.ГГГГ по ДД.ММ.ГГГГ</b>"
        await message.answer(text, parse_mode=ParseMode.HTML)
        return

    parts = input_text.split("по")
    if len(parts) != 2:
        text = "⚠️ <b>Неверный формат.</b>"
        await message.answer(text, parse_mode=ParseMode.HTML)
        return

    try:
        start_str = parts[0].strip()
        end_str = parts[1].strip()
        start_date = datetime.strptime(start_str, "%d.%m.%Y")
        end_date = datetime.strptime(end_str, "%d.%m.%Y").replace(hour=23, minute=59, second=59)
        if start_date > end_date:
            raise ValueError("Начало > конца")
    except Exception:
        text = "⚠️ <b>Неверный формат дат.</b>"
        await message.answer(text, parse_mode=ParseMode.HTML)
        return

    profits = load_profits(message.from_user.id)
    count, total_sell, total = calculate_profit_stats(profits, start_date, end_date)

    text = (
        f"📊 <b>Статистика за период</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"📆 {start_str} — {end_str}\n"
        f"🧾 Записей: <b>{count}</b>\n"
        f"💵 Продажи: <b>{total_sell:.2f} ₽</b>\n"
        f"💰 Чистая прибыль: <b>{total:.2f} ₽</b>"
    )

    await message.answer(text + EXIT_HINT, parse_mode=ParseMode.HTML, reply_markup=cancel_only_kb())
    await state.clear()
