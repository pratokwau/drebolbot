import json
import os


SETTINGS_FILE = "data/xui_settings.json"


def load_xui_settings() -> dict:
    if not os.path.exists(SETTINGS_FILE):
        return {"XUI_URL": "", "XUI_TOKEN": ""}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "XUI_URL": data.get("XUI_URL", ""),
            "XUI_TOKEN": data.get("XUI_TOKEN", ""),
        }
    except Exception:
        return {"XUI_URL": "", "XUI_TOKEN": ""}


def save_xui_settings(xui_url: str, xui_token: str) -> None:
    os.makedirs("data", exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "XUI_URL": xui_url.strip(),
                "XUI_TOKEN": xui_token.strip(),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
