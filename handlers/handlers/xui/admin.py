# handlers/xui/admin.py

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode

from handlers.utils import no_access_reply, no_access_callback
from handlers.xui.api.client import xui_get, xui_post
from handlers.xui.api.inbounds import api_get_inbounds
from handlers.xui.api.clients import (
    api_add_client, api_get_client, api_del_client_by_email,
    api_update_client, api_reset_client_traffic, build_update_payload, email_path,
)
from handlers.xui.api.helpers import parse_clients, get_client_stats_map
from handlers.xui.storage import (
    load_vpn_users, save_vpn_users, get_vpn_user, get_tg_id_by_client,
    add_device_to_user, remove_device_from_user, unbind_vpn_user,
    convert_to_anon, bind_tg_to_anon, create_user, delete_user_completely,
    set_admin_disabled, get_client_note, set_client_note, remove_client_note,
    refresh_username, DEFAULT_MAX_DEVICES, NOTE_MAX_LEN,
)
from handlers.xui.links import build_vless_link, build_instruction_text
from handlers.xui.keyboards import (
    inbounds_kb, clients_kb, client_actions_kb, flow_choice_kb,
)
from handlers.xui.states import (
    XuiAddClient, XuiBindTg, XuiAddUser, XuiNoteEdit, XuiAdminAddDevice,
)
from handlers.xui.utils import is_admin, cache, _cache, format_bytes
from handlers.xui.views import _show_user_menu, _refresh_client_view
from handlers.xui.keyboards import xui_settings_kb
from handlers.xui.states import XuiSettings
from handlers.xui.settings_store import load_xui_settings, save_xui_settings

router = Router()
EXIT_HINT = "\n\n<i>Для выхода введите /cancel</i>"

@router.message(Command("xui"))
async def cmd_xui(message: types.Message):
    if not is_admin(message.from_user.id):
        await no_access_reply(message)
        return

    xui_cfg = load_xui_settings()
    if not xui_cfg.get("XUI_URL") or not xui_cfg.get("XUI_TOKEN"):
        await message.answer(
            "⚠️ <b>XUI не настроен</b>\n\n"
            "Откройте настройки и введите URL панели и токен.\n\n"
            "Для установки панели воспользуйтесь командой:\n"
            "<code>bash &lt;(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh)</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=xui_settings_kb(False),
        )
        return

    wait = await message.answer("⏳ Подключаюсь к панели...")
    inbounds, err = await api_get_inbounds()

    if not inbounds:
        await wait.edit_text(f"❌ Не удалось подключиться к 3X-UI.\n<code>{err}</code>", parse_mode=ParseMode.HTML)
        return

    total_up = sum(s.get("up", 0) for ib in inbounds for s in ib.get("clientStats", []))
    total_down = sum(s.get("down", 0) for ib in inbounds for s in ib.get("clientStats", []))
    total_clients = sum(len(parse_clients(ib)) for ib in inbounds)

    text = (
        f"🖥 <b>3X-UI Панель</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"📡 Инбаундов: <b>{len(inbounds)}</b>\n"
        f"👥 Клиентов всего: <b>{total_clients}</b>\n"
        f"📤 Отправлено: <b>{format_bytes(total_up)}</b>\n"
        f"📥 Получено: <b>{format_bytes(total_down)}</b>\n\n"
        f"Выберите инбаунд:"
    )
    await wait.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=inbounds_kb(inbounds))


@router.callback_query(F.data.startswith("xui_"))
async def cb_xui(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await no_access_callback(call)
        return

    data = call.data

    if data in ("xui_menu", "xui_refresh"):
        xui_cfg = load_xui_settings()
        if not xui_cfg.get("XUI_URL") or not xui_cfg.get("XUI_TOKEN"):
            await call.message.edit_text(
                "⚠️ <b>XUI не настроен</b>\n\n"
                "Откройте настройки и введите URL панели и токен.\n\n"
                "Для установки панели воспользуйтесь командой:\n"
                "<code>bash &lt;(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh)</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=xui_settings_kb(False),
            )
            return await call.answer()

        await call.answer("⏳ Загружаю...")
        inbounds, err = await api_get_inbounds()
        if not inbounds:
            return await call.answer(f"❌ {err}", show_alert=True)

        total_up = sum(s.get("up", 0) for ib in inbounds for s in ib.get("clientStats", []))
        total_down = sum(s.get("down", 0) for ib in inbounds for s in ib.get("clientStats", []))
        total_clients = sum(len(parse_clients(ib)) for ib in inbounds)

        text = (
            f"🖥 <b>3X-UI Панель</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"📡 Инбаундов: <b>{len(inbounds)}</b>\n"
            f"👥 Клиентов всего: <b>{total_clients}</b>\n"
            f"📤 Отправлено: <b>{format_bytes(total_up)}</b>\n"
            f"📥 Получено: <b>{format_bytes(total_down)}</b>\n\n"
            f"Выберите инбаунд:"
        )
        await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=inbounds_kb(inbounds))

    elif data == "xui_settings":
        xui_cfg = load_xui_settings()
        configured = bool(xui_cfg.get("XUI_URL") and xui_cfg.get("XUI_TOKEN"))
        await state.set_state(XuiSettings.waiting_url)
        await call.message.edit_text(
            "⚙️ <b>Настройка XUI</b>\n\n"
            "Отправьте URL панели XUI.\n"
            "Потом я попрошу токен.",
            parse_mode=ParseMode.HTML,
            reply_markup=xui_settings_kb(configured),
        )
        await call.answer()

    elif data.startswith("xui_inst_"):
        # Инструкция для клиента
        cl_h = data[len("xui_inst_"):]
        info = _cache.get(cl_h, {})
        email = info.get("email", "?")
        uuid_val = info.get("uuid", "")
        ib_id = info.get("ib_id")
        if not uuid_val:
            client = await api_get_client(email)
            if client:
                uuid_val = client.get("id", "")
                info["uuid"] = uuid_val
                _cache[cl_h] = info
        inbounds, _ = await api_get_inbounds()
        inbound = next((ib for ib in inbounds if ib.get("id") == ib_id), None)
        if not inbound:
            return await call.answer("Инбаунд не найден", show_alert=True)
        client_flow = ""
        cl_api = await api_get_client(email)
        if cl_api:
            client_flow = cl_api.get("flow", "")
        link = build_vless_link(inbound, uuid_val, email, client_flow)
        if not link:
            return await call.answer("Не VLESS инбаунд", show_alert=True)
        text = build_instruction_text(link, device_name=email)
        await call.answer("⏳")
        await call.message.answer(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    elif data.startswith("xui_del_"):
        # Удаление клиента
        cl_h = data[len("xui_del_"):]
        info = _cache.get(cl_h, {})
        email = info.get("email", "?")
        uuid_val = info.get("uuid", "")
        ib_id = info.get("ib_id")

        if not all([email, uuid_val, ib_id]):
            return await call.answer("Ошибка данных", show_alert=True)

        # Подтверждение
        if not data.endswith("_confirm"):
            await call.message.edit_text(
                f"🗑 <b>Удалить клиента {email}?</b>\n\n"
                f"⚠️ Это действие нельзя отменить.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"xui_delok_{cl_h}")],
                    [InlineKeyboardButton(text="❌ Отмена", callback_data=f"xui_cl_{cl_h}")],
                ])
            )
            await call.answer()
            return

    elif data.startswith("xui_delok_"):
        # Подтвержденное удаление
        cl_h = data[len("xui_delok_"):]
        info = _cache.get(cl_h, {})
        email = info.get("email", "?")
        uuid_val = info.get("uuid", "")
        ib_id = info.get("ib_id")

        # Найдём привязанного TG юзера ДО удаления
        tg_id = get_tg_id_by_client(ib_id, email)

        # v3: detach клиента от этого инбаунда (не удаляет клиента глобально).
        # Если у клиента только один инбаунд — удаляем полностью.
        client_info = await api_get_client(email)
        attached_ids = []
        if client_info:
            # inboundIds приходит в /clients/get
            full_result = await xui_get(f"/panel/api/clients/get/{email_path(email)}")
            if full_result.get("success"):
                attached_ids = full_result.get("obj", {}).get("inboundIds", [])

        if len(attached_ids) <= 1:
            result = await api_del_client_by_email(email)
        else:
            result = await xui_post(
                f"/panel/api/clients/{email_path(email)}/detach",
                data={"inboundIds": [ib_id]}
            )
        if result.get("success"):
            # Удаляем только это устройство из базы бота (не всего пользователя)
            if tg_id:
                remove_device_from_user(tg_id, ib_id, email)
                try:
                    from loader import bot as _bot
                    await _bot.send_message(
                        tg_id,
                        f"🗑 <b>Устройство «{email}» удалено администратором.</b>\n\n"
                        f"Если это ошибка — свяжитесь с админом.",
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    print(f"[XUI NOTIFY DEL ERR] {e}")
            else:
                # single-клиент без TG — просто убираем из vpn_users если вдруг есть
                data_users = load_vpn_users()
                for uk, uinfo in data_users.items():
                    uinfo["devices"] = [
                        d for d in uinfo.get("devices", [])
                        if not (d.get("ib_id") == ib_id and d.get("email") == email)
                    ]
                save_vpn_users(data_users)

            ib_h = cache(f"ib_{ib_id}", {"id": ib_id})
            await call.answer(f"🗑 Устройство {email} удалено", show_alert=True)
            await call.message.edit_text(
                f"✅ <b>Устройство «{email}» удалено из панели.</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ К списку клиентов", callback_data=f"xui_ib_{ib_h}")],
                ])
            )
        else:
            await call.answer(f"❌ Ошибка: {result.get('msg', '?')}", show_alert=True)

    elif data.startswith("xui_bind_"):
        # Привязать TG ID к клиенту
        cl_h = data[len("xui_bind_"):]
        info = _cache.get(cl_h, {})
        if not info:
            return await call.answer("Ошибка данных", show_alert=True)
        await state.update_data(bind_cl_h=cl_h)
        await state.set_state(XuiBindTg.waiting_tg_id)
        await call.message.answer(
            f"📱 <b>Введите Telegram ID</b> для привязки к клиенту <b>{info.get('email')}</b>:\n"
            f"<i>Пример: 5185332965</i>",
            parse_mode=ParseMode.HTML
        )
        await call.answer()

    elif data.startswith("xui_unbind_"):
        # Отвязать TG ID
        cl_h = data[len("xui_unbind_"):]
        info = _cache.get(cl_h, {})
        ib_id = info.get("ib_id")
        uuid_val = info.get("uuid")
        email = info.get("email")
        tg_id = get_tg_id_by_client(ib_id, email)
        if tg_id:
            unbind_vpn_user(tg_id)

            # Чистим tgId в самом 3xUI клиенте
            try:
                inbounds, _ = await api_get_inbounds()
                client = await api_get_client(email)
                if client:
                    client["tgId"] = 0
                    await xui_post(
                        f"/panel/api/clients/update/{email_path(email)}",
                        data=build_update_payload(client)
                    )
            except Exception as e:
                print(f"[XUI UNBIND ERR] {e}")

            # Уведомление пользователю
            try:
                from loader import bot as _bot
                await _bot.send_message(
                    tg_id,
                    "🔓 <b>Ваш VPN был отвязан от аккаунта администратором.</b>\n\n"
                    "Команда /myvpn больше недоступна. Если это ошибка — свяжитесь с админом.",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                print(f"[XUI UNBIND NOTIFY ERR] {e}")

            await call.answer(f"📱 TG {tg_id} отвязан", show_alert=True)
        else:
            await call.answer("Привязка не найдена", show_alert=True)

    elif data.startswith("xui_usr_"):
        # Открыть меню юзера (TG или анонимного) - data: xui_usr_{uk_hash}
        uk_h = data[len("xui_usr_"):]
        info = _cache.get(uk_h, {})
        user_key = info.get("user_key")
        ib_id = info.get("ib_id", 0)
        if not user_key:
            return await call.answer("Ошибка данных", show_alert=True)
        await call.answer("⏳")
        await _show_user_menu(call.message, user_key, ib_id, edit=True)

    elif data.startswith("xui_unote_"):
        # Изменить заметку юзера
        uk_h = data[len("xui_unote_"):]
        info = _cache.get(uk_h, {})
        user_key = info.get("user_key")
        if not user_key:
            return await call.answer("Ошибка данных", show_alert=True)
        await state.update_data(note_target_type="user", note_user_key=user_key, note_ib_id=info.get("ib_id", 0))
        await state.set_state(XuiNoteEdit.waiting_note)
        label = "пользователя без TG" if user_key.startswith("anon_") else f"TG {user_key}"
        await call.message.answer(
            f"📝 <b>Введите заметку</b> (до {NOTE_MAX_LEN} символов) для {label}:\n"
            f"<i>Отправьте «-» чтобы удалить заметку</i>",
            parse_mode=ParseMode.HTML
        )
        await call.answer()

    elif data.startswith("xui_clnote_"):
        # Заметка одиночного клиента
        cl_h = data[len("xui_clnote_"):]
        info = _cache.get(cl_h, {})
        if not info:
            return await call.answer("Ошибка данных", show_alert=True)
        await state.update_data(note_target_type="client", note_cl_h=cl_h)
        await state.set_state(XuiNoteEdit.waiting_note)
        await call.message.answer(
            f"📝 <b>Введите заметку</b> (до {NOTE_MAX_LEN} символов) для клиента {info.get('email')}:\n"
            f"<i>Отправьте «-» чтобы удалить заметку</i>",
            parse_mode=ParseMode.HTML
        )
        await call.answer()

    elif data.startswith("xui_ublk_") or data.startswith("xui_unblk_"):
        # Заблокировать / разблокировать все устройства юзера
        block = data.startswith("xui_ublk_")
        uk_h = data[len("xui_ublk_") if block else len("xui_unblk_"):]
        info_cache = _cache.get(uk_h, {})
        user_key = info_cache.get("user_key")
        ib_id_def = info_cache.get("ib_id", 0)
        if not user_key:
            return await call.answer("Ошибка данных", show_alert=True)

        data_users = load_vpn_users()
        info = data_users.get(user_key)
        if not info:
            return await call.answer("Пользователь не найден", show_alert=True)

        new_enable = not block
        inbounds, _ = await api_get_inbounds()
        for d in info.get("devices", []):
            ib = next((i for i in inbounds if i.get("id") == d.get("ib_id")), None)
            if not ib:
                continue
            d_email = d.get("email")
            client = await api_get_client(d_email)
            if not client:
                continue
            client["enable"] = new_enable
            await xui_post(
                f"/panel/api/clients/update/{email_path(d_email)}",
                data=build_update_payload(client)
            )

        # admin_disabled только для TG юзеров
        if not user_key.startswith("anon_"):
            try:
                set_admin_disabled(int(user_key), block)
            except ValueError:
                pass

            # Уведомление пользователю
            try:
                from loader import bot as _bot
                tg_id_int = int(user_key)
                if block:
                    await _bot.send_message(
                        tg_id_int,
                        "🚫 <b>Все ваши устройства VPN отключены администратором.</b>\n\nСвяжитесь с админом.",
                        parse_mode=ParseMode.HTML
                    )
                else:
                    await _bot.send_message(
                        tg_id_int,
                        "✅ <b>Все ваши устройства VPN снова активны!</b>",
                        parse_mode=ParseMode.HTML
                    )
            except Exception as e:
                print(f"[XUI BLOCK NOTIFY ERR] {e}")

        await call.answer("✅ Готово")
        await _show_user_menu(call.message, user_key, ib_id_def, edit=True)

    elif data.startswith("xui_uunbind_"):
        # Отвязать TG → конвертация в анонимного
        uk_h = data[len("xui_uunbind_"):]
        info_cache = _cache.get(uk_h, {})
        user_key = info_cache.get("user_key")
        ib_id_def = info_cache.get("ib_id", 0)
        if not user_key or user_key.startswith("anon_"):
            return await call.answer("Ошибка данных", show_alert=True)

        try:
            tg_id = int(user_key)
        except ValueError:
            return await call.answer("Ошибка данных", show_alert=True)

        info = get_vpn_user(tg_id)
        if not info:
            return await call.answer("Пользователь не найден", show_alert=True)

        # Чистим tgId в 3xUI клиентах
        inbounds, _ = await api_get_inbounds()
        for d in info.get("devices", []):
            ib = next((i for i in inbounds if i.get("id") == d.get("ib_id")), None)
            if not ib:
                continue
            d_email = d.get("email")
            client = await api_get_client(d_email)
            if client:
                client["tgId"] = 0
                await xui_post(
                    f"/panel/api/clients/update/{email_path(d_email)}",
                    data=build_update_payload(client)
                )

        # Конвертация в анонимного (devices остаются как группа!)
        anon_key = convert_to_anon(tg_id)

        # Уведомление
        try:
            from loader import bot as _bot
            await _bot.send_message(
                tg_id,
                "🔓 <b>Ваш VPN был отвязан от аккаунта администратором.</b>\n\n"
                "Команда /myvpn больше недоступна. Свяжитесь с админом если это ошибка.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            print(f"[XUI UUNBIND NOTIFY ERR] {e}")

        await call.answer(f"📱 TG {tg_id} отвязан, юзер стал анонимным", show_alert=True)
        await _show_user_menu(call.message, anon_key, ib_id_def, edit=True)

    elif data.startswith("xui_uadd_"):
        # Админ добавляет новое устройство юзеру
        uk_h = data[len("xui_uadd_"):]
        info_cache = _cache.get(uk_h, {})
        user_key = info_cache.get("user_key")
        ib_id_def = info_cache.get("ib_id", 0)
        if not user_key:
            return await call.answer("Ошибка данных", show_alert=True)

        info = load_vpn_users().get(user_key)
        if not info:
            return await call.answer("Пользователь не найден", show_alert=True)

        if len(info.get("devices", [])) >= info.get("max_devices", DEFAULT_MAX_DEVICES):
            return await call.answer("❌ Достигнут лимит устройств", show_alert=True)

        await state.update_data(admin_adddev_user_key=user_key, admin_adddev_ib_id=ib_id_def)
        await state.set_state(XuiAdminAddDevice.waiting_name)
        await call.message.answer(
            "📱 <b>Добавление устройства</b>\n\n"
            "Введите название устройства (например, <code>iPhone</code>, <code>PC</code>):\n"
            "<i>Дефолты: безлимит / безлимит / xtls-rprx-vision</i>\n"
            f"{EXIT_HINT}",
            parse_mode=ParseMode.HTML
        )
        await call.answer()

    elif data.startswith("xui_bindanon_"):
        # Запрос TG ID для анонимного юзера
        uk_h = data[len("xui_bindanon_"):]
        info_cache = _cache.get(uk_h, {})
        user_key = info_cache.get("user_key")
        if not user_key or not user_key.startswith("anon_"):
            return await call.answer("Ошибка данных", show_alert=True)
        await state.update_data(bind_anon_key=user_key, bind_anon_ib=info_cache.get("ib_id", 0))
        await state.set_state(XuiBindTg.waiting_tg_id)
        await call.message.answer(
            "📱 <b>Введите Telegram ID</b> для привязки к этому пользователю:",
            parse_mode=ParseMode.HTML
        )
        await call.answer()

    elif data.startswith("xui_udelall_"):
        # Удалить ВСЕ устройства юзера (но юзер останется пустым)
        uk_h = data[len("xui_udelall_"):]
        info_cache = _cache.get(uk_h, {})
        user_key = info_cache.get("user_key")
        ib_id_def = info_cache.get("ib_id", 0)
        if not user_key:
            return await call.answer("Ошибка данных", show_alert=True)

        await state.update_data(udelall_user_key=user_key, udelall_ib_id=ib_id_def)
        n = len(load_vpn_users().get(user_key, {}).get("devices", []))
        label = "пользователя без TG" if user_key.startswith("anon_") else f"TG {user_key}"
        await call.message.edit_text(
            f"🗑 <b>Удалить ВСЕ устройства ({n}) {label}?</b>\n\n"
            f"⚠️ Пользователь останется, но без устройств.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Да, удалить все", callback_data=f"xui_udelallok_{uk_h}")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data=f"xui_usr_{uk_h}")],
            ])
        )
        await call.answer()

    elif data.startswith("xui_udelallok_"):
        # Подтверждённое удаление всех устройств
        uk_h = data[len("xui_udelallok_"):]
        info_cache = _cache.get(uk_h, {})
        user_key = info_cache.get("user_key")
        ib_id_def = info_cache.get("ib_id", 0)
        if not user_key:
            return await call.answer("Ошибка данных", show_alert=True)

        data_users = load_vpn_users()
        info = data_users.get(user_key)
        if not info:
            return await call.answer("Пользователь не найден", show_alert=True)

        devices = list(info.get("devices", []))
        for d in devices:
            await api_del_client_by_email(d.get("email"))

        info["devices"] = []
        data_users[user_key] = info
        save_vpn_users(data_users)

        # Уведомление если TG
        if not user_key.startswith("anon_"):
            try:
                from loader import bot as _bot
                await _bot.send_message(
                    int(user_key),
                    "🗑 <b>Все ваши устройства VPN были удалены администратором.</b>\n\n"
                    "Вы можете создать новые через /myvpn.",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                print(f"[XUI UDELALL NOTIFY ERR] {e}")

        await call.answer(f"🗑 Удалено {len(devices)} устройств", show_alert=True)
        await _show_user_menu(call.message, user_key, ib_id_def, edit=True)

    elif data.startswith("xui_udel_"):
        # Подтверждение удаления юзера и всех его устройств
        uk_h = data[len("xui_udel_"):]
        info_cache = _cache.get(uk_h, {})
        user_key = info_cache.get("user_key")
        if not user_key:
            return await call.answer("Ошибка данных", show_alert=True)

        info = load_vpn_users().get(user_key)
        if not info:
            return await call.answer("Пользователь не найден", show_alert=True)

        n = len(info.get("devices", []))
        label = "пользователя без TG" if user_key.startswith("anon_") else f"TG {user_key}"
        await call.message.edit_text(
            f"🗑 <b>Удалить {label} и все его устройства ({n})?</b>\n\n"
            f"⚠️ Это действие нельзя отменить.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"xui_udelok_{uk_h}")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data=f"xui_usr_{uk_h}")],
            ])
        )
        await call.answer()

    elif data.startswith("xui_udelok_"):
        # Подтверждённое удаление юзера + всех клиентов
        uk_h = data[len("xui_udelok_"):]
        info_cache = _cache.get(uk_h, {})
        user_key = info_cache.get("user_key")
        ib_id_def = info_cache.get("ib_id", 0)
        if not user_key:
            return await call.answer("Ошибка данных", show_alert=True)

        data_users = load_vpn_users()
        info = data_users.get(user_key)
        if not info:
            return await call.answer("Пользователь не найден", show_alert=True)

        devices = info.get("devices", [])
        # Удаляем все клиенты в 3xUI
        for d in devices:
            await api_del_client_by_email(d.get("email"))

        # Уведомление если TG юзер
        if not user_key.startswith("anon_"):
            try:
                from loader import bot as _bot
                await _bot.send_message(
                    int(user_key),
                    "🗑 <b>Ваш VPN полностью удалён администратором.</b>\n\n"
                    "/myvpn больше недоступна.",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                print(f"[XUI UDEL NOTIFY ERR] {e}")

        # Удаляем юзера
        delete_user_completely(user_key)

        ib_h_back = cache(f"ib_{ib_id_def}", {"id": ib_id_def}) if ib_id_def else None
        back_btn = [InlineKeyboardButton(text="⬅️ К списку", callback_data=f"xui_ib_{ib_h_back}")] if ib_h_back else []
        label_done = "пользователь без TG" if user_key.startswith("anon_") else f"Юзер TG {user_key}"
        await call.message.edit_text(
            f"✅ <b>{label_done} полностью удалён.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_btn] if back_btn else [[]])
        )

    elif data.startswith("xui_flow_"):
        # Выбор flow при создании клиента
        flow_type = data.replace("xui_flow_", "")
        client_flow = "xtls-rprx-vision" if flow_type == "xtls" else ""

        state_data = await state.get_data()
        ib_id = state_data.get("xui_ib_id")
        email = state_data.get("xui_new_email")
        days = state_data.get("xui_expiry_days", 0)
        gb = state_data.get("xui_limit_gb", 0)

        if not ib_id or not email:
            await call.answer("Сессия истекла, начните заново", show_alert=True)
            await state.clear()
            return

        await state.clear()
        await call.message.edit_text("⏳ Создаю клиента...")
        result, client_uuid = await api_add_client(ib_id, email, days, gb, client_flow)

        if not result.get("success"):
            return await call.message.edit_text(f"❌ Ошибка: {result.get('msg', '?')}")

        inbounds, _ = await api_get_inbounds()
        inbound = next((ib for ib in inbounds if ib.get("id") == ib_id), None)

        text = f"✅ <b>Клиент {email} создан!</b>\n\n"
        if inbound and inbound.get("protocol", "").lower() == "vless":
            link = build_vless_link(inbound, client_uuid, email, client_flow)
            if link:
                text += f"🔗 <b>VLESS ссылка:</b>\n<code>{link}</code>"
            else:
                text += f"UUID: <code>{client_uuid}</code>"
        else:
            text += f"UUID: <code>{client_uuid}</code>"

        ib_h = cache(f"ib_{ib_id}", {"id": ib_id})
        await call.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ К списку клиентов", callback_data=f"xui_ib_{ib_h}")],
            ])
        )
        await call.answer()

    elif data.startswith("xui_adduser_"):
        # Добавить пользователя (опционально с TG ID, потом лимит устройств)
        ib_h = data[len("xui_adduser_"):]
        info = _cache.get(ib_h, {})
        ib_id = info.get("id")
        if not ib_id:
            return await call.answer("Ошибка данных", show_alert=True)

        await state.update_data(adduser_ib_id=ib_id)
        await state.set_state(XuiAddUser.waiting_tg_id)
        await call.message.edit_text(
            "➕ <b>Новый пользователь</b>\n\n"
            "Введите его <b>Telegram ID</b> (только цифры).\n"
            "Если ID неизвестен — отправьте <code>-</code> чтобы создать пользователя без TG.\n\n"
            f"{EXIT_HINT}",
            parse_mode=ParseMode.HTML
        )
        await call.answer()


@router.message(XuiSettings.waiting_url, F.text)
async def xui_settings_input_url(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await no_access_reply(message)

    xui_url = message.text.strip()
    if not xui_url:
        return await message.answer("Отправьте URL панели XUI ещё раз.")

    await state.update_data(xui_url=xui_url)
    await state.set_state(XuiSettings.waiting_token)
    await message.answer("Теперь отправьте XUI_TOKEN.")


@router.message(XuiSettings.waiting_token, F.text)
async def xui_settings_input_token(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await no_access_reply(message)

    data = await state.get_data()
    xui_url = data.get("xui_url", "")
    xui_token = message.text.strip()
    if not xui_token:
        return await message.answer("Отправьте XUI_TOKEN ещё раз.")

    save_xui_settings(xui_url, xui_token)
    await state.clear()
    await message.answer(
        "✅ <b>XUI сохранён</b>\n\n"
        "Можно снова открыть /xui.",
        parse_mode=ParseMode.HTML,
    )


# ====================== FSM СОЗДАНИЕ КЛИЕНТА ======================

@router.message(XuiAddClient.email, F.text)
async def xui_input_email(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()
    email = message.text.strip()
    if not email:
        return await message.answer("Имя не может быть пустым. Введите снова:")
    await state.update_data(xui_new_email=email)
    await state.set_state(XuiAddClient.expiry)
    await message.answer("⏳ Срок действия в днях (0 = без ограничений):")


@router.message(XuiAddClient.expiry, F.text)
async def xui_input_expiry(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()
    try:
        days = int(message.text.strip())
        if days < 0:
            raise ValueError
    except ValueError:
        return await message.answer("Введите целое число ≥ 0, например: 30 или 0")
    await state.update_data(xui_expiry_days=days)
    await state.set_state(XuiAddClient.limit_gb)
    await message.answer("💾 Лимит трафика в ГБ (0 = без ограничений):")


@router.message(XuiAddClient.limit_gb, F.text)
async def xui_input_limit(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()
    try:
        gb = float(message.text.strip().replace(",", "."))
        if gb < 0:
            raise ValueError
    except ValueError:
        return await message.answer("Введите число ≥ 0, например: 50 или 0")

    await state.update_data(xui_limit_gb=gb)
    await state.set_state(XuiAddClient.flow)
    await message.answer(
        "⚡ <b>Выберите flow</b>\n\n"
        "<i>Для Reality рекомендуется xtls-rprx-vision</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=flow_choice_kb()
    )


# ====================== ПРИВЯЗКА TG ID ======================

@router.message(XuiBindTg.waiting_tg_id, F.text)
async def xui_input_tg_id(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()
    text = message.text.strip()
    if not text.isdigit():
        return await message.answer("⚠️ Введите числовой Telegram ID (только цифры)")

    tg_id = int(text)
    state_data = await state.get_data()
    await state.clear()

    # Проверяем тип привязки: к одиночному клиенту или к анонимному юзеру
    anon_key = state_data.get("bind_anon_key")
    if anon_key:
        # Привязка анонимного → TG юзер
        if not bind_tg_to_anon(anon_key, tg_id):
            return await message.answer(
                f"❌ Не удалось привязать. Возможно TG {tg_id} уже привязан к другому юзеру."
            )

        # Обновляем tgId во всех клиентах анонимного
        info = get_vpn_user(tg_id)
        inbounds, _ = await api_get_inbounds()
        for d in info.get("devices", []):
            ib = next((i for i in inbounds if i.get("id") == d.get("ib_id")), None)
            if not ib:
                continue
            d_email = d.get("email")
            client = await api_get_client(d_email)
            if client:
                client["tgId"] = tg_id
                await xui_post(
                    f"/panel/api/clients/update/{email_path(d_email)}",
                    data=build_update_payload(client)
                )

        await message.answer(
            f"✅ <b>Telegram ID <code>{tg_id}</code> привязан.</b>",
            parse_mode=ParseMode.HTML
        )

        # Уведомление
        try:
            from loader import bot as _bot
            await _bot.send_message(
                tg_id,
                f"🎉 <b>Вам выдан VPN!</b>\n\n"
                f"📱 Используйте /myvpn для управления.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            await message.answer(f"⚠️ Не удалось отправить уведомление: {e}")
        return

    # Старый способ — привязка одиночного клиента
    cl_h = state_data.get("bind_cl_h")
    info_cl = _cache.get(cl_h, {})
    ib_id = info_cl.get("ib_id")
    uuid_val = info_cl.get("uuid")
    email = info_cl.get("email")

    if not all([ib_id, uuid_val, email]):
        return await message.answer("❌ Данные устарели, попробуйте снова")

    add_device_to_user(tg_id, ib_id, uuid_val, email)
    remove_client_note(ib_id, email)

    inbounds, _ = await api_get_inbounds()
    client = await api_get_client(email)
    if client:
        client["tgId"] = tg_id
        await xui_post(
            f"/panel/api/clients/update/{email_path(email)}",
            data=build_update_payload(client)
        )

    await message.answer(
        f"✅ <b>Telegram ID <code>{tg_id}</code> привязан к клиенту {email}</b>",
        parse_mode=ParseMode.HTML
    )

    try:
        from loader import bot as _bot
        await _bot.send_message(
            tg_id,
            f"🎉 <b>Вам выдан VPN!</b>\n\n"
            f"📱 Используйте /myvpn для управления.",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.answer(f"⚠️ Не удалось отправить уведомление: {e}")


# ====================== ДОБАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯ (с TG или без) ======================

@router.message(XuiAddUser.waiting_tg_id, F.text)
async def adduser_input_tg(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()
    text = message.text.strip()

    if text == "-":
        tg_id_val = None
    elif text.isdigit():
        tg_id_val = int(text)
        if get_vpn_user(tg_id_val):
            return await message.answer("⚠️ Такой TG уже есть. Введите другой ID или <code>-</code>", parse_mode=ParseMode.HTML)
    else:
        return await message.answer("⚠️ Введите числовой Telegram ID или <code>-</code> чтобы пропустить", parse_mode=ParseMode.HTML)

    await state.update_data(adduser_tg_id=tg_id_val)
    await state.set_state(XuiAddUser.waiting_limit)
    await message.answer(
        f"📱 <b>Лимит устройств</b>\n\n"
        f"Введите максимальное количество устройств для этого пользователя (по умолчанию <b>{DEFAULT_MAX_DEVICES}</b>).\n"
        f"Отправьте <code>-</code> чтобы оставить по умолчанию.",
        parse_mode=ParseMode.HTML
    )


@router.message(XuiAddUser.waiting_limit, F.text)
async def adduser_input_limit(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()
    text = message.text.strip()

    if text == "-":
        max_devs = DEFAULT_MAX_DEVICES
    else:
        try:
            max_devs = int(text)
            if max_devs < 1:
                raise ValueError
        except ValueError:
            return await message.answer("⚠️ Введите целое число ≥ 1 или <code>-</code>", parse_mode=ParseMode.HTML)

    state_data = await state.get_data()
    tg_id_val = state_data.get("adduser_tg_id")
    ib_id = state_data.get("adduser_ib_id")
    await state.clear()

    user_key = create_user(tg_id_val, max_devs)
    if not user_key.startswith("anon_"):
        await refresh_username(tg_id_val)
        # Уведомление
        try:
            from loader import bot as _bot
            await _bot.send_message(
                tg_id_val,
                f"🎉 <b>Вам выдан VPN!</b>\n\n"
                f"📱 Лимит устройств: <b>{max_devs}</b>\n"
                f"Добавляйте устройства через /myvpn.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            await message.answer(f"⚠️ Не удалось отправить уведомление: {e}")

    label = f"TG {tg_id_val}" if tg_id_val else "Без TG"
    await message.answer(
        f"✅ <b>Пользователь создан!</b>\n\n"
        f"👤 {label}\n"
        f"📱 Лимит устройств: <b>{max_devs}</b>\n\n"
        f"<i>Устройств пока нет. Пользователь добавит их сам через /myvpn, либо вы можете создать их через 3X-UI панель.</i>",
        parse_mode=ParseMode.HTML
    )
    await _show_user_menu(message, user_key, ib_id, edit=False)


# ====================== АДМИН ДОБАВЛЯЕТ УСТРОЙСТВО ЮЗЕРУ ======================

@router.message(XuiAdminAddDevice.waiting_name, F.text)
async def admin_adddev_input_name(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()

    name = message.text.strip()
    if not name or name == "/cancel":
        await state.clear()
        return await message.answer("❌ Отменено")

    state_data = await state.get_data()
    user_key = state_data.get("admin_adddev_user_key")
    ib_id_def = state_data.get("admin_adddev_ib_id", 0)
    await state.clear()

    info = load_vpn_users().get(user_key)
    if not info:
        return await message.answer("❌ Пользователь не найден")

    if len(info.get("devices", [])) >= info.get("max_devices", DEFAULT_MAX_DEVICES):
        return await message.answer("❌ Достигнут лимит устройств")

    # Определяем ib_id для нового устройства
    devices = info.get("devices", [])
    if devices:
        target_ib_id = devices[0].get("ib_id")
    elif ib_id_def:
        target_ib_id = ib_id_def
    else:
        inbounds, _ = await api_get_inbounds()
        vless_ib = next((ib for ib in inbounds if ib.get("protocol", "").lower() == "vless"), None)
        if not vless_ib:
            return await message.answer("❌ Нет доступных VLESS инбаундов")
        target_ib_id = vless_ib.get("id")

    # Проверяем уникальность email
    inbounds, _ = await api_get_inbounds()
    existing = set()
    for ib in inbounds:
        for c in parse_clients(ib):
            existing.add(c.get("email"))
    if name in existing:
        return await message.answer(f"⚠️ Имя <code>{name}</code> уже занято. Введите другое.", parse_mode=ParseMode.HTML)

    wait = await message.answer("⏳ Создаю устройство...")
    result, client_uuid = await api_add_client(
        target_ib_id, name, expiry_days=0, limit_gb=0, flow="xtls-rprx-vision"
    )
    if not result.get("success"):
        await wait.delete()
        return await message.answer(f"❌ Ошибка: {result.get('msg', '?')}")

    # Сохраняем устройство юзеру
    data_users = load_vpn_users()
    data_users[user_key]["devices"].append({
        "ib_id": target_ib_id, "uuid": client_uuid, "email": name
    })
    save_vpn_users(data_users)

    # Обновим tgId в 3xUI клиенте (если у юзера есть TG)
    if not user_key.startswith("anon_"):
        try:
            inbounds, _ = await api_get_inbounds()
            ib = next((i for i in inbounds if i.get("id") == target_ib_id), None)
            if ib:
                cl = await api_get_client(name)
                if cl:
                    cl["tgId"] = int(user_key)
                    await xui_post(
                        f"/panel/api/clients/update/{email_path(name)}",
                        data=build_update_payload(cl)
                    )
        except Exception:
            pass

        # Уведомление пользователю
        try:
            from loader import bot as _bot
            await _bot.send_message(
                int(user_key),
                f"📱 <b>Вам добавлено новое устройство: «{name}»</b>\n\n"
                f"Используйте /myvpn чтобы получить ссылку и инструкцию.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            print(f"[XUI ADMIN ADD DEV NOTIFY ERR] {e}")

    await wait.delete()
    await message.answer(
        f"✅ <b>Устройство «{name}» создано и привязано к пользователю!</b>",
        parse_mode=ParseMode.HTML
    )
    await _show_user_menu(message, user_key, target_ib_id, edit=False)


# ====================== РЕДАКТИРОВАНИЕ ЗАМЕТОК ======================

@router.message(XuiNoteEdit.waiting_note, F.text)
async def xui_input_note(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()

    raw = message.text.strip()
    note = "" if raw == "-" else raw[:NOTE_MAX_LEN]
    state_data = await state.get_data()
    await state.clear()

    target_type = state_data.get("note_target_type")
    if target_type == "user":
        user_key = state_data.get("note_user_key")
        ib_id = state_data.get("note_ib_id", 0)
        if not user_key:
            return await message.answer("❌ Данные устарели")
        data_users = load_vpn_users()
        if user_key in data_users:
            data_users[user_key]["note"] = note
            save_vpn_users(data_users)
        await message.answer(f"✅ Заметка обновлена: <i>{__import__('html').escape(note) or '(удалена)'}</i>", parse_mode=ParseMode.HTML)
        await _show_user_menu(message, user_key, ib_id, edit=False)
    elif target_type == "client":
        cl_h = state_data.get("note_cl_h")
        info_cl = _cache.get(cl_h, {})
        ib_id = info_cl.get("ib_id")
        email = info_cl.get("email")
        if ib_id and email:
            set_client_note(ib_id, email, note)
        await message.answer(f"✅ Заметка обновлена: <i>{__import__('html').escape(note) or '(удалена)'}</i>", parse_mode=ParseMode.HTML)
