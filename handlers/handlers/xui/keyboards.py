# handlers/xui/keyboards.py

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from handlers.xui.api.helpers import parse_clients, get_client_stats_map
from handlers.xui.storage import load_vpn_users
from handlers.xui.utils import cache, _cache, format_bytes, CLIENTS_PAGE_SIZE

# ====================== КЛАВИАТУРЫ ======================

def inbounds_kb(inbounds: list) -> InlineKeyboardMarkup:
    buttons = []
    for ib in inbounds:
        ib_id = ib.get("id")
        protocol = ib.get("protocol", "?").upper()
        port = ib.get("port", "?")
        remark = ib.get("remark") or f"{protocol}:{port}"
        clients_count = len(parse_clients(ib))
        enabled = "✅" if ib.get("enable", True) else "❌"
        h = cache(f"ib_{ib_id}", {"id": ib_id})
        buttons.append([InlineKeyboardButton(
            text=f"{enabled} {remark} | {protocol}:{port} | 👥{clients_count}",
            callback_data=f"xui_ib_{h}"
        )])
    buttons.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="xui_refresh")])
    buttons.append([InlineKeyboardButton(text="⚙️ Настройки", callback_data="xui_settings")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def xui_settings_kb(configured: bool) -> InlineKeyboardMarkup:
    status = "✅ Настроено" if configured else "⚠️ Не настроено"
    rows = [[InlineKeyboardButton(text=f"🔧 XUI: {status}", callback_data="xui_settings")]]
    if configured:
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="xui_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def clients_kb(inbound: dict, page: int = 0) -> InlineKeyboardMarkup:
    """Отображает: юзеров с привязкой (сгруппированно, в т.ч. анонимные) + одиночных клиентов"""
    ib_id = inbound.get("id")
    clients = parse_clients(inbound)
    stats_map = get_client_stats_map(inbound)

    vpn_users = load_vpn_users()
    grouped = {}      # user_key (str) -> info
    bound_emails = set()

    for user_key, info in vpn_users.items():
        # Берём только юзеров у которых есть устройства в ЭТОМ инбаунде ИЛИ нет устройств вообще (только TG)
        user_in_this_ib = any(d.get("ib_id") == ib_id for d in info.get("devices", []))
        # Если у юзера нет устройств вообще (пустой) - тоже показываем
        no_devices = not info.get("devices")
        if user_in_this_ib or no_devices:
            grouped[user_key] = info
            for d in info.get("devices", []):
                if d.get("ib_id") == ib_id:
                    bound_emails.add(d.get("email"))

    singles = [cl for cl in clients if cl.get("email") not in bound_emails]

    items = []  # (type, key, label)

    for user_key, info in sorted(grouped.items()):
        username = info.get("username", "")
        note = info.get("note", "")
        n_devices = len([d for d in info.get("devices", []) if d.get("ib_id") == ib_id])
        admin_disabled = info.get("admin_disabled", False)
        prefix = "🚫" if admin_disabled else "👤"

        if user_key.startswith("anon_"):
            label_id = "БЕЗ TG"
            id_part = "Без TG ID"
        else:
            label_id = user_key
            username_part = f" @{username}" if username else " (без username)"
            id_part = f"{user_key}{username_part}"

        note_suffix = f" • 📝{note[:15]}" if note else ""
        items.append(("user", user_key, f"{prefix} {id_part} ({n_devices} устр.){note_suffix}"))

    for cl in singles:
        email = cl.get("email", "?")
        enabled = cl.get("enable", True)
        stats = stats_map.get(email, {})
        up = format_bytes(stats.get("up", 0))
        down = format_bytes(stats.get("down", 0))
        status = "🟢" if enabled else "🔴"
        items.append(("single", email, f"{status} {email} ↑{up} ↓{down}"))

    total = len(items)
    total_pages = (total + CLIENTS_PAGE_SIZE - 1) // CLIENTS_PAGE_SIZE or 1
    page = max(0, min(page, total_pages - 1))
    start = page * CLIENTS_PAGE_SIZE
    end = start + CLIENTS_PAGE_SIZE
    page_items = items[start:end]

    buttons = []
    for item_type, key, label in page_items:
        if item_type == "user":
            uk_hash = cache(f"uk_{key}", {"user_key": key, "ib_id": ib_id})
            buttons.append([InlineKeyboardButton(text=label, callback_data=f"xui_usr_{uk_hash}")])
        else:
            email = key
            cl = next((c for c in singles if c.get("email") == email), None)
            uuid_val = cl.get("id", "") if cl else ""
            h = cache(f"cl_{ib_id}_{email}", {"email": email, "uuid": uuid_val, "ib_id": ib_id})
            buttons.append([InlineKeyboardButton(text=label, callback_data=f"xui_cl_{h}")])

    ib_h = cache(f"ib_{ib_id}", {"id": ib_id})

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"xui_ibpg_{ib_h}_{page-1}"))
        nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="none"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"xui_ibpg_{ib_h}_{page+1}"))
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="➕ Добавить пользователя", callback_data=f"xui_adduser_{ib_h}")])
    buttons.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"xui_ib_{ib_h}"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="xui_menu"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def client_actions_kb(client_hash: str, enabled: bool, is_vless: bool = False, owner_user_key: str = "") -> InlineKeyboardMarkup:
    info = _cache.get(client_hash, {})
    ib_id = info.get("ib_id")
    ib_h = cache(f"ib_{ib_id}", {"id": ib_id})
    toggle_text = "❌ Отключить" if enabled else "✅ Включить"
    buttons = [
        [InlineKeyboardButton(text=toggle_text, callback_data=f"xui_tog_{client_hash}")],
        [InlineKeyboardButton(text="🔄 Сбросить трафик", callback_data=f"xui_rst_{client_hash}")],
    ]
    if is_vless:
        buttons.append([InlineKeyboardButton(text="🔗 VLESS ссылка", callback_data=f"xui_vless_{client_hash}")])
        buttons.append([InlineKeyboardButton(text="📖 Инструкция", callback_data=f"xui_inst_{client_hash}")])

    # Если клиент НЕ привязан к юзеру — даём кнопки привязки и заметки
    if not owner_user_key:
        buttons.append([InlineKeyboardButton(text="📱 Привязать TG ID", callback_data=f"xui_bind_{client_hash}")])
        buttons.append([InlineKeyboardButton(text="📝 Изменить заметку", callback_data=f"xui_clnote_{client_hash}")])

    buttons.append([InlineKeyboardButton(text="🗑 Удалить устройство", callback_data=f"xui_del_{client_hash}")])

    # Кнопка "Назад" ведёт к юзеру если есть owner, иначе к инбаунду
    if owner_user_key:
        uk_h = cache(f"uk_{owner_user_key}", {"user_key": owner_user_key, "ib_id": ib_id})
        back_cb = f"xui_usr_{uk_h}"
    else:
        back_cb = f"xui_ib_{ib_h}"
    buttons.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"xui_cl_{client_hash}"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def flow_choice_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ xtls-rprx-vision", callback_data="xui_flow_xtls")],
        [InlineKeyboardButton(text="⬜ Без flow", callback_data="xui_flow_none")],
    ])


def user_menu_kb(user_key: str, admin_disabled: bool, devices: list, ib_id_default: int) -> InlineKeyboardMarkup:
    rows = []
    uk_h = cache(f"uk_{user_key}", {"user_key": user_key, "ib_id": ib_id_default})

    # Кнопки на каждое устройство в этом инбаунде
    for d in devices:
        if d.get("ib_id") != ib_id_default:
            continue
        ib_id = d.get("ib_id")
        email = d.get("email", "?")
        uuid_val = d.get("uuid", "")
        h = cache(f"cl_{ib_id}_{email}", {"email": email, "uuid": uuid_val, "ib_id": ib_id, "owner_uk": user_key})
        rows.append([InlineKeyboardButton(text=f"📱 {email}", callback_data=f"xui_cl_{h}")])

    if admin_disabled:
        rows.append([InlineKeyboardButton(text="✅ Включить все", callback_data=f"xui_unblk_{uk_h}")])
    else:
        rows.append([InlineKeyboardButton(text="🚫 Отключить все", callback_data=f"xui_ublk_{uk_h}")])

    rows.append([InlineKeyboardButton(text="➕ Добавить устройство", callback_data=f"xui_uadd_{uk_h}")])
    rows.append([InlineKeyboardButton(text="📝 Изменить заметку", callback_data=f"xui_unote_{uk_h}")])

    if user_key.startswith("anon_"):
        rows.append([InlineKeyboardButton(text="📱 Привязать TG ID", callback_data=f"xui_bindanon_{uk_h}")])
    else:
        rows.append([InlineKeyboardButton(text="🔓 Отвязать TG", callback_data=f"xui_uunbind_{uk_h}")])

    rows.append([InlineKeyboardButton(text="🗑 Удалить все устройства", callback_data=f"xui_udelall_{uk_h}")])
    rows.append([InlineKeyboardButton(text="🗑 Удалить пользователя", callback_data=f"xui_udel_{uk_h}")])

    ib_h = cache(f"ib_{ib_id_default}", {"id": ib_id_default})
    rows.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"xui_usr_{uk_h}"),
        InlineKeyboardButton(text="⬅️ К списку", callback_data=f"xui_ib_{ib_h}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)
