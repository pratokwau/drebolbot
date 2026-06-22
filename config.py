import os

from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
AUTH_FILE = os.getenv("AUTH_FILE", "authorized.json")
FP_TOKEN = os.getenv("FP_TOKEN", "")
DATA_DIR = os.getenv("DATA_DIR", "data")
INVENTORY_FILE = os.getenv("INVENTORY_FILE", f"{DATA_DIR}/inventory.json")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
XUI_URL = os.getenv("XUI_URL", "")
XUI_TOKEN = os.getenv("XUI_TOKEN", "")
