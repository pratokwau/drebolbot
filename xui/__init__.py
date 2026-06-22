# handlers/xui/__init__.py

from aiogram import Router

from handlers.xui.admin import router as admin_router
from handlers.xui.myvpn import router as myvpn_router

router = Router()
router.include_router(admin_router)
router.include_router(myvpn_router)

# Публичный API (обратная совместимость)
from handlers.xui.api.client import xui_get, xui_post
from handlers.xui.storage import get_vpn_user, load_vpn_users
