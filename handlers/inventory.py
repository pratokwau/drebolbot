import hashlib
import json
import io
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile

from config import ADMIN_ID
from handlers.utils import load_inventory, save_inventory, no_access_reply, no_access_callback

router = Router()


class InventoryStates(StatesGroup):
    waiting_for_add = State()
    waiting_for_bulk_add = State()
    waiting_for_new_name = State()
    waiting_for_new_price = State()
    waiting_for_search = State()
    waiting_for_import = State()


def get_hash(text: str):
    return hashlib.md5(text.encode()).hexdigest()[:8]


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


# ====================== КЛАВИАТУРЫ ======================

def get_main_inv_kb(inventory: dict, page: int = 0):
    items_per_page = 10
    names = list(inventory.keys())
    total_pages = (len(names) + items_per_page - 1) // items_per_page or 1

    start = page * items_per_page
    end = start + items_per_page
    current_items = names[start:end]

    buttons = []
    for name in current_items:
        item_id = get_hash(name)
        display_name = (name[:25] + '..') if len(name) > 25 else name
        buttons.append([InlineKeyboardButton(text=f"📦 {display_name}", callback_data=f"inv_view_{item_id}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"inv_pg_{page-1}"))
    nav_row.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="none"))
    if end < len(names):
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"inv_pg_{page+1}"))
    buttons.append(nav_row)

    buttons.append([
        InlineKeyboardButton(text="🔍 Найти", callback_data="inv_search"),
        InlineKeyboardButton(text="➕ Добавить", callback_data="inv_add_new"),
    ])
    buttons.append([
        InlineKeyboardButton(text="📋 Массово", callback_data="inv_bulk_add"),
        InlineKeyboardButton(text="📤 Экспорт", callback_data="inv_export"),
        InlineKeyboardButton(text="📥 Импорт", callback_data="inv_import"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_search_results_kb(results: list):
    buttons = []
    for name in results:
        item_id = get_hash(name)
        display_name = (name[:25] + '..') if len(name) > 25 else name
        buttons.append([InlineKeyboardButton(text=f"📦 {display_name}", callback_data=f"inv_view_{item_id}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="inv_pg_0")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_item_manage_kb(item_hash: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Изменить название", callback_data=f"inv_editname_{item_hash}")],
        [InlineKeyboardButton(text="💰 Изменить цену", callback_data=f"inv_editprice_{item_hash}")],
        [InlineKeyboardButton(text="🗑 Удалить товар", callback_data=f"inv_del_{item_hash}")],
        [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="inv_pg_0")]
    ])


def get_confirm_delete_kb(item_hash: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"inv_delconfirm_{item_hash}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"inv_view_{item_hash}"),
        ]
    ])


def back_to_item_kb(item_hash: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"inv_view_{item_hash}")]
    ])


def back_to_list_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="inv_pg_0")]
    ])


# ====================== ХЕНДЛЕРЫ ======================

@router.message(Command("items"))
async def cmd_list_items(message: types.Message):
    if not is_admin(message.from_user.id):
        await no_access_reply(message)
        return

    inv = load_inventory()
    count = len(inv)
    await message.answer(
        f"📋 <b>Управление товарами</b> ({count} шт.):",
        reply_markup=get_main_inv_kb(inv, 0),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.startswith("inv_"))
async def cb_inventory_all(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await no_access_callback(call)
        return

    data = call.data

    # --- Переключение страниц ---
    if data.startswith("inv_pg_"):
        await state.clear()
        page = int(data.split("_")[2])
        inv = load_inventory()
        await call.message.edit_text(
            f"📋 <b>Управление товарами</b> ({len(inv)} шт.):",
            reply_markup=get_main_inv_kb(inv, page),
            parse_mode=ParseMode.HTML
        )
        await call.answer()

    # --- Просмотр товара ---
    elif data.startswith("inv_view_"):
        await state.clear()
        item_hash = data.split("_")[2]
        inv = load_inventory()
        target_name = next((n for n in inv if get_hash(n) == item_hash), None)
        if not target_name:
            return await call.answer("Товар не найден", show_alert=True)
        text = (
            f"📦 <b>Товар:</b>\n<code>{target_name}</code>\n\n"
            f"💰 <b>Себестоимость:</b> <code>{inv[target_name]:.2f}</code> ₽"
        )
        await call.message.edit_text(text, reply_markup=get_item_manage_kb(item_hash), parse_mode=ParseMode.HTML)
        await call.answer()

    # --- Изменение названия ---
    elif data.startswith("inv_editname_"):
        item_hash = data.split("_")[2]
        await state.update_data(target_hash=item_hash)
        await state.set_state(InventoryStates.waiting_for_new_name)
        await call.message.edit_text(
            f"📝 Введите <b>новое название</b>:{EXIT_HINT}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_item_kb(item_hash)
        )
        await call.answer()

    # --- Изменение цены ---
    elif data.startswith("inv_editprice_"):
        item_hash = data.split("_")[2]
        await state.update_data(target_hash=item_hash)
        await state.set_state(InventoryStates.waiting_for_new_price)
        await call.message.edit_text(
            f"💰 Введите <b>новую цену закупа</b>:{EXIT_HINT}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_item_kb(item_hash)
        )
        await call.answer()

    # --- Запрос подтверждения удаления ---
    elif data.startswith("inv_del_") and not data.startswith("inv_delconfirm_"):
        item_hash = data.split("_")[2]
        inv = load_inventory()
        target_name = next((n for n in inv if get_hash(n) == item_hash), None)
        if not target_name:
            return await call.answer("Товар не найден", show_alert=True)
        display_name = (target_name[:35] + '..') if len(target_name) > 35 else target_name
        await call.message.edit_text(
            f"🗑 <b>Удалить товар?</b>\n\n<code>{display_name}</code>",
            reply_markup=get_confirm_delete_kb(item_hash),
            parse_mode=ParseMode.HTML
        )
        await call.answer()

    # --- Подтверждение удаления ---
    elif data.startswith("inv_delconfirm_"):
        item_hash = data.split("_")[2]
        inv = load_inventory()
        target_name = next((n for n in inv if get_hash(n) == item_hash), None)
        if target_name:
            del inv[target_name]
            save_inventory(inv)
            await call.answer(f"🗑 Удалено: {target_name[:20]}", show_alert=False)
        else:
            await call.answer("Товар уже удалён", show_alert=True)
        inv = load_inventory()
        await call.message.edit_text(
            f"📋 <b>Управление товарами</b> ({len(inv)} шт.):",
            reply_markup=get_main_inv_kb(inv, 0),
            parse_mode=ParseMode.HTML
        )

    # --- Одиночное добавление ---
    elif data == "inv_add_new":
        await state.set_state(InventoryStates.waiting_for_add)
        await call.message.edit_text(
            "📝 <b>Добавление товара</b>\n\n"
            "Введите в формате:\n<code>Название | Цена</code>\n\n"
            "Пример: <code>Ключ Steam | 80</code>"
            f"{EXIT_HINT}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_list_kb()
        )
        await call.answer()

    # --- Массовое добавление ---
    elif data == "inv_bulk_add":
        await state.set_state(InventoryStates.waiting_for_bulk_add)
        await call.message.edit_text(
            "📋 <b>Массовое добавление</b>\n\n"
            "Введите каждый товар с новой строки в формате:\n"
            "<code>Название | Цена</code>\n\n"
            "Пример:\n"
            "<code>Ключ Steam 100р | 80\n"
            "Монеты 1000 | 5.50\n"
            "Скин CS2 | 1200</code>"
            f"{EXIT_HINT}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_list_kb()
        )
        await call.answer()

    # --- Поиск ---
    elif data == "inv_search":
        await state.set_state(InventoryStates.waiting_for_search)
        await call.message.edit_text(
            "🔍 <b>Поиск товара</b>\n\n"
            f"Введите часть названия:{EXIT_HINT}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_list_kb()
        )
        await call.answer()

    # --- Экспорт ---
    elif data == "inv_export":
        inv = load_inventory()
        if not inv:
            await call.answer("📋 Список товаров пуст", show_alert=True)
            return
        json_bytes = json.dumps(inv, ensure_ascii=False, indent=2).encode("utf-8")
        file = BufferedInputFile(json_bytes, filename="inventory.json")
        await call.message.answer_document(
            file,
            caption=f"📤 <b>Экспорт базы товаров</b>\n{len(inv)} позиций",
            parse_mode=ParseMode.HTML
        )
        await call.answer("✅ Файл отправлен")

    # --- Импорт (старт) ---
    elif data == "inv_import":
        await state.set_state(InventoryStates.waiting_for_import)
        await call.message.edit_text(
            "📥 <b>Импорт базы товаров</b>\n\n"
            "Отправьте JSON-файл в формате:\n"
            "<code>{\"Название товара\": цена, ...}</code>\n\n"
            "⚠️ Существующие товары с теми же именами будут <b>перезаписаны</b>."
            f"{EXIT_HINT}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_list_kb()
        )
        await call.answer()


# ====================== ОБРАБОТКА ВВОДА ======================

@router.message(InventoryStates.waiting_for_new_name)
async def proc_new_name(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()

    data = await state.get_data()
    inv = load_inventory()
    old_name = next((n for n in inv if get_hash(n) == data.get('target_hash')), None)

    if old_name:
        price = inv.pop(old_name)
        new_name = message.text.strip()
        inv[new_name] = price
        save_inventory(inv)
        await message.answer(f"✅ Название изменено на: <code>{new_name}</code>", parse_mode=ParseMode.HTML)
    else:
        await message.answer("⚠️ Товар не найден.")

    await state.clear()


@router.message(InventoryStates.waiting_for_new_price)
async def proc_new_price(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()

    data = await state.get_data()
    inv = load_inventory()
    name = next((n for n in inv if get_hash(n) == data.get('target_hash')), None)

    try:
        price = float(message.text.strip().replace(",", "."))
        if name:
            inv[name] = price
            save_inventory(inv)
            await message.answer(f"✅ Цена обновлена: <b>{price:.2f} ₽</b>", parse_mode=ParseMode.HTML)
    except Exception:
        await message.answer("⚠️ Введите число!")

    await state.clear()


@router.message(InventoryStates.waiting_for_add)
async def proc_add(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()

    if "|" not in message.text:
        return await message.answer("⚠️ Формат: <code>Название | Цена</code>", parse_mode=ParseMode.HTML)

    try:
        name, price_str = message.text.split("|", 1)
        name = name.strip()
        price = float(price_str.strip().replace(",", "."))
        inv = load_inventory()
        inv[name] = price
        save_inventory(inv)
        await message.answer(
            f"✅ <b>Товар добавлен!</b>\n<code>{name}</code> — {price:.2f} ₽",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        await message.answer("⚠️ Ошибка в цене. Проверьте формат.")

    await state.clear()


@router.message(InventoryStates.waiting_for_bulk_add)
async def proc_bulk_add(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()

    lines = message.text.strip().split("\n")
    inv = load_inventory()

    added = []
    errors = []

    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        if "|" not in line:
            errors.append(f"Строка {i}: нет разделителя «|»")
            continue
        try:
            name, price_str = line.split("|", 1)
            name = name.strip()
            price = float(price_str.strip().replace(",", "."))
            inv[name] = price
            added.append(f"<code>{name}</code> — {price:.2f} ₽")
        except Exception:
            errors.append(f"Строка {i}: ошибка в цене")

    if added:
        save_inventory(inv)

    text = ""
    if added:
        text += f"✅ <b>Добавлено {len(added)} товаров:</b>\n" + "\n".join(added)
    if errors:
        text += f"\n\n⚠️ <b>Ошибки ({len(errors)}):</b>\n" + "\n".join(errors)
    if not text:
        text = "⚠️ Ничего не добавлено. Проверьте формат."

    await message.answer(text, parse_mode=ParseMode.HTML)
    await state.clear()


@router.message(InventoryStates.waiting_for_search)
async def proc_search(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()

    query = message.text.strip().lower()
    inv = load_inventory()
    results = [name for name in inv if query in name.lower()]

    if not results:
        await message.answer(
            f"🔍 По запросу <b>«{query}»</b> ничего не найдено.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_list_kb()
        )
    else:
        await message.answer(
            f"🔍 <b>Результаты поиска «{query}»</b> ({len(results)} шт.):",
            parse_mode=ParseMode.HTML,
            reply_markup=get_search_results_kb(results)
        )

    await state.clear()


@router.message(InventoryStates.waiting_for_import)
async def proc_import(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()

    if not message.document:
        await message.answer("⚠️ Отправьте JSON-файл документом.")
        return

    if not message.document.file_name.endswith(".json"):
        await message.answer("⚠️ Файл должен быть формата <b>.json</b>", parse_mode=ParseMode.HTML)
        return

    try:
        file = await message.bot.get_file(message.document.file_id)
        downloaded = await message.bot.download_file(file.file_path)
        data = json.loads(downloaded.read().decode("utf-8"))

        if not isinstance(data, dict):
            raise ValueError("Файл должен содержать объект {}")

        inv = load_inventory()
        added = 0
        updated = 0

        for name, price in data.items():
            if not isinstance(name, str) or not isinstance(price, (int, float)):
                continue
            if name in inv:
                updated += 1
            else:
                added += 1
            inv[name] = float(price)

        save_inventory(inv)
        await message.answer(
            f"📥 <b>Импорт завершён!</b>\n\n"
            f"➕ Добавлено: <b>{added}</b>\n"
            f"✏️ Обновлено: <b>{updated}</b>\n"
            f"📦 Итого в базе: <b>{len(inv)}</b>",
            parse_mode=ParseMode.HTML
        )
    except json.JSONDecodeError:
        await message.answer("⚠️ Не удалось прочитать файл. Убедитесь, что это валидный JSON.")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка импорта: {e}")

    await state.clear()
