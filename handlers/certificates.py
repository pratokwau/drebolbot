# handlers/certificates.py

import html as _html
import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import requests as _req
from io import BytesIO
from hashlib import md5

from aiogram import F, Router, types
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from config import ADMIN_ID
from database import db
from handlers.demping import CARDINAL_SERVICE_NAME, CARDINAL_TARGET_PATH
from handlers.minprice import (
    COMMISSION,
    MIN_PROFIT,
    _get_user_lots,
    _match_offers_with_ai,
    groq_client,
    resolve_sbp_rate_for_game,
    strip_leading_emoji,
)
from handlers.utils import no_access_callback

router = Router()

CERT_DEMPING_FILE = "data/certificates_demping.json"
CERT_CARDINAL_TARGET_PATH = os.getenv(
    "CERT_CARDINAL_TARGET_PATH",
    CARDINAL_TARGET_PATH,
)
CERT_CARDINAL_SERVICE_NAME = os.getenv(
    "CERT_CARDINAL_SERVICE_NAME",
    CARDINAL_SERVICE_NAME,
)
GAMES_PER_PAGE = 8
ITEMS_PER_PAGE = 12
class CertificateStates(StatesGroup):
    waiting_game_name = State()
    waiting_rate = State()
    waiting_items = State()
    waiting_edit_items = State()
    waiting_cost = State()
    waiting_offer_id = State()
    waiting_upload = State()


def get_cert_file(user_id: int) -> str:
    path = f"users/{user_id}"
    os.makedirs(path, exist_ok=True)
    return f"{path}/certificates.json"


def load_certificates(user_id: int = ADMIN_ID) -> dict:
    path = get_cert_file(user_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_certificates(data: dict, user_id: int = ADMIN_ID):
    with open(get_cert_file(user_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_cert_demping() -> dict:
    if not os.path.exists(CERT_DEMPING_FILE):
        return {}
    try:
        with open(CERT_DEMPING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cert_demping(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(CERT_DEMPING_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def calc_min_price(cost: float) -> float:
    return round((float(cost) + MIN_PROFIT) / (1 - COMMISSION), 2)


def calc_site_price(cost: float, rate: float) -> float:
    return round(calc_min_price(cost) * float(rate), 2)


def get_hash(text: str) -> str:
    return md5(text.encode()).hexdigest()[:8]


def _money(value) -> float:
    try:
        return round(float(str(value).replace(" ", "").replace("\xa0", "").replace(",", ".")), 2)
    except (TypeError, ValueError):
        return 0.0


def _rate(value) -> float:
    try:
        return float(str(value).replace(" ", "").replace("\xa0", "").replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def _short(text: str, limit: int = 42) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _item_id(nominal: float) -> str:
    return md5(f"cert_{nominal}".encode()).hexdigest()[:8]


def _parse_nominals(text: str) -> list[float]:
    values = []
    for raw in re.findall(r"\d+(?:[\s\xa0]\d{3})*(?:[.,]\d+)?|\d+", text or ""):
        value = _money(raw)
        if value > 0 and value not in values:
            values.append(value)
    return values


def _clean_cert_name(text: str) -> str:
    text = strip_leading_emoji(str(text or "").strip())
    text = re.sub(r"^\s*\d+[\.\)]\s*", "", text)
    text = re.sub(r"\s*\((?:с кэшбеком|без кэшбека|нет кэшбека)\)\s*$", "", text, flags=re.IGNORECASE)
    # Частый мусор OCR/LLM вместо знака ₽: "\: P]$", "\: ₽]$" и похожее.
    text = re.sub(r"\s*\\+\s*:\s*[PРР₽]\]?\$?\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*[\]\$]+\s*$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—")
    nominals = _parse_nominals(text)
    if nominals and re.fullmatch(r"[\d\s\xa0.,]+(?:руб\.?|₽)?", text, flags=re.IGNORECASE):
        return f"{nominals[0]:g} ₽"
    return text


def _normalize_cert_matches(matches: dict, items: dict) -> dict:
    """Убирает minprice-варианты кэшбека из AI-матчинга сертификатов."""
    item_names = {
        _normalize(_clean_cert_name(info.get("name", ""))): _clean_cert_name(info.get("name", ""))
        for _, info in items.items()
        if isinstance(info, dict) and info.get("name")
    }
    normalized = {}
    for raw_name, offer_ids in matches.items():
        clean_name = _clean_cert_name(raw_name)
        norm = _normalize(clean_name)
        if norm not in item_names:
            continue
        display_name = item_names[norm]
        if display_name not in normalized:
            normalized[display_name] = offer_ids
    return normalized


def _match_certificate_lots_by_nominal(lots: dict, items: dict) -> dict:
    """Сертификаты сопоставляем строго: номинал товара = номинал в названии лота."""
    lots_by_nominal = {}
    for offer_id, lot in lots.items():
        lot_name = str(lot.get("name", ""))
        for nominal in _parse_nominals(lot_name):
            lots_by_nominal.setdefault(nominal, [])
            if offer_id not in lots_by_nominal[nominal]:
                lots_by_nominal[nominal].append(offer_id)

    matches = {}
    used_offer_ids = set()
    for _, info in items.items():
        if not isinstance(info, dict):
            continue
        item_name = _clean_cert_name(info.get("name", ""))
        nominal = _money(info.get("nominal")) or _nominal_from_name(item_name)
        if not nominal:
            continue
        offer_ids = lots_by_nominal.get(nominal, [])
        if not offer_ids:
            continue
        offer_id = next((oid for oid in offer_ids if oid not in used_offer_ids), offer_ids[0])
        used_offer_ids.add(offer_id)
        matches[item_name] = [offer_id]
    return matches


def _filter_ai_matches_by_nominal(matches: dict, lots: dict, items: dict) -> dict:
    """Fallback после ИИ: оставляем только offer_id с тем же номиналом."""
    item_nominals = {
        _normalize(_clean_cert_name(info.get("name", ""))): (
            _money(info.get("nominal")) or _nominal_from_name(info.get("name", ""))
        )
        for _, info in items.items()
        if isinstance(info, dict) and info.get("name")
    }
    lot_nominals = {
        str(offer_id): set(_parse_nominals(str(lot.get("name", ""))))
        for offer_id, lot in lots.items()
    }
    filtered = {}
    used_offer_ids = set()
    for raw_name, offer_ids in matches.items():
        clean_name = _clean_cert_name(raw_name)
        nominal = item_nominals.get(_normalize(clean_name))
        if not nominal:
            continue
        good_ids = [
            oid for oid in offer_ids
            if nominal in lot_nominals.get(str(oid), set()) and str(oid) not in used_offer_ids
        ]
        if good_ids:
            filtered[clean_name] = [good_ids[0]]
            used_offer_ids.add(str(good_ids[0]))
    return filtered


def _parse_item_names_from_text(text: str) -> list[str]:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) <= 1:
        nominals = _parse_nominals(text)
        if nominals:
            return [f"{value:g} ₽" for value in nominals]
    names = []
    for line in lines:
        line = re.sub(r"^\s*[\d\-\•\.\)\s]+", "", line).strip()
        line = _clean_cert_name(line)
        if line and len(line) > 1:
            names.append(line)
    return names


def _nominal_from_name(name: str) -> float:
    values = _parse_nominals(name)
    return values[0] if values else 0.0


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", " ", str(text or "").lower()).strip()


def _game_by_hash(data: dict, game_hash: str) -> str | None:
    return next((name for name in data if get_hash(name) == game_hash), None)


def _items(game_data: dict) -> list[tuple[str, dict]]:
    return [(k, v) for k, v in game_data.items() if k != "_meta" and isinstance(v, dict)]


def _looks_like_certificate_order(product_full_name: str, order_game: str | None, data: dict) -> bool:
    """Разрешаем сертификатный автоподбор только для явных сертификатных заказов."""
    text = _normalize(f"{product_full_name} {order_game or ''}")
    hints = (
        "сертификат",
        "подароч",
        "gift card",
        "giftcard",
        "voucher",
        "ваучер",
    )
    if any(hint in text for hint in hints):
        return True

    if order_game:
        game_text = _normalize(order_game)
        for game_name in data:
            norm_game = _normalize(game_name)
            if game_text and (game_text in norm_game or norm_game in game_text):
                return True

    return False


def _ensure_game(data: dict, game_name: str):
    if game_name not in data:
        data[game_name] = {"_meta": {"rate": None}}
    data[game_name].setdefault("_meta", {}).setdefault("rate", None)


async def _fill_sbp_rates_for_games(data: dict, game_names: list[str], attempts: int = 5) -> tuple[int, int]:
    """Ищет СБП-коэффициенты для списка игр сертификатов и пишет их в data."""
    gk, _ = db.get_config()
    if not gk:
        return 0, len(game_names)

    found = 0
    failed = 0
    for idx, game_name in enumerate(game_names):
        if game_name not in data:
            continue
        meta = data[game_name].setdefault("_meta", {})
        lot_id, rate = await resolve_sbp_rate_for_game(gk, game_name, meta.get("lot_id"), attempts=attempts)
        if rate:
            meta["rate"] = rate
            meta["latest_checked_rate"] = rate
            if lot_id:
                meta["lot_id"] = lot_id
            found += 1
        else:
            failed += 1
        if idx < len(game_names) - 1:
            await asyncio.sleep(0.5)
    return found, failed


def _add_item_names(data: dict, game_name: str, names: list[str]) -> int:
    _ensure_game(data, game_name)
    added = 0
    existing_names = {
        _normalize(info.get("name", ""))
        for _, info in _items(data[game_name])
    }
    for name in names:
        name = str(name or "").strip()
        if not name:
            continue
        nominal = _nominal_from_name(name)
        normalized = _normalize(name)
        if normalized in existing_names:
            continue
        key_base = nominal if nominal else name
        iid = _item_id(key_base)
        suffix = 1
        while iid in data[game_name]:
            suffix += 1
            iid = md5(f"cert_{key_base}_{suffix}".encode()).hexdigest()[:8]
        cost = nominal if nominal else 0.0
        data[game_name][iid] = {
            "name": _clean_cert_name(name),
            "nominal": nominal,
            "cost": cost,
            "offer_id": None,
        }
        existing_names.add(normalized)
        added += 1
    return added


def _add_nominals(data: dict, game_name: str, nominals: list[float]) -> int:
    return _add_item_names(data, game_name, [f"{nominal:g} ₽" for nominal in nominals])


def _demping_lot_template(info: dict, price: float) -> dict:
    nominal = _money(info.get("nominal")) or _nominal_from_name(info.get("name", ""))
    if nominal and nominal.is_integer():
        nominal_text = str(int(nominal))
    elif nominal:
        nominal_text = f"{nominal:g}"
    else:
        nominal_text = str(info.get("name") or "подарочный сертификат").lower()
    triggers = (
        f"{nominal_text} ₽ | {nominal_text}+ RUB | {nominal_text} RUB | "
        f"{nominal_text} руб | {nominal_text} рублей"
    )
    return {
        "active": True,
        "triggers": triggers,
        "min_price": price,
        "max_price": 99999,
        "min_rating": 3,
        "skip_no_rating": True,
        "price_step": 0.01,
        "rounding": 0.01,
        "min_one_unit": False,
        "friends": [],
        "outbid_offline": False,
    }


def _import_games_pick_kb(new_games: dict, selected: set, already: list) -> InlineKeyboardMarkup:
    rows = []
    for name in new_games:
        prefix = "✅ " if name in selected else "☐ "
        rows.append([InlineKeyboardButton(
            text=f"{prefix}{_short(name, 42)}",
            callback_data=f"cert_imptoggle_{get_hash(name)}",
        )])
    for name in already[:10]:
        rows.append([InlineKeyboardButton(text=f"— {_short(name, 42)} (уже есть)", callback_data="none")])
    if selected:
        rows.append([InlineKeyboardButton(
            text=f"📥 Добавить выбранные ({len(selected)})",
            callback_data="cert_import_confirm",
        )])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cert_pg_0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_items_preview(target, names: list[str], game_hash: str):
    preview = "\n".join(f"{idx}. {_html.escape(name)}" for idx, name in enumerate(names[:30], start=1))
    if len(names) > 30:
        preview += f"\n<i>...и ещё {len(names) - 30}</i>"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Добавить в эту игру ({len(names)})", callback_data=f"cert_items_add_one_{game_hash}")],
        [InlineKeyboardButton(text=f"🌍 Добавить во все игры ({len(names)})", callback_data=f"cert_items_add_all_{game_hash}")],
        [InlineKeyboardButton(text="✏️ Редактировать список", callback_data=f"cert_items_edit_{game_hash}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"cert_game_{game_hash}")],
    ])
    await target.answer(
        f"📋 <b>Найдено сертификатов: {len(names)}</b>\n\n{preview}\n\n"
        f"<i>Закуп будет взят из номинала в названии. Потом его можно изменить в карточке сертификата.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


def _main_kb(has_demping: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🎮 Игры", callback_data="cert_pg_0")],
        [InlineKeyboardButton(text="📥 Загрузить файл демпинга", callback_data="cert_upload")],
        [InlineKeyboardButton(text="🔄 Создать/обновить цены в файле", callback_data="cert_update_dmp")],
        [InlineKeyboardButton(text="📊 Актуальность данных", callback_data="cert_freshness")],
    ]
    if has_demping:
        rows.append([InlineKeyboardButton(text="📤 Выгрузить файл", callback_data="cert_download")])
        rows.append([InlineKeyboardButton(text="🚀 Отправить в Cardinal", callback_data="cert_to_cardinal")])
    rows.append([InlineKeyboardButton(text="↩️ FunPay Auto", callback_data="funpay_auto_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def freshness_text(data: dict, games: list[str], results: dict | None = None) -> str:
    text = "📊 <b>Актуальность коэффициентов сертификатов</b>\n\n"
    for game in games:
        meta = data.get(game, {}).get("_meta", {})
        rate = _rate(meta.get("rate"))
        latest = _rate(meta.get("latest_checked_rate"))

        if results is not None:
            status = results.get(game)
            if status == "ok":
                icon, detail = "🟢", f"×{rate:.4f} — актуален"
            elif status == "changed":
                new_rate = results.get(game + "__new")
                icon, detail = "🔴", f"×{rate:.4f} → ×{new_rate:.4f} — НЕ актуален (обновите в игре)"
            elif status == "no_lot":
                icon, detail = "❓", "коэффициент не задан"
            else:
                icon, detail = "❓", "не удалось проверить"
        else:
            if rate <= 0:
                icon, detail = "❓", "коэффициент не задан"
            elif latest is not None and abs(latest - rate) >= 0.0001:
                icon, detail = "🔴", f"×{rate:.4f} → ×{latest:.4f} — НЕ актуален (обновите в игре)"
            else:
                icon, detail = "🟢", f"×{rate:.4f} — актуален"

        text += f"{icon} <b>{_html.escape(game)}</b> — {detail}\n"
    return text


def freshness_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить данные", callback_data="cert_freshness_upd")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="cert_menu")],
    ])


def _games_kb(games: list[str], page: int, data: dict) -> InlineKeyboardMarkup:
    total_pages = (len(games) + GAMES_PER_PAGE - 1) // GAMES_PER_PAGE or 1
    start = page * GAMES_PER_PAGE
    rows = []
    for name in games[start:start + GAMES_PER_PAGE]:
        game_data = data.get(name, {})
        item_list = _items(game_data)
        linked = sum(1 for _, info in item_list if info.get("offer_id"))
        rate = _rate(game_data.get("_meta", {}).get("rate", 1.0))
        rows.append([InlineKeyboardButton(
            text=f"🎮 {_short(name, 31)} · ×{rate:g} · {linked}/{len(item_list)}🔗",
            callback_data=f"cert_game_{get_hash(name)}"
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"cert_pg_{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="none"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"cert_pg_{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="➕ Добавить игру", callback_data="cert_add_game")])
    rows.append([InlineKeyboardButton(text="📥 Импорт игр из FunPay", callback_data="cert_import_games")])
    rows.append([InlineKeyboardButton(text="↩️ Сертификаты", callback_data="cert_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _game_kb(game_hash: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows = []
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"cert_items_{game_hash}_{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="none"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"cert_items_{game_hash}_{page + 1}"))
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(text="📈 Коэффициент", callback_data=f"cert_rate_{game_hash}"),
        InlineKeyboardButton(text="➕ Товары", callback_data=f"cert_add_items_{game_hash}"),
    ])
    rows.append([
        InlineKeyboardButton(text="🔄 Найти коэф СБП", callback_data=f"cert_fetchrate_{game_hash}"),
    ])
    rows.append([
        InlineKeyboardButton(text="🤖 Сопоставить лоты", callback_data=f"cert_autolink_{game_hash}"),
    ])
    rows.append([InlineKeyboardButton(text="✏️ Редактировать сертификаты", callback_data=f"cert_edit_{game_hash}_0")])
    rows.append([InlineKeyboardButton(text="🗑 Удалить игру", callback_data=f"cert_delgame_{game_hash}")])
    rows.append([InlineKeyboardButton(text="↩️ Игры", callback_data="cert_pg_0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _items_edit_kb(game_hash: str, items: list[tuple[str, dict]], page: int) -> InlineKeyboardMarkup:
    total_pages = (len(items) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE or 1
    start = page * ITEMS_PER_PAGE
    rows = []
    for iid, info in items[start:start + ITEMS_PER_PAGE]:
        linked = "🔗" if info.get("offer_id") else "—"
        rows.append([InlineKeyboardButton(
            text=f"{linked} {_short(info.get('name', iid), 35)}",
            callback_data=f"cert_item_{game_hash}_{iid}"
        )])
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"cert_edit_{game_hash}_{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="none"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"cert_edit_{game_hash}_{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="↩️ К игре", callback_data=f"cert_game_{game_hash}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_menu_text(data: dict) -> str:
    games = list(data)
    item_count = 0
    linked = 0
    rates = 0
    for game_data in data.values():
        item_list = _items(game_data)
        item_count += len(item_list)
        linked += sum(1 for _, info in item_list if info.get("offer_id"))
        if _rate(game_data.get("_meta", {}).get("rate", 0)) > 0:
            rates += 1
    demping_count = len(load_cert_demping()) if os.path.exists(CERT_DEMPING_FILE) else 0
    return (
        "🎁 <b>Сертификаты</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"🎮 Игр: <b>{len(games)}</b>\n"
        f"📦 Сертификатов: <b>{item_count}</b>\n"
        f"🔗 Привязано лотов: <b>{linked}</b>\n"
        f"📈 Коэффициентов: <b>{rates}/{len(games)}</b>\n"
        f"📁 Лотов в файле: <b>{demping_count}</b>\n\n"
        "<i>Отдельный раздел для подарочных сертификатов и их демпинг-файла.</i>"
    )


def _build_game_text(game_name: str, game_data: dict, page: int) -> tuple[str, int]:
    meta = game_data.get("_meta", {})
    rate = _rate(meta.get("rate", 0))
    display_rate = rate if rate > 0 else None
    item_list = _items(game_data)
    total_pages = (len(item_list) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE or 1
    start = page * ITEMS_PER_PAGE
    linked = sum(1 for _, info in item_list if info.get("offer_id"))
    text = (
        f"🎁 <b>{_html.escape(game_name)}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"📈 Коэффициент: {f'<code>×{display_rate:g}</code>' if display_rate else '<i>не задан</i>'}\n"
        f"📦 Сертификатов: <b>{len(item_list)}</b>\n"
        f"🔗 С лотами: <b>{linked}</b>\n"
    )
    if total_pages > 1:
        text += f"📄 Страница: <b>{page + 1}/{total_pages}</b>\n"
    text += "\n"
    if not item_list:
        text += "📭 <i>Сертификатов пока нет.</i>"
    for idx, (_, info) in enumerate(item_list[start:start + ITEMS_PER_PAGE], start=start + 1):
        cost = _money(info.get("cost", 0))
        min_price = calc_min_price(cost)
        site = calc_site_price(cost, display_rate or 1.0)
        lot = info.get("offer_id")
        lot_text = f'<a href="https://funpay.com/lots/offer?id={lot}">#{lot}</a>' if lot else "—"
        site_line = f"🌐 Сайт (СБП): <code>{site:.2f} ₽</code>\n" if display_rate else "🌐 Сайт (СБП): <i>коэф. не задан</i>\n"
        text += (
            f"<b>{idx}. {_html.escape(str(info.get('name', 'Сертификат')))}</b>\n"
            f"💸 Закуп: <code>{cost:.2f} ₽</code>  |  💰 Мин: <code>{min_price:.2f} ₽</code>\n"
            f"{site_line}"
            f"🔗 Лот: {lot_text}\n\n"
        )
    return text.rstrip(), total_pages


def _build_item_text(game_name: str, info: dict, rate: float) -> str:
    cost = _money(info.get("cost", 0))
    display_rate = rate if rate and rate > 0 else None
    site = calc_site_price(cost, display_rate or 1.0)
    lot = info.get("offer_id")
    lot_text = f'<a href="https://funpay.com/lots/offer?id={lot}">#{lot}</a>' if lot else "—"
    return (
        f"✏️ <b>Сертификат</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎮 <b>{_html.escape(game_name)}</b>\n"
        f"🎁 <b>{_html.escape(str(info.get('name', 'Сертификат')))}</b>\n\n"
        f"💸 Закуп: <code>{cost:.2f} ₽</code>\n"
        f"💰 Мин. цена: <code>{calc_min_price(cost):.2f} ₽</code>\n"
        f"📈 Коэффициент: {f'<code>×{display_rate:g}</code>' if display_rate else '<i>не задан</i>'}\n"
        f"🌐 Цена в демпинге: {f'<code>{site:.2f} ₽</code>' if display_rate else '<i>коэф. не задан</i>'}\n"
        f"🔗 Лот: {lot_text}"
    )


@router.callback_query(F.data == "cert_menu")
async def cb_cert_menu(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    await state.clear()
    data = load_certificates(call.from_user.id)
    await call.message.edit_text(
        _build_menu_text(data),
        parse_mode=ParseMode.HTML,
        reply_markup=_main_kb(os.path.exists(CERT_DEMPING_FILE)),
    )
    await call.answer()


@router.callback_query(F.data == "cert_freshness")
async def cb_cert_freshness(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    await state.clear()
    data = load_certificates(call.from_user.id)
    games = list(data.keys())
    await call.message.edit_text(
        freshness_text(data, games),
        parse_mode=ParseMode.HTML,
        reply_markup=freshness_kb(),
    )
    await call.answer()


@router.callback_query(F.data == "cert_freshness_upd")
async def cb_cert_freshness_upd(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)

    data = load_certificates(call.from_user.id)
    games = list(data.keys())
    if not games:
        return await call.answer("Нет игр", show_alert=True)

    await call.message.edit_text(
        "⏳ <b>Проверяю коэффициенты...</b>\n\nЭто может занять несколько секунд.",
        parse_mode=ParseMode.HTML,
    )
    await call.answer()

    gk, _ = db.get_config()
    if not gk:
        return await call.message.edit_text(
            "❌ Golden Key не настроен. Откройте /funpayauto -> Данные аккаунта.",
            parse_mode=ParseMode.HTML,
            reply_markup=freshness_kb(),
        )

    results = {}
    for idx, game in enumerate(games):
        meta = data.get(game, {}).setdefault("_meta", {})
        old_rate = _rate(meta.get("rate", 0))
        lot_id = meta.get("lot_id")

        if old_rate <= 0:
            results[game] = "no_lot"
            continue

        lot_id, new_rate = await resolve_sbp_rate_for_game(gk, game, lot_id, attempts=5)
        if new_rate is None:
            results[game] = "error"
        elif abs(new_rate - old_rate) < 0.0001:
            meta["latest_checked_rate"] = new_rate
            if lot_id:
                meta["lot_id"] = lot_id
            results[game] = "ok"
        else:
            meta["latest_checked_rate"] = new_rate
            if lot_id:
                meta["lot_id"] = lot_id
            results[game] = "changed"
            results[game + "__new"] = new_rate

        if idx < len(games) - 1:
            await asyncio.sleep(0.5)

    save_certificates(data, call.from_user.id)
    await call.message.edit_text(
        freshness_text(data, games, results),
        parse_mode=ParseMode.HTML,
        reply_markup=freshness_kb(),
    )


@router.callback_query(F.data.startswith("cert_pg_"))
async def cb_cert_games(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    await state.clear()
    page = int(call.data.split("_")[2])
    data = load_certificates(call.from_user.id)
    games = list(data)
    text = (
        "🎮 <b>Игры с сертификатами</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"Всего: <b>{len(games)}</b>\n\n"
        "<i>Выберите игру.</i>"
    )
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=_games_kb(games, page, data))
    await call.answer()


@router.callback_query(F.data == "cert_import_games")
async def cb_cert_import_games(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)

    await call.answer("🔄 Загружаю игры с FunPay...")
    gk, ua = db.get_config()
    if not gk:
        return await call.message.answer("❌ Golden Key не настроен. Откройте /funpayauto -> Данные аккаунта.")

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
        resp = await loop.run_in_executor(
            None,
            lambda: session.get(f"https://funpay.com/users/{acc.id}/", timeout=15),
        )
        names = re.findall(
            r'<div[^>]+class="offer-list-title"[^>]*>\s*<h3><a[^>]*>([^<]+)</a>',
            resp.text,
        )
        found = {name.strip(): None for name in names if name.strip()}
        if not found:
            return await call.message.answer("⚠️ Не удалось найти игры на профиле FunPay.")

        data = load_certificates(call.from_user.id)
        new_games = {name: None for name in found if name not in data}
        already = [name for name in found if name in data]
        if not new_games:
            return await call.message.answer(f"ℹ️ Все найденные игры уже добавлены: <b>{len(found)}</b>.", parse_mode=ParseMode.HTML)

        selected = set(new_games.keys())
        await state.update_data(cert_import_games=new_games, cert_import_selected=list(selected), cert_import_already=already)
        await call.message.answer(
            f"📥 <b>Найдено новых игр: {len(new_games)}</b>\n"
            f"<i>Все выбраны. Снимите галочку с тех, что не нужны.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=_import_games_pick_kb(new_games, selected, already),
        )
    except Exception as e:
        await call.message.answer(f"❌ Ошибка импорта: <code>{_html.escape(str(e))}</code>", parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("cert_imptoggle_"))
async def cb_cert_import_toggle(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    game_hash = call.data.split("_")[2]
    state_data = await state.get_data()
    new_games = state_data.get("cert_import_games", {})
    already = state_data.get("cert_import_already", [])
    selected = set(state_data.get("cert_import_selected", []))
    game_name = next((name for name in new_games if get_hash(name) == game_hash), None)
    if not game_name:
        return await call.answer()
    if game_name in selected:
        selected.discard(game_name)
    else:
        selected.add(game_name)
    await state.update_data(cert_import_selected=list(selected))
    await call.message.edit_reply_markup(reply_markup=_import_games_pick_kb(new_games, selected, already))
    await call.answer()


@router.callback_query(F.data == "cert_import_confirm")
async def cb_cert_import_confirm(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    state_data = await state.get_data()
    new_games = state_data.get("cert_import_games", {})
    selected = set(state_data.get("cert_import_selected", []))
    if not selected:
        return await call.answer("Ничего не выбрано", show_alert=True)
    data = load_certificates(call.from_user.id)
    added = 0
    selected_games = list(selected)
    for name in selected_games:
        if name not in data:
            _ensure_game(data, name)
            added += 1
    save_certificates(data, call.from_user.id)
    await state.clear()
    await call.message.edit_text(
        f"✅ <b>Игры добавлены</b>\n\n"
        f"🎮 Новых игр: <b>{added}</b>\n"
        f"⏳ Ищу коэффициенты СБП...",
        parse_mode=ParseMode.HTML,
    )
    found_rates, failed_rates = await _fill_sbp_rates_for_games(data, selected_games)
    save_certificates(data, call.from_user.id)
    await call.message.edit_text(
        f"✅ <b>Игры добавлены</b>\n\n"
        f"🎮 Новых игр: <b>{added}</b>\n\n"
        f"📈 Коэффициенты найдены: <b>{found_rates}</b>\n"
        f"❓ Не удалось найти: <b>{failed_rates}</b>\n\n"
        f"<i>Теперь откройте любую игру и добавьте сертификаты во все игры одной кнопкой.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ К играм", callback_data="cert_pg_0")]
        ]),
    )
    await call.answer()


@router.callback_query(F.data == "cert_add_game")
async def cb_cert_add_game(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    await state.set_state(CertificateStates.waiting_game_name)
    await call.message.edit_text(
        "➕ <b>Добавление игры</b>\n\n"
        "Введите название игры. Сертификаты можно будет добавить в игре текстом или фото.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cert_menu")]
        ]),
    )
    await call.answer()


@router.message(CertificateStates.waiting_game_name)
async def proc_cert_game_name(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await state.clear()
    game_name = (message.text or "").strip()
    if not game_name:
        return await message.answer("⚠️ Введите название игры.")
    data = load_certificates(message.from_user.id)
    _ensure_game(data, game_name)
    save_certificates(data, message.from_user.id)
    await state.clear()
    progress = await message.answer(
        f"✅ <b>Игра добавлена</b>\n\n"
        f"🎮 <b>{_html.escape(game_name)}</b>\n"
        f"⏳ Ищу коэффициент СБП...",
        parse_mode=ParseMode.HTML,
    )
    found_rates, failed_rates = await _fill_sbp_rates_for_games(data, [game_name])
    save_certificates(data, message.from_user.id)
    await message.answer(
        f"✅ <b>Игра добавлена</b>\n\n"
        f"🎮 <b>{_html.escape(game_name)}</b>\n"
        f"📈 Коэффициент: {'найден' if found_rates else 'не найден'}\n"
        f"📦 Теперь добавьте сертификаты через кнопку <b>«Товары»</b>.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Открыть игру", callback_data=f"cert_game_{get_hash(game_name)}")]
        ]),
    )
    try:
        await progress.delete()
    except Exception:
        pass


@router.callback_query(F.data.startswith("cert_game_"))
async def cb_cert_game(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    await state.clear()
    game_hash = call.data.split("_")[2]
    data = load_certificates(call.from_user.id)
    game_name = _game_by_hash(data, game_hash)
    if not game_name:
        return await call.answer("Игра не найдена", show_alert=True)
    text, total_pages = _build_game_text(game_name, data[game_name], 0)
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=_game_kb(game_hash, 0, total_pages))
    await call.answer()


@router.callback_query(F.data.regexp(r"^cert_items_[0-9a-f]{8}_\d+$"))
async def cb_cert_items_page(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    _, _, game_hash, page_raw = call.data.split("_")
    page = int(page_raw)
    data = load_certificates(call.from_user.id)
    game_name = _game_by_hash(data, game_hash)
    if not game_name:
        return await call.answer("Игра не найдена", show_alert=True)
    text, total_pages = _build_game_text(game_name, data[game_name], page)
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=_game_kb(game_hash, page, total_pages))
    await call.answer()


@router.callback_query(F.data.startswith("cert_rate_"))
async def cb_cert_rate(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    game_hash = call.data.split("_")[2]
    data = load_certificates(call.from_user.id)
    game_name = _game_by_hash(data, game_hash)
    if not game_name:
        return await call.answer("Игра не найдена", show_alert=True)
    await state.update_data(cert_game_hash=game_hash)
    await state.set_state(CertificateStates.waiting_rate)
    current = data[game_name].get("_meta", {}).get("rate", 1.0)
    await call.message.edit_text(
        f"📈 <b>Коэффициент сертификатов</b>\n\n"
        f"🎮 <b>{_html.escape(game_name)}</b>\n"
        f"Текущий: <code>×{current:g}</code>\n\n"
        f"Введите новый коэффициент, например <code>1.1711</code>.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"cert_game_{game_hash}")]
        ]),
    )
    await call.answer()


@router.message(CertificateStates.waiting_rate)
async def proc_cert_rate(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await state.clear()
    state_data = await state.get_data()
    game_hash = state_data.get("cert_game_hash")
    rate = _rate(message.text)
    if rate <= 0:
        return await message.answer("⚠️ Введите коэффициент больше нуля.")
    data = load_certificates(message.from_user.id)
    game_name = _game_by_hash(data, game_hash)
    if not game_name:
        await state.clear()
        return await message.answer("❌ Игра не найдена.")
    data[game_name].setdefault("_meta", {})["rate"] = rate
    save_certificates(data, message.from_user.id)
    await state.clear()
    await message.answer(
        f"✅ Коэффициент обновлён: <code>×{rate:g}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ К игре", callback_data=f"cert_game_{game_hash}")]
        ]),
    )


@router.callback_query(F.data.startswith("cert_add_items_"))
async def cb_cert_add_items(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    game_hash = call.data.split("_")[3]
    await state.update_data(cert_game_hash=game_hash)
    await state.set_state(CertificateStates.waiting_items)
    await call.message.edit_text(
        "➕ <b>Добавление сертификатов</b>\n\n"
        "Отправьте список текстом или фото.\n\n"
        "Можно так:\n"
        "<code>100 250 500 1000 2500</code>\n\n"
        "Или так:\n"
        "<code>Подарочный сертификат 100 ₽\nПодарочный сертификат 500 ₽</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"cert_game_{game_hash}")]
        ]),
    )
    await call.answer()


@router.message(CertificateStates.waiting_items, F.text)
async def proc_cert_items_text(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await state.clear()
    state_data = await state.get_data()
    game_hash = state_data.get("cert_game_hash")
    names = _parse_item_names_from_text(message.text or "")
    if not names:
        return await message.answer("⚠️ Не нашёл товары. Отправьте суммы или список названий.")
    await state.update_data(cert_item_names=names)
    await state.set_state(None)
    await _send_items_preview(message, names, game_hash)


@router.message(CertificateStates.waiting_items, F.photo)
async def proc_cert_items_photo(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await state.clear()
    state_data = await state.get_data()
    game_hash = state_data.get("cert_game_hash")
    await message.answer("⏳ <b>Распознаю сертификаты...</b>", parse_mode=ParseMode.HTML)
    try:
        photo = message.photo[-1]
        file_info = await message.bot.get_file(photo.file_id)
        buf = BytesIO()
        await message.bot.download_file(file_info.file_path, buf)
        image_data = base64.b64encode(buf.getvalue()).decode("utf-8")
        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
                    {
                        "type": "text",
                        "text": (
                            "На скриншоте список подарочных сертификатов. "
                            "Выпиши только названия сертификатов с номиналом. "
                            "Каждый сертификат с новой строки, без нумерации, без пояснений, без заголовков."
                        ),
                    },
                ],
            }],
            max_tokens=1024,
        )
        raw = response.choices[0].message.content.strip()
        names = [_clean_cert_name(line) for line in raw.splitlines() if line.strip()]
        names = [name for name in names if name and not name.endswith(":") and len(name) > 1]
        if not names:
            return await message.answer("❌ Не удалось распознать сертификаты. Попробуйте другой скриншот.")
        await state.update_data(cert_item_names=names)
        await state.set_state(None)
        await _send_items_preview(message, names, game_hash)
    except Exception as e:
        await state.clear()
        await message.answer(f"❌ Ошибка распознавания: <code>{_html.escape(str(e))}</code>", parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("cert_items_edit_"))
async def cb_cert_items_edit(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    game_hash = call.data.split("_")[3]
    await state.update_data(cert_game_hash=game_hash)
    await state.set_state(CertificateStates.waiting_edit_items)
    state_data = await state.get_data()
    names = state_data.get("cert_item_names", [])
    current = "\n".join(f"{idx}. {name}" for idx, name in enumerate(names, start=1))
    await call.message.edit_text(
        f"✏️ <b>Редактирование списка</b>\n\n"
        f"<code>{_html.escape(current)}</code>\n\n"
        f"Напишите исправленный список или что нужно поправить.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"cert_game_{game_hash}")]
        ]),
    )
    await call.answer()


@router.message(CertificateStates.waiting_edit_items, F.text)
async def proc_cert_edit_items(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await state.clear()
    state_data = await state.get_data()
    game_hash = state_data.get("cert_game_hash")
    old_names = state_data.get("cert_item_names", [])
    current = "\n".join(f"{idx}. {name}" for idx, name in enumerate(old_names, start=1))
    try:
        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": (
                    f"Вот список сертификатов:\n{current}\n\n"
                    f"Инструкция пользователя: {message.text}\n\n"
                    f"Отредактируй именно список выше по инструкции. "
                    f"Если пользователь просит убрать лишний текст, символы или суффиксы, убери их у всех строк. "
                    f"Верни только итоговые названия сертификатов, каждое с новой строки. "
                    f"Без нумерации, без пояснений, без вступлений."
                ),
            }],
            max_tokens=1024,
        )
        raw = response.choices[0].message.content.strip()
        names = [_clean_cert_name(line) for line in raw.splitlines() if line.strip()]
    except Exception as e:
        return await message.answer(f"❌ Ошибка: <code>{_html.escape(str(e))}</code>", parse_mode=ParseMode.HTML)
    names = [name for name in names if name and len(name) > 1]
    if not names:
        return await message.answer("⚠️ Список пустой, попробуйте ещё раз.")
    await state.update_data(cert_item_names=names)
    await state.set_state(None)
    await _send_items_preview(message, names, game_hash)


@router.callback_query(F.data.startswith("cert_items_add_"))
async def cb_cert_items_add(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    parts = call.data.split("_")
    scope = parts[3]
    game_hash = parts[4]
    state_data = await state.get_data()
    names = state_data.get("cert_item_names", [])
    if not names:
        return await call.answer("Список товаров пуст", show_alert=True)
    data = load_certificates(call.from_user.id)
    game_name = _game_by_hash(data, game_hash)
    if not game_name:
        return await call.answer("Игра не найдена", show_alert=True)
    targets = list(data.keys()) if scope == "all" else [game_name]
    added_total = 0
    for target_game in targets:
        added_total += _add_item_names(data, target_game, names)
    save_certificates(data, call.from_user.id)
    await state.clear()
    await call.message.edit_text(
        f"✅ <b>Сертификаты добавлены</b>\n\n"
        f"🎮 Игр: <b>{len(targets)}</b>\n"
        f"📦 Новых товаров: <b>{added_total}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ К игре", callback_data=f"cert_game_{game_hash}")]
        ]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("cert_fetchrate_"))
async def cb_cert_fetch_rate(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    game_hash = call.data.split("_")[2]
    data = load_certificates(call.from_user.id)
    game_name = _game_by_hash(data, game_hash)
    if not game_name:
        return await call.answer("Игра не найдена", show_alert=True)

    await call.message.edit_text(
        f"⏳ <b>Ищу коэффициент СБП для «{_html.escape(game_name)}»...</b>",
        parse_mode=ParseMode.HTML,
    )
    await call.answer()
    gk, _ = db.get_config()
    if not gk:
        return await call.message.edit_text(
            "❌ Golden Key не настроен.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↩️ К игре", callback_data=f"cert_game_{game_hash}")]
            ]),
        )

    meta = data[game_name].setdefault("_meta", {})
    lot_id, rate = await resolve_sbp_rate_for_game(gk, game_name, meta.get("lot_id"), attempts=5)
    if lot_id:
        meta["lot_id"] = lot_id
    if rate:
        meta["rate"] = rate
        save_certificates(data, call.from_user.id)
        text, total_pages = _build_game_text(game_name, data[game_name], 0)
        text += f"\n\n✅ <i>Коэффициент обновлён: ×{rate:.4f}</i>"
        await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=_game_kb(game_hash, 0, total_pages))
    else:
        await call.message.edit_text(
            f"❌ <b>Не удалось найти коэффициент СБП</b>\n\n"
            f"Проверьте, что игра есть на профиле и лоты активны.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↩️ К игре", callback_data=f"cert_game_{game_hash}")]
            ]),
        )


@router.callback_query(F.data.startswith("cert_autolink_"))
async def cb_cert_autolink(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    game_hash = call.data.split("_")[2]
    data = load_certificates(call.from_user.id)
    game_name = _game_by_hash(data, game_hash)
    if not game_name:
        return await call.answer("Игра не найдена", show_alert=True)
    items = {iid: info for iid, info in _items(data[game_name])}
    if not items:
        return await call.answer("Сначала добавьте сертификаты", show_alert=True)

    await call.message.edit_text(
        f"🔗 <b>Сопоставление лотов</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎮 <b>{_html.escape(game_name)}</b>\n\n"
        f"📦 Сертификатов: <b>{len(items)}</b>\n"
        f"✅ С лотами: <b>{sum(1 for _, info in items.items() if info.get('offer_id'))}</b>\n"
        f"🕳 Без лотов: <b>{sum(1 for _, info in items.items() if not info.get('offer_id'))}</b>\n\n"
        f"<i>Выберите режим сопоставления.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🤖 Сопоставить все товары", callback_data=f"cert_autolink_all_{game_hash}")],
            [InlineKeyboardButton(text="🕳 Только без лотов", callback_data=f"cert_autolink_unlinked_{game_hash}")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data=f"cert_game_{game_hash}")],
        ]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("cert_autolink_all_") or F.data.startswith("cert_autolink_unlinked_"))
async def cb_cert_autolink_mode(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    only_unlinked = call.data.startswith("cert_autolink_unlinked_")
    game_hash = call.data.split("_")[4]
    data = load_certificates(call.from_user.id)
    game_name = _game_by_hash(data, game_hash)
    if not game_name:
        return await call.answer("Игра не найдена", show_alert=True)

    all_items = {iid: info for iid, info in _items(data[game_name])}
    if not all_items:
        return await call.answer("Сначала добавьте сертификаты", show_alert=True)

    items = {
        iid: info for iid, info in all_items.items()
        if not only_unlinked or not info.get("offer_id")
    }
    if not items:
        return await call.answer("Нет товаров без лотов", show_alert=True)

    await call.message.edit_text(
        f"⏳ <b>Получаю лоты «{_html.escape(game_name)}»...</b>\n\n"
        f"<i>Режим: {'только товары без лотов' if only_unlinked else 'все товары'}</i>",
        parse_mode=ParseMode.HTML,
    )
    await call.answer()

    lots = await _get_user_lots(game_name)
    if not lots:
        return await call.message.edit_text(
            "❌ <b>Лоты не найдены</b>\n\n"
            "Проверьте, что на профиле есть активные лоты этой игры.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↩️ К игре", callback_data=f"cert_game_{game_hash}")]
            ]),
        )

    await call.message.edit_text(
        f"⏳ <b>Сопоставляю сертификаты по номиналам...</b>\n\n"
        f"<i>Режим: {'только товары без лотов' if only_unlinked else 'все товары'}</i>",
        parse_mode=ParseMode.HTML,
    )
    matches = _match_certificate_lots_by_nominal(lots, items)
    if len(matches) < len(items):
        await call.message.edit_text(
            "⏳ <b>Часть номиналов не найдена. Пробую ИИ fallback...</b>",
            parse_mode=ParseMode.HTML,
        )
        ai_matches = await _match_offers_with_ai(game_name, lots, items)
        ai_matches = _normalize_cert_matches(ai_matches, items)
        ai_matches = _filter_ai_matches_by_nominal(ai_matches, lots, items)
        for item_name, offer_ids in ai_matches.items():
            matches.setdefault(item_name, offer_ids)
    if not matches:
        return await call.message.edit_text(
            "❌ <b>Совпадения не найдены</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↩️ К игре", callback_data=f"cert_game_{game_hash}")]
            ]),
        )

    matched_lines = []
    for item_name, offer_ids in matches.items():
        ids = f"#{offer_ids[0]}" if offer_ids else "—"
        matched_lines.append(f"📦 <b>{_html.escape(_short(item_name, 60))}</b>\n   🔗 <code>{ids}</code>")
    text = (
        f"🤖 <b>Сопоставление лотов</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎮 <b>{_html.escape(game_name)}</b>\n"
        f"🧭 Режим: <b>{'только товары без лотов' if only_unlinked else 'все товары'}</b>\n"
        f"🔎 Лотов найдено: <b>{len(lots)}</b>\n"
        f"✅ Совпадений: <b>{len(matches)}</b>\n\n"
        + "\n".join(matched_lines[:20])
    )
    if len(matched_lines) > 20:
        text += f"\n<i>... и ещё {len(matched_lines) - 20}</i>"
    await state.update_data(cert_auto_matches=matches, cert_game_hash=game_hash)
    await call.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"cert_auto_confirm_{game_hash}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"cert_game_{game_hash}")],
        ]),
    )


@router.callback_query(F.data.startswith("cert_auto_confirm_"))
async def cb_cert_auto_confirm(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    game_hash = call.data.split("_")[3]
    state_data = await state.get_data()
    matches = state_data.get("cert_auto_matches", {})
    data = load_certificates(call.from_user.id)
    game_name = _game_by_hash(data, game_hash)
    if not game_name:
        return await call.answer("Игра не найдена", show_alert=True)

    saved = 0
    used = set()
    for full_name, offer_ids in matches.items():
        clean_name = _clean_cert_name(full_name)
        clean_norm = _normalize(clean_name)
        for item_id, info in _items(data[game_name]):
            if item_id in used:
                continue
            if _normalize(info.get("name", "")) == clean_norm:
                info["offer_id"] = str(offer_ids[0])
                used.add(item_id)
                saved += 1
                break
    save_certificates(data, call.from_user.id)
    await state.clear()
    await call.message.edit_text(
        f"✅ <b>Лоты сопоставлены</b>\n\n"
        f"🔗 Сохранено привязок: <b>{saved}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ К игре", callback_data=f"cert_game_{game_hash}")]
        ]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("cert_edit_"))
async def cb_cert_edit(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    _, _, game_hash, page_raw = call.data.split("_")
    page = int(page_raw)
    data = load_certificates(call.from_user.id)
    game_name = _game_by_hash(data, game_hash)
    if not game_name:
        return await call.answer("Игра не найдена", show_alert=True)
    item_list = _items(data[game_name])
    linked = sum(1 for _, info in item_list if info.get("offer_id"))
    text = (
        f"✏️ <b>Редактирование сертификатов</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎮 <b>{_html.escape(game_name)}</b>\n"
        f"📦 Всего: <b>{len(item_list)}</b>\n"
        f"🔗 С лотами: <b>{linked}</b>\n\n"
        f"<i>Выберите сертификат.</i>"
    )
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=_items_edit_kb(game_hash, item_list, page))
    await call.answer()


@router.callback_query(F.data.startswith("cert_item_"))
async def cb_cert_item(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    _, _, game_hash, item_id = call.data.split("_")
    data = load_certificates(call.from_user.id)
    game_name = _game_by_hash(data, game_hash)
    if not game_name or item_id not in data.get(game_name, {}):
        return await call.answer("Сертификат не найден", show_alert=True)
    info = data[game_name][item_id]
    rate = _rate(data[game_name].get("_meta", {}).get("rate", 0))
    await state.update_data(cert_game_hash=game_hash, cert_item_id=item_id)
    rows = [
        [
            InlineKeyboardButton(text="💸 Закуп", callback_data=f"cert_cost_{game_hash}_{item_id}"),
            InlineKeyboardButton(text="🔗 Лот", callback_data=f"cert_offer_{game_hash}_{item_id}"),
        ],
        [InlineKeyboardButton(text="🗑 Удалить сертификат", callback_data=f"cert_delitem_{game_hash}_{item_id}")],
        [InlineKeyboardButton(text="↩️ К списку", callback_data=f"cert_edit_{game_hash}_0")],
    ]
    await call.message.edit_text(
        _build_item_text(game_name, info, rate),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await call.answer()


@router.callback_query(F.data.startswith("cert_delgame_"))
async def cb_cert_del_game(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    game_hash = call.data.split("_")[2]
    data = load_certificates(call.from_user.id)
    game_name = _game_by_hash(data, game_hash)
    if not game_name:
        return await call.answer("Игра не найдена", show_alert=True)
    del data[game_name]
    save_certificates(data, call.from_user.id)
    await call.message.edit_text(
        f"🗑 <b>Игра удалена:</b> {_html.escape(game_name)}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ К играм", callback_data="cert_pg_0")]
        ]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("cert_delitem_"))
async def cb_cert_del_item(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    _, _, game_hash, item_id = call.data.split("_")
    data = load_certificates(call.from_user.id)
    game_name = _game_by_hash(data, game_hash)
    if not game_name or item_id not in data.get(game_name, {}):
        return await call.answer("Сертификат не найден", show_alert=True)
    name = data[game_name][item_id].get("name", item_id)
    del data[game_name][item_id]
    save_certificates(data, call.from_user.id)
    await call.message.edit_text(
        f"🗑 <b>Сертификат удалён:</b> {_html.escape(str(name))}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ К списку", callback_data=f"cert_edit_{game_hash}_0")]
        ]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("cert_cost_"))
async def cb_cert_cost(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    _, _, game_hash, item_id = call.data.split("_")
    await state.update_data(cert_game_hash=game_hash, cert_item_id=item_id)
    await state.set_state(CertificateStates.waiting_cost)
    await call.message.edit_text(
        "💸 <b>Цена закупа</b>\n\nВведите новую закупочную цену сертификата.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"cert_item_{game_hash}_{item_id}")]
        ]),
    )
    await call.answer()


@router.message(CertificateStates.waiting_cost)
async def proc_cert_cost(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await state.clear()
    state_data = await state.get_data()
    game_hash = state_data.get("cert_game_hash")
    item_id = state_data.get("cert_item_id")
    cost = _money(message.text)
    if cost <= 0:
        return await message.answer("⚠️ Введите сумму больше нуля.")
    data = load_certificates(message.from_user.id)
    game_name = _game_by_hash(data, game_hash)
    if not game_name or item_id not in data.get(game_name, {}):
        await state.clear()
        return await message.answer("❌ Сертификат не найден.")
    data[game_name][item_id]["cost"] = cost
    save_certificates(data, message.from_user.id)
    await state.clear()
    await message.answer(
        f"✅ Закуп обновлён: <code>{cost:.2f} ₽</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ К сертификату", callback_data=f"cert_item_{game_hash}_{item_id}")]
        ]),
    )


@router.callback_query(F.data.startswith("cert_offer_"))
async def cb_cert_offer(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    _, _, game_hash, item_id = call.data.split("_")
    await state.update_data(cert_game_hash=game_hash, cert_item_id=item_id)
    await state.set_state(CertificateStates.waiting_offer_id)
    await call.message.edit_text(
        "🔗 <b>Лот сертификата</b>\n\n"
        "Отправьте числовой <code>offer_id</code>. У каждого номинала один лот.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"cert_item_{game_hash}_{item_id}")]
        ]),
    )
    await call.answer()


@router.message(CertificateStates.waiting_offer_id)
async def proc_cert_offer(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await state.clear()
    state_data = await state.get_data()
    game_hash = state_data.get("cert_game_hash")
    item_id = state_data.get("cert_item_id")
    match = re.search(r"\d+", message.text or "")
    if not match:
        return await message.answer("⚠️ Отправьте числовой offer_id.")
    offer_id = match.group(0)
    data = load_certificates(message.from_user.id)
    game_name = _game_by_hash(data, game_hash)
    if not game_name or item_id not in data.get(game_name, {}):
        await state.clear()
        return await message.answer("❌ Сертификат не найден.")
    data[game_name][item_id]["offer_id"] = offer_id
    save_certificates(data, message.from_user.id)
    await state.clear()
    await message.answer(
        f"✅ Лот привязан: <code>#{offer_id}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ К сертификату", callback_data=f"cert_item_{game_hash}_{item_id}")]
        ]),
    )


@router.callback_query(F.data == "cert_upload")
async def cb_cert_upload(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    await state.set_state(CertificateStates.waiting_upload)
    await call.message.edit_text(
        "📥 <b>Загрузка демпинг-файла сертификатов</b>\n\n"
        "Отправьте JSON файл Cardinal для сертификатов.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cert_menu")]
        ]),
    )
    await call.answer()


@router.message(CertificateStates.waiting_upload, F.document)
async def proc_cert_upload(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await state.clear()
    doc = message.document
    if not doc.file_name.endswith(".json"):
        return await message.answer("⚠️ Отправьте JSON файл.")
    try:
        from io import BytesIO
        buf = BytesIO()
        await message.bot.download(doc, buf)
        buf.seek(0)
        data = json.loads(buf.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Файл должен быть JSON-словарём")
        save_cert_demping(data)
        await state.clear()
        await message.answer(
            f"✅ <b>Файл сертификатов загружен.</b>\nЛотов: <b>{len(data)}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_kb(True),
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: <code>{_html.escape(str(e))}</code>", parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "cert_download")
async def cb_cert_download(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    if not os.path.exists(CERT_DEMPING_FILE):
        return await call.answer("Файл не найден", show_alert=True)
    with open(CERT_DEMPING_FILE, "rb") as f:
        payload = f.read()
    await call.message.answer_document(
        BufferedInputFile(payload, filename="price_optimizer_lots.json"),
        caption="📤 Актуальный демпинг-файл сертификатов",
    )
    await call.answer()


@router.callback_query(F.data == "cert_update_dmp")
async def cb_cert_update_dmp(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    certs = load_certificates(call.from_user.id)
    demping = load_cert_demping()
    updated = 0
    unchanged = 0
    created = 0
    no_offer = 0
    no_rate = 0
    for game_name, game_data in certs.items():
        rate = _rate(game_data.get("_meta", {}).get("rate", 0))
        if rate <= 0:
            no_rate += 1
            continue
        for _, info in _items(game_data):
            offer_id = str(info.get("offer_id") or "")
            if not offer_id:
                no_offer += 1
                continue
            new_price = calc_site_price(_money(info.get("cost", 0)), rate)
            if offer_id not in demping:
                demping[offer_id] = _demping_lot_template(info, new_price)
                created += 1
                continue
            old_price = _money(demping[offer_id].get("min_price"))
            if old_price == new_price:
                unchanged += 1
            else:
                demping[offer_id]["min_price"] = new_price
                updated += 1
    save_cert_demping(demping)
    await call.message.answer(
        "✅ <b>Демпинг сертификатов обновлён</b>\n\n"
        f"🔄 Актуализировано лотов: <b>{updated}</b>\n"
        f"🆕 Создано лотов: <b>{created}</b>\n"
        f"⏸ Без изменений: <b>{unchanged}</b>\n"
        f"📈 Игр без коэффициента: <b>{no_rate}</b>\n"
        f"🔗 Сертификатов без лота: <b>{no_offer}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=_main_kb(True),
    )
    await call.answer()


@router.callback_query(F.data == "cert_to_cardinal")
async def cb_cert_to_cardinal(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    if not os.path.exists(CERT_DEMPING_FILE):
        return await call.answer("Файл не найден", show_alert=True)
    await call.message.edit_text("⏳ <b>Отправляю сертификаты в Cardinal...</b>", parse_mode=ParseMode.HTML)
    try:
        target_dir = os.path.dirname(CERT_CARDINAL_TARGET_PATH)
        os.makedirs(target_dir, exist_ok=True)
        shutil.copy2(CERT_DEMPING_FILE, CERT_CARDINAL_TARGET_PATH)
        result = subprocess.run(
            ["systemctl", "restart", CERT_CARDINAL_SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "unknown error").strip())
        await call.message.edit_text(
            "✅ <b>Файл сертификатов отправлен в Cardinal</b>\n\n"
            f"📁 <code>{CERT_CARDINAL_TARGET_PATH}</code>\n"
            f"🔄 Сервис <code>{CERT_CARDINAL_SERVICE_NAME}</code> перезапущен",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_kb(True),
        )
    except Exception as e:
        await call.message.edit_text(
            f"❌ <b>Не удалось отправить файл</b>\n\n<code>{_html.escape(str(e)[:500])}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_kb(True),
        )
    await call.answer()


def get_certificate_auto_buy_prices(product_full_name: str, order_game: str | None = None, order_amount: int = 1) -> list:
    data = load_certificates(ADMIN_ID)
    if not data:
        return []

    if not _looks_like_certificate_order(product_full_name, order_game, data):
        return []

    product_clean = _normalize(product_full_name)
    game_clean = _normalize(order_game or "")

    if order_game:
        direct_games = {
            game_name: game_data
            for game_name, game_data in data.items()
            if game_clean in _normalize(game_name) or _normalize(game_name) in game_clean
        }
    else:
        direct_games = data
    if not direct_games:
        return []

    candidates = []
    for game_name, game_data in direct_games.items():
        for _, info in _items(game_data):
            name = str(info.get("name", ""))
            nominal = str(info.get("nominal", ""))
            probes = [_normalize(name), _normalize(nominal)]
            score = max((len(probe) for probe in probes if probe and probe in product_clean), default=0)
            if not score:
                continue
            qty = order_amount if order_amount and order_amount > 1 else 1
            total_cost = round(_money(info.get("cost", 0)) * qty, 2)
            candidates.append((score, game_name, name, total_cost, qty))

    if not candidates:
        return []

    best_score = max(score for score, *_ in candidates)
    results = []
    seen = set()
    for score, game_name, name, total_cost, qty in candidates:
        if score != best_score:
            continue
        key = (game_name, name, total_cost)
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "title": name,
            "cashback": "none",
            "cashback_label": "сертификат",
            "qty": qty,
            "total_cost": total_cost,
            "game_name": game_name,
        })
    return results
