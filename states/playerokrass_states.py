# states/playerokrass_states.py
from aiogram.fsm.state import State, StatesGroup

class PlayerOkStates(StatesGroup):
    waiting_sale_commission = State()
    waiting_withdraw_commission = State()
    waiting_prices = State()
