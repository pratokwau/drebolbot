# handlers/xui/myvpn.py

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode

from handlers.xui.api.inbounds import api_get_inbounds
from handlers.xui.api.clients import api_add_client, api_get_client, api_del_client_by_email, build_update_payload, email_path
from handlers.xui.api.client import xui_post
from handlers.xui.api.helpers import parse_clients, get_client_stats_map
from handlers.xui.storage import (
    get_vpn_user, add_device_to_user, remove_device_from_user, set_user_username,
    DEFAULT_MAX_DEVICES,
)
from handlers.xui.links import build_instruction_text, fetch_subscription_link
from handlers.xui.states import MyVpnAddDevice
from handlers.xui.utils import cache, _cache, format_bytes
from handlers.xui.views import show_myvpn, show_myvpn_device

router = Router()
EXIT_HINT = "\n\n<i>Для выхода введите /cancel</i>"

@router.message(Command("myvpn"))
async def cmd_myvpn(message: types.Message):
    await show_myvpn(message, message.from_user.id, edit=False, real_username=message.from_user.username or "")


@router.callback_query(F.data.startswith("myvpn_"))
async def cb_myvpn(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    binding = get_vpn_user(user_id)
    if not binding:
        return await call.answer("У вас нет привязанного VPN", show_alert=True)

    data = call.data
    admin_disabled = binding.get("admin_disabled", False)

    if data == "myvpn_refresh":
        await call.answer("⏳")
        return await show_myvpn(call.message, user_id, edit=True, real_username=call.from_user.username or "")

    if data == "myvpn_add":
        if admin_disabled:
            return await call.answer("VPN заблокирован админом", show_alert=True)
        if len(binding.get("devices", [])) >= binding.get("max_devices", DEFAULT_MAX_DEVICES):
            return await call.answer("Достигнут лимит устройств", show_alert=True)
        await state.set_state(MyVpnAddDevice.waiting_name)
        await call.message.answer(
            "📱 <b>Добавление нового устройства</b>\n\n"
            "Введите название устройства (например, <code>iPhone</code> или <code>PCработа</code>):\n"
            f"{EXIT_HINT}",
            parse_mode=ParseMode.HTML
        )
        return await call.answer()

    if data.startswith("myvpn_dev_"):
        dev_hash = data[len("myvpn_dev_"):]
        await call.answer("⏳")
        return await show_myvpn_device(call, dev_hash)

    # Остальные действия требуют dev_hash
    parts = data.split("_", 2)
    if len(parts) < 3:
        return await call.answer("Ошибка данных", show_alert=True)
    action, dev_hash = parts[1], parts[2]

    info = _cache.get(dev_hash, {})
    email = info.get("email")
    uuid_val = info.get("uuid")
    ib_id = info.get("ib_id")

    # Проверка что устройство принадлежит юзеру
    own = any(d.get("ib_id") == ib_id and d.get("email") == email for d in binding.get("devices", []))
    if not own:
        return await call.answer("Это не ваше устройство", show_alert=True)

    inbounds, _ = await api_get_inbounds()
    inbound = next((ib for ib in inbounds if ib.get("id") == ib_id), None)
    if not inbound:
        return await call.answer("Сервер недоступен", show_alert=True)

    client = await api_get_client(email)
    if not client:
        return await call.answer("Устройство не найдено", show_alert=True)
    # Берём актуальный uuid из API (кэш может быть устаревшим)
    uuid_val = client.get("id") or uuid_val
    sub_id = client.get("subId", "") or ""

    if action == "link":
        link = await fetch_subscription_link(sub_id)
        if not link:
            return await call.answer("Ссылка подписки не найдена", show_alert=True)
        await call.answer()
        await call.message.answer(
            f"🔗 <b>Ссылка на подписку ({email}):</b>\n\n<code>{link}</code>\n\n<i>Нажмите чтобы скопировать</i>",
            parse_mode=ParseMode.HTML
        )

    elif action == "inst":
        link = await fetch_subscription_link(sub_id)
        if not link:
            return await call.answer("Ссылка подписки не найдена", show_alert=True)
        text = build_instruction_text(link, device_name=email)
        await call.answer("⏳")
        await call.message.answer(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    elif action == "tog":
        if admin_disabled:
            return await call.answer("VPN заблокирован админом", show_alert=True)
        client["enable"] = not client.get("enable", True)
        result = await xui_post(
            f"/panel/api/clients/update/{email_path(email)}",
            data=build_update_payload(client)
        )
        if result.get("success"):
            await call.answer("✅ Готово")
            await show_myvpn_device(call, dev_hash)
        else:
            await call.answer(f"❌ Ошибка: {result.get('msg', '?')}", show_alert=True)

    elif action == "del":
        result = await api_del_client_by_email(email)
        if result.get("success"):
            remove_device_from_user(user_id, ib_id, email)
            await call.answer(f"🗑 Устройство {email} удалено", show_alert=True)
            await show_myvpn(call.message, user_id, edit=True, real_username=call.from_user.username or "")
        else:
            await call.answer(f"❌ Ошибка: {result.get('msg', '?')}", show_alert=True)


@router.message(MyVpnAddDevice.waiting_name, F.text)
async def myvpn_input_device_name(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    binding = get_vpn_user(user_id)
    if not binding:
        await state.clear()
        return await message.answer("❌ У вас нет привязки")

    name = message.text.strip()
    if not name or name == "/cancel":
        await state.clear()
        return await message.answer("❌ Отменено")

    if len(binding.get("devices", [])) >= binding.get("max_devices", DEFAULT_MAX_DEVICES):
        await state.clear()
        return await message.answer("❌ Достигнут лимит устройств")

    # Проверяем что email уникальный среди всех клиентов
    inbounds, _ = await api_get_inbounds()
    existing_emails = set()
    for ib in inbounds:
        for c in parse_clients(ib):
            existing_emails.add(c.get("email"))
    if name in existing_emails:
        return await message.answer(f"⚠️ Имя <code>{name}</code> уже занято. Введите другое.", parse_mode=ParseMode.HTML)

    # Определяем ib_id из существующих устройств юзера (если есть)
    devices = binding.get("devices", [])
    if devices:
        target_ib_id = devices[0].get("ib_id")
    else:
        inbound = next((ib for ib in inbounds if ib.get("id")), None)
        if not inbound:
            await state.clear()
            return await message.answer("❌ Нет доступных инбаундов")
        target_ib_id = inbound.get("id")

    await state.clear()
    wait = await message.answer("⏳ Создаю устройство...")

    # Создаём с дефолтами
    result, client_uuid = await api_add_client(
        target_ib_id, name, expiry_days=0, limit_gb=0, flow="xtls-rprx-vision"
    )

    if not result.get("success"):
        await wait.delete()
        return await message.answer(f"❌ Ошибка: {result.get('msg', '?')}")

    # Сохраняем устройство юзеру
    add_device_to_user(user_id, target_ib_id, client_uuid, name)

    # Также обновим tgId в клиенте
    try:
        inbounds, _ = await api_get_inbounds()
        ib = next((i for i in inbounds if i.get("id") == target_ib_id), None)
        if ib:
            cl = await api_get_client(name)
            if cl:
                cl["tgId"] = user_id
                await xui_post(
                    f"/panel/api/clients/update/{email_path(name)}",
                    data=build_update_payload(cl)
                )
    except Exception:
        pass

    await wait.delete()
    await message.answer(f"✅ <b>Устройство «{name}» создано!</b>", parse_mode=ParseMode.HTML)
    await show_myvpn(message, user_id, edit=False, real_username=message.from_user.username or "")
