import asyncio
import html as _html

from aiogram import Router, F, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext

from loader import bot
from config import ADMIN_ID
from database import db
from handlers.utils import no_access_reply
from handlers.funpay_admin import extract_order_amount, fetch_funpay_sales, get_auto_buy_prices, make_funpay_account

router = Router()


CHECK_DEPTH = 150


def _summary_text(count: int, manual: bool = False) -> str:
    title = "🧾 <b>Незаполненные заказы</b>"
    lead = "Сканирование завершено." if manual else "Пора закрыть хвосты по продажам."
    return (
        f"{title}\n\n"
        f"{lead}\n\n"
        f"📌 Найдено без себестоимости: <b>{count}</b>\n"
        f"🔎 Проверено: последние <b>{CHECK_DEPTH}</b> заказов\n\n"
        f"<i>Нажмите кнопку ниже, чтобы открыть карточки заказов.</i>"
    )


def _empty_text() -> str:
    return (
        "✅ <b>Незаполненных заказов нет</b>\n\n"
        f"Проверены последние <b>{CHECK_DEPTH}</b> заказов."
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

    await message.answer(f"🔍 <b>Сканирую последние {CHECK_DEPTH} заказов...</b>", parse_mode=ParseMode.HTML)
    
    gk, ua = db.get_config()
    if not gk:
        return await message.answer("❌ Ошибка: Golden Key не настроен.")

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
            
            await message.answer(
                _summary_text(len(to_remind_ids), manual=True),
                reply_markup=kb.as_markup(),
                parse_mode=ParseMode.HTML
            )
        else:
            await message.answer(_empty_text(), parse_mode=ParseMode.HTML)

    except Exception as e:
        print(f"[CMD ERROR] {e}")
        await message.answer(
            f"❌ <b>Не удалось получить заказы FunPay</b>\n\n"
            f"<code>{_short_error(e)}</code>",
            parse_mode=ParseMode.HTML
        )

@router.callback_query(F.data == "task_fill_unfilled")
async def cb_process_tasks(call: types.CallbackQuery, state: FSMContext): # Добавили state
    await call.answer("Загружаю список. Пожалуйста, подождите...")
    try:
        await call.message.delete()
    except Exception:
        pass

    gk, ua = db.get_config()
    try:
        acc = make_funpay_account(gk, ua)
        sales = fetch_funpay_sales(acc, limit=CHECK_DEPTH)

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
