# handlers/xui/api/helpers.py

import json


def parse_clients(inbound: dict) -> list:
    """Безопасно извлекает список клиентов из инбаунда 3X-UI"""
    if not inbound:
        return []
    settings = inbound.get("settings", "")
    if not settings:
        return []

    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except Exception:
            return []

    return settings.get("clients", [])


def parse_stream_settings(inbound: dict) -> dict:
    """Парсит streamSettings (новый формат - dict, старый - str)"""
    ss = inbound.get("streamSettings")
    if not ss:
        return {}
    if isinstance(ss, str):
        try:
            return json.loads(ss)
        except Exception:
            return {}
    return ss if isinstance(ss, dict) else {}


def get_client_stats_map(inbound: dict) -> dict:
    stats = {}
    for s in inbound.get("clientStats", []):
        stats[s.get("email", "")] = s
    return stats
