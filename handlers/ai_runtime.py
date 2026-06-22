from config import GROQ_API_KEY as ENV_GROQ_API_KEY, OPENROUTER_API_KEY as ENV_OPENROUTER_API_KEY
from handlers.ai_settings import load_ai_settings


def get_groq_api_key() -> str:
    return load_ai_settings().get("GROQ_API_KEY") or ENV_GROQ_API_KEY


def get_openrouter_api_key() -> str:
    return load_ai_settings().get("OPENROUTER_API_KEY") or ENV_OPENROUTER_API_KEY
