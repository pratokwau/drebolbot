# states/states.py
from aiogram.fsm.state import State, StatesGroup

class ProfitCalc(StatesGroup):
    waiting_for_commission = State()
    waiting_for_input = State()


class GiveAccess(StatesGroup):
    waiting_for_user_id = State()


class RevokeAccess(StatesGroup):
    waiting_for_user_id = State()


class SaveProfitStates(StatesGroup):
    # Добавление новой прибыли
    choose_type = State()                     # FunPay или PlayerOK
    waiting_funpay_prices = State()           # две цены для FunPay
    waiting_sale_c = State()                  # комиссия продажи для PlayerOK
    waiting_withdraw_c = State()              # комиссия вывода для PlayerOK
    waiting_playerok_prices = State()         # цены для PlayerOK

    # Редактирование существующей записи
    choose_edit = State()                     # выбор номера записи для редактирования
    edit_choose_type = State()                # выбор типа при редактировании
    edit_waiting_funpay_prices = State()      # редактирование FunPay цен
    edit_waiting_sale_c = State()             # редактирование % продажи PlayerOK
    edit_waiting_withdraw_c = State()         # редактирование % вывода PlayerOK
    edit_waiting_playerok_prices = State()    # редактирование цен PlayerOK

    # Удаление записи
    choose_delete = State()                   # выбор номера записи для удаления


class ProfitStatsStates(StatesGroup):
    waiting_custom_period = State()  # ввод "ДД.ММ.ГГГГ по ДД.ММ.ГГГГ"


class TaskUnfilledStates(StatesGroup):
    waiting_custom_period = State()


class BlockCommand(StatesGroup):
    waiting_for_command = State()    # ввод команды для блокировки
    waiting_for_unblock = State()    # выбор команды для разблокировки


class SendMessage(StatesGroup):
    waiting_message = State()        # ожидание сообщения/медиа для отправки пользователю


class Broadcast(StatesGroup):
    waiting_text = State()           # ожидание текста рассылки
    waiting_confirm = State()        # подтверждение отправки


class AddGroup(StatesGroup):
    waiting_group_id = State()


class AdminUserSettings(StatesGroup):
    waiting_time = State()


class AdminUserNote(StatesGroup):
    waiting_text = State()

