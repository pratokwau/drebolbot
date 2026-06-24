import sqlite3

from base_store import admin_db_path, connect


def _conn() -> sqlite3.Connection:
    return connect(admin_db_path("xui_inbound_settings"))


def _create_tables():
    c = _conn().cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS xui_inbound_settings (
            inbound_id TEXT PRIMARY KEY,
            sub_port TEXT DEFAULT ''
        )
        """
    )
    _conn().commit()


def get_inbound_sub_port(inbound_id) -> str:
    _create_tables()
    c = _conn().cursor()
    c.execute("SELECT sub_port FROM xui_inbound_settings WHERE inbound_id = ?", (str(inbound_id),))
    row = c.fetchone()
    return row[0] if row and row[0] is not None else ""


def set_inbound_sub_port(inbound_id, sub_port: str) -> None:
    _create_tables()
    c = _conn().cursor()
    c.execute(
        "INSERT OR REPLACE INTO xui_inbound_settings (inbound_id, sub_port) VALUES (?, ?)",
        (str(inbound_id), str(sub_port).strip()),
    )
    _conn().commit()

