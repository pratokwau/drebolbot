import asyncio
import sys
import json
import os
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler

sys.stdout.reconfigure(encoding='utf-8')

from aiogram import types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from loader import bot, dp
from config import ADMIN_ID

MUTE_FILE = "data/mute_restart.json"


def _load_mutes() -> dict:
    if not os.path.exists(MUTE_FILE):
        return {}
    try:
        with open(MUTE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_mutes(mutes: dict):
    os.makedirs("data", exist_ok=True)
    with open(MUTE_FILE, "w", encoding="utf-8") as f:
        json.dump(mutes, f)


def _is_muted(uid: int) -> bool:
    exp = _load_mutes().get(str(uid))
    if not exp:
        return False
    return datetime.fromisoformat(exp) > datetime.now()


def _set_mute(uid: int, minutes: int):
    mutes = _load_mutes()
    mutes[str(uid)] = (datetime.now() + timedelta(minutes=minutes)).isoformat()
    _save_mutes(mutes)


def _restart_mute_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔕 30 мин", callback_data="restart_mute_30"),
        InlineKeyboardButton(text="🔕 1 час",  callback_data="restart_mute_60"),
        InlineKeyboardButton(text="🔕 1 день", callback_data="restart_mute_1440"),
    ]])


from handlers.cancel import router as cancel_router
from handlers.start import router as start_router
from handlers.admin import router as admin_router
from handlers.rassstart import router as rass_router
from handlers.playerokrass import router as playerok_router
from handlers.saveprofit import router as saveprofit_router
from handlers.wallet import send_daily_report, router as wallet_router
from handlers.funpay_admin import router as fp_admin_router
from handlers.tasks import remind_unfilled_orders, router as tasks_router
from handlers.minprice import router as minprice_router, check_sbp_rates_for_admin
from handlers.ai_chat import router as ai_router
from handlers.ai_settings import router as ai_settings_router
from handlers.update_manager import get_update_status
from handlers.xui import router as xui_router
from handlers.tickets import router as tickets_router
from handlers.settings import router as settings_router
from handlers.about import router as about_router
from handlers.demping import router as demping_router
from handlers.certificates import router as certificates_router
from handlers.status import router as status_router, log_event, log_downtime, write_heartbeat, check_heartbeat_and_log
from middlewares.command_restriction import CommandRestrictionMiddleware


async def send_saveprofit_notifications():
    """Каждую минуту проверяет, кому из пользователей пора слать ежедневный отчёт."""
    from handlers.settings import load_all, DEFAULTS
    from loader import authorized_users

    now = datetime.now().strftime("%H:%M")
    all_settings = load_all()

    for uid in authorized_users:
        if int(uid) == int(ADMIN_ID):
            continue
        s = {**DEFAULTS, **all_settings.get(str(uid), {})}
        if s.get("saveprofit_notify") and s.get("saveprofit_time") == now:
            try:
                await send_daily_report(bot, uid)
            except Exception as e:
                print(f"[SAVEPROFIT NOTIFY] Ошибка для {uid}: {e}")


_last_admin_report_date = None


async def send_admin_daily_report():
    """Админский отчёт: сначала обновляет СБП, потом отправляет статистику."""
    try:
        await check_sbp_rates_for_admin()
    except Exception as e:
        print(f"[ADMIN DAILY REPORT] Ошибка проверки СБП: {e}")
    await send_daily_report(bot, ADMIN_ID)


async def check_admin_daily_report_time():
    """Каждую минуту проверяет, пора ли отправить главный админский отчёт."""
    global _last_admin_report_date
    from handlers.settings import get_user_settings

    now = datetime.now()
    settings = get_user_settings(ADMIN_ID)
    if not settings.get("admin_report_notify", True):
        return
    if settings.get("admin_report_time", "23:59") != now.strftime("%H:%M"):
        return

    today_key = now.strftime("%Y-%m-%d")
    if _last_admin_report_date == today_key:
        return

    _last_admin_report_date = today_key
    await send_admin_daily_report()


# Глобальная переменная для отслеживания начала даунтайма VPN
_vpn_down_since = None


async def check_vpn_status():
    """Проверяет доступность 3X-UI панели и состояние Xray каждые 5 минут."""
    global _vpn_down_since
    try:
        from handlers.xui import xui_get
        result = await xui_get("/panel/api/server/status")
        xray_state = result.get("obj", {}).get("xray", {}).get("state", "")
        ok = result.get("success") and xray_state == "running"

        if not ok:
            if _vpn_down_since is None:
                _vpn_down_since = datetime.now()
        else:
            if _vpn_down_since is not None:
                log_downtime(
                    "vpn",
                    from_dt=_vpn_down_since,
                    to_dt=datetime.now(),
                    desc="Панель/Xray были недоступны"
                )
                _vpn_down_since = None
    except Exception as e:
        if _vpn_down_since is None:
            _vpn_down_since = datetime.now()
        log_event("vpn", "check_error", str(e)[:80])


async def bot_heartbeat():
    """Каждую минуту: проверяет паузу в работе бота, потом обновляет heartbeat."""
    check_heartbeat_and_log()
    write_heartbeat()


async def _check_downtime_on_startup():
    """Вызывается сразу при старте — фиксирует даунтайм бота и VPN пока сервер лежал."""
    HEARTBEAT_FILE = "data/heartbeat.json"

    last_alive = None
    if os.path.exists(HEARTBEAT_FILE):
        try:
            with open(HEARTBEAT_FILE, encoding="utf-8") as f:
                hb = json.load(f)
            last_alive = datetime.fromisoformat(hb["time"])
        except Exception:
            pass

    # 1. Даунтайм бота — по разрыву heartbeat
    check_heartbeat_and_log()

    # 2. Даунтайм VPN — проверяем последний heartbeat и текущее состояние панели
    try:
        from handlers.xui import xui_get

        # Проверяем панель сейчас
        result = await xui_get("/panel/api/server/status")
        xray_state = result.get("obj", {}).get("xray", {}).get("state", "")
        vpn_ok_now = result.get("success") and xray_state == "running"

        if not vpn_ok_now and last_alive:
            # VPN сейчас недоступен. Когда именно он упал во время простоя бота — неизвестно,
            # поэтому начинаем отсчёт с текущего запуска, а сам простой бота график VPN покажет белым.
            global _vpn_down_since
            _vpn_down_since = datetime.now()
            log_event("vpn", "check_error", "VPN недоступен после перезапуска сервера")
    except Exception as e:
        print(f"[STARTUP VPN CHECK] {e}")
    finally:
        write_heartbeat()


async def main():
    dp.message.middleware(CommandRestrictionMiddleware())

    dp.include_router(cancel_router)
    dp.include_router(tickets_router)
    dp.include_router(settings_router)
    dp.include_router(about_router)
    dp.include_router(demping_router)
    dp.include_router(certificates_router)
    dp.include_router(start_router)
    dp.include_router(admin_router)
    dp.include_router(rass_router)
    dp.include_router(playerok_router)
    dp.include_router(saveprofit_router)
    dp.include_router(wallet_router)
    dp.include_router(fp_admin_router)
    dp.include_router(tasks_router)
    dp.include_router(minprice_router)
    dp.include_router(ai_router)
    dp.include_router(ai_settings_router)
    dp.include_router(xui_router)
    dp.include_router(status_router)

    job_defaults = {
        'coalesce': True,
        'max_instances': 1
    }
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow", job_defaults=job_defaults)

    scheduler.add_job(remind_unfilled_orders,        "cron",     hour=23, minute=40)
    scheduler.add_job(remind_unfilled_orders,        "cron",     hour=23, minute=55)
    scheduler.add_job(check_admin_daily_report_time, "cron",     minute="*")
    scheduler.add_job(send_saveprofit_notifications, "cron",     minute="*")
    scheduler.add_job(check_vpn_status,              "interval", minutes=5)
    scheduler.add_job(bot_heartbeat,                 "interval", minutes=1)
    scheduler.start()

    print("[INFO] Запуск бота...")

    @dp.callback_query(F.data.startswith("restart_mute_"))
    async def cb_restart_mute(call: types.CallbackQuery):
        minutes = int(call.data.split("_")[2])
        _set_mute(call.from_user.id, minutes)
        labels = {30: "30 минут", 60: "1 час", 1440: "1 день"}
        await call.answer(f"🔕 Уведомления отключены на {labels.get(minutes, f'{minutes} мин')}", show_alert=False)
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    async def notify_restart():
        from loader import authorized_users
        from handlers.settings import is_enabled

        # Сразу при старте фиксируем даунтайм бота и VPN
        await _check_downtime_on_startup()

        for uid in authorized_users:
            if not is_enabled(uid, "restart_notify"):
                continue
            if _is_muted(uid):
                continue
            try:
                await bot.send_message(
                    uid,
                    "🔄 <b>Бот был перезагружен.</b>\n\n"
                    "Если вы были в каком-либо режиме (AI, расчёт, минпрайс и т.д.) — "
                    "введите команду заново.",
                    parse_mode="HTML",
                    reply_markup=_restart_mute_kb()
                )
            except Exception:
                pass

    await notify_restart()

    while True:
        try:
            me = await bot.get_me()
            print(f"[INFO] Бот @{me.username} успешно запущен!")
            await dp.start_polling(bot, polling_timeout=5)
        except Exception as e:
            print(f"[CRITICAL ERROR] Вылет поллинга: {e}")
            print("[INFO] Попытка перезапуска через 5 секунд...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except (KeyboardInterrupt, SystemExit):
        print("[INFO] Бот остановлен вручную.")
    finally:
        loop.run_until_complete(bot.session.close())
        loop.close()
