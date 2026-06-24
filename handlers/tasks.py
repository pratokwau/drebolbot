import asyncio
import html as _html
from datetime import datetime, timedelta

from aiogram import Router, F, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext

from loader import bot
from config import ADMIN_ID
from database import db
from handlers.utils import no_access_reply, no_access_callback
from handlers.funpay_admin import extract_order_amount, fetch_funpay_sales, get_auto_buy_prices, make_funpay_account
from states.states import TaskUnfilledStates

router = Router()


CHECK_DEPTH = 150
TASK_PERIOD_LABELS = {
    "day": "за день",
    "prev_day": "за прошлый день",
    "custom": "за свой период",
}


def _task_period_kb() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="📅 За день", callback_data="task_unfilled_period_day"))
    kb.row(types.InlineKeyboardButton(text="🕓 За прошлый день", callback_data="task_unfilled_period_prev_day"))
    kb.row(types.InlineKeyboardButton(text="📝 Свой период", callback_data="task_unfilled_period_custom"))
    kb.row(types.InlineKeyboardButton(text="↩️ Назад", callback_data="funpay_auto_main"))
    return kb


def _task_back_kb() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="↩️ Назад", callback_data="task_fill_unfilled"))
    return kb


def _parse_task_date(text: str) -> datetime:
    return datetime.strptime(text.strip(), "%d.%m.%Y")


def _task_period_bounds(period: str, custom_text: str | None = None) -> tuple[datetime, datetime]:
    now = datetime.now()
    if period == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        return start, end
    if period == "prev_day":
        prev = now - timedelta(days=1)
        start = prev.replace(hour=0, minute=0, second=0, microsecond=0)
        end = prev.replace(hour=23, minute=59, second=59, microsecond=999999)
        return start, end
    if period == "custom" and custom_text:
        raw = custom_text.strip()
        if "-" in raw:
            start_raw, end_raw = [x.strip() for x in raw.split("-", 1)]
        else:
            start_raw = end_raw = raw
        start = _parse_task_date(start_raw).replace(hour=0, minute=0, second=0, microsecond=0)
        end = _parse_task_date(end_raw).replace(hour=23, minute=59, second=59, microsecond=999999)
        if end < start:
            raise ValueError("Конечная дата раньше начальной")
        return start, end
    raise ValueError("Неизвестный период")


def _sale_datetime(sale) -> datetime | None:
    raw = getattr(sale, "date", getattr(sale, "created_at", None))
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    text = str(raw).strip()
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _filter_sales_by_period(sales: list, start: datetime, end: datetime) -> list:
    filtered = []
    for sale in sales:
        dt = _sale_datetime(sale)
        if dt is None or start <= dt <= end:
            filtered.append(sale)
    return filtered


def _summary_text(count: int, manual: bool = False, period_label: str = "за последние заказы") -> str:
    title = "🧾 <b>Незаполненные заказы</b>"
    lead = "Сканирование завершено." if manual else "Пора закрыть хвосты по продажам."
    return (
        f"{title}\n\n"
        f"{lead}\n\n"
        f"📌 Найдено без себестоимости: <b>{count}</b>\n"
        f"🔎 Период: <b>{_html.escape(period_label)}</b>\n\n"
        f"<i>Нажмите кнопку ниже, чтобы открыть карточки заказов.</i>"
    )


def _empty_text(period_label: str = "за последние заказы") -> str:
    return (
        "✅ <b>Незаполненных заказов нет</b>\n\n"
        f"Период: <b>{_html.escape(period_label)}</b>."
    )


def _short_error(error: Exception, limit: int = 900) -> str:
    text = str(error).strip()
    if len(text) > limit:
        text = text[:limit] + "..."
    return _html.escape(text)


def _extract_order_amount(product_name: str, sale) -> int:
    return extract_order_amount(product_name)


def _extract_order_game(sale) -> str | None:
    subcategory_name = getattr(sale, 'subcategory_name', '') or ''
    if ',' in subcategory_name:
        return subcategory_name.rsplit(',', 1)[0].strip()
    return None


def _build_order_card(sale, price_text: str, auto_variants: list) -> str:
    s_id = str(sale.id)
    product_name = getattr(sale, 'description', getattr(sale, 'product_name', 'Без названия'))
    buyer_username = getattr(sale, 'buyer_username', getattr(sale, 'buyer', ''))
    order_date = str(getattr(sale, 'date', getattr(sale, 'created_at', '')))
    order_game = _extract_order_game(sale)

    buyer_line = f"👤 Покупатель: <b>@{_html.escape(str(buyer_username))}</b>\n" if buyer_username else ""
    date_line = f"📅 Дата: <b>{_html.escape(order_date)}</b>\n" if order_date else ""
    game_line = f"🎮 Игра: <b>{_html.escape(order_game)}</b>\n" if order_game else ""

    auto_text = ""
    if auto_variants:
        lines = []
        for v in auto_variants:
            title = _html.escape(str(v["title"]))
            cashback = _html.escape(str(v.get("cashback_label", "")))
            lines.append(f"• <b>{title}</b> <i>({cashback})</i> — <code>{v['total_cost']} ₽</code>")
        auto_text = "\n\n🤖 <b>Автоподбор закупа</b>\n" + "\n".join(lines)

    return (
        f"🧾 <b>Заказ <a href=\"https://funpay.com/orders/{s_id}/\">#{s_id}</a></b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"{buyer_line}"
        f"{date_line}"
        f"{game_line}"
        f"\n🛒 <b>Товар</b>\n"
        f"<code>{_html.escape(str(product_name))}</code>\n\n"
        f"💵 Продажа: <b>{_html.escape(str(price_text))}</b>\n"
        f"📉 Себестоимость: <b>не введена</b>"
        f"{auto_text}"
    )


async def remind_unfilled_orders():
    """Проверка незаполненных ID (глубина 150)"""
    print("[TASKS] Проверка незаполненных заказов...")
    
    gk, ua = db.get_config()
    if not gk: return

    try:
        acc = make_funpay_account(gk, ua)
        sales = fetch_funpay_sales(acc, limit=CHECK_DEPTH)

        to_remind_ids = []
        
        for s in sales[:CHECK_DEPTH]:
            s_id = str(s.id)
            if "refund" in str(s.status).lower():
                continue

            if not db.get_prime_cost(s_id):
                to_remind_ids.append(s)

        if to_remind_ids:
            kb = InlineKeyboardBuilder()
            kb.row(types.InlineKeyboardButton(
                text=f"📝 Заполнить хвосты ({len(to_remind_ids)} шт.)", 
                callback_data="task_fill_unfilled")
            )
            
            await bot.send_message(
                ADMIN_ID, 
                _summary_text(len(to_remind_ids)),
                reply_markup=kb.as_markup(),
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        print(f"[TASKS ERROR] {e}")

@router.message(F.text == "/notconord")
async def cmd_check_unfilled(message: types.Message):
    """Ручная проверка незаполненных заказов по команде"""
    # Проверка на админа (на всякий случай)
    if message.from_user.id != ADMIN_ID:
        await no_access_reply(message)
        return

    await message.answer(
        "🧾 <b>Незаполненные заказы</b>\n\n"
        "Выберите период проверки:",
        parse_mode=ParseMode.HTML,
        reply_markup=_task_period_kb().as_markup()
    )


@router.callback_query(F.data == "task_fill_unfilled")
async def cb_task_fill_unfilled_menu(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    await state.clear()
    await call.message.edit_text(
        "🧾 <b>Незаполненные заказы</b>\n\n"
        "Выберите период проверки:",
        parse_mode=ParseMode.HTML,
        reply_markup=_task_period_kb().as_markup()
    )
    await call.answer()


@router.callback_query(F.data == "task_unfilled_period_day")
async def cb_task_unfilled_day(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    await state.clear()
    await _run_unfilled_check(call.message, state, "day")
    await call.answer()


@router.callback_query(F.data == "task_unfilled_period_prev_day")
async def cb_task_unfilled_prev_day(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    await state.clear()
    await _run_unfilled_check(call.message, state, "prev_day")
    await call.answer()


@router.callback_query(F.data == "task_unfilled_period_custom")
async def cb_task_unfilled_custom(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    await state.set_state(TaskUnfilledStates.waiting_custom_period)
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="↩️ Назад", callback_data="task_fill_unfilled"))
    await call.message.answer(
        "📅 <b>Свой период</b>\n\n"
        "Отправьте дату в формате <code>ДД.ММ.ГГГГ</code>\n"
        "или диапазон <code>ДД.ММ.ГГГГ-ДД.ММ.ГГГГ</code>.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.as_markup()
    )
    await call.answer()


@router.message(TaskUnfilledStates.waiting_custom_period)
async def process_task_unfilled_custom(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await state.clear()
    text = (message.text or "").strip()
    if text == "/cancel":
        await state.clear()
        return await message.answer("Отменено.", reply_markup=_task_period_kb().as_markup())
    await state.clear()
    await _run_unfilled_check(message, state, "custom", text)


async def _run_unfilled_check(target, state: FSMContext, period: str, custom_text: str | None = None):
    gk, ua = db.get_config()
    if not gk:
        if isinstance(target, types.Message):
            return await target.answer("❌ Ошибка: Golden Key не настроен.")
        return await target.edit_text("❌ Ошибка: Golden Key не настроен.", parse_mode=ParseMode.HTML)

    try:
        start, end = _task_period_bounds(period, custom_text)
        period_label = f"{start.strftime('%d.%m.%Y')} - {end.strftime('%d.%m.%Y')}" if period == "custom" else TASK_PERIOD_LABELS[period]
        await state.update_data(
            task_unfilled_period=period,
            task_unfilled_custom_text=custom_text or "",
            task_unfilled_period_label=period_label,
        )
    except Exception as e:
        err_text = (
            f"❌ <b>Период задан неверно</b>\n\n"
            f"<code>{_short_error(e)}</code>"
        )
        if isinstance(target, types.Message):
            return await target.answer(err_text, parse_mode=ParseMode.HTML, reply_markup=_task_back_kb().as_markup())
        return await target.edit_text(err_text, parse_mode=ParseMode.HTML, reply_markup=_task_back_kb().as_markup())

    progress = target
    if isinstance(target, types.Message):
        progress = await target.answer(f"🔍 <b>Сканирую {period_label}...</b>", parse_mode=ParseMode.HTML)
    else:
        await progress.edit_text(f"🔍 <b>Сканирую {period_label}...</b>", parse_mode=ParseMode.HTML)

    try:
        acc = make_funpay_account(gk, ua)
        sales = fetch_funpay_sales(acc, limit=CHECK_DEPTH)
        sales = _filter_sales_by_period(sales, start, end)

        to_remind_ids = []
        for s in sales[:CHECK_DEPTH]:
            s_id = str(s.id)
            if "refund" in str(s.status).lower():
                continue
            if not db.get_prime_cost(s_id):
                to_remind_ids.append(s)

        if to_remind_ids:
            kb = InlineKeyboardBuilder()
            kb.row(types.InlineKeyboardButton(
                text=f"📝 Заполнить хвосты ({len(to_remind_ids)} шт.)",
                callback_data="task_unfilled_run")
            )
            await progress.edit_text(
                _summary_text(len(to_remind_ids), manual=True, period_label=period_label),
                reply_markup=kb.as_markup(),
                parse_mode=ParseMode.HTML
            )
        else:
            await progress.edit_text(
                _empty_text(period_label),
                parse_mode=ParseMode.HTML,
                reply_markup=_task_period_kb().as_markup()
            )

    except Exception as e:
        print(f"[CMD ERROR] {e}")
        await progress.edit_text(
            f"❌ <b>Не удалось получить заказы FunPay</b>\n\n"
            f"<code>{_short_error(e)}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=_task_period_kb().as_markup()
        )


@router.callback_query(F.data == "task_unfilled_run")
async def cb_process_tasks(call: types.CallbackQuery, state: FSMContext): # Добавили state
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    await call.answer("Загружаю список. Пожалуйста, подождите...")
    try:
        await call.message.delete()
    except Exception:
        pass

    gk, ua = db.get_config()
    try:
        acc = make_funpay_account(gk, ua)
        sales = fetch_funpay_sales(acc, limit=CHECK_DEPTH)
        state_data = await state.get_data()
        period = state_data.get("task_unfilled_period") or "day"
        custom_text = state_data.get("task_unfilled_custom_text") or None
        start, end = _task_period_bounds(period, custom_text)
        period_label = state_data.get("task_unfilled_period_label") or (
            f"{start.strftime('%d.%m.%Y')} - {end.strftime('%d.%m.%Y')}" if period == "custom" else TASK_PERIOD_LABELS.get(period, period)
        )
        sales = _filter_sales_by_period(sales, start, end)

        to_fill = []
        for s in sales[:CHECK_DEPTH]:
            if "refund" not in str(s.status).lower() and not db.get_prime_cost(str(s.id)):
                to_fill.append(s)

        if not to_fill:
            await bot.send_message(ADMIN_ID, "✅ Все заказы уже заполнены!")
            await call.answer()
            return

        total_to_send = len(to_fill)
        await bot.send_message(
            ADMIN_ID,
            f"⏳ <b>Готовлю карточки заказов</b>\n\n"
            f"Всего к заполнению: <b>{total_to_send}</b>\n"
            f"Период: <b>{_html.escape(period_label)}</b>\n"
            f"<i>Отправляю постепенно, чтобы не словить лимиты.</i>",
            parse_mode=ParseMode.HTML
        )

        # Получаем данные о кастомных ценах из стейта, чтобы отобразить их, если они уже были введены
        state_data = await state.get_data()
        custom_prices = state_data.get("custom_sell_prices", {})

        for s in to_fill:
            s_id = str(s.id)
            if db.get_prime_cost(s_id):
                continue

            product_name = getattr(s, 'description', getattr(s, 'product_name', 'Без названия'))
            order_amount = _extract_order_amount(product_name, s)
            order_game = _extract_order_game(s)

            # Если мы уже правили цену через кнопку, берем её, иначе из API
            if s_id in custom_prices:
                price = custom_prices[s_id]
                price_text = f"{price} ₽"
            else:
                price = getattr(s, 'price', 0)
                price_text = f"{price} ₽"

            auto_variants = get_auto_buy_prices(product_name, order_game, order_amount)

            text = _build_order_card(s, price_text, auto_variants)

            kb = InlineKeyboardBuilder()
            for v in auto_variants:
                label = f"✅ {v['title']} ({v['cashback_label']}) — {v['total_cost']} ₽"
                kb.row(types.InlineKeyboardButton(
                    text=label,
                    callback_data=f"fast_save_{s_id}_{price}_{v['total_cost']}"
                ))
            
            # Кнопка ручного ввода закупа
            kb.row(types.InlineKeyboardButton(text="💸 Вручную", callback_data=f"setc_{s_id}_{price}"))
            
            # ДОБАВЛЕННАЯ КНОПКА ИЗМЕНЕНИЯ ЦЕНЫ ПРОДАЖИ
            kb.row(types.InlineKeyboardButton(text="✏️ Изменить цену продажи", callback_data=f"editsell_{s_id}"))
            
            kb.row(types.InlineKeyboardButton(text="🗑 Закрыть", callback_data="delete_msg"))

            try:
                await bot.send_message(ADMIN_ID, text, reply_markup=kb.as_markup(), parse_mode=ParseMode.HTML)
                await asyncio.sleep(3) 
            except Exception as e:
                print(f"[SEND ERROR] {e}")
                await asyncio.sleep(10)
                
    except Exception as e:
        print(f"[CALLBACK ERROR] {e}")
        await bot.send_message(
            ADMIN_ID,
            f"❌ <b>Не удалось вывести незаполненные заказы</b>\n\n"
            f"<code>{_short_error(e)}</code>",
            parse_mode=ParseMode.HTML
        )
