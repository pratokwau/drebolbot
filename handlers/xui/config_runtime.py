from config import XUI_TOKEN as ENV_XUI_TOKEN, XUI_URL as ENV_XUI_URL
from handlers.xui.settings_store import load_xui_settings


def get_xui_url() -> str:
    return load_xui_settings().get("XUI_URL") or ENV_XUI_URL


def get_xui_token() -> str:
    return load_xui_settings().get("XUI_TOKEN") or ENV_XUI_TOKEN


def get_xui_sub_port() -> str:
    return load_xui_settings().get("XUI_SUB_PORT") or ""
