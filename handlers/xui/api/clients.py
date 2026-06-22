# handlers/xui/api/clients.py

import time
import uuid as uuid_lib
from urllib.parse import quote

from handlers.xui.api.client import xui_get, xui_post
from handlers.xui.api.helpers import parse_clients


def email_path(email: str) -> str:
    return quote(str(email or ""), safe="")


def build_update_payload(client: dict) -> dict:
    """Формирует тело запроса для /clients/update — только допустимые поля."""
    allowed = {"email", "subId", "id", "flow", "totalGB", "expiryTime",
               "limitIp", "tgId", "comment", "enable", "reset"}
    payload = {k: v for k, v in client.items() if k in allowed}
    if "id" in payload and isinstance(payload["id"], int):
        payload.pop("id")
    return payload


async def api_add_client(ib_id: int, email: str, expiry_days: int, limit_gb: float, flow: str = "") -> tuple:
    client_uuid = str(uuid_lib.uuid4())
    expiry_time = int((time.time() + expiry_days * 86400) * 1000) if expiry_days > 0 else 0
    total_bytes = int(limit_gb * 1024 ** 3) if limit_gb > 0 else 0

    client = {
        "id": client_uuid,
        "email": email,
        "flow": flow,
        "limitIp": 0,
        "totalGB": total_bytes,
        "expiryTime": expiry_time,
        "enable": True,
        "tgId": 0,
        "subId": "",
        "reset": 0,
    }

    result = await xui_post(
        "/panel/api/clients/add",
        data={"client": client, "inboundIds": [ib_id]},
    )
    return result, client_uuid


async def api_get_client(email: str) -> dict | None:
    """Получить полный объект клиента по email для /clients/update."""
    result = await xui_get(f"/panel/api/clients/get/{email_path(email)}")
    if not result.get("success"):
        return None
    obj = result.get("obj", {})
    client = obj.get("client") if isinstance(obj, dict) and "client" in obj else obj
    if not client or not isinstance(client, dict):
        return None

    db_id = client.get("id")
    if isinstance(db_id, int):
        client["_db_id"] = db_id
        client.pop("id", None)

    inbounds_result = await xui_get("/panel/api/inbounds/list")
    if inbounds_result.get("success"):
        for ib in inbounds_result.get("obj", []):
            for c in parse_clients(ib):
                if c.get("email") == email:
                    uuid_str = c.get("id", "")
                    if uuid_str and isinstance(uuid_str, str):
                        client["id"] = uuid_str
                    break
            if client.get("id"):
                break

    return client


async def api_del_client(ib_id: int, client_uuid: str) -> dict:
    """Удаление клиента через v3 API по uuid — ищет email через clients/list."""
    try:
        result = await xui_get("/panel/api/clients/list")
        if result.get("success"):
            for c in result.get("obj", []):
                if c.get("id") == client_uuid:
                    email = c.get("email")
                    if email:
                        return await xui_post(f"/panel/api/clients/del/{email_path(email)}")
        return {"success": False, "msg": "Клиент не найден"}
    except Exception as e:
        return {"success": False, "msg": str(e)}


async def api_del_client_by_email(email: str) -> dict:
    return await xui_post(f"/panel/api/clients/del/{email_path(email)}")


async def api_update_client(ib_id: int, client_uuid: str, client_obj: dict) -> dict:
    email = client_obj.get("email")
    if not email:
        return {"success": False, "msg": "email не задан"}
    payload = build_update_payload(client_obj)
    return await xui_post(f"/panel/api/clients/update/{email_path(email)}", data=payload)


async def api_reset_client_traffic(ib_id: int, email: str) -> dict:
    return await xui_post(f"/panel/api/clients/resetTraffic/{email_path(email)}")
