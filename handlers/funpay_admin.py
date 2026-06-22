# handlers/funpayau.py

import re
import os
import sys
import json
import html as _html
import time
from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode

CARDINAL_PATH = "/root/FunPayCardinal"
if os.path.isdir(os.path.join(CARDINAL_PATH, "FunPayAPI")) and CARDINAL_PATH not in sys.path:
    sys.path.insert(0, CARDINAL_PATH)

from FunPayAPI import Account
try:
    import lxml  # noqa: F401
except ImportError:
    try:
        import FunPayAPI.account as _fp_account
        from bs4 import BeautifulSoup as _BeautifulSoup

        def _beautiful_soup_compat(markup="", features=None, *args, **kwargs):
            if features == "lxml":
                features = "html.parser"
            return _BeautifulSoup(markup, features, *args, **kwargs)

        _fp_account.BeautifulSoup = _beautiful_soup_compat
    except Exception:
        pass
from database import db
from config import ADMIN_ID
from handlers.utils import load_profits, save_profits, format_date_now, load_inventory, no_access_reply, no_access_callback

router = Router()

SALES_PER_PAGE = 15
SALES_BATCH_SIZE = 150
SALES_PAGES_PER_BATCH = SALES_BATCH_SIZE // SALES_PER_PAGE


class FPAdminStates(StatesGroup):
    wait_gk = State()
    wait_ua = State()
    wait_cost = State()
    wait_sell_price = State() # Новое состояние для изменения цены
    wait_order_search = State()


def get_status_emoji(status):
    s = str(status).lower()
    if "paid" in s or "оплачен" in s:
        return "🔵"
    if "refund" in s or "возврат" in s:
        return "🟠"
    if "closed" in s or "закрыт" in s:
        return "🟢"
    return "⚪"


def make_funpay_account(golden_key: str, user_agent: str | None = None) -> Account:
    try:
        account = Account(golden_key, user_agent)
    except TypeError:
        account = Account(golden_key)
        if user_agent:
            account.user_agent = user_agent

    try:
        account.get(update_phpsessid=True)
    except TypeError:
        account.get()
    return account


def _get_sales_page(account: Account, start_from, locale, subcategories, state=None):
    kwargs = {
        "start_from": start_from or None,
        "locale": locale,
    }
    if state:
        kwargs["state"] = state
    try:
        return account.get_sales(**kwargs, sudcategories=subcategories)
    except TypeError:
        return account.get_sales(**kwargs, subcategories=subcategories)


def fetch_funpay_sales(account: Account, limit: int | None = None, state=None) -> list:
    """Получает продажи через get_sales, как в Cardinal-плагине, с fallback на get_sells."""
    if hasattr(account, "get_sales"):
        sales = []
        start_from = ""
        locale = None
        subcategories = None

        while start_from is not None and (limit is None or len(sales) < limit):
            last_error = None
            for attempt in range(3):
                try:
                    result = _get_sales_page(account, start_from, locale, subcategories, state=state)
                    break
                except Exception as e:
                    last_error = e
                    time.sleep(1)
            else:
                raise last_error

            start_from = result[0]
            page_sales = result[1]
            locale = result[2]
            subcategories = result[3]
            sales.extend(page_sales)

            if start_from is not None and (limit is None or len(sales) < limit):
                time.sleep(1)

        return sales[:limit] if limit else sales

    sales_raw = account.get_sells()
    sales = sales_raw[1] if isinstance(sales_raw, tuple) else sales_raw
    sales = list(sales)
    return sales[:limit] if limit else sales


def fetch_funpay_sales_window(account: Account, offset: int = 0, limit: int = SALES_BATCH_SIZE, state=None) -> list:
    """Получает окно продаж. API FunPay курсорный, поэтому до offset нужно пройти последовательно."""
    fetch_limit = max(offset + limit, limit)
    sales = fetch_funpay_sales(account, limit=fetch_limit, state=state)
    return sales[offset:offset + limit]


def find_funpay_sale(account: Account, order_id: str, max_depth: int = 3000, state=None):
    """Ищет заказ по ID без привязки к текущей странице."""
    needle = str(order_id).strip().lstrip("#").upper()
    if hasattr(account, "get_sales"):
        checked = 0
        start_from = ""
        locale = None
        subcategories = None

        while start_from is not None and checked < max_depth:
            last_error = None
            for attempt in range(3):
                try:
                    result = _get_sales_page(account, start_from, locale, subcategories, state=state)
                    break
                except Exception as e:
                    last_error = e
                    time.sleep(1)
            else:
                raise last_error

            start_from = result[0]
            page_sales = result[1]
            locale = result[2]
            subcategories = result[3]

            for sale in page_sales:
                if str(getattr(sale, "id", "")).upper() == needle:
                    return sale, checked
                checked += 1
                if checked >= max_depth:
                    break

            if start_from is not None and checked < max_depth:
                time.sleep(1)
        return None, checked

    sales = fetch_funpay_sales(account, limit=max_depth, state=state)
    for idx, sale in enumerate(sales):
        if str(getattr(sale, "id", "")).upper() == needle:
            return sale, idx
    return None, len(sales)


def extract_order_id(raw_text: str) -> str | None:
    text = (raw_text or "").strip()
    match = re.search(r'/orders/([A-Za-z0-9]+)/?', text)
    if not match:
        match = re.search(r'#?([A-Za-z0-9]{6,})', text)
    return match.group(1).upper() if match else None


def clean_price(raw_price):
    """Надёжно очищает цену от пробелов, неразрывных пробелов и запятых"""
    if not raw_price:
        return "0"
    return str(raw_price).replace(" ", "").replace("\xa0", "").replace(",", ".")


CASHBACK_LABELS = {"yes": "с кэшбеком", "no": "без кэшбека", "none": "нет кэшбека"}


def strip_min_order_terms(text: str) -> str:
    """Убирает из названия условия минимального заказа, чтобы они не влияли на подбор и количество."""
    text = text or ""
    text = re.sub(r'\[[^\]]*\bот\s+\d+[^\]]*\]', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'\([^\)]*\bот\s+\d+[^\)]*\)', ' ', text, flags=re.IGNORECASE)
    text = re.sub(
        r'\bот\s+\d+\s*(?:шт\.?|штук|[A-Za-zА-Яа-яЁё0-9]+)',
        ' ',
        text,
        flags=re.IGNORECASE,
    )
    return text


def extract_order_amount(product_name: str) -> int:
    """Парсит именно заказанное количество, игнорируя условия типа "от 100 шт" в названии."""
    product_for_qty = strip_min_order_terms(product_name)
    qty_matches = re.findall(r'([\d\s\xa0]+)\s*(?:шт\.?|штук)\b', product_for_qty or '', flags=re.IGNORECASE)
    if qty_matches:
        return int(re.sub(r'\D+', '', qty_matches[-1]))
    return 1


def get_auto_buy_prices(product_full_name: str, order_game: str = None, order_amount: int = 1) -> list:
    """Возвращает список всех совпадений из minprice (с учётом кэшбека)."""
    def certificate_matches() -> list:
        try:
            from handlers.certificates import get_certificate_auto_buy_prices
            return get_certificate_auto_buy_prices(product_full_name, order_game, order_amount)
        except Exception as e:
            print(f"[AUTO_BUY] certificates error: {e}")
            return []

    # Карта похожих латинских ↔ кириллических букв
    _LAT_TO_CYR = str.maketrans({
        'a': 'а', 'e': 'е', 'o': 'о', 'c': 'с', 'p': 'р', 'x': 'х',
        'y': 'у', 'b': 'в', 'k': 'к', 'm': 'м', 'h': 'н', 't': 'т',
        'A': 'А', 'E': 'Е', 'O': 'О', 'C': 'С', 'P': 'Р', 'X': 'Х',
        'Y': 'У', 'B': 'В', 'K': 'К', 'M': 'М', 'H': 'Н', 'T': 'Т',
    })

    def clean_text(text):
        text = re.sub(r'[^\w\s]', '', text).lower().strip()
        # Нормализуем латинские буквы к кириллическим (для смешанных слов типа "MC" / "МС")
        text = text.translate(_LAT_TO_CYR)
        return text

    def game_tokens(text):
        suffixes = (
            "иями", "ями", "ами", "ого", "его", "ыми", "ими", "ых", "их",
            "ая", "яя", "ое", "ее", "ые", "ие", "ой", "ей", "ах", "ях",
            "ам", "ям", "ом", "ем", "ов", "ев", "а", "я", "ы", "и", "е",
            "у", "ю",
        )
        tokens = []
        for word in clean_text(text).split():
            if len(word) <= 2:
                continue
            root = word
            for suffix in suffixes:
                if len(root) > len(suffix) + 2 and root.endswith(suffix):
                    root = root[:-len(suffix)]
                    break
            tokens.append(root)
        return set(tokens)

    product_for_match = strip_min_order_terms(product_full_name)
    cleaned_product = clean_text(product_for_match)
    quantity = order_amount if order_amount and order_amount > 1 else 1

    try:
        mp_path = f"users/{ADMIN_ID}/minprice.json"
        if not os.path.exists(mp_path):
            return certificate_matches()
        with open(mp_path, encoding="utf-8") as f:
            mp_data = json.load(f)

        if order_game:
            cleaned_order_game = clean_text(order_game)
            direct_games = {
                g: items for g, items in mp_data.items()
                if cleaned_order_game in clean_text(g) or clean_text(g) in cleaned_order_game
            }
            if direct_games:
                games_to_search = direct_games
            else:
                order_tokens = game_tokens(order_game)
                scored_games = []
                for game_name, items in mp_data.items():
                    overlap = order_tokens & game_tokens(game_name)
                    if overlap:
                        scored_games.append((len(overlap), game_name, items))
                if scored_games:
                    best_score = max(score for score, _, _ in scored_games)
                    games_to_search = {
                        game_name: items
                        for score, game_name, items in scored_games
                        if score == best_score
                    }
                else:
                    games_to_search = mp_data
        else:
            games_to_search = mp_data

        # Находим максимальную длину совпадения для КАЖДОЙ категории кэшбека отдельно
        # Так если "100 BC" с кэшбеком и "100 BC (Black Coin)" без кэшбека - возьмем оба
        best_len_by_cb = {}  # {cashback: max_length}
        for game_name, items in games_to_search.items():
            for item_id, item_info in items.items():
                if item_id == "_meta" or not isinstance(item_info, dict):
                    continue
                cleaned_item = clean_text(item_info.get("name", ""))
                if cleaned_item and cleaned_item in cleaned_product:
                    cb = item_info.get("cashback", "none")
                    if len(cleaned_item) > best_len_by_cb.get(cb, 0):
                        best_len_by_cb[cb] = len(cleaned_item)

        if not best_len_by_cb:
            return certificate_matches()

        # Собираем варианты с лучшим совпадением в каждой категории кэшбека
        results = []
        seen_variants = set()
        for game_name, items in games_to_search.items():
            for item_id, item_info in items.items():
                if item_id == "_meta" or not isinstance(item_info, dict):
                    continue
                item_name = item_info.get("name", "")
                item_cost = item_info.get("cost", 0)
                cashback = item_info.get("cashback", "none")
                cleaned_item = clean_text(item_name)
                if cleaned_item and cleaned_item in cleaned_product and len(cleaned_item) == best_len_by_cb.get(cashback, 0):
                    total_cost = round(item_cost * quantity, 2)
                    variant_key = (cleaned_item, cashback, total_cost)
                    if variant_key in seen_variants:
                        continue
                    seen_variants.add(variant_key)
                    results.append({
                        "title": item_name,
                        "cashback": cashback,
                        "cashback_label": CASHBACK_LABELS.get(cashback, ""),
                        "qty": quantity,
                        "total_cost": total_cost,
                        "game_name": game_name,
                    })
        return results + certificate_matches()
    except Exception as e:
        print(f"[AUTO_BUY] minprice error: {e}")

    return certificate_matches()


def get_auto_buy_price(product_full_name: str, order_game: str = None, order_amount: int = 1):
    """Обратная совместимость — возвращает первый результат или None."""
    results = get_auto_buy_prices(product_full_name, order_game, order_amount)
    return results[0] if results else None


def _order_card_text(s_id: str, product_name: str, final_price, cost_str: str,
                     auto_variants: list, buyer_username: str = "",
                     order_date: str = "", order_game: str | None = None) -> str:
    buyer_line = f"👤 Покупатель: <b>@{_html.escape(str(buyer_username))}</b>\n" if buyer_username else ""
    date_line = f"📅 Дата: <b>{_html.escape(str(order_date))}</b>\n" if order_date else ""
    game_line = f"🎮 Игра: <b>{_html.escape(str(order_game))}</b>\n" if order_game else ""

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
        f"💵 Продажа: <b>{_html.escape(str(final_price))} ₽</b>\n"
        f"📉 Себестоимость: <b>{_html.escape(str(cost_str))}</b>"
        f"{auto_text}"
    )


def is_funpay_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def _short_error(error: Exception, limit: int = 900) -> str:
    text = str(error).strip()
    if len(text) > limit:
        text = text[:limit] + "..."
    return _html.escape(text)


def funpay_auto_kb():
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="📊 Продажи FunPay", callback_data="fp_sales_0"))
    kb.row(types.InlineKeyboardButton(text="🎮 Минимальные цены", callback_data="mp_pg_0"))
    kb.row(types.InlineKeyboardButton(text="🎁 Сертификаты", callback_data="cert_menu"))
    kb.row(types.InlineKeyboardButton(text="📁 Демпинг Cardinal", callback_data="dmp_menu"))
    kb.row(types.InlineKeyboardButton(text="⚙️ Данные аккаунта", callback_data="fp_settings"))
    kb.row(types.InlineKeyboardButton(text="📝 Незаполненные заказы", callback_data="task_fill_unfilled"))
    return kb.as_markup()


async def show_funpay_auto_menu(target):
    text = (
        "🛸 <b>FunPay Auto</b>\n\n"
        "Выберите, что открыть:"
    )
    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=funpay_auto_kb(), parse_mode=ParseMode.HTML)
        await target.answer()
    else:
        await target.answer(text, reply_markup=funpay_auto_kb(), parse_mode=ParseMode.HTML)


# ====================== ХЕНДЛЕРЫ ======================

@router.message(Command("funpayauto", "funpayau"))
async def cmd_funpayauto(message: types.Message):
    if not is_funpay_admin(message.from_user.id):
        return await no_access_reply(message)

    await show_funpay_auto_menu(message)


@router.callback_query(F.data == "funpay_auto_main")
async def cb_funpay_auto_main(call: types.CallbackQuery, state: FSMContext):
    if not is_funpay_admin(call.from_user.id):
        return await no_access_callback(call)
    await state.clear()
    await show_funpay_auto_menu(call)


@router.callback_query(F.data == "fp_settings")
async def fp_settings(call: types.CallbackQuery):
    if not is_funpay_admin(call.from_user.id):
        return await no_access_callback(call)

    gk, ua = db.get_config()
    text = (f"🛠 <b>Настройки доступа</b>\n\n"
            f"<b>Golden Key:</b> <code>{gk[:8] if gk else '❌'}***</code>\n"
            f"<b>User-Agent:</b> <code>{ua[:15] if ua else '❌'}***</code>")

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="🔑 Golden Key", callback_data="edit_gk"))
    kb.row(types.InlineKeyboardButton(text="🌐 User Agent", callback_data="edit_ua"))
    kb.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="fp_main"))

    await call.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode=ParseMode.HTML)
    await call.answer()


@router.callback_query(F.data.startswith("fp_sales_"))
async def view_sales(call: types.CallbackQuery):
    if not is_funpay_admin(call.from_user.id):
        return await no_access_callback(call)

    page = int(call.data.split("_")[2])
    gk, ua = db.get_config()
    if not gk:
        return await call.answer("❌ Сначала настройте Golden Key!", show_alert=True)

    try:
        await call.answer("⏳ Загружаю продажи...")
        account = make_funpay_account(gk, ua)

        batch_index = page // SALES_PAGES_PER_BATCH
        page_in_batch = page % SALES_PAGES_PER_BATCH
        batch_offset = batch_index * SALES_BATCH_SIZE
        sales = fetch_funpay_sales_window(account, offset=batch_offset, limit=SALES_BATCH_SIZE)

        start, end = page_in_batch * SALES_PER_PAGE, (page_in_batch + 1) * SALES_PER_PAGE
        current_sales = sales[start:end]

        kb = InlineKeyboardBuilder()
        for s in current_sales:
            s_id = str(getattr(s, 'id', '???'))
            emoji = get_status_emoji(getattr(s, 'status', ''))
            has_cost = "✅" if db.get_prime_cost(s_id) else "❌"

            raw_price = getattr(s, 'price', getattr(s, 'amount', 0))
            price_str = clean_price(raw_price)

            btn_text = f"#{s_id} | {emoji} | {has_cost}"
            kb.row(types.InlineKeyboardButton(text=btn_text, callback_data=f"fpdet_{s_id}_{price_str}_{page}"))

        nav = []
        if page > 0:
            nav.append(types.InlineKeyboardButton(text="⬅️", callback_data=f"fp_sales_{page-1}"))
        nav.append(types.InlineKeyboardButton(text="🔄 Обновить", callback_data=f"fp_sales_{page}_update"))
        if len(sales) > end or len(sales) >= SALES_BATCH_SIZE:
            nav.append(types.InlineKeyboardButton(text="➡️", callback_data=f"fp_sales_{page+1}"))

        kb.row(*nav)
        kb.row(types.InlineKeyboardButton(text="🔎 Найти заказ", callback_data=f"fp_search_order_{page}"))
        kb.row(types.InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="fp_main"))

        if not current_sales:
            await call.message.edit_text(
                "📊 <b>Продажи FunPay</b>\n\n"
                f"Заказы не найдены в диапазоне <b>{batch_offset + 1}-{batch_offset + SALES_BATCH_SIZE}</b>.",
                reply_markup=kb.as_markup(),
                parse_mode=ParseMode.HTML
            )
            return

        await call.message.edit_text(
            f"📊 <b>Продажи FunPay</b>\n"
            f"Страница: <b>{page + 1}</b>\n"
            f"Диапазон: <b>{batch_offset + 1}-{batch_offset + len(sales)}</b>\n"
            f"Последнее обновление: <b>{format_date_now().split()[1]}</b>",
            reply_markup=kb.as_markup(),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        print(f"[FUNPAY SALES ERROR] {e}")
        await call.answer("Ошибка FunPay API. Подробности отправил сообщением.", show_alert=True)
        await call.message.answer(
            f"❌ <b>FunPay API не отдал список продаж</b>\n\n"
            f"<code>{_short_error(e)}</code>",
            parse_mode=ParseMode.HTML
        )


# --- ДЕТАЛИ ЗАКАЗА ---
@router.callback_query(F.data.startswith("fpdet_"))
async def order_info(call: types.CallbackQuery, state: FSMContext):
    if not is_funpay_admin(call.from_user.id):
        return await no_access_callback(call)

    data = call.data.split("_")
    s_id = data[1]
    api_price = clean_price(data[2])
    source_page = int(data[3]) if len(data) > 3 and data[3].isdigit() else 0
    await state.update_data(last_fp_sales_page=source_page)

    gk, ua = db.get_config()
    cost = db.get_prime_cost(s_id)

    product_name = "Загрузка..."
    order_status = ""
    buyer_username = ""
    order_date = ""
    order_game = None
    order_amount = 1

    try:
        account = make_funpay_account(gk, ua)
        batch_index = source_page // SALES_PAGES_PER_BATCH
        batch_offset = batch_index * SALES_BATCH_SIZE
        sales = fetch_funpay_sales_window(account, offset=batch_offset, limit=SALES_BATCH_SIZE)

        for s in sales:
            if str(getattr(s, 'id', '')) == s_id:
                product_name = getattr(s, 'product_name', getattr(s, 'description', 'Без названия'))
                order_status = str(getattr(s, 'status', '')).lower()
                raw_p = getattr(s, 'price', getattr(s, 'amount', api_price))
                api_price = clean_price(raw_p)
                buyer_username = getattr(s, 'buyer_username', getattr(s, 'buyer', ''))
                order_date = str(getattr(s, 'date', getattr(s, 'created_at', '')))

                order_amount = extract_order_amount(product_name)

                subcategory_name = getattr(s, 'subcategory_name', '') or ''
                if ',' in subcategory_name:
                    order_game = subcategory_name.rsplit(',', 1)[0].strip()
                break
    except Exception:
        product_name = "Ошибка API"

    # Кастомная цена из стэйта
    state_data = await state.get_data()
    custom_prices = state_data.get("custom_sell_prices", {})
    final_price = custom_prices.get(s_id, api_price)

    is_refunded = "refund" in order_status or "возврат" in order_status

    auto_variants = get_auto_buy_prices(product_name, order_game, order_amount) if not is_refunded else []

    cost_str = f"{cost} ₽" if cost else "не введена"

    text = _order_card_text(
        s_id=s_id,
        product_name=product_name,
        final_price=final_price,
        cost_str=cost_str,
        auto_variants=auto_variants,
        buyer_username=buyer_username,
        order_date=order_date,
        order_game=order_game,
    )
    if is_refunded:
        text += "\n\n⚠️ <b>По заказу сделан возврат.</b>"

    kb = InlineKeyboardBuilder()
    if not is_refunded:
        if auto_variants and not cost:
            for v in auto_variants:
                label = f"✅ {v['title']} ({v['cashback_label']}) — {v['total_cost']} ₽"
                kb.row(types.InlineKeyboardButton(
                    text=label,
                    callback_data=f"fast_save_{s_id}_{final_price}_{v['total_cost']}"
                ))
        if not cost:
            kb.row(types.InlineKeyboardButton(text="💸 Ввести закуп", callback_data=f"setc_{s_id}_{final_price}"))
        else:
            kb.row(types.InlineKeyboardButton(text="✏️ Изменить закуп", callback_data=f"setc_{s_id}_{final_price}"))
        kb.row(types.InlineKeyboardButton(text="✏️ Изменить цену продажи", callback_data=f"editsell_{s_id}"))

    kb.row(types.InlineKeyboardButton(text="⬅️ Обратно к заказам", callback_data=f"fp_sales_{source_page}"))

    await call.message.answer(text, reply_markup=kb.as_markup(), parse_mode=ParseMode.HTML)
    await call.answer()


@router.callback_query(F.data.startswith("fp_search_order_"))
async def start_order_search(call: types.CallbackQuery, state: FSMContext):
    if not is_funpay_admin(call.from_user.id):
        return await no_access_callback(call)

    page = int(call.data.split("_")[3])
    await state.update_data(last_fp_sales_page=page)
    await state.set_state(FPAdminStates.wait_order_search)
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"fp_sales_{page}"))
    await call.message.answer(
        "🔎 <b>Поиск заказа</b>\n\n"
        "Отправьте ссылку на заказ, номер с <code>#</code> или просто номер заказа.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.as_markup()
    )
    await call.answer()


@router.message(FPAdminStates.wait_order_search)
async def process_order_search(message: types.Message, state: FSMContext):
    if not is_funpay_admin(message.from_user.id):
        return await state.clear()

    order_id = extract_order_id(message.text or "")
    state_data = await state.get_data()

    if not order_id:
        return await message.answer(
            "⚠️ Не понял номер заказа. Отправьте ссылку FunPay, <code>#ZDS9J85S</code> или <code>ZDS9J85S</code>.",
            parse_mode=ParseMode.HTML
        )

    await state.set_state(None)

    gk, ua = db.get_config()
    if not gk:
        return await message.answer("❌ Golden Key не настроен.")

    progress = await message.answer(f"⏳ Ищу заказ <b>#{_html.escape(order_id)}</b>...", parse_mode=ParseMode.HTML)

    try:
        account = make_funpay_account(gk, ua)
        sale, index = find_funpay_sale(account, order_id)
    except Exception as e:
        return await progress.edit_text(
            f"❌ <b>Не удалось найти заказ</b>\n\n<code>{_short_error(e)}</code>",
            parse_mode=ParseMode.HTML
        )

    if not sale:
        return await progress.edit_text(
            f"❌ Заказ <b>#{_html.escape(order_id)}</b> не найден.\n\n"
            f"Проверено заказов: <b>{index}</b>.",
            parse_mode=ParseMode.HTML
        )

    source_page = index // SALES_PER_PAGE
    await state.update_data(last_fp_sales_page=source_page)

    s_id = str(getattr(sale, 'id', order_id))
    product_name = getattr(sale, 'product_name', getattr(sale, 'description', 'Без названия'))
    order_status = str(getattr(sale, 'status', '')).lower()
    raw_p = getattr(sale, 'price', getattr(sale, 'amount', 0))
    api_price = clean_price(raw_p)
    buyer_username = getattr(sale, 'buyer_username', getattr(sale, 'buyer', ''))
    order_date = str(getattr(sale, 'date', getattr(sale, 'created_at', '')))
    order_game = None
    subcategory_name = getattr(sale, 'subcategory_name', '') or ''
    if ',' in subcategory_name:
        order_game = subcategory_name.rsplit(',', 1)[0].strip()

    cost = db.get_prime_cost(s_id)
    custom_prices = state_data.get("custom_sell_prices", {})
    final_price = custom_prices.get(s_id, api_price)
    is_refunded = "refund" in order_status or "возврат" in order_status
    order_amount = extract_order_amount(product_name)
    auto_variants = get_auto_buy_prices(product_name, order_game, order_amount) if not is_refunded else []
    cost_str = f"{cost} ₽" if cost else "не введена"

    text = _order_card_text(
        s_id=s_id,
        product_name=product_name,
        final_price=final_price,
        cost_str=cost_str,
        auto_variants=auto_variants,
        buyer_username=buyer_username,
        order_date=order_date,
        order_game=order_game,
    )
    if is_refunded:
        text += "\n\n⚠️ <b>По заказу сделан возврат.</b>"

    kb = InlineKeyboardBuilder()
    if not is_refunded:
        if auto_variants and not cost:
            for v in auto_variants:
                label = f"✅ {v['title']} ({v['cashback_label']}) — {v['total_cost']} ₽"
                kb.row(types.InlineKeyboardButton(
                    text=label,
                    callback_data=f"fast_save_{s_id}_{final_price}_{v['total_cost']}"
                ))
        if not cost:
            kb.row(types.InlineKeyboardButton(text="💸 Ввести закуп", callback_data=f"setc_{s_id}_{final_price}"))
        else:
            kb.row(types.InlineKeyboardButton(text="✏️ Изменить закуп", callback_data=f"setc_{s_id}_{final_price}"))
        kb.row(types.InlineKeyboardButton(text="✏️ Изменить цену продажи", callback_data=f"editsell_{s_id}"))

    kb.row(types.InlineKeyboardButton(text="⬅️ Обратно к заказам", callback_data=f"fp_sales_{source_page}"))
    await progress.edit_text(text, reply_markup=kb.as_markup(), parse_mode=ParseMode.HTML)

# --- СМЕНА GOLDEN KEY ---
@router.callback_query(F.data == "edit_gk")
async def edit_gk(call: types.CallbackQuery, state: FSMContext):
    if not is_funpay_admin(call.from_user.id):
        return await no_access_callback(call)

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="❌ Отмена", callback_data="fp_settings"))

    await call.message.edit_text(
        "🔑 <b>Введите новый Golden Key:</b>\n\n"
        "Его можно найти в куках браузера на FunPay (cookie <code>golden_key</code>)",
        reply_markup=kb.as_markup(),
        parse_mode=ParseMode.HTML
    )
    await state.set_state(FPAdminStates.wait_gk)
    await call.answer()


@router.message(FPAdminStates.wait_gk)
async def process_gk(message: types.Message, state: FSMContext):
    if not is_funpay_admin(message.from_user.id):
        return await state.clear()

    new_gk = message.text.strip()
    db.update_config(gk=new_gk)  # ← правильный метод
    await state.clear()
    await message.answer("✅ <b>Golden Key успешно обновлён!</b>", parse_mode=ParseMode.HTML)


# --- СМЕНА USER AGENT ---
@router.callback_query(F.data == "edit_ua")
async def edit_ua(call: types.CallbackQuery, state: FSMContext):
    if not is_funpay_admin(call.from_user.id):
        return await no_access_callback(call)

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="❌ Отмена", callback_data="fp_settings"))

    await call.message.edit_text(
        "🌐 <b>Введите новый User-Agent:</b>\n\n"
        "Пример:\n<code>Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36</code>",
        reply_markup=kb.as_markup(),
        parse_mode=ParseMode.HTML
    )
    await state.set_state(FPAdminStates.wait_ua)
    await call.answer()


@router.message(FPAdminStates.wait_ua)
async def process_ua(message: types.Message, state: FSMContext):
    if not is_funpay_admin(message.from_user.id):
        return await state.clear()

    new_ua = message.text.strip()
    db.update_config(ua=new_ua)  # ← правильный метод
    await state.clear()
    await message.answer("✅ <b>User-Agent успешно обновлён!</b>", parse_mode=ParseMode.HTML)

# === ЛОГИКА ИЗМЕНЕНИЯ ЦЕНЫ ПРОДАЖИ ===
@router.callback_query(F.data.startswith("editsell_"))
async def start_edit_sell(call: types.CallbackQuery, state: FSMContext):
    if not is_funpay_admin(call.from_user.id):
        return await no_access_callback(call)
        
    s_id = call.data.split("_")[1]
    await state.update_data(edit_order_id=s_id)
    
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="Отмена", callback_data="delete_msg"))
    
    await call.message.answer(
        f"✏️ <b>Исправление цены для заказа #{s_id}</b>\n\n"
        f"Библиотека API отдала кривую цену.\n"
        f"Пожалуйста, отправьте <b>правильную цену продажи</b> в чат (например: <code>10348.05</code>):",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.as_markup()
    )
    await state.set_state(FPAdminStates.wait_sell_price)
    await call.answer()


@router.message(FPAdminStates.wait_sell_price)
async def process_edit_sell(message: types.Message, state: FSMContext):
    if not is_funpay_admin(message.from_user.id):
        return await state.clear()
        
    try:
        val_str = message.text.replace(",", ".").replace(" ", "").replace("\xa0", "")
        val = float(val_str)
    except ValueError:
        return await message.answer("⚠️ Пожалуйста, отправьте число (можно с точкой)!")
        
    data = await state.get_data()
    s_id = data.get('edit_order_id')
    if not s_id:
        await state.clear()
        return await message.answer("Ошибка: ID заказа потерян.")
        
    # Сохраняем кастомную цену продажи в память стэйта
    custom_prices = data.get("custom_sell_prices", {})
    custom_prices[s_id] = val
    await state.update_data(custom_sell_prices=custom_prices)
    
    # Очищаем состояние
    await state.set_state(None)
    
    kb = InlineKeyboardBuilder()
    sales_page = data.get("last_fp_sales_page", 0)
    kb.row(types.InlineKeyboardButton(text=f"➡️ Вернуться к заказу #{s_id}", callback_data=f"fpdet_{s_id}_{val}_{sales_page}"))
    
    await message.answer(
        f"✅ <b>Цена продажи успешно обновлена на:</b> {val} ₽\n\n"
        f"Нажмите кнопку ниже, чтобы продолжить.",
        reply_markup=kb.as_markup(),
        parse_mode=ParseMode.HTML
    )


# --- АВТО-ЗАКУП ---
@router.callback_query(F.data.startswith("fast_save_"))
async def fast_save_cost(call: types.CallbackQuery):
    if not is_funpay_admin(call.from_user.id):
        return await no_access_callback(call)

    params = call.data.split("_")
    order_id = params[2]
    
    sell_price = float(params[3].replace(" ", "").replace("\xa0", ""))
    buy_price = float(str(params[4]).replace(" ", "").replace("\xa0", ""))

    profit = (sell_price * 0.97) - buy_price
    db.set_prime_cost(order_id, buy_price)

    user_id = call.from_user.id
    profits = load_profits(user_id)

    existing_idx = next(
        (i for i, p in enumerate(profits) if f"FP #{order_id}" in str(p.get('type', ''))),
        None
    )
    new_entry = {
        "type": f"FP #{order_id} (AUTO)",
        "buy_price": round(buy_price, 2),
        "sell_price": round(sell_price, 2),
        "profit": round(profit, 2),
        "date": format_date_now()
    }
    if existing_idx is not None:
        new_entry["date"] = profits[existing_idx].get("date", format_date_now())
        profits[existing_idx] = new_entry
    else:
        profits.append(new_entry)
    save_profits(user_id, profits)

    await call.message.edit_text(f"✅ Заказ #{order_id} сохранен!\nПрибыль: <b>{profit:.2f} ₽</b>", parse_mode=ParseMode.HTML)
    await call.answer()


# --- РУЧНОЙ ВВОД ЗАКУПА ---
@router.callback_query(F.data.startswith("setc_"))
async def start_cost(call: types.CallbackQuery, state: FSMContext):
    if not is_funpay_admin(call.from_user.id):
        return await no_access_callback(call)

    data = call.data.split("_")
    s_id, price = data[1], data[2]
    # Сохраняем message_id заказа чтобы потом удалить
    await state.update_data(
        order_id=s_id,
        price=price,
        order_message_id=call.message.message_id,
        order_chat_id=call.message.chat.id
    )
    await call.message.answer(f"Введите ЗАКУП (себестоимость) для заказа #{s_id}:")
    await state.set_state(FPAdminStates.wait_cost)
    await call.answer()


@router.message(FPAdminStates.wait_cost)
async def process_cost(message: types.Message, state: FSMContext):
    if not is_funpay_admin(message.from_user.id):
        return await state.clear()

    try:
        buy_price = float(message.text.replace(",", "."))
        data = await state.get_data()
        order_id, sell_price = data['order_id'], float(data['price'])

        profit = (sell_price * 0.97) - buy_price
        db.set_prime_cost(order_id, buy_price)

        profits = load_profits(message.from_user.id)

        # Ищем существующую запись по этому заказу
        existing_idx = next(
            (i for i, p in enumerate(profits) if f"FP #{order_id}" in str(p.get('type', ''))),
            None
        )

        new_entry = {
            "type": f"FP #{order_id}",
            "buy_price": round(buy_price, 2),
            "sell_price": round(sell_price, 2),
            "profit": round(profit, 2),
            "date": format_date_now()
        }

        if existing_idx is not None:
            new_entry["date"] = profits[existing_idx].get("date", format_date_now())
            profits[existing_idx] = new_entry
            result_text = f"✏️ <b>Заказ #{order_id} обновлён!</b>\nПрибыль: <b>{profit:.2f} ₽</b>"
        else:
            profits.append(new_entry)
            result_text = f"✅ <b>Заказ #{order_id} сохранён!</b>\nПрибыль: <b>{profit:.2f} ₽</b>"

        save_profits(message.from_user.id, profits)

        # Удаляем оригинальное сообщение с заказом и сообщение пользователя
        order_msg_id = data.get('order_message_id')
        order_chat_id = data.get('order_chat_id')
        if order_msg_id and order_chat_id:
            try:
                await message.bot.delete_message(order_chat_id, order_msg_id)
            except Exception:
                pass
        try:
            await message.delete()
        except Exception:
            pass

        await message.answer(result_text, parse_mode=ParseMode.HTML)
        await state.clear()
    except ValueError:
        await message.answer("⚠️ Введите число!")


@router.callback_query(F.data == "delete_msg")
async def delete_msg(call: types.CallbackQuery):
    await call.message.delete()
    await call.answer()


@router.callback_query(F.data == "fp_main")
async def back_main(call: types.CallbackQuery, state: FSMContext):
    if not is_funpay_admin(call.from_user.id):
        return await no_access_callback(call)

    await state.clear()
    await show_funpay_auto_menu(call)
