#!/usr/bin/env python3
"""
Утилита администратора для управления VPN-only пользователями.
Использование:
    python3 admin_vpn_only.py add <user_id>      - Добавить пользователя в VPN-only
    python3 admin_vpn_only.py remove <user_id>   - Удалить пользователя из VPN-only
    python3 admin_vpn_only.py list                - Показать всех VPN-only пользователей
    python3 admin_vpn_only.py check <user_id>    - Проверить статус пользователя
"""

import sys
from handlers.utils import (
    add_vpn_only_user,
    remove_vpn_only_user,
    is_vpn_only_user,
    load_vpn_only_users
)
from loader import is_authorized


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    command = sys.argv[1].lower()

    if command == "add":
        if len(sys.argv) < 3:
            print("❌ Укажите user_id: python3 admin_vpn_only.py add <user_id>")
            return
        try:
            user_id = int(sys.argv[2])
            add_vpn_only_user(user_id)
            print(f"✅ Пользователь {user_id} добавлен в VPN-only")
        except ValueError:
            print("❌ user_id должен быть числом")

    elif command == "remove":
        if len(sys.argv) < 3:
            print("❌ Укажите user_id: python3 admin_vpn_only.py remove <user_id>")
            return
        try:
            user_id = int(sys.argv[2])
            remove_vpn_only_user(user_id)
            print(f"✅ Пользователь {user_id} удален из VPN-only")
        except ValueError:
            print("❌ user_id должен быть числом")

    elif command == "list":
        users = load_vpn_only_users()
        if not users:
            print("📭 Нет VPN-only пользователей")
            return
        print(f"📋 VPN-only пользователи ({len(users)}):")
        for uid in users:
            auth_status = "✅ авторизован" if is_authorized(uid) else "❌ не авторизован"
            print(f"  • {uid} {auth_status}")

    elif command == "check":
        if len(sys.argv) < 3:
            print("❌ Укажите user_id: python3 admin_vpn_only.py check <user_id>")
            return
        try:
            user_id = int(sys.argv[2])
            is_vpn = is_vpn_only_user(user_id)
            is_auth = is_authorized(user_id)
            print(f"\n👤 Статус пользователя {user_id}:")
            print(f"  • VPN-only: {'✅ Да' if is_vpn else '❌ Нет'}")
            print(f"  • Авторизован: {'✅ Да' if is_auth else '❌ Нет'}")
            if is_vpn and is_auth:
                print("  ⚠️  Пользователь в обоих списках (приоритет: VPN-only)")
            print()
        except ValueError:
            print("❌ user_id должен быть числом")

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
