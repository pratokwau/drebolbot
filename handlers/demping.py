# handlers/demping.py

import os
import json
import asyncio
import html as _html

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.enums import ParseMode

from loader import is_authorized
from handlers.utils import no_access_reply, no_access_callback
from config import ADMIN_ID

router = Router()

DEMPING_FILE = "data/demping.json"


class DempingStates(StatesGroup):
    waiting_upload = State()
    waiting_cashback = State()


# ====================== ХРАНИЛИЩЕ ======================

def load_demping() -> dict:
    if not os.path.exists(DEMPING_FILE):
        return {}
    try:
        with open(DEMPING_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_demping(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(DEMPING_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ====================== ВСПОМОГАТЕЛЬНЫЕ ======================

def _load_mp(user_id: int) -> dict:
    from handlers.minprice import load_mp
    return load_mp(user_id)


def _save_mp(user_id: int, data: dict):
    from handlers.minprice import save_mp
    save_mp(user_id, data)


def _get_items(mp: dict, game: str) -> dict:
    return {k: v for k, v in mp.get(game, {}).items() if k != "_meta"}


def _normalize(text: str) -> str:
    return text.lower().strip()


def _money(value) -> float | None:
    if value is None:
        return None
    try:
        return round(float(str(value).replace(" ", "").replace("\xa0", "").replace(",", ".")), 2)
    except (TypeError, ValueError):
        return None


def _cashback_priority(cashback: str, preferred: str | None) -> int:
    if preferred and cashback == preferred:
        return 2
    if cashback == "none":
        return 0
    return 1


def _lot_match_score(lot: dict, game_name: str, item_name: str) -> int:
    trigger = _normalize(str(lot.get("triggers", "")))
    item = _normalize(item_name)
    game = _normalize(game_name)
    lot_text = _normalize(json.dumps(lot, ensure_ascii=False))

    score = 0
    if trigger and trigger != "другое количество":
        if trigger == item:
            score += 6
        elif trigger in item or item in trigger:
            score += 4
    if game and game in lot_text:
        score += 3
    if item and item in lot_text:
        score += 2
    return score


def _update_report_kb(result: dict) -> InlineKeyboardMarkup:
    rows = []
    if result.get("conflicts"):
        rows.append([InlineKeyboardButton(text="⚠️ Посмотреть конфликты", callback_data="dmp_conflicts")])
    rows.extend(demping_kb().inline_keyboard)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_conflicts(conflicts: list) -> str:
    if not conflicts:
        return "✅ <b>Конфликтов привязок нет.</b>"

    lines = [
        f"⚠️ <b>Конфликты привязок</b>",
        f"<i>Показаны первые {min(len(conflicts), 20)} из {len(conflicts)}.</i>",
        "",
    ]
    for conflict in conflicts[:20]:
        offer_id = _html.escape(str(conflict.get("offer_id", "")))
        applied = conflict.get("applied_price")
        lines.append(
            f"🔗 <b>offer_id:</b> <code>{offer_id}</code>"
            f" → применено <code>{applied:.2f} ₽</code>"
        )
        for variant in conflict.get("variants", [])[:6]:
            game = _html.escape(str(variant.get("game", "")))
            item = _html.escape(str(variant.get("item", "")))
            price = variant.get("price")
            cashback = {
                "yes": "с кэшбеком",
                "no": "без кэшбека",
                "none": "нет кэшбека",
            }.get(variant.get("cashback"), str(variant.get("cashback", "")))
            match_score = variant.get("match_score", 0)
            lines.append(
                f"   • {game} / {item} ({cashback}, совп. {match_score}): "
                f"<code>{price:.2f} ₽</code>"
            )
        if len(conflict.get("variants", [])) > 6:
            lines.append(f"   • ... ещё {len(conflict.get('variants', [])) - 6}")
        lines.append("")
    return "\n".join(lines).strip()


def demping_kb() -> InlineKeyboardMarkup:
    has_file = os.path.exists(DEMPING_FILE)
    buttons = [
        [InlineKeyboardButton(text="📥 Загрузить файл демпинга", callback_data="dmp_upload")],
    ]
    if has_file:
        buttons.append([InlineKeyboardButton(text="🔄 Обновить цены в файле", callback_data="dmp_update")])
        buttons.append([InlineKeyboardButton(text="🔗 Автопривязать товары", callback_data="dmp_autolink")])
        buttons.append([InlineKeyboardButton(text="📤 Выгрузить файл демпинга", callback_data="dmp_download")])
        buttons.append([InlineKeyboardButton(text="🚀 Отправить в Cardinal", callback_data="dmp_to_cardinal")])
    buttons.append([InlineKeyboardButton(text="↩️ FunPay Auto", callback_data="funpay_auto_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Конфигурация для отправки в Cardinal.
CARDINAL_TARGET_PATH = "/root/FunPayCardinal/storage/plugins/price_optimizer_lots.json"
CARDINAL_SERVICE_NAME = "funpaycardinal"


# ====================== ХЕНДЛЕРЫ ======================

@router.callback_query(F.data == "dmp_menu")
async def cb_dmp_menu(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    await state.clear()
    has_file = os.path.exists(DEMPING_FILE)
    text = "📁 <b>Управление файлом демпинга</b>\n\n"
    if has_file:
        demping = load_demping()
        text += f"<i>Лотов в файле: {len(demping)}</i>"
    else:
        text += "<i>Файл ещё не загружен.</i>"
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=demping_kb())
    await call.answer()


# --- Загрузка файла ---

@router.callback_query(F.data == "dmp_upload")
async def cb_dmp_upload(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    await state.set_state(DempingStates.waiting_upload)
    await call.message.answer(
        "📥 <b>Загрузка файла демпинга</b>\n\n"
        "Отправьте JSON файл (<code>price_optimizer_lots.json</code>)."
        "\n\n<i>Для выхода введите /cancel</i>",
        parse_mode=ParseMode.HTML
    )
    await call.answer()


@router.message(DempingStates.waiting_upload, F.document)
async def proc_dmp_upload(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await state.clear()

    doc = message.document
    if not doc.file_name.endswith(".json"):
        return await message.answer("⚠️ Отправьте именно <b>.json</b> файл.", parse_mode=ParseMode.HTML)

    try:
        from io import BytesIO
        buf = BytesIO()
        await message.bot.download(doc, buf)
        buf.seek(0)
        data = json.loads(buf.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Не словарь")
        save_demping(data)
        await state.clear()
        await message.answer(
            f"✅ <b>Файл загружен.</b>\n<i>Лотов: {len(data)}</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=demping_kb()
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: <code>{e}</code>", parse_mode=ParseMode.HTML)


# --- Выгрузка файла ---

@router.callback_query(F.data == "dmp_download")
async def cb_dmp_download(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    if not os.path.exists(DEMPING_FILE):
        return await call.answer("Файл не найден", show_alert=True)
    with open(DEMPING_FILE, "rb") as f:
        data = f.read()
    await call.message.answer_document(
        BufferedInputFile(data, filename="price_optimizer_lots.json"),
        caption="📤 Актуальный файл демпинга"
    )
    await call.answer()


@router.callback_query(F.data == "dmp_to_cardinal")
async def cb_dmp_to_cardinal(call: types.CallbackQuery):
    """Копирует demping.json в директорию Cardinal и перезапускает его"""
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)
    if not os.path.exists(DEMPING_FILE):
        return await call.answer("Файл не найден", show_alert=True)

    import shutil
    import subprocess

    await call.message.edit_text(
        "⏳ <b>Отправляю в Cardinal...</b>",
        parse_mode=ParseMode.HTML
    )

    try:
        # 1. Копируем файл
        target_dir = os.path.dirname(CARDINAL_TARGET_PATH)
        if not os.path.isdir(target_dir):
            await call.message.edit_text(
                f"❌ <b>Директория не найдена:</b>\n<code>{target_dir}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=demping_kb()
            )
            return await call.answer()

        shutil.copy2(DEMPING_FILE, CARDINAL_TARGET_PATH)

        # 2. Перезапускаем Cardinal
        result = subprocess.run(
            ["systemctl", "restart", CARDINAL_SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()[:500]
            await call.message.edit_text(
                f"⚠️ <b>Файл скопирован, но не удалось перезапустить Cardinal:</b>\n\n"
                f"<code>{err or 'unknown error'}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=demping_kb()
            )
            return await call.answer()

        # Проверяем что сервис запустился
        await asyncio.sleep(2)
        status = subprocess.run(
            ["systemctl", "is-active", CARDINAL_SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=10
        )
        is_active = status.stdout.strip() == "active"

        if is_active:
            await call.message.edit_text(
                f"✅ <b>Файл отправлен в Cardinal!</b>\n\n"
                f"📁 <code>{CARDINAL_TARGET_PATH}</code>\n"
                f"🔄 Сервис <code>{CARDINAL_SERVICE_NAME}</code> перезапущен",
                parse_mode=ParseMode.HTML,
                reply_markup=demping_kb()
            )
        else:
            await call.message.edit_text(
                f"⚠️ <b>Файл скопирован, но сервис не активен</b>\n\n"
                f"Статус: <code>{status.stdout.strip()}</code>\n"
                f"Проверь: <code>journalctl -u {CARDINAL_SERVICE_NAME} -n 30</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=demping_kb()
            )

    except subprocess.TimeoutExpired:
        await call.message.edit_text(
            "⚠️ <b>Превышено время ожидания</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=demping_kb()
        )
    except PermissionError as e:
        await call.message.edit_text(
            f"❌ <b>Нет прав доступа:</b>\n<code>{str(e)[:300]}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=demping_kb()
        )
    except Exception as e:
        await call.message.edit_text(
            f"❌ <b>Ошибка:</b>\n<code>{type(e).__name__}: {str(e)[:300]}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=demping_kb()
        )

    await call.answer()


# --- Автопривязка offer_id ---

@router.callback_query(F.data == "dmp_autolink")
async def cb_dmp_autolink(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)

    if not os.path.exists(DEMPING_FILE):
        return await call.answer("Сначала загрузите файл демпинга", show_alert=True)

    demping = load_demping()
    mp = _load_mp(call.from_user.id)

    linked = 0
    skipped = 0

    for game_name, game_data in mp.items():
        items = _get_items(mp, game_name)
        for item_id, info in items.items():
            if not isinstance(info, dict):
                continue
            if info.get("offer_id"):
                skipped += 1
                continue
            item_name = _normalize(info.get("name", ""))
            # Ищем совпадение по triggers в demping.json
            for offer_id, lot in demping.items():
                trigger = _normalize(lot.get("triggers", ""))
                if not trigger or trigger == "другое количество":
                    continue
                if item_name == trigger or trigger in item_name or item_name in trigger:
                    info["offer_id"] = int(offer_id)
                    mp[game_name][item_id] = info
                    linked += 1
                    break

    _save_mp(call.from_user.id, mp)

    await call.message.answer(
        f"🔗 <b>Автопривязка завершена</b>\n\n"
        f"✅ Привязано: <b>{linked}</b>\n"
        f"⏭ Уже было привязано: <b>{skipped}</b>\n\n"
        f"<i>Непривязанные товары можно привязать вручную через кнопку в карточке товара.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=demping_kb()
    )
    await call.answer()


# --- Обновление цен ---

@router.callback_query(F.data == "dmp_update")
async def cb_dmp_update(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)

    demping = load_demping()
    mp = _load_mp(call.from_user.id)

    def _has_offer_ids(info):
        ids = info.get("offer_ids", [])
        if not ids and info.get("offer_id"):
            ids = [info["offer_id"]]
        return bool(ids)

    # Ищем игры где есть привязанные товары и с кэшбеком, и без него
    ambiguous_games = []
    for game_name, game_data in mp.items():
        meta = game_data.get("_meta", {})
        if not meta.get("sbp_rate"):
            continue
        items = _get_items(mp, game_name)
        linked = [v for v in items.values() if isinstance(v, dict) and _has_offer_ids(v)]
        if not linked:
            continue
        has_yes = False
        has_no = False
        for v in linked:
            cb = v.get("cashback", "none")
            if cb == "yes":
                has_yes = True
            elif cb == "no":
                has_no = True
            if has_yes and has_no:
                break
        if has_yes and has_no:
            ambiguous_games.append(game_name)

    if ambiguous_games:
        # По умолчанию - "без кэшбека"
        prefs = {g: "no" for g in ambiguous_games}
        await state.update_data(ambiguous_games=ambiguous_games, prefs=prefs, cashback_page=0)
        await state.set_state(DempingStates.waiting_cashback)
        await call.message.edit_text(
            _build_cashback_menu_text(ambiguous_games, prefs, page=0),
            parse_mode=ParseMode.HTML,
            reply_markup=_build_cashback_menu_kb(ambiguous_games, prefs, page=0)
        )
        await call.answer()
        return

    # Если неоднозначностей нет — сразу обновляем (все без кэшбека по умолчанию)
    result = _do_update(mp, demping, call.from_user.id, prefs_override={})
    await state.update_data(last_dmp_conflicts=result.get("conflicts", []))
    await call.message.answer(
        _update_report(result),
        parse_mode=ParseMode.HTML,
        reply_markup=_update_report_kb(result)
    )
    await call.answer()


CASHBACK_PAGE_SIZE = 5


def _build_cashback_menu_text(games: list, prefs: dict, page: int = 0) -> str:
    total_pages = (len(games) + CASHBACK_PAGE_SIZE - 1) // CASHBACK_PAGE_SIZE or 1
    text = "💳 <b>Выберите кэшбек для каждой игры</b>\n"
    text += f"<i>Страница {page+1}/{total_pages} • Всего игр: {len(games)}</i>\n\n"
    text += "<i>В строке игры выберите: ✅ с или ✅ без.</i>\n\n"

    # Показываем сводку выбранных кэшбеков (всех игр, не только на странице)
    yes_count = sum(1 for g in games if prefs.get(g, "no") == "yes")
    no_count = len(games) - yes_count
    text += f"💳 С кэшбеком: <b>{yes_count}</b> • 💵 Без кэшбека: <b>{no_count}</b>\n"
    return text


def _build_cashback_menu_kb(games: list, prefs: dict, page: int = 0) -> InlineKeyboardMarkup:
    total_pages = (len(games) + CASHBACK_PAGE_SIZE - 1) // CASHBACK_PAGE_SIZE or 1
    start = page * CASHBACK_PAGE_SIZE
    end = start + CASHBACK_PAGE_SIZE
    page_games = games[start:end]

    rows = []
    for local_idx, g in enumerate(page_games):
        global_idx = start + local_idx
        choice = prefs.get(g, "no")
        cb_mark = "✅" if choice == "yes" else "☐"
        no_mark = "✅" if choice == "no" else "☐"
        short = (g[:24] + "…") if len(g) > 24 else g
        rows.append([
            InlineKeyboardButton(text=f"🎮 {short}", callback_data="none"),
            InlineKeyboardButton(text=f"{cb_mark} с", callback_data=f"dmp_pref_{global_idx}_yes"),
            InlineKeyboardButton(text=f"{no_mark} без", callback_data=f"dmp_pref_{global_idx}_no"),
        ])

    # Пагинация
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"dmp_cbpg_{page-1}"))
        nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="none"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"dmp_cbpg_{page+1}"))
        rows.append(nav)

    # Массовый выбор
    rows.append([
        InlineKeyboardButton(text="💳 Все с кэшбеком", callback_data="dmp_setall_yes"),
        InlineKeyboardButton(text="💵 Все без кэшбека", callback_data="dmp_setall_no"),
    ])
    rows.append([InlineKeyboardButton(text="✅ Обновить цены", callback_data="dmp_apply_update")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="dmp_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("dmp_setall_"))
async def cb_dmp_setall(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)

    choice = call.data.split("_")[2]  # "yes" / "no"
    state_data = await state.get_data()
    ambiguous_games = state_data.get("ambiguous_games", [])
    page = state_data.get("cashback_page", 0)

    prefs = {g: choice for g in ambiguous_games}
    await state.update_data(prefs=prefs)

    await call.message.edit_text(
        _build_cashback_menu_text(ambiguous_games, prefs, page=page),
        parse_mode=ParseMode.HTML,
        reply_markup=_build_cashback_menu_kb(ambiguous_games, prefs, page=page)
    )
    await call.answer(f"Установлено для всех: {'с кэшбеком' if choice == 'yes' else 'без кэшбека'}")


@router.callback_query(F.data.startswith("dmp_cbpg_"))
async def cb_dmp_cashback_page(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)

    page = int(call.data.split("_")[2])
    state_data = await state.get_data()
    ambiguous_games = state_data.get("ambiguous_games", [])
    prefs = state_data.get("prefs", {})

    await state.update_data(cashback_page=page)
    await call.message.edit_text(
        _build_cashback_menu_text(ambiguous_games, prefs, page=page),
        parse_mode=ParseMode.HTML,
        reply_markup=_build_cashback_menu_kb(ambiguous_games, prefs, page=page)
    )
    await call.answer()


@router.callback_query(F.data.startswith("dmp_pref_"))
async def cb_dmp_toggle_pref(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)

    parts = call.data.split("_")
    idx = int(parts[2])
    choice = parts[3]

    state_data = await state.get_data()
    ambiguous_games = state_data.get("ambiguous_games", [])
    prefs = state_data.get("prefs", {})
    page = state_data.get("cashback_page", 0)

    if idx >= len(ambiguous_games):
        return await call.answer()

    game_name = ambiguous_games[idx]
    prefs[game_name] = choice
    await state.update_data(prefs=prefs)

    await call.message.edit_text(
        _build_cashback_menu_text(ambiguous_games, prefs, page=page),
        parse_mode=ParseMode.HTML,
        reply_markup=_build_cashback_menu_kb(ambiguous_games, prefs, page=page)
    )
    await call.answer()


@router.callback_query(F.data == "dmp_apply_update")
async def cb_dmp_apply_update(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)

    state_data = await state.get_data()
    prefs = state_data.get("prefs", {})

    await state.clear()
    mp = _load_mp(call.from_user.id)
    demping = load_demping()
    result = _do_update(mp, demping, call.from_user.id, prefs_override=prefs)
    await state.update_data(last_dmp_conflicts=result.get("conflicts", []))
    await call.message.answer(
        _update_report(result),
        parse_mode=ParseMode.HTML,
        reply_markup=_update_report_kb(result)
    )
    await call.answer()


@router.callback_query(F.data == "dmp_conflicts")
async def cb_dmp_conflicts(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await no_access_callback(call)

    state_data = await state.get_data()
    conflicts = state_data.get("last_dmp_conflicts", [])
    await call.message.answer(
        _format_conflicts(conflicts),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Демпинг", callback_data="dmp_menu")]
        ])
    )
    await call.answer()


def _do_update(mp: dict, demping: dict, user_id: int, prefs_override: dict = None) -> dict:
    updated_lots = 0       # реально изменились цены в файле
    updated_items = 0      # товаров (в боте) которые повлияли
    unchanged_lots = 0     # цена уже совпадала
    skipped_no_rate = 0    # игр без sbp_rate
    skipped_no_offer = 0   # товаров без привязок
    not_in_file = 0        # offer_id отсутствует в demping.json
    prefs_override = prefs_override or {}
    target_prices = {}
    target_items = {}
    target_priorities = {}
    target_match_scores = {}
    conflicting_lots = set()
    target_sources = {}

    for game_name, game_data in mp.items():
        meta = game_data.get("_meta", {})
        sbp_rate = meta.get("sbp_rate")
        cashback_pref = prefs_override.get(game_name) or meta.get("cashback_pref")

        if not sbp_rate:
            skipped_no_rate += 1
            continue

        items = {k: v for k, v in game_data.items() if k != "_meta" and isinstance(v, dict)}

        def _get_ids(info):
            ids = info.get("offer_ids", [])
            if not ids and info.get("offer_id"):
                ids = [info["offer_id"]]
            return ids

        if cashback_pref:
            name_to_item = {}
            for item_id, info in items.items():
                ids = _get_ids(info)
                if not ids:
                    continue
                # Одинаковые названия могут быть разными товарами с разными лотами.
                # Схлопывать можно только варианты одного и того же набора offer_id.
                name = (_normalize(info.get("name", "")), tuple(sorted(str(i) for i in ids)))
                cb = info.get("cashback", "none")
                if cb == cashback_pref:
                    name_to_item[name] = info
                elif name not in name_to_item:
                    name_to_item[name] = info
            work_items = list(name_to_item.values())
        else:
            work_items = [v for v in items.values() if _get_ids(v)]

        for info in work_items:
            offer_ids = _get_ids(info)
            if not offer_ids:
                skipped_no_offer += 1
                continue

            new_min = _money(info["min_price"] * sbp_rate)
            if new_min is None:
                continue
            item_cashback = info.get("cashback", "none")
            item_priority = _cashback_priority(item_cashback, cashback_pref)
            item_name = info.get("name", "")

            for oid in offer_ids:
                oid_str = str(oid)
                if oid_str not in demping:
                    not_in_file += 1
                    continue
                match_score = _lot_match_score(demping.get(oid_str, {}), game_name, item_name)
                item_key = (game_name, _normalize(item_name), new_min, item_cashback, match_score)
                # Один и тот же offer_id может быть привязан к нескольким товарам.
                # Сначала собираем финальное значение, потом применяем один раз,
                # иначе повторный запуск снова считает лот "изменённым".
                target_sources.setdefault(oid_str, [])
                if item_key not in target_sources[oid_str]:
                    target_sources[oid_str].append(item_key)
                if oid_str not in target_prices:
                    target_prices[oid_str] = new_min
                    target_items[oid_str] = item_key
                    target_priorities[oid_str] = item_priority
                    target_match_scores[oid_str] = match_score
                    continue

                old_priority = target_priorities.get(oid_str, 0)
                old_match_score = target_match_scores.get(oid_str, 0)
                if target_prices[oid_str] != new_min:
                    if match_score > old_match_score or (
                        match_score == old_match_score and item_priority > old_priority
                    ):
                        target_prices[oid_str] = new_min
                        target_items[oid_str] = item_key
                        target_priorities[oid_str] = item_priority
                        target_match_scores[oid_str] = match_score
                        conflicting_lots.discard(oid_str)
                    elif match_score == old_match_score and item_priority == old_priority:
                        conflicting_lots.add(oid_str)

    changed_items = set()
    for oid_str, new_min in target_prices.items():
        old_min = _money(demping[oid_str].get("min_price"))
        if old_min == new_min:
            unchanged_lots += 1
        else:
            demping[oid_str]["min_price"] = new_min
            updated_lots += 1
            changed_items.add(target_items.get(oid_str))

    updated_items = len([item for item in changed_items if item])
    conflicts = []
    for oid_str in sorted(conflicting_lots, key=lambda x: int(x) if str(x).isdigit() else str(x)):
        variants = [
            {"game": game, "item": item, "price": price, "cashback": cashback, "match_score": match_score}
            for game, item, price, cashback, match_score in target_sources.get(oid_str, [])
        ]
        conflicts.append({
            "offer_id": oid_str,
            "current_price": _money(demping.get(oid_str, {}).get("min_price")),
            "applied_price": target_prices.get(oid_str),
            "variants": variants,
        })

    save_demping(demping)
    return {
        "updated_lots": updated_lots,
        "updated_items": updated_items,
        "unchanged_lots": unchanged_lots,
        "skipped_no_rate": skipped_no_rate,
        "skipped_no_offer": skipped_no_offer,
        "not_in_file": not_in_file,
        "conflicting_lots": len(conflicting_lots),
        "conflicts": conflicts,
    }


def _update_report(r: dict) -> str:
    return (
        f"✅ <b>Файл демпинга обновлён</b>\n\n"
        f"🔄 Актуализировано лотов: <b>{r['updated_lots']}</b> (товаров: <b>{r['updated_items']}</b>)\n"
        f"⏸ Без изменений: <b>{r['unchanged_lots']}</b>\n"
        f"❓ Игр без ставки СБП: <b>{r['skipped_no_rate']}</b>\n"
        f"🔗 Товаров без привязок: <b>{r['skipped_no_offer']}</b>\n"
        f"📄 Лотов нет в файле: <b>{r['not_in_file']}</b>\n"
        f"⚠️ Конфликтов привязок: <b>{r.get('conflicting_lots', 0)}</b>"
    )
