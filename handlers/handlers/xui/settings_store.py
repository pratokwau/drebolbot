import sqlite3

from base_store import admin_db_path, connect


def _conn() -> sqlite3.Connection:
    return connect(admin_db_path("xui_settings"))


def _create_tables():
    c = _conn().cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS xui_settings
                 (key TEXT PRIMARY KEY, value TEXT)''')
    _conn().commit()


def _get(key: str) -> str:
    _create_tables()
    c = _conn().cursor()
    c.execute("SELECT value FROM xui_settings WHERE key = ?", (key,))
    row = c.fetchone()
    return row[0] if row else ""


def _set(key: str, value: str):
    _create_tables()
    c = _conn().cursor()
    c.execute("INSERT OR REPLACE INTO xui_settings (key, value) VALUES (?, ?)", (key, value.strip()))
    _conn().commit()


def load_xui_settings() -> dict:
    return {
        "XUI_URL": _get("XUI_URL"),
        "XUI_TOKEN": _get("XUI_TOKEN"),
    }


def save_xui_settings(xui_url: str, xui_token: str) -> None:
    _set("XUI_URL", xui_url)
    _set("XUI_TOKEN", xui_token)
