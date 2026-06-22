# handlers/xui/states.py

from aiogram.fsm.state import State, StatesGroup

class XuiAddClient(StatesGroup):
    email = State()
    expiry = State()
    limit_gb = State()
    flow = State()


class XuiBindTg(StatesGroup):
    waiting_tg_id = State()


class XuiAddUser(StatesGroup):
    waiting_tg_id = State()
    waiting_limit = State()


class XuiNoteEdit(StatesGroup):
    waiting_note = State()


class MyVpnAddDevice(StatesGroup):
    waiting_name = State()


class XuiAdminAddDevice(StatesGroup):
    waiting_name = State()


class XuiSettings(StatesGroup):
    waiting_url = State()
    waiting_token = State()
