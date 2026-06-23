import os
import json
import html
from datetime import datetime
from aiogram import Router, types
from aiogram.enums import ParseMode
from handlers.utils import load_profits
from config import ADMIN_ID

router = Router()

def update_wallet(user_id: int, amount: float):
    """
    Заглушка: теперь баланс считается динамически из профитов.
    Функция оставлена для совместимости с другими файлами.
    """
    return 0

async def send_daily_report(bot, user_id):
    profits = load_profits(user_id)
    today = datetime.now().strftime("%d.%m.%Y")
    
    fp_orders = [
        p for p in profits 
        if today in p.get('date', '') 
        and (p.get('type') == "FunPay" or "FP #" in p.get('type', ''))
    ]
    fp_count = len(fp_orders)
    fp_sum = sum(p.get('sell_price', 0) for p in fp_orders)

    po_orders = [
        p for p in profits 
        if today in p.get('date', '') 
        and (p.get('type') == "PlayerOK" or "PO #" in p.get('type', ''))
    ]
    po_count = len(po_orders)
    po_sum = sum(p.get('sell_price', 0) for p in po_orders)

    total_profit = sum(p['profit'] for p in profits if today in p.get('date','') and p['profit'] > 0)

    text = (
        f"🌙 <b>Статистика за {today}</b>\n\n"
        f"📦 Продажи FunPay: <b>{fp_count}</b> на сумму <b>{fp_sum:.2f} ₽</b>\n"
        f"📦 Продажи PlayerOK: <b>{po_count}</b> на сумму <b>{po_sum:.2f} ₽</b>\n"
        f"──────────────────\n"
        f"💰 Чистая прибыль: <b>{total_profit:.2f} ₽</b>"
    )

    # Блок про изменения коэффициентов СБП - только для админа
    if int(user_id) == int(ADMIN_ID):
        try:
            from handlers.minprice import load_sbp_changes_today
            sbp_data = load_sbp_changes_today()
            text += "\n\n📈 <b>Коэффициенты СБП за день</b>\n"
            if not sbp_data:
                text += "\n⚠️ <i>Нет свежих данных проверки СБП.</i>"
            else:
                changes = sbp_data.get("changes", [])
                unchanged = sbp_data.get("unchanged", [])
                errors = sbp_data.get("errors", [])

                if changes:
                    text += f"\n🔄 <b>Изменились ({len(changes)}):</b>\n"
                    for ch in changes:
                        name = html.escape(ch["name"])
                        old = ch["old"]
                        new = ch["new"]
                        arrow = "⬆️" if new > old else "⬇️"
                        text += f"  {arrow} {name}: ×{old:.4f} → ×{new:.4f}\n"
                else:
                    text += "\n✅ <i>За день не изменились</i>\n"

                text += f"\n📊 Без изменений: <b>{len(unchanged)}</b>"
        except Exception as e:
            print(f"[DAILY REPORT] Ошибка загрузки SBP изменений: {e}")
            text += "\n\n📈 <b>Коэффициенты СБП за день</b>\n⚠️ <i>Ошибка загрузки данных проверки.</i>"

    await bot.send_message(user_id, text, parse_mode=ParseMode.HTML)
