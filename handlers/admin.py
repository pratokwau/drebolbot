from __future__ import annotations

import asyncio
import html
import subprocess

from aiogram import F, Router, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from updater import apply_update, request_restart, update_available
from storage import load_authorized_users, load_xui_settings, save_xui_settings, save_update_state
from loader import bot
from sub.adminpaysub.paid_storage import load_paid_subscriptions
from sub.adminsub.storage import load_vpn_users
from handlers.migration import MigrationTarget, migrate_bot_to_server
from sub.keyboards import settings_kb
from sub.utils import is_admin


router = Router()


class XuiSettings(StatesGroup):
    url = State()
    token = State()
    sub_port = State()


class Broadcast(StatesGroup):
    waiting_text = State()
    confirm = State()


class Migration(StatesGroup):
    host = State()
    port = State()
    username = State()
    password = State()
    target_dir = State()
    confirm = State()
    stop_old = State()


def _admin_kb() -> types.InlineKeyboardMarkup:
    configured = update_available()
    rows = [
        [types.InlineKeyboardButton(text="🔄 Проверить обновление", callback_data="app_update_check")],
        [types.InlineKeyboardButton(text="⚙️ Настроить XUI", callback_data="admin_xui_settings")],
        [types.InlineKeyboardButton(text="🚚 Миграция на новый сервер", callback_data="admin_migration")],
        [types.InlineKeyboardButton(text="🎫 Тикеты", callback_data="admin_tickets")],
        [types.InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
    ]
    if configured:
        rows[0] = [types.InlineKeyboardButton(text="⬆️ Обновить бота", callback_data="app_update_apply")]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


async def render_admin_menu(message_or_call, user_id: int, *, edit: bool = False) -> None:
    status = "есть обновление" if update_available() else "обновлений нет"
    text = (
        f"⚙️ <b>Админ-панель</b>\n\n"
        f"Статус обновления: <b>{status}</b>\n"
        f"Пользователь: <code>{user_id}</code>"
    )
    markup = _admin_kb()
    if edit:
        await message_or_call.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
        return
    await message_or_call.answer(text, parse_mode=ParseMode.HTML, reply_markup=markup)


@router.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return
    await render_admin_menu(message, message.from_user.id)


@router.callback_query(F.data == "admin_xui_settings")
async def cb_admin_xui_settings(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    data = load_xui_settings()
    url = data.get("XUI_URL") or "не задан"
    token = data.get("XUI_TOKEN") or "не задан"
    sub_port = data.get("XUI_SUB_PORT") or "не задан"
    await call.message.edit_text(
        "⚙️ <b>Настройки XUI</b>\n\n"
        f"URL: <code>{url}</code>\n"
        f"Токен: <code>{token}</code>\n\n"
        f"Порт подписки: <code>{sub_port}</code>\n\n"
        "Нажми кнопку ниже, чтобы изменить значение.",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_kb(),
    )
    await call.answer()


def _migration_summary_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Начать перенос", callback_data="admin_migration_run")],
            [InlineKeyboardButton(text="↩️ Отмена", callback_data="admin_migration_cancel")],
        ]
    )


@router.callback_query(F.data == "admin_migration")
async def cb_admin_migration(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await state.clear()
    await state.set_state(Migration.host)
    await call.message.edit_text(
        "🚚 <b>Миграция на новый сервер</b>\n\n"
        "Я последовательно спрошу данные для подключения к новому серверу и перенесу проект, данные и автозапуск.\n\n"
        "Отправь <b>IP или домен</b> нового сервера.\n\n"
        "Для выхода введите /cancel",
        parse_mode=ParseMode.HTML,
    )
    await call.answer()


@router.message(Migration.host)
async def migration_host_input(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    host = (message.text or "").strip()
    if not host:
        await message.answer("Хост не может быть пустым.\n\nДля выхода введите /cancel")
        return
    await state.update_data(migration_host=host)
    await state.set_state(Migration.port)
    await message.answer(
        "Отправь <b>SSH-порт</b> нового сервера.\n\n"
        "Обычно это <code>22</code>.\n\n"
        "Для выхода введите /cancel",
        parse_mode=ParseMode.HTML,
    )


@router.message(Migration.port)
async def migration_port_input(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Нужен числовой SSH-порт.\n\nДля выхода введите /cancel")
        return
    await state.update_data(migration_port=int(raw))
    await state.set_state(Migration.username)
    await message.answer(
        "Отправь <b>SSH-логин</b> нового сервера.\n\n"
        "Обычно это <code>root</code>.\n\n"
        "Для выхода введите /cancel",
        parse_mode=ParseMode.HTML,
    )


@router.message(Migration.username)
async def migration_username_input(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    username = (message.text or "").strip()
    if not username:
        await message.answer("Логин не может быть пустым.\n\nДля выхода введите /cancel")
        return
    await state.update_data(migration_username=username)
    await state.set_state(Migration.password)
    await message.answer(
        "Отправь <b>SSH-пароль</b> нового сервера.\n\n"
        "Он нужен только для переноса, в чат не выводится.\n\n"
        "Для выхода введите /cancel",
        parse_mode=ParseMode.HTML,
    )


@router.message(Migration.password)
async def migration_password_input(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    password = (message.text or "").strip()
    if not password:
        await message.answer("Пароль не может быть пустым.\n\nДля выхода введите /cancel")
        return
    await state.update_data(migration_password=password)
    await state.set_state(Migration.target_dir)
    await message.answer(
        "Отправь <b>папку установки</b> на новом сервере.\n\n"
        "По умолчанию можно оставить <code>/root/drebol-vpn</code>.\n\n"
        "Для выхода введите /cancel",
        parse_mode=ParseMode.HTML,
    )


@router.message(Migration.target_dir)
async def migration_target_dir_input(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    target_dir = (message.text or "").strip() or "/root/drebol-vpn"
    if not target_dir.startswith("/"):
        await message.answer("Путь должен быть абсолютным, например <code>/root/drebol-vpn</code>.", parse_mode=ParseMode.HTML)
        return
    await state.update_data(migration_target_dir=target_dir)
    data = await state.get_data()
    summary = (
        "🚚 <b>Проверь данные миграции</b>\n\n"
        f"🌐 Хост: <code>{html.escape(str(data.get('migration_host') or ''))}</code>\n"
        f"🔌 Порт: <code>{html.escape(str(data.get('migration_port') or ''))}</code>\n"
        f"👤 Пользователь: <code>{html.escape(str(data.get('migration_username') or ''))}</code>\n"
        f"🔑 Пароль: <code>{'•' * max(8, len(str(data.get('migration_password') or '')))}</code>\n"
        f"📁 Папка установки: <code>{html.escape(target_dir)}</code>\n"
        f"⚙️ Сервис: <code>drebol-vpn</code>\n\n"
        "Если всё верно, нажми «Начать перенос»."
    )
    await state.set_state(Migration.confirm)
    await message.answer(summary, parse_mode=ParseMode.HTML, reply_markup=_migration_summary_kb())


@router.callback_query(F.data == "admin_migration_cancel")
async def cb_admin_migration_cancel(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await state.clear()
    await render_admin_menu(call.message, call.from_user.id, edit=True)
    await call.answer("Отменено")


@router.callback_query(F.data == "admin_migration_run")
async def cb_admin_migration_run(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    data = await state.get_data()
    if await state.get_state() != Migration.confirm.state:
        return await call.answer("Сначала заполни данные миграции.", show_alert=True)
    target = MigrationTarget(
        host=str(data.get("migration_host") or "").strip(),
        port=int(data.get("migration_port") or 22),
        username=str(data.get("migration_username") or "root").strip() or "root",
        password=str(data.get("migration_password") or ""),
        target_dir=str(data.get("migration_target_dir") or "/root/drebol-vpn").strip() or "/root/drebol-vpn",
    )
    await call.answer()
    await call.message.edit_text(
        "⏳ <b>Перенос выполняется...</b>\n\n"
        "Я собираю проект, передаю его на новый сервер и поднимаю сервис.",
        parse_mode=ParseMode.HTML,
    )

    async def _progress(text: str) -> None:
        try:
            await call.message.edit_text(text, parse_mode=ParseMode.HTML)
        except Exception:
            pass

    result = await migrate_bot_to_server(target, progress_cb=_progress)
    await state.clear()
    if result.ok:
        await call.message.edit_text(
            "✅ <b>Миграция завершена успешно.</b>\n\n"
            "На новом сервере автозапуск уже включён, сервис перезапущен.\n\n"
            "Команда для запуска на новом сервере:\n"
            "<code>sudo systemctl start drebol-vpn</code>\n\n"
            "Команда для остановки на этом сервере:\n"
            "<code>sudo systemctl stop drebol-vpn</code>\n\n"
            "Сейчас я попробую мягко остановить этот сервер через несколько секунд.",
            parse_mode=ParseMode.HTML,
        )
        async def _stop_old_server() -> None:
            await asyncio.sleep(5)
            try:
                subprocess.Popen(
                    ["systemctl", "stop", "drebol-vpn"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except Exception:
                pass

        asyncio.create_task(_stop_old_server())
        return
    await call.message.edit_text(
        "❌ <b>Миграция не удалась.</b>\n\n"
        f"<code>{html.escape(result.message)}</code>\n\n"
        "Попробуй ещё раз или проверь доступ по SSH.",
        parse_mode=ParseMode.HTML,
        reply_markup=_admin_kb(),
    )


@router.callback_query(F.data == "xui_settings")
async def cb_xui_settings(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    data = load_xui_settings()
    url = data.get("XUI_URL") or "не задан"
    token = data.get("XUI_TOKEN") or "не задан"
    sub_port = data.get("XUI_SUB_PORT") or "не задан"
    await call.message.edit_text(
        "⚙️ <b>Настройки XUI</b>\n\n"
        f"URL: <code>{url}</code>\n"
        f"Токен: <code>{token}</code>\n\n"
        f"Порт подписки: <code>{sub_port}</code>\n\n"
        "Нажми кнопку ниже, чтобы изменить значение.",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_kb(),
    )
    await call.answer()


@router.callback_query(F.data == "xui_set_url")
async def cb_xui_set_url(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await state.set_state(XuiSettings.url)
    await call.message.edit_text(
        "Отправь <b>URL панели 3x-ui</b> следующим сообщением.\n\n"
        "Пример: <code>https://example.com</code>\n\n"
        "Для выхода введите /cancel",
        parse_mode=ParseMode.HTML,
    )
    await call.answer()


@router.callback_query(F.data == "xui_set_token")
async def cb_xui_set_token(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await state.set_state(XuiSettings.token)
    await call.message.edit_text(
        "Отправь <b>API токен</b> панели следующим сообщением.\n\n"
        "Для выхода введите /cancel",
        parse_mode=ParseMode.HTML,
    )
    await call.answer()


@router.callback_query(F.data == "xui_set_sub_port")
async def cb_xui_set_sub_port(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await state.set_state(XuiSettings.sub_port)
    await call.message.edit_text(
        "Отправь <b>порт подписки</b> следующим сообщением.\n\n"
        "Пример: <code>2096</code>\n\n"
        "Для выхода введите /cancel",
        parse_mode=ParseMode.HTML,
    )
    await call.answer()


@router.callback_query(F.data == "xui_back")
async def cb_xui_back(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await state.clear()
    data = load_xui_settings()
    url = data.get("XUI_URL") or "не задан"
    token = data.get("XUI_TOKEN") or "не задан"
    sub_port = data.get("XUI_SUB_PORT") or "не задан"
    await call.message.edit_text(
        "⚙️ <b>Настройки XUI</b>\n\n"
        f"URL: <code>{url}</code>\n"
        f"Токен: <code>{token}</code>\n\n"
        f"Порт подписки: <code>{sub_port}</code>\n\n"
        "Нажми кнопку ниже, чтобы изменить значение.",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_kb(),
    )
    await call.answer()


@router.message(XuiSettings.url)
async def xui_url_input(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    url = (message.text or "").strip()
    if not url:
        await message.answer("URL не может быть пустым.")
        return
    data = load_xui_settings()
    data["XUI_URL"] = url
    save_xui_settings(data)
    await state.clear()
    await message.answer("✅ URL панели сохранён.")


@router.message(XuiSettings.token)
async def xui_token_input(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    token = (message.text or "").strip()
    if not token:
        await message.answer("Токен не может быть пустым.")
        return
    data = load_xui_settings()
    data["XUI_TOKEN"] = token
    save_xui_settings(data)
    await state.clear()
    await message.answer("✅ API токен сохранён.")


@router.message(XuiSettings.sub_port)
async def xui_sub_port_input(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    sub_port = (message.text or "").strip()
    if not sub_port or not sub_port.isdigit():
        await message.answer("Порт должен быть числом.")
        return
    data = load_xui_settings()
    data["XUI_SUB_PORT"] = sub_port
    save_xui_settings(data)
    await state.clear()
    await message.answer("✅ Порт подписки сохранён.")


@router.callback_query(F.data == "app_update_check")
async def cb_update_check(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    if update_available():
        await call.message.edit_text(
            "📦 <b>Найдено обновление</b>\n\n"
            "Нажми кнопку ниже, чтобы скачать апдейт и перезапустить бота.",
            parse_mode=ParseMode.HTML,
            reply_markup=_admin_kb(),
        )
    else:
        await call.message.edit_text(
            "✅ <b>Обновлений нет</b>\n\n"
            "Локальная версия совпадает с GitHub.",
            parse_mode=ParseMode.HTML,
            reply_markup=_admin_kb(),
        )
    await call.answer()


@router.callback_query(F.data == "app_update_apply")
async def cb_update_apply(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)

    await call.message.edit_text(
        "⏳ <b>Обновляю бота...</b>\n\n"
        "Сейчас я скачиваю свежую версию и начинаю перезагрузку.",
        parse_mode=ParseMode.HTML,
    )
    ok, msg = apply_update()
    if ok:
        save_update_state(
            {
                "chat_id": call.message.chat.id,
                "admin_id": call.from_user.id,
                "status": "pending_success",
            }
        )
        await call.message.edit_text(
            "🔄 <b>Обновление установлено</b>\n\n"
            "Сейчас бот завершает работу и перезапускается. После старта я пришлю подтверждение.",
            parse_mode=ParseMode.HTML,
        )
        request_restart()
    else:
        await call.message.edit_text(
            f"❌ <b>Не удалось обновиться</b>\n\n<code>{msg}</code>",
            parse_mode=ParseMode.HTML,
        )
    await call.answer()


def _collect_user_ids() -> set[int]:
    user_ids: set[int] = set()
    for user_id in load_authorized_users():
        try:
            user_ids.add(int(user_id))
        except Exception:
            continue
    for key in load_vpn_users().keys():
        try:
            user_ids.add(int(key))
        except Exception:
            continue
    for key in load_paid_subscriptions().keys():
        try:
            user_ids.add(int(key))
        except Exception:
            continue
    return user_ids


def _broadcast_prompt_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад", callback_data="admin_broadcast_cancel")]
        ]
    )


def _broadcast_preview_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Отправить", callback_data="broadcast_confirm"),
                InlineKeyboardButton(text="↩️ Назад", callback_data="admin_broadcast_cancel"),
            ]
        ]
    )


@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await state.set_state(Broadcast.waiting_text)
    await call.message.edit_text(
        "📢 <b>Рассылка</b>\n\n"
        "Отправьте текст рассылки следующим сообщением.\n\n"
        "Для выхода введите /cancel",
        parse_mode=ParseMode.HTML,
        reply_markup=_broadcast_prompt_kb(),
    )
    await call.answer()


@router.message(Broadcast.waiting_text)
async def broadcast_text_input(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = message.text or message.caption
    if not text:
        await message.answer("❌ Рассылка должна содержать текст.")
        return
    await state.update_data(broadcast_text=text)
    await state.set_state(Broadcast.confirm)
    preview = (
        "📢 <b>Предпросмотр рассылки</b>\n\n"
        f"{text}\n\n"
        f"👥 Получателей: <b>{len(_collect_user_ids())}</b>"
    )
    await message.answer(preview, parse_mode=ParseMode.HTML, reply_markup=_broadcast_preview_kb())


@router.callback_query(F.data == "broadcast_confirm")
async def cb_broadcast_confirm(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    data = await state.get_data()
    text = data.get("broadcast_text")
    if not text:
        await state.clear()
        return await call.answer("Текст рассылки не найден", show_alert=True)

    user_ids = _collect_user_ids()
    sent = 0
    failed = 0
    for user_id in user_ids:
        try:
            await bot.send_message(user_id, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
            await asyncio.sleep(0.05)

    await state.clear()
    await call.message.edit_text(
        f"✅ <b>Рассылка завершена</b>\n\n"
        f"Отправлено: <b>{sent}</b>\n"
        f"Не удалось: <b>{failed}</b>",
        parse_mode=ParseMode.HTML,
    )
    await call.answer()


@router.callback_query(F.data == "admin_broadcast_cancel")
async def cb_broadcast_cancel(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await state.clear()
    await render_admin_menu(call.message, call.from_user.id, edit=True)
    await call.answer()
