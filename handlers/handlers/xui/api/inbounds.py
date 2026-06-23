# handlers/xui/api/inbounds.py

from handlers.xui.api.client import xui_get


async def api_get_inbounds() -> tuple:
    result = await xui_get("/panel/api/inbounds/list")
    if result.get("success"):
        return result.get("obj", []), None
    return [], result.get("msg", "Неизвестная ошибка API")
