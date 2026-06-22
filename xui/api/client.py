# handlers/xui/api/client.py

import json

import aiohttp
from aiohttp import ClientSession

from handlers.xui.config_runtime import get_xui_token, get_xui_url

_session: ClientSession | None = None


async def get_session() -> ClientSession:
    global _session
    if _session is None or _session.closed:
        connector = aiohttp.TCPConnector(ssl=False)
        _session = ClientSession(connector=connector)
    return _session


async def xui_get(path: str) -> dict:
    xui_url = get_xui_url()
    xui_token = get_xui_token()
    if not xui_url or not xui_token:
        return {"success": False, "msg": "XUI_URL/XUI_TOKEN not configured"}
    session = await get_session()
    try:
        headers = {
            "Authorization": f"Bearer {xui_token}",
            "Content-Type": "application/json",
        }
        async with session.get(f"{xui_url}{path}", headers=headers, ssl=False) as resp:
            text = await resp.text()
            print(f"[XUI GET] {path} → {resp.status} {text[:200]}")
            try:
                return json.loads(text)
            except Exception:
                return {"success": False, "msg": f"HTTP {resp.status}: {text[:100]}"}
    except Exception as e:
        return {"success": False, "msg": str(e)}


async def xui_post(path: str, data=None) -> dict:
    xui_url = get_xui_url()
    xui_token = get_xui_token()
    if not xui_url or not xui_token:
        return {"success": False, "msg": "XUI_URL/XUI_TOKEN not configured"}
    session = await get_session()
    try:
        kwargs = {
            "headers": {
                "Authorization": f"Bearer {xui_token}",
                "Content-Type": "application/json",
            },
            "ssl": False,
            "allow_redirects": True,
        }
        if data is not None:
            kwargs["json"] = data
        async with session.post(f"{xui_url}{path}", **kwargs) as resp:
            text = await resp.text()
            print(f"[XUI POST] {path} → {resp.status} {text[:200]}")
            try:
                return json.loads(text)
            except Exception:
                return {"success": False, "msg": f"HTTP {resp.status}: {text[:100]}"}
    except Exception as e:
        return {"success": False, "msg": str(e)}
