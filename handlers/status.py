# handlers/status.py
import io
import json
import os
from datetime import datetime, timedelta

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.enums import ParseMode
from PIL import Image, ImageDraw, ImageFont

from loader import is_authorized
from handlers.utils import is_vpn_only_user

router = Router()

STATUS_LOG_FILE  = "data/status_log.json"
HEARTBEAT_FILE   = "data/heartbeat.json"
MONITOR_START_FILE = "data/status_monitor_start.json"
os.makedirs("data", exist_ok=True)


# ====================== FSM ======================

class StatusPeriod(StatesGroup):
    waiting_bot_custom = State()
    waiting_vpn_custom = State()


# ====================== HEARTBEAT ======================

def write_heartbeat():
    """Записывает текущее время как heartbeat. Вызывается каждую минуту."""
    try:
        _ensure_monitor_started("bot")
        _ensure_monitor_started("vpn")
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
            json.dump({"time": datetime.now().isoformat(timespec="seconds")}, f)
    except Exception:
        pass


def check_heartbeat_and_log():
    """Проверяет heartbeat. Если пауза > 2 минут — пишет инцидент в лог бота."""
    try:
        if not os.path.exists(HEARTBEAT_FILE):
            return
        with open(HEARTBEAT_FILE, encoding="utf-8") as f:
            data = json.load(f)
        last = datetime.fromisoformat(data["time"])
        now  = datetime.now()
        diff = (now - last).total_seconds()
        if diff > 120:  # больше 2 минут — была пауза
            log_downtime(
                category="bot",
                from_dt=last,
                to_dt=now,
                desc=f"Бот был недоступен ~{int(diff // 60)} мин"
            )
            # Сбрасываем heartbeat чтобы не дублировать
            write_heartbeat()
    except Exception:
        pass


# ====================== ЛОГ ======================

def _load_log() -> dict:
    if not os.path.exists(STATUS_LOG_FILE):
        return {"bot": [], "vpn": []}
    try:
        with open(STATUS_LOG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("bot", [])
        data.setdefault("vpn", [])
        return data
    except Exception:
        return {"bot": [], "vpn": []}


def _save_log(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(STATUS_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_monitor_start() -> dict:
    if not os.path.exists(MONITOR_START_FILE):
        return {}
    try:
        with open(MONITOR_START_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_monitor_start(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(MONITOR_START_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _event_started_at(ev: dict) -> datetime | None:
    raw = ev.get("from") or ev.get("time")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _ensure_monitor_started(category: str):
    data = _load_monitor_start()
    if category not in data:
        started = _monitor_started_at(category)
        data[category] = (started or datetime.now()).isoformat(timespec="seconds")
        _save_monitor_start(data)


def _monitor_started_at(category: str) -> datetime | None:
    starts = []

    data = _load_monitor_start()
    for key in ([category, "bot"] if category == "vpn" else [category]):
        raw = data.get(key)
        if raw:
            try:
                starts.append(datetime.fromisoformat(raw))
            except Exception:
                pass

    log = _load_log()
    log_categories = [category, "bot"] if category == "vpn" else [category]
    for log_category in log_categories:
        for ev in log.get(log_category, []):
            started = _event_started_at(ev)
            if started:
                starts.append(started)

    # Если новый файл старта появился после старых логов, берём именно старые логи.
    # Если логов вообще нет, не делаем вид, что история до обновления неизвестна.
    return min(starts) if starts else None


def log_event(category: str, event: str, desc: str = ""):
    """Записать одиночное событие (перезапуск, ошибка Xray и т.д.)."""
    _ensure_monitor_started(category)
    data = _load_log()
    entry = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "event": event,
        "desc": desc,
    }
    data[category].append(entry)
    data[category] = data[category][-10000:]
    _save_log(data)


def log_downtime(category: str, from_dt: datetime, to_dt: datetime, desc: str = ""):
    """Записать блок недоступности с from/to."""
    _ensure_monitor_started(category)
    data = _load_log()
    entry = {
        "from": from_dt.isoformat(timespec="seconds"),
        "to":   to_dt.isoformat(timespec="seconds"),
        "event": "down",
        "desc": desc,
        "minutes": int((to_dt - from_dt).total_seconds() // 60),
    }
    data[category].append(entry)
    data[category] = data[category][-10000:]
    _save_log(data)


def _filter_events(events: list, since: datetime, until: datetime) -> list:
    """Возвращает события в диапазоне. Поддерживает и одиночные (time) и блоки (from/to)."""
    result = []
    for e in events:
        try:
            if "from" in e:
                dt = datetime.fromisoformat(e["from"])
                dt_to = datetime.fromisoformat(e["to"])
            else:
                dt = datetime.fromisoformat(e["time"])
                dt_to = dt
            if dt <= until and dt_to >= since:
                result.append({**e, "_dt": dt, "_dt_to": dt_to})
        except Exception:
            pass
    return result


def _filter_downtime_events(events: list, since: datetime, until: datetime) -> list:
    """Возвращает только интервалы простоя."""
    return [
        ev for ev in _filter_events(events, since, until)
        if ev.get("event") == "down"
    ]


def _format_incidents(events: list) -> str:
    if not events:
        return "✅ <b>Инцидентов нет.</b>"

    lines = []
    for i, ev in enumerate(events[-30:], start=max(1, len(events) - 29)):
        desc = ev.get("desc") or ev.get("event", "")
        if "from" in ev:
            f_str = datetime.fromisoformat(ev["from"]).strftime("%d.%m %H:%M")
            t_str = datetime.fromisoformat(ev["to"]).strftime("%d.%m %H:%M")
            mins = ev.get("minutes", 0)
            lines.append(f"<b>{i}.</b> {f_str} → {t_str} · <b>{mins} мин</b>\n<i>{desc}</i>")
        else:
            dt_str = datetime.fromisoformat(ev["time"]).strftime("%d.%m %H:%M")
            lines.append(f"<b>{i}.</b> {dt_str}\n<i>{desc}</i>")

    if len(events) > 30:
        lines.insert(0, f"<i>Показаны последние 30 из {len(events)}.</i>")
    return "\n\n".join(lines)


# ====================== ГРАФИК ======================

W, H = 1100, 520
BG = (15, 18, 24)
PANEL = (25, 30, 40)
PANEL_2 = (31, 38, 50)
GRID = (57, 68, 84)
GREEN = (63, 220, 151)
GREEN_DARK = (30, 142, 98)
RED = (245, 91, 91)
RED_DARK = (140, 45, 55)
WHITE = (238, 242, 247)
MUTED = (148, 163, 184)
YELLOW = (250, 204, 21)
UNKNOWN = (235, 241, 248)
UNKNOWN_TEXT = (71, 85, 105)


def _try_font(size: int):
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _draw_round_rect(draw: ImageDraw.ImageDraw, xy, radius: int, fill, outline=None, width: int = 1):
    try:
        draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)
    except Exception:
        draw.rectangle(xy, fill=fill, outline=outline, width=width)


def _event_down_minutes(events: list, since: datetime, until: datetime) -> int:
    total = 0
    for ev in events:
        if ev.get("event") != "down":
            continue
        total += int((min(ev["_dt_to"], until) - max(ev["_dt"], since)).total_seconds() // 60)
    return max(total, 0)


def _merge_intervals(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    cleaned = sorted((a, b) for a, b in intervals if b > a)
    if not cleaned:
        return []
    merged = [cleaned[0]]
    for start, end in cleaned[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _interval_seconds(intervals: list[tuple[datetime, datetime]]) -> int:
    return int(sum((b - a).total_seconds() for a, b in _merge_intervals(intervals)))


def _subtract_intervals(base_start: datetime, base_end: datetime, subtract: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    parts = [(base_start, base_end)]
    for sub_start, sub_end in _merge_intervals(subtract):
        next_parts = []
        for start, end in parts:
            if sub_end <= start or sub_start >= end:
                next_parts.append((start, end))
                continue
            if sub_start > start:
                next_parts.append((start, min(sub_start, end)))
            if sub_end < end:
                next_parts.append((max(sub_end, start), end))
        parts = next_parts
    return parts


def _subtract_from_intervals(intervals: list[tuple[datetime, datetime]],
                             subtract: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    result = []
    for start, end in _merge_intervals(intervals):
        result.extend(_subtract_intervals(start, end, subtract))
    return _merge_intervals(result)


def _down_minutes_excluding_unknown(events: list, since: datetime, until: datetime,
                                    unknown_intervals: list[tuple[datetime, datetime]]) -> int:
    total = 0
    for ev in events:
        if ev.get("event") != "down":
            continue
        start = max(ev["_dt"], since)
        end = min(ev["_dt_to"], until)
        for known_start, known_end in _subtract_intervals(start, end, unknown_intervals):
            total += int((known_end - known_start).total_seconds() // 60)
    return max(total, 0)


def _unknown_intervals_for(cat: str, since: datetime, until: datetime, known_since: datetime | None) -> list[tuple[datetime, datetime]]:
    intervals = []
    if known_since and known_since > since:
        intervals.append((since, min(known_since, until)))

    # Будущие интервалы мы не анализировали, поэтому помечаем их как неизвестные.
    now = datetime.now()
    if until > now:
        intervals.append((max(now, since), until))

    return _merge_intervals(intervals)


def _effective_events_for(cat: str, since: datetime, until: datetime) -> list[dict]:
    log = _load_log()
    events = _filter_downtime_events(log.get(cat, []), since, until)

    # Для VPN отображаем простои панели/бота как недоступность VPN.
    # Текст делаем нейтральным, чтобы не светить бот в отчёте VPN.
    if cat == "vpn":
        for ev in _filter_downtime_events(log.get("bot", []), since, until):
            events.append({
                **ev,
                "event": "down",
                "desc": "Панель/Xray были недоступны",
            })
        for ev in events:
            if ev.get("event") == "down" and not ev.get("desc"):
                ev["desc"] = "Панель/Xray были недоступны"
    return sorted(events, key=lambda e: (e.get("_dt"), e.get("_dt_to")))


def _build_chart(events: list, since: datetime, until: datetime, title: str,
                 known_since: datetime | None = None,
                 unknown_intervals: list[tuple[datetime, datetime]] | None = None,
                 unknown_label: str = "неизвестно") -> io.BytesIO:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    font_title = _try_font(30)
    font_metric = _try_font(28)
    font_label = _try_font(16)
    font_small = _try_font(13)

    total_secs = max((until - since).total_seconds(), 1)
    known_from = max(since, known_since) if known_since else since
    unknown_intervals = _merge_intervals(unknown_intervals or [])
    unknown_secs = min(_interval_seconds(unknown_intervals), int(total_secs))
    known_secs = max(total_secs - unknown_secs, 1)
    total_down = _down_minutes_excluding_unknown(events, known_from, until, unknown_intervals)
    uptime = None if unknown_secs >= total_secs else max(0.0, 100.0 - ((total_down * 60) / known_secs * 100.0))

    # Header
    draw.text((44, 38), title, fill=WHITE, font=font_title, anchor="la")
    draw.text((44, 78), f"{since.strftime('%d.%m %H:%M')}  -  {until.strftime('%d.%m %H:%M')}",
              fill=MUTED, font=font_label, anchor="la")

    # Metric cards
    cards = [
        (44, 116, 330, 205, "Доступность", "н/д" if uptime is None else f"{uptime:.2f}%", UNKNOWN if uptime is None else GREEN if uptime >= 99 else YELLOW),
        (360, 116, 646, 205, "Инциденты", str(len(events)), RED if events else GREEN),
        (676, 116, 1056, 205, "Недоступен", f"{total_down} мин", YELLOW if total_down else GREEN),
    ]
    for x1, y1, x2, y2, label, value, color in cards:
        _draw_round_rect(draw, [x1, y1, x2, y2], 18, PANEL, outline=(39, 48, 64), width=2)
        cx = (x1 + x2) // 2
        draw.text((cx, y1 + 27), label, fill=MUTED, font=font_label, anchor="ma")
        draw.text((cx, y1 + 66), value, fill=color, font=font_metric, anchor="mm")

    # Timeline panel
    panel_x1, panel_y1, panel_x2, panel_y2 = 44, 245, 1056, 425
    _draw_round_rect(draw, [panel_x1, panel_y1, panel_x2, panel_y2], 20, PANEL_2, outline=(39, 48, 64), width=2)
    draw.text((panel_x1 + 22, panel_y1 + 28), "Линия доступности", fill=WHITE, font=font_label, anchor="la")

    bar_x1 = panel_x1 + 32
    bar_x2 = panel_x2 - 32
    bar_y = panel_y1 + 88
    bar_h = 36
    bar_w = bar_x2 - bar_x1

    _draw_round_rect(draw, [bar_x1, bar_y, bar_x2, bar_y + bar_h], 18, GREEN_DARK)
    _draw_round_rect(draw, [bar_x1 + 2, bar_y + 2, bar_x2 - 2, bar_y + bar_h - 2], 16, GREEN)

    MIN_PX = 4
    for ev in events:
        dt_from: datetime = ev["_dt"]
        dt_to:   datetime = ev["_dt_to"]
        # Клампим к диапазону
        dt_from = max(dt_from, since)
        dt_to   = min(dt_to,   until)
        off_from = (dt_from - since).total_seconds()
        off_to   = (dt_to   - since).total_seconds()
        px1 = bar_x1 + int(off_from / total_secs * bar_w)
        px2 = bar_x1 + int(off_to   / total_secs * bar_w)
        px2 = max(px2, px1 + MIN_PX)
        px1 = max(bar_x1, min(bar_x2 - MIN_PX, px1))
        px2 = max(bar_x1 + MIN_PX, min(bar_x2, px2))
        _draw_round_rect(draw, [px1, bar_y, px2, bar_y + bar_h], 8, RED_DARK)
        draw.rectangle([px1, bar_y + 3, px2, bar_y + bar_h - 3], fill=RED)

    for unk_start, unk_end in unknown_intervals:
        off_from = (max(unk_start, since) - since).total_seconds()
        off_to = (min(unk_end, until) - since).total_seconds()
        px1 = bar_x1 + int(off_from / total_secs * bar_w)
        px2 = bar_x1 + int(off_to / total_secs * bar_w)
        px1 = max(bar_x1, min(bar_x2, px1))
        px2 = max(bar_x1, min(bar_x2, px2))
        if px2 <= px1:
            continue
        _draw_round_rect(draw, [px1, bar_y, px2, bar_y + bar_h], 8, UNKNOWN)
        if px2 - px1 > 90:
            draw.text(((px1 + px2) // 2, bar_y + bar_h // 2), unknown_label,
                      fill=UNKNOWN_TEXT, font=font_small, anchor="mm")

    for i in range(6):
        frac = i / 5
        px = bar_x1 + int(frac * bar_w)
        tick_dt = since + timedelta(seconds=frac * total_secs)
        draw.line([(px, bar_y - 16), (px, bar_y + bar_h + 12)], fill=GRID, width=1)
        label = tick_dt.strftime("%d.%m %H:%M")
        draw.text((px, bar_y + bar_h + 22), label, fill=MUTED, font=font_small, anchor="mt")

    leg_y = 468
    draw.rounded_rectangle([44, leg_y, 64, leg_y + 20], radius=5, fill=GREEN)
    draw.text((74, leg_y + 10), "Норма", fill=MUTED, font=font_label, anchor="lm")
    draw.rounded_rectangle([170, leg_y, 190, leg_y + 20], radius=5, fill=RED)
    draw.text((200, leg_y + 10), "Недоступен", fill=MUTED, font=font_label, anchor="lm")
    draw.rounded_rectangle([350, leg_y, 370, leg_y + 20], radius=5, fill=UNKNOWN)
    draw.text((380, leg_y + 10), "Неизвестно", fill=MUTED, font=font_label, anchor="lm")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ====================== КЛАВИАТУРЫ ======================

def _kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Сбои бота",  callback_data="status_cat_bot")],
        [InlineKeyboardButton(text="📡 Сбои VPN",   callback_data="status_cat_vpn")],
    ])


def _kb_period(cat: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="За час",   callback_data=f"status_{cat}_hour"),
            InlineKeyboardButton(text="За день",  callback_data=f"status_{cat}_day"),
            InlineKeyboardButton(text="За месяц", callback_data=f"status_{cat}_month"),
        ],
        [InlineKeyboardButton(text="📅 Свой период", callback_data=f"status_{cat}_custom")],
        [InlineKeyboardButton(text="⬅️ Назад",        callback_data="status_back")],
    ])


def _kb_after_chart(cat: str, since: datetime, until: datetime, has_events: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
        text="📋 Инциденты" if has_events else "📋 Инцидентов нет",
        callback_data=f"status_inc_{cat}_{int(since.timestamp())}_{int(until.timestamp())}"
    )]]
    rows.extend([
        [InlineKeyboardButton(text="⬅️ К периодам",  callback_data=f"status_cat_{cat}")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="status_back")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ====================== КОМАНДА /status ======================

@router.message(Command("status"))
async def cmd_status(message: types.Message):
    user_id = message.from_user.id
    
    # Полный доступ или VPN-привязка без доступа к остальному боту.
    if not is_authorized(user_id):
        if not is_vpn_only_user(user_id):
            return
    
    await message.answer(
        "📊 <b>Мониторинг статуса</b>\n\nВыберите раздел:",
        parse_mode=ParseMode.HTML,
        reply_markup=_kb_main()
    )


# ====================== CALLBACKS ======================

@router.callback_query(F.data == "status_back")
async def cb_status_back(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    try:
        await call.message.edit_text(
            "📊 <b>Мониторинг статуса</b>\n\nВыберите раздел:",
            parse_mode=ParseMode.HTML,
            reply_markup=_kb_main()
        )
    except Exception:
        await call.message.answer(
            "📊 <b>Мониторинг статуса</b>\n\nВыберите раздел:",
            parse_mode=ParseMode.HTML,
            reply_markup=_kb_main()
        )


@router.callback_query(F.data.startswith("status_cat_"))
async def cb_status_cat(call: types.CallbackQuery):
    cat = call.data[len("status_cat_"):]
    labels = {"bot": "🤖 Сбои бота", "vpn": "📡 Сбои VPN"}
    await call.answer()
    try:
        await call.message.edit_text(
            f"📊 <b>{labels.get(cat, cat)}</b>\n\nВыберите период:",
            parse_mode=ParseMode.HTML,
            reply_markup=_kb_period(cat)
        )
    except Exception:
        await call.message.answer(
            f"📊 <b>{labels.get(cat, cat)}</b>\n\nВыберите период:",
            parse_mode=ParseMode.HTML,
            reply_markup=_kb_period(cat)
        )


@router.callback_query(F.data.startswith("status_inc_"))
async def cb_status_incidents(call: types.CallbackQuery):
    parts = call.data.split("_")
    if len(parts) != 5:
        return await call.answer("Ошибка данных", show_alert=True)

    cat = parts[2]
    try:
        since = datetime.fromtimestamp(int(parts[3]))
        until = datetime.fromtimestamp(int(parts[4]))
    except Exception:
        return await call.answer("Ошибка периода", show_alert=True)

    events = _effective_events_for(cat, since, until)
    if cat == "vpn":
        for ev in events:
            if ev.get("event") == "down":
                ev["desc"] = "Панель/Xray были недоступны"
    labels = {"bot": "бота", "vpn": "VPN"}
    text = (
        f"📋 <b>Инциденты {labels.get(cat, cat)}</b>\n"
        f"<i>{since.strftime('%d.%m %H:%M')} — {until.strftime('%d.%m %H:%M')}</i>\n\n"
        f"{_format_incidents(events)}"
    )

    await call.message.answer(text, parse_mode=ParseMode.HTML)
    await call.answer()


@router.callback_query(F.data.regexp(r"^status_(bot|vpn)_(hour|day|month|custom)$"))
async def cb_status_period(call: types.CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    cat = parts[1]
    period = parts[2]
    now = datetime.now()

    if period == "custom":
        await state.set_state(
            StatusPeriod.waiting_bot_custom if cat == "bot"
            else StatusPeriod.waiting_vpn_custom
        )
        await state.update_data(status_cat=cat)
        await call.answer()
        try:
            await call.message.edit_text(
                "📅 Введите период в формате:\n"
                "<code>дд.мм.гггг-дд.мм.гггг</code>\n\n"
                "Например: <code>01.05.2025-31.05.2025</code>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            await call.message.answer(
                "📅 Введите период в формате:\n"
                "<code>дд.мм.гггг-дд.мм.гггг</code>\n\n"
                "Например: <code>01.05.2025-31.05.2025</code>",
                parse_mode=ParseMode.HTML
            )
        return

    if period == "hour":
        since = now - timedelta(hours=1)
        period_label = "за последний час"
    elif period == "day":
        since = now - timedelta(days=1)
        period_label = "за последние 24 часа"
    else:
        since = now - timedelta(days=30)
        period_label = "за последние 30 дней"

    await call.answer("⏳ Строю график...")
    await _send_chart(call.message, cat, since, now, period_label)


@router.message(StatusPeriod.waiting_bot_custom)
@router.message(StatusPeriod.waiting_vpn_custom)
async def handle_custom_period(message: types.Message, state: FSMContext):
    if not is_authorized(message.from_user.id) and not is_vpn_only_user(message.from_user.id):
        return
    text = message.text.strip()
    data = await state.get_data()
    cat  = data.get("status_cat", "bot")
    await state.clear()

    try:
        raw = text.split("-")
        since = datetime.strptime(raw[0].strip(), "%d.%m.%Y")
        until = datetime.strptime(raw[1].strip(), "%d.%m.%Y").replace(
            hour=23, minute=59, second=59
        )
    except Exception:
        await message.answer(
            "❌ Неверный формат. Попробуйте: <code>01.05.2025-31.05.2025</code>",
            parse_mode=ParseMode.HTML
        )
        return

    period_label = f"{since.strftime('%d.%m.%Y')} — {until.strftime('%d.%m.%Y')}"
    await _send_chart(message, cat, since, until, period_label)


# ====================== ОТПРАВКА ГРАФИКА ======================

async def _send_chart(target, cat: str, since: datetime, until: datetime, period_label: str):
    events = _effective_events_for(cat, since, until)
    known_since = _monitor_started_at(cat)
    unknown_intervals = _unknown_intervals_for(cat, since, until, known_since)

    labels = {"bot": "Сбои бота", "vpn": "Сбои VPN"}
    title  = f"{labels.get(cat, cat)} · {period_label}"

    unknown_label = "недоступен" if cat == "vpn" else "неизвестно"
    buf = _build_chart(
        events,
        since,
        until,
        title,
        known_since=known_since,
        unknown_intervals=unknown_intervals,
        unknown_label=unknown_label,
    )

    known_from = max(since, known_since) if known_since else since
    has_unknown = bool(unknown_intervals)
    total_down = _down_minutes_excluding_unknown(events, known_from, until, unknown_intervals)

    # Краткая подпись к фото (строго до 1024 символов)
    if events:
        caption = (
            f"📊 <b>{title}</b>\n\n"
            f"🔴 Инцидентов: <b>{len(events)}</b>\n"
            f"⏱ Суммарно недоступен: <b>{total_down} мин</b>"
        )
    else:
        caption = (
            f"📊 <b>{title}</b>\n\n"
            f"✅ Сбоев не зафиксировано за выбранный период."
        )
    if has_unknown:
        caption += "\n⚪ Часть периода недоступна." if cat == "vpn" else "\n⚪ Часть периода неизвестна."

    photo = BufferedInputFile(buf.read(), filename="status.png")
    await target.answer_photo(photo, caption=caption,
                              parse_mode=ParseMode.HTML,
                              reply_markup=_kb_after_chart(cat, since, until, bool(events)))
