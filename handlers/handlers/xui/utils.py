# handlers/xui/utils.py

import hashlib

from config import ADMIN_ID

_callback_cache = {}

NOTE_MAX_LEN = 50
DEFAULT_MAX_DEVICES = 10
CLIENTS_PAGE_SIZE = 10


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def get_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:8]


def cache(key: str, value) -> str:
    h = get_hash(key)
    _callback_cache[h] = value
    return h


def get_cached(key: str):
    return _callback_cache.get(key)


def format_bytes(b: int) -> str:
    if not b or b == 0:
        return "0"
    if b < 1024 ** 2:
        return f"{b / 1024:.1f} KB"
    if b < 1024 ** 3:
        return f"{b / 1024 ** 2:.1f} MB"
    return f"{b / 1024 ** 3:.2f} GB"


# alias for callback hash storage (used across handlers)
_cache = _callback_cache