# handlers/minprice.py

import os
import re
import json
import asyncio
import html as _html
import hashlib
import base64
import secrets
import requests as _req
from io import BytesIO

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from groq import Groq

from loader import is_authorized
from handlers.utils import no_access_reply, no_access_callback
from handlers.demping import load_demping, DEMPING_FILE
from config import ADMIN_ID, FP_TOKEN
from handlers.ai_runtime import get_groq_api_key, get_openrouter_api_key
from database import db
from FunPayAPI import Account

groq_client = None

# OpenRouter - OpenAI-совместимый клиент для fallback (бесплатные модели и платные)
try:
    from openai import OpenAI
    openrouter_client = None
except ImportError:
    openrouter_client = None
    print("[INIT] openai пакет не установлен, OpenRouter fallback недоступен")

router = Router()

COMMISSION = 0.03
MIN_PROFIT = 0.01
GAMES_PER_PAGE = 10
ITEMS_PER_PAGE = 20

CASHBACK_OPTIONS = {
    "yes":  "с кэшбеком",
    "no":   "без кэшбека",
    "none": "нет кэшбека",
}

CASHBACK_EMOJI = {
    "yes":  "💳",
    "no":   "💵",
    "none": "—",
}


def _money(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _short(text: str, limit: int = 54) -> str:
    text = str(text or "").strip()
    return (text[:limit - 1] + "…") if len(text) > limit else text


def _cashback_badge(cashback_key: str) -> str:
    label = CASHBACK_OPTIONS.get(cashback_key, "нет кэшбека")
    icon = CASHBACK_EMOJI.get(cashback_key, "—")
    return f"{icon} {label}" if icon != "—" else label


def _offer_links(offer_ids: list) -> str:
    if not offer_ids:
        return "—"
    return ", ".join(
        f'<a href="https://funpay.com/lots/offer?id={oid}">#{oid}</a>'
        for oid in offer_ids
    )


def _items_list(items: dict) -> list:
    return [(k, v) for k, v in items.items() if k != "_meta" and isinstance(v, dict)]


def build_games_text(games: list, mp: dict) -> str:
    total_items = 0
    linked_items = 0
    rates_count = 0

    for game_name in games:
        items = get_items(mp, game_name)
        item_list = _items_list(items)
        total_items += len(item_list)
        linked_items += sum(1 for _, info in item_list if get_item_offer_ids(info))
        if get_game_meta(mp, game_name).get("sbp_rate"):
            rates_count += 1

    text = (
        f"🎮 <b>Минимальные цены</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"🧩 Игр: <b>{len(games)}</b>\n"
        f"📦 Товаров: <b>{total_items}</b>\n"
        f"🔗 С лотами: <b>{linked_items}</b>\n"
        f"📈 Ставок СБП: <b>{rates_count}</b>"
    )
    if not games:
        text += "\n\n📭 <i>Игр пока нет. Добавьте первую!</i>"
    else:
        text += "\n\n<i>Выберите игру ниже.</i>"
    return text


def build_edit_item_text(name: str, cost, min_price, cashback_key: str, offer_ids: list) -> str:
    cost = _money(cost)
    min_price = _money(min_price)
    ids_display = _offer_links(offer_ids)
    lots_count = len(offer_ids)

    return (
        f"✏️ <b>Товар</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"📦 <b>{_html.escape(str(name))}</b>\n\n"
        f"💸 Закуп: <code>{cost:.2f} ₽</code>\n"
        f"💰 Мин. цена: <code>{min_price:.2f} ₽</code>\n"
        f"💳 Кэшбек: <code>{_html.escape(_cashback_badge(cashback_key))}</code>\n"
        f"🔗 Лоты ({lots_count}): {ids_display}\n\n"
        f"<i>Выберите, что изменить.</i>"
    )


# ====================== СОСТОЯНИЯ ======================

class MinPriceStates(StatesGroup):
    waiting_add_game = State()
    waiting_rename_game = State()
    waiting_add_item_name = State()
    waiting_add_item_cost = State()
    waiting_cashback = State()
    waiting_photo_import = State()
    waiting_bulk_cost = State()
    waiting_sbp_rate = State()
    waiting_edit_items = State()
    waiting_manual_offer_id = State()
    waiting_edit_item_select = State()
    waiting_edit_param_value = State()


# ====================== ХРАНИЛИЩЕ (per-user) ======================

def get_mp_file(user_id: int) -> str:
    path = f"users/{user_id}"
    os.makedirs(path, exist_ok=True)
    return f"{path}/minprice.json"


def load_mp(user_id: int) -> dict:
    path = get_mp_file(user_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        changed = False
        for game_data in data.values():
            if not isinstance(game_data, dict):
                continue
            for item_id, info in game_data.items():
                if item_id == "_meta" or not isinstance(info, dict) or "cost" not in info:
                    continue
                try:
                    cost = float(info.get("cost") or 0)
                except (TypeError, ValueError):
                    continue
                expected_min = round((cost + MIN_PROFIT) / (1 - COMMISSION), 2)
                if info.get("min_price") != expected_min:
                    info["min_price"] = expected_min
                    changed = True
        if changed:
            save_mp(user_id, data)
        return data
    except Exception:
        return {}


def save_mp(user_id: int, data: dict):
    with open(get_mp_file(user_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:8]


def get_item_offer_ids(info: dict) -> list:
    """Возвращает список offer_ids из info, поддерживая старый формат (offer_id)"""
    if not isinstance(info, dict):
        return []
    offer_ids = info.get("offer_ids", [])
    if not offer_ids and info.get("offer_id"):
        offer_ids = [info["offer_id"]]
    return offer_ids


def add_offer_id_to_item(info: dict, offer_id: int):
    """Добавляет offer_id в список offer_ids товара (мигрирует со старого формата)"""
    current = get_item_offer_ids(info)
    if offer_id not in current:
        current.append(offer_id)
    info["offer_ids"] = current
    if "offer_id" in info:
        del info["offer_id"]


def calc_min_price(cost: float) -> float:
    return round((cost + MIN_PROFIT) / (1 - COMMISSION), 2)


def strip_leading_emoji(text: str) -> str:
    return re.sub(r'^[^\w\d\(]+', '', text, flags=re.UNICODE).strip()


def get_items(mp: dict, game_name: str) -> dict:
    return {k: v for k, v in mp.get(game_name, {}).items() if k != "_meta"}


def get_game_meta(mp: dict, game_name: str) -> dict:
    return mp.get(game_name, {}).get("_meta", {})


def set_game_meta(mp: dict, game_name: str, meta: dict):
    if game_name not in mp:
        mp[game_name] = {}
    mp[game_name]["_meta"] = meta


# ====================== ПОИСК СТАВКИ СБП ======================

def _parse_sbp_rate_from_offer_html(html: str) -> float | None:
    seller_price = None
    m = re.search(r'name=["\']price["\'][^>]*value=["\']([0-9.]+)["\']', html)
    if not m:
        m = re.search(r'value=["\']([0-9.]+)["\'][^>]*name=["\']price["\']', html)
    if m:
        seller_price = float(m.group(1))

    sbp_price = None
    m = re.search(r'СБП[^<]*</th>\s*<td>([0-9][0-9\s]*[.,][0-9]+)', html)
    if m:
        sbp_price = float(m.group(1).replace(' ', '').replace(',', '.'))

    if seller_price and sbp_price and seller_price > 0:
        return round(sbp_price / seller_price, 6)
    return None


def _sync_fetch_sbp_rate(game_name: str) -> tuple:
    """Ищет лот на FunPay и возвращает (lot_id, sbp_rate)"""
    try:
        from FunPayAPI import Account
        gk, ua = db.get_config()
        if not gk:
            return None, None

        session = _req.Session()
        session.cookies.set("golden_key", gk, domain=".funpay.com")
        session.headers["User-Agent"] = ua or "Mozilla/5.0"

        acc = Account(gk)
        if ua:
            acc.user_agent = ua
        acc.get()

        # Шаг 1: находим node_id игры через страницу профиля
        r = session.get(f"https://funpay.com/users/{acc.id}/", timeout=10)
        profile_html = r.text

        pairs = re.findall(r'<h3><a href="https://funpay\.com/lots/(\d+)/">([^<]+)</a></h3>', profile_html)
        game_lower = game_name.lower()
        node_ids = []
        for nid, title in pairs:
            t = title.strip().lower()
            if game_lower == t or game_lower in t or t in game_lower:
                node_ids.append(int(nid))

        if not node_ids:
            print(f"[MINPRICE] '{game_name}' не найдена в профиле")
            return None, None

        last_lot_id = None
        checked = 0

        for node_id in node_ids:
            # Шаг 2: находим offer_id через страницу trade
            r = session.get(f"https://funpay.com/lots/{node_id}/trade", timeout=10)
            matches = re.findall(r'offerEdit\?node=\d+&(?:amp;)?offer=(\d+)', r.text)
            offer_ids = []
            for oid in matches:
                if oid not in offer_ids:
                    offer_ids.append(oid)

            if not offer_ids:
                print(f"[MINPRICE] Офферы не найдены для node={node_id}")
                continue

            # Шаг 3: проверяем не один, а несколько офферов. В некоторых категориях
            # первый лот может не отдавать СБП, хотя следующий уже отдаёт.
            for raw_lot_id in offer_ids[:12]:
                lot_id = int(raw_lot_id)
                last_lot_id = lot_id
                checked += 1
                r = session.get(f"https://funpay.com/lots/offerEdit?offer={lot_id}", timeout=10)
                rate = _parse_sbp_rate_from_offer_html(r.text)
                if rate is not None:
                    if checked > 1:
                        print(f"[MINPRICE] '{game_name}' СБП найден на offer={lot_id}, проверено офферов: {checked}")
                    return lot_id, rate

        return last_lot_id, None

    except Exception as e:
        print(f"[MINPRICE RATE ERROR] {e}")
        return None, None


async def fetch_sbp_rate(game_name: str) -> tuple:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_fetch_sbp_rate, game_name)


async def fetch_sbp_rate_with_retries(game_name: str, attempts: int = 5) -> tuple:
    """Ищет lot_id и коэффициент СБП с повторами, если FunPay временно не отдал данные."""
    for attempt in range(1, attempts + 1):
        lot_id, rate = await fetch_sbp_rate(game_name)
        if lot_id and rate is not None:
            if attempt > 1:
                print(f"[SBP-CHECK] '{game_name}' найден через поиск с попытки {attempt}")
            return lot_id, rate
        if attempt < attempts:
            await asyncio.sleep(min(2 * attempt, 8))
    return None, None


async def resolve_sbp_rate_for_game(gk: str, game_name: str, lot_id=None, attempts: int = 5) -> tuple:
    """Возвращает (lot_id, rate), сначала по сохранённому лоту, потом через поиск игры."""
    if lot_id:
        rate = await check_rate_for_lot_with_retries(gk, lot_id, attempts=attempts)
        if rate is not None:
            return lot_id, rate
    return await fetch_sbp_rate_with_retries(game_name, attempts=attempts)


SBP_CHANGES_FILE = "data/sbp_changes_today.json"


def save_sbp_check_result(changes=None, unchanged=None, errors=None):
    os.makedirs(os.path.dirname(SBP_CHANGES_FILE), exist_ok=True)
    with open(SBP_CHANGES_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "date": __import__("datetime").datetime.now().strftime("%Y-%m-%d"),
            "changes": changes or [],
            "unchanged": unchanged or [],
            "errors": errors or [],
        }, f, ensure_ascii=False, indent=2)


async def check_sbp_rates_for_admin():
    """Проверяет актуальность ставок СБП для всех игр админа.
    Использует сохранённый lot_id - только 1 запрос на игру.
    Последовательно с паузами чтобы не получить 429 от FunPay.
    """
    import time
    start = time.time()
    print("[SBP-CHECK] Старт проверки коэффициентов СБП")

    try:
        mp = load_mp(ADMIN_ID)
    except Exception as e:
        print(f"[SBP-CHECK] Не удалось загрузить minprice: {e}")
        save_sbp_check_result(errors=[f"Не удалось загрузить minprice: {e}"])
        return

    try:
        from handlers.certificates import load_certificates, save_certificates
        certs = load_certificates(ADMIN_ID)
    except Exception as e:
        print(f"[SBP-CHECK] Не удалось загрузить сертификаты: {e}")
        certs = {}

    # Берём golden key один раз (без параллелизма к sqlite)
    try:
        gk, _ = db.get_config()
    except Exception as e:
        print(f"[SBP-CHECK] Ошибка получения golden_key: {e}")
        save_sbp_check_result(errors=[f"Ошибка получения golden_key: {e}"])
        return

    if not gk:
        print("[SBP-CHECK] Golden Key не настроен")
        save_sbp_check_result(errors=["Golden Key не настроен"])
        return

    # Собираем игры с сохранённым коэффициентом. Если lot_id потерялся,
    # попробуем найти его заново по названию игры.
    to_check = []
    for game_name, game_data in mp.items():
        meta = game_data.get("_meta", {})
        old_rate = meta.get("sbp_rate")
        lot_id = meta.get("lot_id")
        if old_rate:
            to_check.append(("minprice", game_name, old_rate, lot_id))

    for game_name, game_data in certs.items():
        meta = game_data.get("_meta", {})
        old_rate = meta.get("rate")
        lot_id = meta.get("lot_id")
        if old_rate:
            to_check.append(("certificates", game_name, old_rate, lot_id))

    print(f"[SBP-CHECK] Игр для проверки: {len(to_check)}")

    changes = []
    unchanged = []
    errors = []

    for i, (source, game_name, old_rate, lot_id) in enumerate(to_check):
        lot_id, new_rate = await resolve_sbp_rate_for_game(gk, game_name, lot_id, attempts=5)
        display_name = f"🎁 {game_name}" if source == "certificates" else game_name

        if new_rate is None:
            errors.append(display_name)
        else:
            if source == "certificates":
                certs[game_name]["_meta"]["latest_checked_rate"] = new_rate
                certs[game_name]["_meta"]["lot_id"] = lot_id
            else:
                mp[game_name]["_meta"]["latest_checked_rate"] = new_rate
                mp[game_name]["_meta"]["lot_id"] = lot_id
            if round(old_rate, 4) != round(new_rate, 4):
                changes.append({"name": display_name, "old": old_rate, "new": new_rate})
            else:
                unchanged.append(display_name)

        # Пауза между запросами чтобы избежать rate limit (≈0.5с)
        if i < len(to_check) - 1:
            await asyncio.sleep(0.5)

    save_mp(ADMIN_ID, mp)
    if certs:
        try:
            save_certificates(certs, ADMIN_ID)
        except Exception as e:
            print(f"[SBP-CHECK] Не удалось сохранить сертификаты: {e}")
    elapsed = time.time() - start
    print(f"[SBP-CHECK] Проверка завершена за {elapsed:.1f}с: изм={len(changes)}, без_изм={len(unchanged)}, err={len(errors)}")

    # Сохраняем результат в файл для использования в отчёте
    try:
        save_sbp_check_result(changes=changes, unchanged=unchanged, errors=errors)
        print(f"[SBP-CHECK] Готово: изменено {len(changes)}, без изм. {len(unchanged)}, ошибок {len(errors)}")
    except Exception as e:
        print(f"[SBP-CHECK] Не удалось сохранить файл: {e}")


def load_sbp_changes_today() -> dict:
    """Загружает данные о изменениях коэффициентов за сегодня"""
    if not os.path.exists(SBP_CHANGES_FILE):
        return {}
    try:
        with open(SBP_CHANGES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Проверяем что файл за сегодня
        today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
        if data.get("date") != today:
            return {}
        return data
    except Exception:
        return {}


FUNPAY_USER_ID = 8632814  # ID пользователя на FunPay


async def _get_user_lots(game_name: str) -> dict:
    """Получает все лоты игры со страницы публичного профиля FunPay"""
    try:
        import requests as req
        session = req.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })

        # Получаем публичную страницу профиля
        url = f"https://funpay.com/users/{FUNPAY_USER_ID}/"
        r = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: session.get(url, timeout=15)
        )
        profile_html = r.text
        print(f"[AUTO-LINK] Профиль скачан, размер: {len(profile_html)} символов")

        # На публичной странице профиля каждая игра имеет заголовок и таблицу лотов
        # Структура:
        # <div class="offer-list-title">
        #   <h3><a href="https://funpay.com/lots/NODE_ID/">Game Name</a></h3>
        # </div>
        # <a href="https://funpay.com/lots/offer?id=OFFER_ID" ...>
        #   ... lot description ...
        # </a>

        # Находим все секции игр
        # Каждая секция: заголовок игры + лоты до следующей секции
        sections = re.split(r'<div[^>]*class="offer-list-title"[^>]*>', profile_html)

        game_lower = game_name.lower()
        target_section = None

        for section in sections[1:]:  # Пропускаем первую часть (до первой секции)
            # Извлекаем название игры из этой секции
            title_match = re.search(r'<h3><a[^>]*>([^<]+)</a></h3>', section)
            if not title_match:
                continue

            section_title = title_match.group(1).strip().lower()
            if game_lower == section_title or game_lower in section_title or section_title in game_lower:
                target_section = section
                print(f"[AUTO-LINK] Найдена секция '{title_match.group(1).strip()}'")
                break

        if not target_section:
            print(f"[AUTO-LINK] Игра '{game_name}' не найдена в профиле")
            # Дебаг: вывести все найденные игры
            all_titles = re.findall(r'<h3><a[^>]*>([^<]+)</a></h3>', profile_html)
            print(f"[AUTO-LINK] Найденные игры в профиле: {all_titles[:20]}")
            return {}

        # Парсим лоты из target_section
        # Каждый лот: <a href="https://funpay.com/lots/offer?id=OFFER_ID" ... > или /lots/offer?id=
        # Также может быть формат: tc-item с data-offer
        lot_patterns = [
            r'href="https://funpay\.com/lots/offer\?id=(\d+)"[^>]*>([^<]*(?:<[^>]+>[^<]*)*)</a>',
            r'/lots/offer\?id=(\d+)"',
            r'data-offer="(\d+)"',
        ]

        lots = {}

        # Сначала попробуем найти лоты с названиями
        offer_blocks = re.findall(
            r'<a[^>]+href="[^"]*offer\?id=(\d+)"[^>]*>(.*?)</a>',
            target_section,
            re.DOTALL
        )

        if offer_blocks:
            for offer_id, content in offer_blocks:
                # Извлекаем сервер (если есть)
                server = ""
                server_match = re.search(r'<div[^>]*class="[^"]*tc-server[^"]*"[^>]*>(.*?)</div>', content, re.DOTALL)
                if server_match:
                    server = re.sub(r'<[^>]+>', '', server_match.group(1)).strip()

                # Извлекаем описание из tc-desc-text или tc-desc
                desc_text = ""
                desc_match = re.search(r'<div[^>]*class="[^"]*tc-desc-text[^"]*"[^>]*>(.*?)</div>', content, re.DOTALL)
                if not desc_match:
                    desc_match = re.search(r'class="[^"]*tc-desc[^"]*"[^>]*>(.*?)(?:<div[^>]*class="tc-price|</a>)', content, re.DOTALL)
                if desc_match:
                    desc_text = re.sub(r'<[^>]+>', ' ', desc_match.group(1)).strip()
                    desc_text = re.sub(r'\s+', ' ', desc_text)

                # Если описание не нашлось, берём весь текст контента и убираем сервер
                if not desc_text:
                    full_text = re.sub(r'<[^>]+>', ' ', content)
                    full_text = re.sub(r'\s+', ' ', full_text).strip()
                    # Убираем повтор сервера в начале
                    if server and full_text.startswith(server):
                        full_text = full_text[len(server):].strip()
                    desc_text = full_text[:200] if full_text else ""

                # Финальное название с сервером и описанием
                if server and desc_text:
                    lot_name = f"[{server}] {desc_text}"
                elif desc_text:
                    lot_name = desc_text
                elif server:
                    lot_name = f"[{server}] {game_name}"
                else:
                    lot_name = game_name

                lots[int(offer_id)] = {"name": lot_name}
        else:
            # Простой вариант - только offer_id'шки
            simple_ids = re.findall(r'/lots/offer\?id=(\d+)', target_section)
            for oid in set(simple_ids):
                lots[int(oid)] = {"name": game_name}

        if not lots:
            print(f"[AUTO-LINK] Лоты не найдены в секции игры '{game_name}'")
            print(f"[AUTO-LINK] Размер секции: {len(target_section)} символов")
            return {}

        print(f"[AUTO-LINK] Найдено {len(lots)} лотов для игры '{game_name}'")
        for oid, info in list(lots.items())[:5]:
            print(f"  #{oid}: {info['name'][:80]}")

        return lots

    except Exception as e:
        print(f"[AUTO-LINK ERROR] {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return {}


async def _match_offers_with_ai(game_name: str, lots_dict: dict, items_dict: dict) -> dict:
    """Использует ИИ для сопоставления лотов с товарами. Возвращает {item_name: [offer_id, ...]}"""
    if not lots_dict or not items_dict:
        return {}

    # Извлекаем названия товаров с кэшбеком из items_dict
    item_names = []
    for item_id, info in items_dict.items():
        if isinstance(info, dict):
            name = info.get("name", "")
            cashback = info.get("cashback", "none")
            cashback_label = CASHBACK_OPTIONS.get(cashback, "")
            if name:
                full_name = f"{name} ({cashback_label})" if cashback_label else name
                item_names.append(full_name)

    if not item_names:
        return {}

    lots_text = "\n".join([f"- #{oid}: {lot['name']}" for oid, lot in lots_dict.items()])
    items_text = "\n".join([f"- {name}" for name in item_names])

    prompt = f"""Игра: {game_name}

Лоты со скрейпа профиля FunPay:
{lots_text}

Товары в боте (из минпрайса):
{items_text}

ЗАДАЧА: Для каждого товара из бота найти подходящие лоты на сайте.

ПРАВИЛА:
1. Сопоставляй по ключевой части названия (число + название, или название набора).
   "100 бонусов" в боте = лоты "100 бонусов" на сайте.
   "Набор Олигарх" в боте = лоты "Набор Олигарх" на сайте.
   "Бонусы" в боте = лот "Бонусы [Цена за 1 шт.]" на сайте.

2. Один товар = ВСЕ лоты с таким же ключевым названием/числом.
   Пример: "100 бонусов" → "100 бонусов [Сервер1]" + "100 бонусов [Сервер2]"

3. НИКОГДА не привязывай к товару лоты с другим числом или другим названием набора!
   "100 бонусов" ≠ "200 бонусов", "Набор Олигарх" ≠ "Набор Хватит на все"

4. Если в названии есть число - оно ДОЛЖНО совпадать точно.
   "1 Бриллиант и более" ≠ другие количества бриллиантов!

5. Для каждого товара есть варианты с кэшбеком и без - используй ОДНИ И ТЕ ЖЕ offer_id:
   "100 бонусов (с кэшбеком)" и "100 бонусов (без кэшбека)" → одинаковые offer_id

6. Названия наборов в кавычках, без числа - сопоставляй по содержимому кавычек:
   "Набор Олигарх" соответствует лоту с текстом "Олигарх"

ФОРМАТ (строго одна строка на товар, в КОНЦЕ → список offer_id через запятую):
полное_название_товара_из_бота → offer_id1, offer_id2

Пример идеального ответа:
100 бонусов (без кэшбека) → 47107842
Набор "Олигарх" (без кэшбека) → 47125000
Бонусы (без кэшбека) → 47125555
не_найденный_товар (без кэшбека) → не найдено

ВАЖНО: попытайся найти КАЖДЫЙ товар. Используй "не найдено" только если совсем ничего не подходит.
Не добавляй ничего лишнего!"""

    # Список провайдеров по приоритету: (provider, model_name)
    providers = [
        ("groq", "llama-3.3-70b-versatile"),
        ("openrouter", "meta-llama/llama-3.3-70b-instruct:free"),
        ("openrouter", "nousresearch/hermes-3-llama-3.1-405b:free"),
        ("openrouter", "qwen/qwen3-next-80b-a3b-instruct:free"),
        ("openrouter", "openai/gpt-oss-120b:free"),
        ("openrouter", "deepseek/deepseek-v4-flash:free"),
        ("groq", "llama-3.1-8b-instant"),
    ]

    response = None
    last_error = None
    for provider, model_name in providers:
        try:
            if provider == "groq":
                resp = _get_groq_client().chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4000,
                    temperature=0.1,
                    timeout=60
                )
            elif provider == "openrouter":
                if not _get_openrouter_client():
                    continue
                resp = _get_openrouter_client().chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4000,
                    temperature=0.1,
                    timeout=60
                )
            else:
                continue

            # Проверяем что ответ не пустой
            content = resp.choices[0].message.content if resp.choices else None
            if not content:
                print(f"[AUTO-LINK AI] {provider}/{model_name} вернул пустой ответ, пробую следующую")
                continue

            response = resp
            print(f"[AUTO-LINK AI] Использована модель: {provider}/{model_name}")
            break
        except Exception as e:
            print(f"[AUTO-LINK AI] Ошибка {provider}/{model_name}: {type(e).__name__}: {str(e)[:100]}")
            last_error = e
            continue

    if response is None:
        raise last_error if last_error else Exception("Все модели недоступны или вернули пустой ответ")

    try:

        ai_text = response.choices[0].message.content
        print(f"[AUTO-LINK AI] Ответ ИИ:\n{ai_text}")

        # Допустимые offer_id (только из скрейпинга, чтобы исключить галлюцинации ИИ)
        valid_ids = set(lots_dict.keys())

        result = {}
        for line in ai_text.split("\n"):
            line = line.strip()
            if "→" in line:
                parts = line.split("→")
                if len(parts) == 2:
                    item_name = parts[0].strip().lstrip("-").strip()
                    offer_ids_str = parts[1].strip()
                    if offer_ids_str.lower() == "не найдено":
                        continue

                    # Парсим список offer_id'шков и фильтруем только валидные
                    ids_list = []
                    invalid_ids = []
                    for id_part in offer_ids_str.split(","):
                        id_clean = id_part.strip().replace("#", "")
                        try:
                            oid = int(id_clean)
                            if oid in valid_ids:
                                ids_list.append(oid)
                            else:
                                invalid_ids.append(oid)
                        except ValueError:
                            pass

                    if invalid_ids:
                        print(f"[AUTO-LINK AI] ⚠️ Галлюцинация для '{item_name}': {invalid_ids} - игнорированы")

                    if ids_list:
                        result[item_name] = ids_list

        # Fallback: если для товара нет привязки, ищем по совпадению ключевых слов в начале названия
        for item_name in item_names:
            if item_name in result:
                continue
            base_name = re.sub(r'\s*\((с кэшбеком|без кэшбека|нет кэшбека)\)\s*$', '', item_name).strip().lower()
            base_words = base_name.split()

            for key in result.keys():
                key_base = re.sub(r'\s*\((с кэшбеком|без кэшбека|нет кэшбека)\)\s*$', '', key).strip().lower()
                key_words = key_base.split()

                # Сравниваем по словам в начале названия
                # Берём минимум из двух длин и считаем совпадающие слова в начале
                min_len = min(len(base_words), len(key_words))
                if min_len < 2:
                    continue

                matching = 0
                for i in range(min_len):
                    if base_words[i] == key_words[i]:
                        matching += 1
                    else:
                        break

                # Если совпадает 2+ слов И это вся короткая фраза целиком - считаем что это тот же товар
                if matching >= 2 and matching == min_len:
                    result[item_name] = result[key]
                    print(f"[AUTO-LINK AI FALLBACK] {item_name} → {result[key]} (взято из {key}, совпало {matching} слов)")
                    break

        return result
    except Exception as e:
        print(f"[AUTO-LINK AI ERROR] {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return {}


async def _show_edit_menu(call, game_hash, item_id, game_name, mp, state_data):
    """Helper to show edit menu with current/pending values"""
    item_info = mp[game_name].get(item_id, {})
    pending = state_data.get("pending_changes", {})

    name = pending.get("name", item_info.get("name", item_id))
    cost = pending.get("cost", item_info.get("cost", 0))
    min_price = calc_min_price(cost) if "cost" in pending else item_info.get("min_price", 0)
    cashback_key = pending.get("cashback", item_info.get("cashback", "none"))
    offer_ids = pending.get("offer_ids", get_item_offer_ids(item_info))

    text = build_edit_item_text(name, cost, min_price, cashback_key, offer_ids)

    rows = [
        [
            InlineKeyboardButton(text="📝 Название", callback_data=f"mp_edt_name_{game_hash}_{item_id}"),
            InlineKeyboardButton(text="💸 Цена закупа", callback_data=f"mp_edt_cost_{game_hash}_{item_id}"),
        ],
        [
            InlineKeyboardButton(text="💳 Кэшбек", callback_data=f"mp_edt_cashback_{game_hash}_{item_id}"),
            InlineKeyboardButton(text="🔗 Лоты", callback_data=f"mp_edt_offerid_{game_hash}_{item_id}"),
        ],
        [
            InlineKeyboardButton(text="💾 Сохранить", callback_data=f"mp_edt_save_{game_hash}_{item_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"mp_edt_cancel_{game_hash}_{item_id}"),
        ],
        [
            InlineKeyboardButton(text="↩️ К списку", callback_data=f"mp_editlist_{game_hash}"),
        ]
    ]

    await call.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


# ====================== КЛАВИАТУРЫ ======================

def games_kb(games: list, page: int, mp: dict | None = None) -> InlineKeyboardMarkup:
    start = page * GAMES_PER_PAGE
    end = start + GAMES_PER_PAGE
    current = games[start:end]
    total_pages = (len(games) + GAMES_PER_PAGE - 1) // GAMES_PER_PAGE or 1

    buttons = []
    for name in current:
        suffix = ""
        if mp is not None:
            item_list = _items_list(get_items(mp, name))
            linked_count = sum(1 for _, info in item_list if get_item_offer_ids(info))
            suffix = f" · {len(item_list)}📦/{linked_count}🔗"
        buttons.append([InlineKeyboardButton(
            text=f"🎮 {_short(name, 46)}{suffix}",
            callback_data=f"mp_game_{get_hash(name)}"
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"mp_pg_{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="none"))
    if end < len(games):
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"mp_pg_{page+1}"))
    buttons.append(nav)

    buttons.append([
        InlineKeyboardButton(text="➕ Добавить игру", callback_data="mp_add_game"),
        InlineKeyboardButton(text="🗑 Удалить игру", callback_data="mp_delgame_pick"),
    ])
    buttons.append([
        InlineKeyboardButton(text="📥 Импорт игр из FunPay", callback_data="mp_import_games"),
    ])
    buttons.append([
        InlineKeyboardButton(text="📊 Актуальность данных", callback_data="mp_freshness"),
    ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ FunPay Auto", callback_data="funpay_auto_main"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def game_view_kb(game_hash: str, page: int, total_pages: int, has_rate: bool = False) -> InlineKeyboardMarkup:
    buttons = []

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"mp_items_pg_{game_hash}_{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="none"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"mp_items_pg_{game_hash}_{page+1}"))
    if len(nav) > 1:
        buttons.append(nav)

    buttons.append([
        InlineKeyboardButton(text="➕ Добавить товар", callback_data=f"mp_add_item_{game_hash}"),
        InlineKeyboardButton(text="🗑 Удалить товар", callback_data=f"mp_delitem_pick_{game_hash}"),
    ])
    rate_btn_text = "🔄 Обновить ставку СБП" if has_rate else "🔍 Найти ставку СБП"
    buttons.append([
        InlineKeyboardButton(text=rate_btn_text, callback_data=f"mp_fetchrate_{game_hash}"),
    ])
    buttons.append([
        InlineKeyboardButton(text="✏️ Переименовать", callback_data=f"mp_rename_{game_hash}"),
        InlineKeyboardButton(text="🔗 Редактирование айди", callback_data=f"mp_editoffers_{game_hash}"),
    ])
    buttons.append([
        InlineKeyboardButton(text="📝 Редактировать товары", callback_data=f"mp_editlist_{game_hash}"),
    ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="mp_pg_0"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def cashback_kb(bulk: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="💳 С кэшбеком", callback_data="mp_cb_yes")],
        [InlineKeyboardButton(text="💵 Без кэшбека", callback_data="mp_cb_no")],
    ]
    if bulk:
        rows.append([InlineKeyboardButton(text="⛔ Прекратить добавление", callback_data="mp_bulk_stop")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def del_game_pick_kb(games: list, selected: set = None) -> InlineKeyboardMarkup:
    if selected is None:
        selected = set()
    buttons = []
    for name in games:
        prefix = "✅ " if name in selected else "☐ "
        buttons.append([InlineKeyboardButton(
            text=f"{prefix}{name}",
            callback_data=f"mp_delgame_toggle_{get_hash(name)}"
        )])
    if selected:
        buttons.append([InlineKeyboardButton(
            text=f"🗑 Удалить выбранные ({len(selected)})",
            callback_data="mp_delgame_do"
        )])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="mp_pg_0")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def import_games_pick_kb(new_games: dict, selected: set, already: list) -> InlineKeyboardMarkup:
    buttons = []
    for name in new_games:
        prefix = "✅ " if name in selected else "☐ "
        buttons.append([InlineKeyboardButton(
            text=f"{prefix}{name}",
            callback_data=f"mp_imptoggle_{get_hash(name)}"
        )])
    for name in already:
        buttons.append([InlineKeyboardButton(
            text=f"— {name} (уже есть)",
            callback_data="none"
        )])
    if selected:
        buttons.append([InlineKeyboardButton(
            text=f"📥 Добавить выбранные ({len(selected)})",
            callback_data="mp_import_games_confirm"
        )])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="mp_pg_0")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def del_item_pick_kb(game_hash: str, items: dict, selected: set = None) -> InlineKeyboardMarkup:
    if selected is None:
        selected = set()
    buttons = []
    for item_id, info in items.items():
        if item_id == "_meta":
            continue
        name = info.get("name", item_id) if isinstance(info, dict) else item_id
        cb_emoji = CASHBACK_EMOJI.get(info.get("cashback", "none") if isinstance(info, dict) else "none", "")
        display = f"{cb_emoji} {name[:28]}" if cb_emoji and cb_emoji != "—" else name[:33]
        prefix = "✅ " if item_id in selected else "☐ "
        buttons.append([InlineKeyboardButton(
            text=f"{prefix}{display}",
            callback_data=f"mp_delitem_toggle_{game_hash}_{item_id}"
        )])
    if selected:
        buttons.append([InlineKeyboardButton(
            text=f"🗑 Удалить выбранные ({len(selected)})",
            callback_data=f"mp_delitem_do_{game_hash}"
        )])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"mp_game_{game_hash}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_kb(callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=callback)]
    ])


# ====================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ======================

def _sync_check_rate_for_lot(gk: str, lot_id: int) -> float | None:
    try:
        session = _req.Session()
        session.cookies.set("golden_key", gk, domain=".funpay.com")
        session.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        resp = session.get(f"https://funpay.com/lots/offerEdit?offer={lot_id}", timeout=10)
        return _parse_sbp_rate_from_offer_html(resp.text)
    except Exception as e:
        print(f"[MINPRICE] check_rate error lot={lot_id}: {e}")
        return None


async def check_rate_for_lot_with_retries(gk: str, lot_id: int, attempts: int = 5) -> float | None:
    """Проверяет коэффициент с ретраями, чтобы временный сбой FunPay не давал error."""
    loop = asyncio.get_event_loop()
    for attempt in range(1, attempts + 1):
        new_rate = await loop.run_in_executor(None, _sync_check_rate_for_lot, gk, lot_id)
        if new_rate is not None:
            if attempt > 1:
                print(f"[SBP-CHECK] lot={lot_id} успешно с попытки {attempt}")
            return new_rate
        if attempt < attempts:
            await asyncio.sleep(min(2 * attempt, 8))
    return None


def freshness_text(mp: dict, games: list, results: dict = None) -> str:
    text = "📊 <b>Актуальность коэффициентов</b>\n\n"
    for game in games:
        meta = get_game_meta(mp, game)
        rate = meta.get("sbp_rate")
        latest = meta.get("latest_checked_rate")
        lot_id = meta.get("lot_id")

        # Если есть результат свежей проверки - используем его
        if results is not None:
            status = results.get(game)
            if status == "ok":
                icon, detail = "🟢", f"×{rate:.4f} — актуален"
            elif status == "changed":
                new_rate = results.get(game + "__new")
                icon, detail = "🔴", f"×{rate:.4f} → ×{new_rate:.4f} — НЕ актуален (обновите в игре)"
            elif status == "no_lot":
                icon, detail = "❓", "ставка не задана"
            else:
                icon, detail = "❓", "не удалось проверить"
        else:
            # Без свежей проверки - показываем кэшированное состояние
            if not rate:
                icon, detail = "❓", "ставка не задана"
            elif latest is not None and abs(latest - rate) >= 0.0001:
                icon, detail = "🔴", f"×{rate:.4f} → ×{latest:.4f} — НЕ актуален (обновите в игре)"
            else:
                icon, detail = "🟢", f"×{rate:.4f} — актуален"
        text += f"{icon} <b>{game}</b> — {detail}\n"
    return text


def freshness_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить данные", callback_data="mp_freshness_upd")],
        [InlineKeyboardButton(text="📁 Управление демпингом", callback_data="dmp_menu")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="mp_pg_0")],
    ])


def build_game_text(game_name: str, items: dict, page: int, sbp_rate: float = None) -> tuple[str, int]:
    item_list = _items_list(items)
    total_pages = (len(item_list) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE or 1

    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    current = item_list[start:end]

    linked_count = sum(1 for _, info in item_list if get_item_offer_ids(info))
    rate_line = f"📈 СБП: <code>×{sbp_rate:.4f}</code>\n" if sbp_rate else "📈 СБП: <i>не задана</i>\n"

    text = (
        f"🎮 <b>{_html.escape(game_name)}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"📦 Товаров: <b>{len(item_list)}</b>\n"
        f"🔗 С лотами: <b>{linked_count}</b>\n"
        f"{rate_line}"
    )
    if total_pages > 1:
        text += f"📄 Страница: <b>{page + 1}/{total_pages}</b>\n"
    text += "\n"

    if not current:
        text += "📭 <i>Товаров пока нет. Добавьте первый!</i>"
    else:
        for idx, (item_id, info) in enumerate(current, start=start + 1):
            name = info.get("name", item_id) if isinstance(info, dict) else item_id
            cost = _money(info.get("cost", 0))
            min_price = _money(info.get("min_price", 0))
            cashback_key = info.get("cashback", "none")
            cashback_label = _cashback_badge(cashback_key)
            display = _html.escape(_short(str(name), 70))

            offer_ids = get_item_offer_ids(info)
            lots_line = f"🔗 Лоты ({len(offer_ids)}): {_offer_links(offer_ids)}" if offer_ids else "🔗 Лоты: —"

            text += (
                f"<b>{idx}. {display}</b>\n"
                f"💳 <i>{_html.escape(cashback_label)}</i>\n"
                f"💸 Закуп: <code>{cost:.2f} ₽</code>  |  💰 Мин: <code>{min_price:.2f} ₽</code>\n"
            )
            if sbp_rate:
                site_price = round(min_price * sbp_rate, 2)
                text += f"🌐 Сайт (СБП): <code>{site_price:.2f} ₽</code>\n"
            text += f"{lots_line}\n"
            text += "\n"

    return text, total_pages


# ====================== ХЕНДЛЕРЫ ======================

@router.message(Command("minprice"))
async def cmd_minprice(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await no_access_reply(message)
        return

    await state.clear()
    data = load_mp(message.from_user.id)
    games = list(data.keys())

    text = build_games_text(games, data)

    await message.answer(text, reply_markup=games_kb(games, 0, data), parse_mode="HTML")


@router.callback_query(F.data.startswith("mp_"))
async def cb_minprice(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        await no_access_callback(call)
        return

    data = call.data
    user_id = call.from_user.id
    mp = load_mp(user_id)
    games = list(mp.keys())

    # --- Главная страница ---
    if data.startswith("mp_pg_"):
        await state.clear()
        page = int(data.split("_")[2])
        text = build_games_text(games, mp)
        await call.message.edit_text(text, reply_markup=games_kb(games, page, mp), parse_mode="HTML")
        await call.answer()

    # --- Открыть игру ---
    elif data.startswith("mp_game_"):
        await state.clear()
        game_hash = data.split("_")[2]
        game_name = next((g for g in games if get_hash(g) == game_hash), None)
        if not game_name:
            return await call.answer("Игра не найдена", show_alert=True)

        meta = get_game_meta(mp, game_name)
        sbp_rate = meta.get("sbp_rate")
        items = get_items(mp, game_name)
        text, total_pages = build_game_text(game_name, items, 0, sbp_rate)
        await state.update_data(current_game_hash=game_hash)
        await call.message.edit_text(
            text,
            reply_markup=game_view_kb(game_hash, 0, total_pages, has_rate=bool(sbp_rate)),
            parse_mode="HTML"
        )
        await call.answer()

    # --- Пагинация товаров ---
    elif data.startswith("mp_items_pg_"):
        parts = data.split("_")
        game_hash = parts[3]
        page = int(parts[4])
        game_name = next((g for g in games if get_hash(g) == game_hash), None)
        if not game_name:
            return await call.answer("Игра не найдена", show_alert=True)

        meta = get_game_meta(mp, game_name)
        sbp_rate = meta.get("sbp_rate")
        items = get_items(mp, game_name)
        text, total_pages = build_game_text(game_name, items, page, sbp_rate)
        await call.message.edit_text(
            text,
            reply_markup=game_view_kb(game_hash, page, total_pages, has_rate=bool(sbp_rate)),
            parse_mode="HTML"
        )
        await call.answer()

    # --- Добавить игру ---
    elif data == "mp_add_game":
        await state.set_state(MinPriceStates.waiting_add_game)
        await call.message.edit_text(
            "🎮 <b>Добавление игры</b>\n\nВведите название игры:",
            parse_mode="HTML",
            reply_markup=back_kb("mp_pg_0")
        )
        await call.answer()

    # --- Актуальность данных ---
    elif data == "mp_freshness":
        await state.clear()
        text = freshness_text(mp, games)
        await call.message.edit_text(text, reply_markup=freshness_kb(), parse_mode="HTML")
        await call.answer()

    elif data == "mp_freshness_upd":
        if not games:
            return await call.answer("Нет игр", show_alert=True)
        await call.message.edit_text(
            "⏳ <b>Проверяю коэффициенты...</b>\n\nЭто может занять несколько секунд.",
            parse_mode="HTML"
        )
        await call.answer()

        gk, _ = db.get_config()
        if not gk:
            await call.message.edit_text(
                "❌ Golden Key не настроен. Откройте /funpayauto -> Данные аккаунта.",
                parse_mode="HTML",
                reply_markup=freshness_kb()
            )
            return

        results = {}
        for game in games:
            meta = get_game_meta(mp, game)
            lot_id = meta.get("lot_id")
            old_rate = meta.get("sbp_rate")
            if old_rate is None:
                results[game] = "no_lot"
                continue

            lot_id, new_rate = await resolve_sbp_rate_for_game(gk, game, lot_id, attempts=5)
            if new_rate is None:
                results[game] = "error"
            elif old_rate is None or abs(new_rate - old_rate) < 0.0001:
                # Очищаем флаг последней проверки если был
                meta["latest_checked_rate"] = new_rate
                if lot_id:
                    meta["lot_id"] = lot_id
                set_game_meta(mp, game, meta)
                results[game] = "ok"
            else:
                # НЕ обновляем sbp_rate - только сохраняем что было обнаружено
                meta["latest_checked_rate"] = new_rate
                if lot_id:
                    meta["lot_id"] = lot_id
                set_game_meta(mp, game, meta)
                results[game] = "changed"
                results[game + "__new"] = new_rate

            if game != games[-1]:
                await asyncio.sleep(0.5)

        save_mp(user_id, mp)
        mp = load_mp(user_id)
        text = freshness_text(mp, games, results)
        await call.message.edit_text(text, reply_markup=freshness_kb(), parse_mode="HTML")

    # --- Переименовать игру ---
    elif data.startswith("mp_rename_"):
        game_hash = data.split("_")[2]
        game_name = next((g for g in games if get_hash(g) == game_hash), None)
        if not game_name:
            return await call.answer("Игра не найдена", show_alert=True)
        await state.update_data(current_game_hash=game_hash, current_game_name=game_name)
        await state.set_state(MinPriceStates.waiting_rename_game)
        await call.message.edit_text(
            f"✏️ <b>Переименование игры</b>\n\nТекущее название: <b>{game_name}</b>\n\nВведите новое название:",
            parse_mode="HTML",
            reply_markup=back_kb(f"mp_game_{game_hash}")
        )
        await call.answer()

    # --- Выбор игр для удаления (мультиселект) ---
    elif data == "mp_delgame_pick":
        if not games:
            return await call.answer("Нет игр для удаления", show_alert=True)
        await state.update_data(delgame_selected=[])
        await call.message.edit_text(
            "🗑 <b>Выберите игры для удаления:</b>\n<i>Нажмите на игру, чтобы выделить. Все товары внутри тоже удалятся.</i>",
            reply_markup=del_game_pick_kb(games, set()),
            parse_mode="HTML"
        )
        await call.answer()

    # --- Переключение выделения игры ---
    elif data.startswith("mp_delgame_toggle_"):
        game_hash = data.split("_")[3]
        game_name = next((g for g in games if get_hash(g) == game_hash), None)
        if not game_name:
            return await call.answer("Игра не найдена", show_alert=True)
        state_data = await state.get_data()
        selected = set(state_data.get("delgame_selected", []))
        if game_name in selected:
            selected.discard(game_name)
        else:
            selected.add(game_name)
        await state.update_data(delgame_selected=list(selected))
        await call.message.edit_reply_markup(reply_markup=del_game_pick_kb(games, selected))
        await call.answer()

    # --- Удаление выбранных игр ---
    elif data == "mp_delgame_do":
        state_data = await state.get_data()
        selected = set(state_data.get("delgame_selected", []))
        if not selected:
            return await call.answer("Ничего не выбрано", show_alert=True)
        deleted = 0
        for name in selected:
            if name in mp:
                del mp[name]
                deleted += 1
        save_mp(user_id, mp)
        await state.update_data(delgame_selected=[])
        await call.answer(f"🗑 Удалено игр: {deleted}")
        games = list(mp.keys())
        text = build_games_text(games, mp)
        await call.message.edit_text(text, reply_markup=games_kb(games, 0, mp), parse_mode="HTML")

    # --- Импорт игр из FunPay ---
    elif data == "mp_import_games":
        await call.answer("🔄 Загружаю лоты с FunPay...")
        gk, ua = db.get_config()
        if not gk:
            return await call.message.answer("❌ Golden Key не настроен.")
        try:
            from FunPayAPI import Account
            acc = Account(gk)
            if ua:
                acc.user_agent = ua
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, acc.get)

            session = _req.Session()
            session.cookies.set("golden_key", gk, domain="funpay.com")
            session.headers["User-Agent"] = ua or "Mozilla/5.0"
            r = await loop.run_in_executor(
                None, lambda: session.get(f"https://funpay.com/users/{acc.id}/", timeout=10)
            )
            html = r.text

            names = re.findall(
                r'<div[^>]+class="offer-list-title"[^>]*>\s*<h3><a[^>]*>([^<]+)</a>',
                html
            )
            found = {name.strip(): None for name in names if name.strip()}

            if not found:
                return await call.message.answer(
                    "⚠️ Не удалось найти лоты. Убедитесь, что аккаунт авторизован (/funpayauto -> Данные аккаунта) и есть активные лоты."
                )

            existing = list(mp.keys())
            new_games = {n: lid for n, lid in found.items() if n not in existing}
            already = [n for n in found if n in existing]

            if not new_games:
                return await call.message.answer(
                    f"ℹ️ Все {len(found)} найденных игр уже добавлены.",
                    parse_mode="HTML"
                )

            selected = set(new_games.keys())
            await state.update_data(fp_import_games=new_games, fp_import_already=already, fp_import_selected=list(selected))
            await call.message.answer(
                f"📥 <b>Найдено новых игр: {len(new_games)}</b>\n"
                f"<i>Все выбраны. Снимите галочку с тех, что не нужны.</i>",
                parse_mode="HTML",
                reply_markup=import_games_pick_kb(new_games, selected, already)
            )
        except Exception as e:
            await call.message.answer(f"❌ Ошибка при загрузке: {e}")

    # --- Переключение выделения при импорте ---
    elif data.startswith("mp_imptoggle_"):
        game_hash = data.split("_")[2]
        state_data = await state.get_data()
        new_games = state_data.get("fp_import_games", {})
        already = state_data.get("fp_import_already", [])
        selected = set(state_data.get("fp_import_selected", []))
        game_name = next((n for n in new_games if get_hash(n) == game_hash), None)
        if not game_name:
            return await call.answer()
        if game_name in selected:
            selected.discard(game_name)
        else:
            selected.add(game_name)
        await state.update_data(fp_import_selected=list(selected))
        await call.message.edit_reply_markup(reply_markup=import_games_pick_kb(new_games, selected, already))
        await call.answer()

    # --- Подтверждение импорта игр ---
    elif data == "mp_import_games_confirm":
        state_data = await state.get_data()
        new_games = state_data.get("fp_import_games", {})
        selected = set(state_data.get("fp_import_selected", []))
        if not selected:
            return await call.answer("Ничего не выбрано", show_alert=True)
        added = 0
        for name in selected:
            lot_id = new_games.get(name)
            if name not in mp:
                mp[name] = {"_meta": {"lot_id": lot_id, "sbp_rate": None}}
                added += 1
        save_mp(user_id, mp)
        await state.update_data(fp_import_games={}, fp_import_selected=[])
        await call.answer(f"✅ Добавлено игр: {added}")
        games = list(mp.keys())
        text = build_games_text(games, mp)
        await call.message.edit_text(text, reply_markup=games_kb(games, 0, mp), parse_mode="HTML")

    # --- Подтверждение импорта из фото ---
    elif data.startswith("mp_import_confirm_"):
        game_hash = data.split("_")[3]
        state_data = await state.get_data()
        photo_items = state_data.get("photo_items", [])
        game_name = state_data.get("current_game_name")

        if not photo_items or not game_name:
            return await call.answer("Ошибка: данные не найдены. Попробуйте снова.", show_alert=True)

        first_name = photo_items[0]
        await state.update_data(
            photo_idx=0,
            current_item_name=first_name,
            bulk_mode=True,
            current_game_hash=game_hash
        )
        await state.set_state(MinPriceStates.waiting_bulk_cost)
        await call.message.edit_text(
            f"📦 <b>Товар 1/{len(photo_items)}:</b> <code>{first_name}</code>\n\n"
            f"Введите <b>закупочную цену</b> (₽):",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⛔ Прекратить добавление", callback_data="mp_bulk_stop")]
            ])
        )
        await call.answer()

    # --- Найти / обновить ставку СБП ---
    elif data.startswith("mp_fetchrate_"):
        game_hash = data.split("_")[2]
        game_name = next((g for g in games if get_hash(g) == game_hash), None)
        if not game_name:
            return await call.answer("Игра не найдена", show_alert=True)

        await call.message.edit_text(
            f"⏳ <b>Ищу лот «{game_name}» на FunPay...</b>",
            parse_mode="HTML"
        )
        await call.answer()

        lot_id, sbp_rate = await fetch_sbp_rate(game_name)

        mp = load_mp(user_id)
        meta = get_game_meta(mp, game_name)
        if lot_id:
            meta["lot_id"] = lot_id
        if sbp_rate:
            meta["sbp_rate"] = sbp_rate
            # Применили новое значение - актуализируем latest_checked_rate
            meta["latest_checked_rate"] = sbp_rate
        set_game_meta(mp, game_name, meta)
        save_mp(user_id, mp)

        items = get_items(mp, game_name)
        text, total_pages = build_game_text(game_name, items, 0, sbp_rate)

        if not sbp_rate:
            text += "\n\n❌ <i>Ставку СБП не удалось найти. Убедитесь что название совпадает с FunPay и есть активные лоты.</i>"
        else:
            text += f"\n\n✅ <i>Ставка СБП обновлена: ×{sbp_rate:.4f}</i>"

        await call.message.edit_text(
            text,
            reply_markup=game_view_kb(game_hash, 0, total_pages, has_rate=bool(sbp_rate)),
            parse_mode="HTML"
        )

    # --- Добавить товар ---
    elif data.startswith("mp_add_item_"):
        game_hash = data.split("_")[3]
        game_name = next((g for g in games if get_hash(g) == game_hash), None)
        if not game_name:
            return await call.answer("Игра не найдена", show_alert=True)
        await state.update_data(current_game_hash=game_hash, current_game_name=game_name)
        await state.set_state(MinPriceStates.waiting_add_item_name)
        await call.message.edit_text(
            f"➕ <b>Добавление товара в «{game_name}»</b>\n\n"
            f"Введите название товара <b>текстом</b> или отправьте <b>фото</b> со списком — бот распознает все автоматически.",
            parse_mode="HTML",
            reply_markup=back_kb(f"mp_game_{game_hash}")
        )
        await call.answer()

    # --- Остановить пошаговое добавление товаров ---
    elif data == "mp_bulk_stop":
        state_data = await state.get_data()
        photo_idx = state_data.get("photo_idx", 0)
        photo_items = state_data.get("photo_items", [])
        game_hash = state_data.get("current_game_hash")
        await state.clear()
        await call.message.edit_text(
            f"⛔ <b>Добавление остановлено</b>\n\n"
            f"Добавлено: <b>{photo_idx}</b> из <b>{len(photo_items)}</b> товаров.",
            parse_mode="HTML",
            reply_markup=back_kb(f"mp_game_{game_hash}") if game_hash else None
        )
        await call.answer()

    # --- Меню редактирования айди (автопривязка + вручную) ---
    elif data.startswith("mp_editoffers_"):
        game_hash = data.split("_")[2]
        game_name = next((g for g in games if get_hash(g) == game_hash), None)
        if not game_name:
            return await call.answer("Игра не найдена", show_alert=True)

        item_list = _items_list(get_items(mp, game_name))
        linked_count = sum(1 for _, info in item_list if get_item_offer_ids(info))
        unlinked_count = len(item_list) - linked_count

        await call.message.edit_text(
            f"🔗 <b>Привязка лотов</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"🎮 <b>{_html.escape(game_name)}</b>\n\n"
            f"📦 Товаров: <b>{len(item_list)}</b>\n"
            f"✅ С лотами: <b>{linked_count}</b>\n"
            f"🕳 Без лотов: <b>{unlinked_count}</b>\n\n"
            f"<i>Автодобавка пробует сопоставить лоты с профиля, вручную — выбрать товар и ввести ID.</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🤖 Сопоставить все товары", callback_data=f"mp_edt_auto_all_{game_hash}")],
                [InlineKeyboardButton(text="🕳 Только без лотов", callback_data=f"mp_edt_auto_unlinked_{game_hash}")],
                [InlineKeyboardButton(text="🔗 Вручную", callback_data=f"mp_edt_manual_{game_hash}")],
                [InlineKeyboardButton(text="↩️ Назад", callback_data=f"mp_game_{game_hash}")],
            ])
        )
        await call.answer()

    # --- Автодобавка offer_id'шков ---
    elif data.startswith("mp_edt_auto_all_") or data.startswith("mp_edt_auto_unlinked_"):
        only_unlinked = data.startswith("mp_edt_auto_unlinked_")
        game_hash = data.split("_")[4]
        game_name = next((g for g in games if get_hash(g) == game_hash), None)
        if not game_name:
            return await call.answer("Игра не найдена", show_alert=True)

        await call.message.edit_text(
            f"⏳ <b>Получаю лоты со скрейпа профиля...</b>\n\n"
            f"<i>Режим: {'только товары без лотов' if only_unlinked else 'все товары'}</i>",
            parse_mode="HTML"
        )

        # Скрейпим лоты
        lots = await _get_user_lots(game_name)
        if not lots:
            await call.message.edit_text(
                f"❌ <b>Лоты не найдены</b>\n\n"
                f"Проверьте что у вас есть активные лоты для «{_html.escape(game_name)}» на FunPay",
                parse_mode="HTML",
                reply_markup=back_kb(f"mp_editoffers_{game_hash}")
            )
            return await call.answer()

        # Получаем товары из бота
        items = get_items(mp, game_name)
        if only_unlinked:
            items = {
                iid: info for iid, info in items.items()
                if isinstance(info, dict) and not get_item_offer_ids(info)
            }
        if not items:
            await call.message.edit_text(
                f"❌ <b>Товары не найдены в боте</b>",
                parse_mode="HTML",
                reply_markup=back_kb(f"mp_editoffers_{game_hash}")
            )
            return await call.answer()

        await call.message.edit_text(
            f"⏳ <b>ИИ сопоставляет лоты с товарами...</b>\n\n"
            f"<i>Режим: {'только товары без лотов' if only_unlinked else 'все товары'}</i>",
            parse_mode="HTML"
        )

        # ИИ сопоставление
        matches = await _match_offers_with_ai(game_name, lots, items)

        # Строим результаты - используем НАЗВАНИЯ товаров (с кэшбеком), а не item_id
        matched_count = 0
        not_found = []
        matched_lines = []

        # Получаем список (название_с_кэшбеком, item_id, info)
        item_names_list = []
        for iid, info in items.items():
            if isinstance(info, dict):
                name = info.get("name", "")
                cashback = info.get("cashback", "none")
                cashback_label = CASHBACK_OPTIONS.get(cashback, "")
                if name:
                    full_name = f"{name} ({cashback_label})" if cashback_label else name
                    item_names_list.append(full_name)

        for full_name in sorted(item_names_list):
            if full_name in matches:
                offer_ids = matches[full_name]
                ids_str = ", ".join([f"#{oid}" for oid in offer_ids])
                matched_lines.append(
                    f"📦 <b>{_html.escape(_short(full_name, 70))}</b>\n"
                    f"   🔗 <code>{ids_str}</code>"
                )
                matched_count += 1
            else:
                not_found.append(full_name)

        text = (
            f"✅ <b>Результаты автосопоставления</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"🎮 <b>{_html.escape(game_name)}</b>\n\n"
            f"🧭 Режим: <b>{'только товары без лотов' if only_unlinked else 'все товары'}</b>\n"
            f"🔎 Лотов найдено: <b>{len(lots)}</b>\n"
            f"📦 Товаров проверено: <b>{len(item_names_list)}</b>\n"
            f"✅ Совпадений: <b>{matched_count}</b>\n"
            f"❓ Без совпадения: <b>{len(not_found)}</b>\n"
        )

        if matched_lines:
            text += "\n<b>Найденные привязки</b>\n"
            text += "\n".join(matched_lines[:15])
            if len(matched_lines) > 15:
                text += f"\n<i>... и ещё {len(matched_lines) - 15}</i>"

        if not_found:
            text += f"\n❓ <b>Не найдено ({len(not_found)}):</b>\n"
            for full_name in not_found[:5]:
                text += f"  • {_html.escape(_short(full_name, 70))}\n"
            if len(not_found) > 5:
                text += f"  ... и ещё {len(not_found) - 5}"

        await state.update_data(auto_matches=matches, current_game_hash=game_hash, current_game_name=game_name)
        await call.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"mp_edt_auto_confirm_{game_hash}")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data=f"mp_edt_auto_cancel_{game_hash}")],
            ])
        )
        await call.answer()

    # --- Подтверждение автопривязки ---
    elif data.startswith("mp_edt_auto_confirm_"):
        game_hash = data.split("_")[4]
        state_data = await state.get_data()
        matches = state_data.get("auto_matches", {})
        game_name = state_data.get("current_game_name")

        if not matches:
            return await call.answer("Нет совпадений для сохранения", show_alert=True)

        # Сохраняем привязки. matches: {full_name (with cashback): [offer_ids]}
        # Каждый item_id может быть привязан только один раз!
        saved_count = 0
        used_item_ids = set()

        # Первый проход: точные совпадения по полному имени с кэшбеком
        for full_name, offer_ids in matches.items():
            for item_id, info in mp[game_name].items():
                if item_id == "_meta" or not isinstance(info, dict) or item_id in used_item_ids:
                    continue
                name = info.get("name", "")
                cashback = info.get("cashback", "none")
                cashback_label = CASHBACK_OPTIONS.get(cashback, "")
                item_full = f"{name} ({cashback_label})" if cashback_label else name

                if item_full == full_name:
                    mp[game_name][item_id]["offer_ids"] = offer_ids
                    if "offer_id" in mp[game_name][item_id]:
                        del mp[game_name][item_id]["offer_id"]
                    used_item_ids.add(item_id)
                    saved_count += 1
                    break

        # Второй проход: для оставшихся товаров пробуем по чистому имени
        for full_name, offer_ids in matches.items():
            clean_name = re.sub(r'\s*\((с кэшбеком|без кэшбека|нет кэшбека)\)\s*$', '', full_name).strip()
            for item_id, info in mp[game_name].items():
                if item_id == "_meta" or not isinstance(info, dict) or item_id in used_item_ids:
                    continue
                name = info.get("name", "")
                if name == clean_name:
                    mp[game_name][item_id]["offer_ids"] = offer_ids
                    if "offer_id" in mp[game_name][item_id]:
                        del mp[game_name][item_id]["offer_id"]
                    used_item_ids.add(item_id)
                    saved_count += 1
                    break

        save_mp(user_id, mp)
        await state.clear()

        await call.message.edit_text(
            f"✅ <b>Привязано к {saved_count} товарам!</b>",
            parse_mode="HTML",
            reply_markup=back_kb(f"mp_game_{game_hash}")
        )
        await call.answer()

    # --- Отмена автопривязки ---
    elif data.startswith("mp_edt_auto_cancel_"):
        game_hash = data.split("_")[4]
        await state.clear()
        await call.message.edit_text(
            f"❌ Отменено",
            parse_mode="HTML",
            reply_markup=back_kb(f"mp_editoffers_{game_hash}")
        )
        await call.answer()

    # --- Вручную (использует старую функцию демпинга) ---
    elif data.startswith("mp_edt_manual_"):
        game_hash = data.split("_")[3]
        game_name = next((g for g in games if get_hash(g) == game_hash), None)
        if not game_name:
            return await call.answer("Игра не найдена", show_alert=True)

        if not os.path.exists(DEMPING_FILE):
            return await call.answer("Сначала загрузите файл демпинга через /funpayauto -> Демпинг Cardinal", show_alert=True)

        demping = load_demping()
        items = mp.get(game_name, {})
        linked = 0
        already = 0

        for item_id, info in items.items():
            if item_id == "_meta" or not isinstance(info, dict):
                continue
            if get_item_offer_ids(info):
                already += 1
                continue
            item_name = info.get("name", "").lower().strip()
            for offer_id, lot in demping.items():
                trigger = lot.get("triggers", "").lower().strip()
                if not trigger or trigger == "другое количество":
                    continue
                if item_name == trigger or trigger in item_name or item_name in trigger:
                    add_offer_id_to_item(info, int(offer_id))
                    mp[game_name][item_id] = info
                    linked += 1
                    break

        save_mp(user_id, mp)
        items = mp.get(game_name, {})
        total_items = len([k for k in items if k != "_meta"])
        unlinked = total_items - already - linked
        link_kb_rows = []
        if unlinked > 0:
            link_kb_rows.append([InlineKeyboardButton(
                text="🔗 Привязать вручную",
                callback_data=f"mp_manuallink_{game_hash}"
            )])
        link_kb_rows.append([InlineKeyboardButton(
            text="↩️ К редактированию",
            callback_data=f"mp_editoffers_{game_hash}"
        )])
        await call.message.edit_text(
            f"🔗 <b>Привязка для «{_html.escape(game_name)}»</b>\n\n"
            f"✅ Привязано: <b>{linked}</b>\n"
            f"⏭ Уже было: <b>{already}</b>\n"
            f"❌ Не найдено: <b>{unlinked}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=link_kb_rows)
        )
        await call.answer()

    # --- Привязать offer_id к товарам игры (устаревший блок, оставляю для совместимости) ---
    elif data.startswith("mp_linkoffers_"):
        game_hash = data.split("_")[2]
        game_name = next((g for g in games if get_hash(g) == game_hash), None)
        if not game_name:
            return await call.answer("Игра не найдена", show_alert=True)

        if not os.path.exists(DEMPING_FILE):
            return await call.answer("Сначала загрузите файл демпинга через /funpayauto -> Демпинг Cardinal", show_alert=True)

        demping = load_demping()
        items = mp.get(game_name, {})
        linked = 0
        already = 0

        for item_id, info in items.items():
            if item_id == "_meta" or not isinstance(info, dict):
                continue
            if info.get("offer_id"):
                already += 1
                continue
            item_name = info.get("name", "").lower().strip()
            for offer_id, lot in demping.items():
                trigger = lot.get("triggers", "").lower().strip()
                if not trigger or trigger == "другое количество":
                    continue
                if item_name == trigger or trigger in item_name or item_name in trigger:
                    info["offer_id"] = int(offer_id)
                    mp[game_name][item_id] = info
                    linked += 1
                    break

        save_mp(user_id, mp)
        items = mp.get(game_name, {})
        total_items = len([k for k in items if k != "_meta"])
        unlinked = total_items - already - linked
        link_kb_rows = []
        if unlinked > 0:
            link_kb_rows.append([InlineKeyboardButton(
                text="🔗 Привязать вручную",
                callback_data=f"mp_manuallink_{game_hash}"
            )])
        link_kb_rows.append([InlineKeyboardButton(
            text="↩️ К игре",
            callback_data=f"mp_game_{game_hash}"
        )])
        await call.message.answer(
            f"🔗 <b>Привязка для «{_html.escape(game_name)}»</b>\n\n"
            f"✅ Привязано: <b>{linked}</b>\n"
            f"⏭ Уже было: <b>{already}</b>\n"
            f"❌ Не найдено: <b>{unlinked}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=link_kb_rows)
        )
        await call.answer()

    # --- Привязать offer_id вручную: выбор товара ---
    elif data.startswith("mp_manuallink_"):
        game_hash = data.split("_")[2]
        game_name = next((g for g in games if get_hash(g) == game_hash), None)
        if not game_name:
            return await call.answer("Игра не найдена", show_alert=True)
        items = mp.get(game_name, {})
        unlinked_items = [
            (iid, info) for iid, info in items.items()
            if iid != "_meta" and isinstance(info, dict) and not get_item_offer_ids(info)
        ]
        if not unlinked_items:
            return await call.answer("Все товары уже привязаны!", show_alert=True)
        rows = []
        for iid, info in unlinked_items:
            name = info.get("name", iid)
            cashback_key = info.get("cashback", "none")
            short = _short(name, 36)
            badge = _cashback_badge(cashback_key)
            rows.append([InlineKeyboardButton(
                text=f"📦 {short} | {badge}",
                callback_data=f"mp_manualitem_{game_hash}_{iid}"
            )])
        rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data=f"mp_game_{game_hash}")])
        await call.message.edit_text(
            f"🔗 <b>Ручная привязка лота</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"🎮 <b>{_html.escape(game_name)}</b>\n"
            f"🕳 Без лотов: <b>{len(unlinked_items)}</b>\n\n"
            f"<i>Выберите товар, к которому нужно добавить offer_id.</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )
        await call.answer()

    # --- Привязать offer_id вручную: ввод ID ---
    elif data.startswith("mp_manualitem_"):
        parts = data.split("_")
        game_hash = parts[2]
        item_id = parts[3]
        game_name = next((g for g in games if get_hash(g) == game_hash), None)
        if not game_name:
            return await call.answer("Игра не найдена", show_alert=True)
        item_info = mp.get(game_name, {}).get(item_id)
        if not item_info:
            return await call.answer("Товар не найден", show_alert=True)
        item_name = item_info.get("name", item_id)
        await state.update_data(manual_game_hash=game_hash, manual_item_id=item_id)
        await state.set_state(MinPriceStates.waiting_manual_offer_id)
        await call.message.edit_text(
            f"🔗 <b>Добавление лота</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"🎮 <b>{_html.escape(game_name)}</b>\n"
            f"📦 <b>{_html.escape(item_name)}</b>\n\n"
            f"Отправьте числовой <code>offer_id</code> лота с FunPay.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data=f"mp_manuallink_{game_hash}")]
            ])
        )
        await call.answer()

    # --- Редактировать список товаров с фото ---
    elif data.startswith("mp_edit_items_"):
        game_hash = data.split("_")[3]
        state_data = await state.get_data()
        names = state_data.get("photo_items", [])
        current = "\n".join(f"{i+1}. {n}" for i, n in enumerate(names))
        await state.update_data(current_game_hash=game_hash)
        await state.set_state(MinPriceStates.waiting_edit_items)
        await call.message.answer(
            f"✏️ <b>Редактирование списка</b>\n\n"
            f"Текущий список:\n<code>{_html.escape(current)}</code>\n\n"
            f"Напишите что нужно исправить — ИИ обновит список.",
            parse_mode="HTML"
        )
        await call.answer()

    # --- Выбор кэшбека ---
    elif data.startswith("mp_cb_"):
        cb_key = data.split("_")[2]
        if cb_key not in CASHBACK_OPTIONS:
            return await call.answer("Неверный вариант", show_alert=True)

        state_data = await state.get_data()
        game_name = state_data.get("current_game_name")
        item_name = state_data.get("current_item_name")
        game_hash = state_data.get("current_game_hash")
        cost = state_data.get("current_item_cost")
        min_price = calc_min_price(cost)

        mp = load_mp(user_id)
        if game_name not in mp:
            mp[game_name] = {}
        # Генерируем уникальный ID чтобы одинаковые названия не перезаписывали друг друга
        import secrets
        item_id = hashlib.md5(f"{item_name}_{cb_key}_{secrets.token_hex(4)}".encode()).hexdigest()[:8]
        mp[game_name][item_id] = {
            "name": item_name,
            "cost": cost,
            "min_price": min_price,
            "cashback": cb_key
        }
        save_mp(user_id, mp)

        cashback_label = CASHBACK_OPTIONS[cb_key]

        # --- Проверяем bulk-режим (импорт из фото) ---
        bulk_mode = state_data.get("bulk_mode", False)
        if bulk_mode:
            photo_items = state_data.get("photo_items", [])
            photo_idx = state_data.get("photo_idx", 0) + 1

            if photo_idx < len(photo_items):
                next_name = photo_items[photo_idx]
                next_min = calc_min_price(0)
                await state.update_data(
                    photo_idx=photo_idx,
                    current_item_name=next_name,
                    current_item_cost=None
                )
                await state.set_state(MinPriceStates.waiting_bulk_cost)
                await call.message.edit_text(
                    f"✅ <i>Сохранено ({photo_idx}/{len(photo_items)})</i>\n\n"
                    f"📦 <b>Товар {photo_idx + 1}/{len(photo_items)}:</b> <code>{next_name}</code>\n\n"
                    f"Введите <b>закупочную цену</b> (₽):",
                    parse_mode="HTML"
                )
            else:
                await state.clear()
                await call.message.edit_text(
                    f"✅ <b>Импорт завершён!</b>\n\n"
                    f"Добавлено товаров: <b>{len(photo_items)}</b>",
                    parse_mode="HTML",
                    reply_markup=back_kb(f"mp_game_{game_hash}")
                )
            await call.answer()
            return

        # --- Обычный режим ---
        await call.message.edit_text(
            f"✅ <b>Товар добавлен!</b>\n\n"
            f"📦 <b>{item_name}</b> <i>({cashback_label})</i>\n"
            f"   💸 Закуп: <code>{cost:.2f} ₽</code>\n"
            f"   💰 Мин. цена: <code>{min_price:.2f} ₽</code>",
            parse_mode="HTML",
            reply_markup=back_kb(f"mp_game_{game_hash}")
        )
        await state.clear()
        await call.answer()

    # --- Список товаров для редактирования ---
    elif data.startswith("mp_editlist_") and not data.startswith("mp_editlist_pg_"):
        game_hash = data.split("_")[2]
        game_name = next((g for g in games if get_hash(g) == game_hash), None)
        if not game_name:
            return await call.answer("Игра не найдена", show_alert=True)
        items = get_items(mp, game_name)
        if not items:
            return await call.answer("Товаров нет", show_alert=True)

        item_list = list(items.items())
        total_pages = (len(item_list) + 15) // 15 or 1
        page = 0

        await state.update_data(current_game_hash=game_hash, current_game_name=game_name)
        await state.set_state(MinPriceStates.waiting_edit_item_select)

        linked_count = sum(1 for _, info in item_list if get_item_offer_ids(info))
        text = (
            f"📝 <b>Редактирование товаров</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"🎮 <b>{_html.escape(game_name)}</b>\n"
            f"📦 Товаров: <b>{len(item_list)}</b>\n"
            f"🔗 С лотами: <b>{linked_count}</b>\n\n"
            f"<i>Выберите товар.</i>"
        )
        rows = []
        for iid, info in item_list[page*15:(page+1)*15]:
            name = info.get("name", iid) if isinstance(info, dict) else iid
            cashback_key = info.get("cashback", "none") if isinstance(info, dict) else "none"
            rows.append([InlineKeyboardButton(
                text=f"📦 {_short(name, 36)} | {_cashback_badge(cashback_key)}",
                callback_data=f"mp_edititem_{game_hash}_{iid}"
            )])

        if total_pages > 1:
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"mp_editlist_pg_{game_hash}_{page-1}"))
            nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="none"))
            if page < total_pages - 1:
                nav.append(InlineKeyboardButton(text="➡️", callback_data=f"mp_editlist_pg_{game_hash}_{page+1}"))
            rows.append(nav)

        rows.append([InlineKeyboardButton(text="↩️ К игре", callback_data=f"mp_game_{game_hash}")])

        await call.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await call.answer()

    # --- Пагинация списка редактирования ---
    elif data.startswith("mp_editlist_pg_"):
        parts = data.split("_")
        game_hash = parts[3]
        page = int(parts[4])
        game_name = next((g for g in games if get_hash(g) == game_hash), None)
        if not game_name:
            return await call.answer("Игра не найдена", show_alert=True)
        items = get_items(mp, game_name)
        item_list = list(items.items())
        total_pages = (len(item_list) + 15) // 15 or 1

        linked_count = sum(1 for _, info in item_list if get_item_offer_ids(info))
        text = (
            f"📝 <b>Редактирование товаров</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"🎮 <b>{_html.escape(game_name)}</b>\n"
            f"📦 Товаров: <b>{len(item_list)}</b>\n"
            f"🔗 С лотами: <b>{linked_count}</b>\n"
            f"📄 Страница: <b>{page + 1}/{total_pages}</b>\n\n"
            f"<i>Выберите товар.</i>"
        )
        rows = []
        for iid, info in item_list[page*15:(page+1)*15]:
            name = info.get("name", iid) if isinstance(info, dict) else iid
            cashback_key = info.get("cashback", "none") if isinstance(info, dict) else "none"
            rows.append([InlineKeyboardButton(
                text=f"📦 {_short(name, 36)} | {_cashback_badge(cashback_key)}",
                callback_data=f"mp_edititem_{game_hash}_{iid}"
            )])

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"mp_editlist_pg_{game_hash}_{page-1}"))
        nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="none"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"mp_editlist_pg_{game_hash}_{page+1}"))
        rows.append(nav)
        rows.append([InlineKeyboardButton(text="↩️ К игре", callback_data=f"mp_game_{game_hash}")])

        await call.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await call.answer()

    # --- Меню редактирования товара ---
    elif data.startswith("mp_edititem_"):
        parts = data.split("_")
        game_hash = parts[2]
        item_id = parts[3]
        game_name = next((g for g in games if get_hash(g) == game_hash), None)
        if not game_name:
            return await call.answer("Игра не найдена", show_alert=True)

        item_info = mp.get(game_name, {}).get(item_id)
        if not item_info:
            return await call.answer("Товар не найден", show_alert=True)

        await state.update_data(
            editing_item_id=item_id,
            editing_game_hash=game_hash,
            editing_game_name=game_name,
            pending_changes={}
        )

        name = item_info.get("name", item_id)
        cost = item_info.get("cost", 0)
        min_price = item_info.get("min_price", 0)
        cashback_key = item_info.get("cashback", "none")
        offer_ids = get_item_offer_ids(item_info)

        text = build_edit_item_text(name, cost, min_price, cashback_key, offer_ids)

        rows = [
            [
                InlineKeyboardButton(text="📝 Название", callback_data=f"mp_edt_name_{game_hash}_{item_id}"),
                InlineKeyboardButton(text="💸 Цена закупа", callback_data=f"mp_edt_cost_{game_hash}_{item_id}"),
            ],
            [
                InlineKeyboardButton(text="💳 Кэшбек", callback_data=f"mp_edt_cashback_{game_hash}_{item_id}"),
                InlineKeyboardButton(text="🔗 Лоты", callback_data=f"mp_edt_offerid_{game_hash}_{item_id}"),
            ],
            [
                InlineKeyboardButton(text="💾 Сохранить", callback_data=f"mp_edt_save_{game_hash}_{item_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"mp_edt_cancel_{game_hash}_{item_id}"),
            ],
            [
                InlineKeyboardButton(text="↩️ К списку", callback_data=f"mp_editlist_{game_hash}"),
            ]
        ]

        await call.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await call.answer()

    # --- Редактирование параметров ---
    elif data.startswith("mp_edt_name_"):
        parts = data.split("_")
        game_hash = parts[2]
        item_id = parts[3]
        state_data = await state.get_data()
        current_name = mp[state_data.get("editing_game_name", "")].get(item_id, {}).get("name", "")

        await state.update_data(editing_param="name")
        await state.set_state(MinPriceStates.waiting_edit_param_value)
        await call.message.edit_text(
            f"📝 <b>Редактирование названия</b>\n\n"
            f"Текущее: <code>{_html.escape(current_name)}</code>\n\n"
            f"Введите новое название:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data=f"mp_edititem_{game_hash}_{item_id}")]
            ])
        )
        await call.answer()

    elif data.startswith("mp_edt_cost_"):
        parts = data.split("_")
        game_hash = parts[2]
        item_id = parts[3]
        state_data = await state.get_data()
        game_name = state_data.get("editing_game_name", "")
        current_cost = mp[game_name].get(item_id, {}).get("cost", 0)

        await state.update_data(editing_param="cost")
        await state.set_state(MinPriceStates.waiting_edit_param_value)
        await call.message.edit_text(
            f"💸 <b>Редактирование цены закупа</b>\n\n"
            f"Текущая: <code>{current_cost:.2f} ₽</code>\n\n"
            f"Введите новую цену (₽):",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data=f"mp_edititem_{game_hash}_{item_id}")]
            ])
        )
        await call.answer()

    elif data.startswith("mp_edt_offerid_"):
        parts = data.split("_")
        game_hash = parts[2]
        item_id = parts[3]
        state_data = await state.get_data()
        game_name = state_data.get("editing_game_name", "")
        item_info = mp[game_name].get(item_id, {})
        item_name = item_info.get("name", item_id)
        current_ids = get_item_offer_ids(item_info)
        ids_display = ", ".join([f"#{oid}" for oid in current_ids]) if current_ids else "нет"

        await state.update_data(editing_param="offer_ids")
        await state.set_state(MinPriceStates.waiting_edit_param_value)
        await call.message.edit_text(
            f"🔗 <b>Редактирование лотов</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"📦 <b>{_html.escape(item_name)}</b>\n"
            f"Текущие: <code>{ids_display}</code>\n\n"
            f"Отправьте новые ID через запятую.\n"
            f"Пример: <code>12345, 67890</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data=f"mp_edititem_{game_hash}_{item_id}")]
            ])
        )
        await call.answer()

    elif data.startswith("mp_edt_cashback_"):
        parts = data.split("_")
        game_hash = parts[2]
        item_id = parts[3]

        await state.update_data(editing_param="cashback")
        await call.message.edit_text(
            f"💳 <b>Выберите тип кэшбека</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 С кэшбеком", callback_data=f"mp_edt_cashback_choice_{game_hash}_{item_id}_yes")],
                [InlineKeyboardButton(text="💵 Без кэшбека", callback_data=f"mp_edt_cashback_choice_{game_hash}_{item_id}_no")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data=f"mp_edititem_{game_hash}_{item_id}")],
            ])
        )
        await call.answer()

    elif data.startswith("mp_edt_cashback_choice_"):
        parts = data.split("_")
        game_hash = parts[3]
        item_id = parts[4]
        cashback_val = parts[5]

        state_data = await state.get_data()
        pending = state_data.get("pending_changes", {})
        pending["cashback"] = cashback_val
        await state.update_data(pending_changes=pending)

        game_name = state_data.get("editing_game_name", "")
        await _show_edit_menu(call, game_hash, item_id, game_name, mp, state_data)
        await call.answer()

    # --- Сохранение изменений ---
    elif data.startswith("mp_edt_save_"):
        parts = data.split("_")
        game_hash = parts[3]
        item_id = parts[4]
        state_data = await state.get_data()
        game_name = state_data.get("editing_game_name", "")
        pending = state_data.get("pending_changes", {})

        if pending:
            for key, val in pending.items():
                mp[game_name][item_id][key] = val
            if "cost" in pending:
                mp[game_name][item_id]["min_price"] = calc_min_price(pending["cost"])
            save_mp(user_id, mp)

        await state.clear()
        await call.message.edit_text("✅ Изменения сохранены!", parse_mode="HTML", reply_markup=back_kb(f"mp_editlist_{game_hash}"))
        await call.answer()

    # --- Отмена изменений ---
    elif data.startswith("mp_edt_cancel_"):
        parts = data.split("_")
        game_hash = parts[3]
        item_id = parts[4]

        await state.clear()
        await call.message.edit_text("❌ Изменения отменены", parse_mode="HTML", reply_markup=back_kb(f"mp_editlist_{game_hash}"))
        await call.answer()

    # --- Выбор товаров для удаления (мультиселект) ---
    elif data.startswith("mp_delitem_pick_"):
        game_hash = data.split("_")[3]
        game_name = next((g for g in games if get_hash(g) == game_hash), None)
        if not game_name:
            return await call.answer("Игра не найдена", show_alert=True)
        items = mp.get(game_name, {})
        real_items = {k: v for k, v in items.items() if k != "_meta"}
        if not real_items:
            return await call.answer("Товаров нет", show_alert=True)
        await state.update_data(del_selected=[], del_game_hash=game_hash)
        await call.message.edit_text(
            f"🗑 <b>Выберите товары для удаления из «{_html.escape(game_name)}»:</b>\n"
            f"<i>Нажмите на товар, чтобы выделить его</i>",
            reply_markup=del_item_pick_kb(game_hash, items, set()),
            parse_mode="HTML"
        )
        await call.answer()

    # --- Переключение выделения товара ---
    elif data.startswith("mp_delitem_toggle_"):
        parts = data.split("_")
        game_hash = parts[3]
        item_id = parts[4]
        game_name = next((g for g in games if get_hash(g) == game_hash), None)
        if not game_name:
            return await call.answer("Игра не найдена", show_alert=True)
        items = mp.get(game_name, {})
        state_data = await state.get_data()
        selected = set(state_data.get("del_selected", []))
        if item_id in selected:
            selected.discard(item_id)
        else:
            selected.add(item_id)
        await state.update_data(del_selected=list(selected))
        await call.message.edit_reply_markup(reply_markup=del_item_pick_kb(game_hash, items, selected))
        await call.answer()

    # --- Удаление выбранных товаров ---
    elif data.startswith("mp_delitem_do_"):
        game_hash = data.split("_")[3]
        game_name = next((g for g in games if get_hash(g) == game_hash), None)
        if not game_name:
            return await call.answer("Игра не найдена", show_alert=True)
        state_data = await state.get_data()
        selected = set(state_data.get("del_selected", []))
        if not selected:
            return await call.answer("Ничего не выбрано", show_alert=True)
        items = mp.get(game_name, {})
        deleted = 0
        for item_id in selected:
            if item_id in items:
                del items[item_id]
                deleted += 1
        mp[game_name] = items
        save_mp(user_id, mp)
        await state.update_data(del_selected=[])
        await call.answer(f"🗑 Удалено товаров: {deleted}")
        meta = get_game_meta(mp, game_name)
        sbp_rate = meta.get("sbp_rate")
        text, total_pages = build_game_text(game_name, get_items(mp, game_name), 0, sbp_rate)
        await call.message.edit_text(
            text,
            reply_markup=game_view_kb(game_hash, 0, total_pages, has_rate=bool(sbp_rate)),
            parse_mode="HTML"
        )


# ====================== ОБРАБОТКА ВВОДА ======================

@router.message(MinPriceStates.waiting_add_game)
async def proc_add_game(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await state.clear()

    game_name = message.text.strip()
    if not game_name:
        return await message.answer("⚠️ Введите название игры.")

    mp = load_mp(message.from_user.id)
    if game_name in mp:
        await message.answer(f"⚠️ Игра <b>«{game_name}»</b> уже существует.", parse_mode="HTML")
    else:
        mp[game_name] = {}
        save_mp(message.from_user.id, mp)
        await message.answer(
            f"✅ <b>Игра «{game_name}» добавлена!</b>\nТеперь можно добавлять товары.",
            parse_mode="HTML"
        )
    await state.clear()


@router.message(MinPriceStates.waiting_rename_game)
async def proc_rename_game(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await state.clear()

    new_name = message.text.strip()
    if not new_name:
        return await message.answer("⚠️ Введите название игры.")

    state_data = await state.get_data()
    old_name = state_data.get("current_game_name")
    game_hash = state_data.get("current_game_hash")

    mp = load_mp(message.from_user.id)
    if new_name in mp and new_name != old_name:
        return await message.answer(f"⚠️ Игра <b>«{new_name}»</b> уже существует.", parse_mode="HTML")

    if old_name in mp:
        mp[new_name] = mp.pop(old_name)
        save_mp(message.from_user.id, mp)
        new_hash = get_hash(new_name)
        await message.answer(
            f"✅ <b>Переименовано:</b> «{old_name}» → «{new_name}»",
            parse_mode="HTML",
            reply_markup=back_kb(f"mp_game_{new_hash}")
        )
    else:
        await message.answer("❌ Игра не найдена.", parse_mode="HTML")

    await state.clear()


@router.message(MinPriceStates.waiting_add_item_name, F.text)
async def proc_add_item_name(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await state.clear()

    item_name = message.text.strip()
    if not item_name:
        return await message.answer("⚠️ Введите название товара.")

    await state.update_data(current_item_name=item_name)
    await state.set_state(MinPriceStates.waiting_add_item_cost)
    await message.answer(
        f"💸 <b>Товар:</b> <code>{item_name}</code>\n\nВведите <b>закупочную цену</b> (₽):",
        parse_mode="HTML"
    )


@router.message(MinPriceStates.waiting_add_item_name, F.photo)
async def proc_add_item_photo(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await state.clear()

    await message.answer("⏳ <b>Распознаю товары...</b>", parse_mode="HTML")

    try:
        photo = message.photo[-1]
        file_info = await message.bot.get_file(photo.file_id)
        buf = BytesIO()
        await message.bot.download_file(file_info.file_path, buf)
        image_data = base64.b64encode(buf.getvalue()).decode("utf-8")

        response = _get_groq_client().chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}
                    },
                    {
                        "type": "text",
                        "text": (
                            "На скриншоте список товаров. "
                            "Для каждого товара выпиши только его основное название — то что отличает один товар от другого. "
                            "Включай количество, единицу и название предмета. "
                            "Не включай значки, иконки, пометки типа 'Пополнение', 'Быстро', эмодзи и прочие декорации. "
                            "Каждый товар с новой строки, без нумерации, без заголовков, без вступлений. "
                            "Первая строка — сразу первый товар."
                        )
                    }
                ]
            }],
            max_tokens=1024
        )

        raw = response.choices[0].message.content.strip()
        names = [strip_leading_emoji(line) for line in raw.splitlines() if line.strip()]
        # Убираем пустые строки, заголовки (оканчивающиеся на :), слишком короткие строки
        names = [n for n in names if n and not n.endswith(":") and len(n) > 2]

        if not names:
            return await message.answer("❌ Не удалось распознать товары. Попробуйте другой скриншот.")

        state_data = await state.get_data()
        game_name = state_data.get("current_game_name")
        game_hash = state_data.get("current_game_hash")

        await state.update_data(photo_items=names)
        await state.set_state(MinPriceStates.waiting_photo_import)
        await _send_photo_preview(message, names, game_hash)

    except Exception as e:
        await message.answer(f"❌ Ошибка распознавания: <code>{e}</code>", parse_mode="HTML")
        await state.clear()


async def _send_photo_preview(target, names: list, game_hash: str):
    preview = "\n".join(f"{i+1}. {n}" for i, n in enumerate(names[:30]))
    if len(names) > 30:
        preview += f"\n<i>...и ещё {len(names) - 30}</i>"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"✅ Добавить все ({len(names)} шт.)",
            callback_data=f"mp_import_confirm_{game_hash}"
        )],
        [InlineKeyboardButton(text="✏️ Редактировать список", callback_data=f"mp_edit_items_{game_hash}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"mp_game_{game_hash}")],
    ])
    await target.answer(
        f"📋 <b>Найдено товаров: {len(names)}</b>\n\n{preview}\n\n"
        f"Всё верно? Или нажмите <b>«Редактировать»</b> чтобы исправить список.",
        parse_mode="HTML",
        reply_markup=kb
    )


@router.message(MinPriceStates.waiting_edit_items, F.text)
async def proc_edit_items(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await state.clear()

    state_data = await state.get_data()
    names = state_data.get("photo_items", [])
    game_hash = state_data.get("current_game_hash")
    current_list = "\n".join(f"{i+1}. {n}" for i, n in enumerate(names))

    await message.answer("⏳ <b>Обновляю список...</b>", parse_mode="HTML")

    try:
        response = _get_groq_client().chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": (
                    f"Вот список товаров:\n{current_list}\n\n"
                    f"Инструкция по редактированию: {message.text}\n\n"
                    f"Верни исправленный список — только названия товаров, каждое с новой строки, "
                    f"без нумерации, без пояснений, без вступлений."
                )
            }],
            max_tokens=1024
        )
        raw = response.choices[0].message.content.strip()
        new_names = [strip_leading_emoji(line) for line in raw.splitlines() if line.strip()]
        new_names = [n for n in new_names if n and len(n) > 1]
        if not new_names:
            return await message.answer("⚠️ ИИ вернул пустой список, попробуйте ещё раз.")
        await state.update_data(photo_items=new_names)
        await state.set_state(MinPriceStates.waiting_photo_import)
        await _send_photo_preview(message, new_names, game_hash)
    except Exception as e:
        await message.answer(f"❌ Ошибка: <code>{e}</code>", parse_mode="HTML")
        await state.set_state(MinPriceStates.waiting_edit_items)


@router.message(MinPriceStates.waiting_bulk_cost)
async def proc_bulk_cost(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await state.clear()

    try:
        cost = float(message.text.strip().replace(",", "."))
        if cost <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("⚠️ Введите корректное положительное число.")

    state_data = await state.get_data()
    item_name = state_data.get("current_item_name")
    photo_items = state_data.get("photo_items", [])
    photo_idx = state_data.get("photo_idx", 0)
    min_price = calc_min_price(cost)

    await state.update_data(current_item_cost=cost)
    await state.set_state(MinPriceStates.waiting_cashback)

    await message.answer(
        f"💸 Закуп: <code>{cost:.2f} ₽</code>\n"
        f"💰 Мин. цена: <code>{min_price:.2f} ₽</code>\n\n"
        f"Кэшбек для <b>«{item_name}»</b>?  <i>({photo_idx + 1}/{len(photo_items)})</i>",
        parse_mode="HTML",
        reply_markup=cashback_kb(bulk=True)
    )


@router.message(MinPriceStates.waiting_add_item_cost)
async def proc_add_item_cost(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await state.clear()

    try:
        cost = float(message.text.strip().replace(",", "."))
        if cost <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("⚠️ Введите корректное положительное число.")

    data = await state.get_data()
    item_name = data.get("current_item_name")
    min_price = calc_min_price(cost)

    await state.update_data(current_item_cost=cost)
    await state.set_state(MinPriceStates.waiting_cashback)

    await message.answer(
        f"💸 Закуп: <code>{cost:.2f} ₽</code>\n"
        f"💰 Мин. цена: <code>{min_price:.2f} ₽</code>\n\n"
        f"Как покупается товар <b>«{item_name}»</b>?",
        parse_mode="HTML",
        reply_markup=cashback_kb()
    )


@router.message(MinPriceStates.waiting_manual_offer_id)
async def proc_manual_offer_id(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await state.clear()

    text = message.text.strip() if message.text else ""
    if not text.isdigit():
        return await message.answer("⚠️ Введите числовой ID лота (только цифры).")

    offer_id = int(text)
    state_data = await state.get_data()
    game_hash = state_data.get("manual_game_hash")
    item_id = state_data.get("manual_item_id")

    mp = load_mp(message.from_user.id)
    games = list(mp.keys())
    game_name = next((g for g in games if get_hash(g) == game_hash), None)
    if not game_name or item_id not in mp.get(game_name, {}):
        await state.clear()
        return await message.answer("❌ Товар не найден.")

    add_offer_id_to_item(mp[game_name][item_id], offer_id)
    save_mp(message.from_user.id, mp)
    item_name = mp[game_name][item_id].get("name", item_id)
    current_ids = get_item_offer_ids(mp[game_name][item_id])
    ids_str = ", ".join([f"#{oid}" for oid in current_ids])

    await state.clear()
    await message.answer(
        f"✅ <b>Лот привязан</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎮 <b>{_html.escape(game_name)}</b>\n"
        f"📦 <b>{_html.escape(item_name)}</b>\n"
        f"🔗 Лоты: <code>{ids_str}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Привязать ещё", callback_data=f"mp_manuallink_{game_hash}")],
            [InlineKeyboardButton(text="↩️ К игре", callback_data=f"mp_game_{game_hash}")],
        ])
    )


@router.message(MinPriceStates.waiting_edit_param_value)
async def proc_edit_param_value(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await state.clear()

    state_data = await state.get_data()
    param = state_data.get("editing_param")
    game_name = state_data.get("editing_game_name")
    item_id = state_data.get("editing_item_id")
    game_hash = state_data.get("editing_game_hash")
    pending = state_data.get("pending_changes", {})

    text = message.text.strip() if message.text else ""
    if not text:
        return await message.answer("⚠️ Значение не может быть пустым.")

    try:
        if param == "name":
            pending["name"] = text
        elif param == "cost":
            cost = float(text.replace(",", "."))
            if cost <= 0:
                raise ValueError
            pending["cost"] = cost
        elif param == "offer_ids":
            # Парсим список ID через запятую
            ids_list = []
            for id_part in text.split(","):
                id_clean = id_part.strip().replace("#", "")
                if not id_clean:
                    continue
                if not id_clean.isdigit():
                    raise ValueError
                ids_list.append(int(id_clean))
            if not ids_list:
                raise ValueError
            pending["offer_ids"] = ids_list
        else:
            return await message.answer("⚠️ Неизвестный параметр.")

    except ValueError:
        if param == "cost":
            return await message.answer("⚠️ Введите корректное положительное число для цены.")
        elif param == "offer_ids":
            return await message.answer("⚠️ Введите числовые ID лотов через запятую (например: 12345, 67890).")
        else:
            return await message.answer("⚠️ Некорректное значение.")

    await state.update_data(pending_changes=pending)

    mp = load_mp(message.from_user.id)
    item_info = mp[game_name].get(item_id, {})

    name = pending.get("name", item_info.get("name", item_id))
    cost = pending.get("cost", item_info.get("cost", 0))
    min_price = calc_min_price(cost) if "cost" in pending else item_info.get("min_price", 0)
    cashback_key = pending.get("cashback", item_info.get("cashback", "none"))
    offer_ids = pending.get("offer_ids", get_item_offer_ids(item_info))

    text = build_edit_item_text(name, cost, min_price, cashback_key, offer_ids)

    rows = [
        [
            InlineKeyboardButton(text="📝 Название", callback_data=f"mp_edt_name_{game_hash}_{item_id}"),
            InlineKeyboardButton(text="💸 Цена закупа", callback_data=f"mp_edt_cost_{game_hash}_{item_id}"),
        ],
        [
            InlineKeyboardButton(text="💳 Кэшбек", callback_data=f"mp_edt_cashback_{game_hash}_{item_id}"),
            InlineKeyboardButton(text="🔗 Лоты", callback_data=f"mp_edt_offerid_{game_hash}_{item_id}"),
        ],
        [
            InlineKeyboardButton(text="💾 Сохранить", callback_data=f"mp_edt_save_{game_hash}_{item_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"mp_edt_cancel_{game_hash}_{item_id}"),
        ],
        [
            InlineKeyboardButton(text="↩️ К списку", callback_data=f"mp_editlist_{game_hash}"),
        ]
    ]

    await message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
def _get_groq_client():
    global groq_client
    key = get_groq_api_key()
    if groq_client is None or getattr(groq_client, "api_key", None) != key:
        groq_client = Groq(api_key=key)
    return groq_client


def _get_openrouter_client():
    global openrouter_client
    key = get_openrouter_api_key()
    if openrouter_client is None:
        try:
            from openai import OpenAI
            openrouter_client = OpenAI(
                api_key=key,
                base_url="https://openrouter.ai/api/v1"
            )
        except ImportError:
            openrouter_client = None
    return openrouter_client
