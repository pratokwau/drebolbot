# handlers/migration.py
import os
import json
import asyncio
import tempfile
import shutil
from pathlib import Path
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from config import ADMIN_ID
from handlers.utils import no_access_reply, no_access_callback
from states.states import MigrationStates
from loader import is_authorized

router = Router()

# Путь к проекту
PROJECT_ROOT = Path(__file__).parent.parent

# Файлы и папки для миграции
MIGRATION_ITEMS = [
    "main.py",
    "config.py",
    "database.py",
    "loader.py",
    "update_manager.py",
    "base_store.py",
    "requirements.txt",
    "install.py",
    "install.sh",
    "handlers",
    "states",
    "middlewares",
    "FunPayAPI",
    "data"
]


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


@router.message(Command("migrate"))
async def cmd_migrate(message: types.Message):
    if not is_admin(message.from_user.id):
        await no_access_reply(message)
        return
    
    await message.answer(
        "🚀 <b>Миграция на новый сервер</b>\n\n"
        "Эта функция перенесет бота на другой сервер.\n"
        "Вам понадобятся:\n"
        "• IP-адрес нового сервера\n"
        "• Пароль root-пользователя\n\n"
        "Введите IP-адрес нового сервера:",
        parse_mode=ParseMode.HTML
    )
    await message.answer("❌ Для отмены введите /cancel")


@router.callback_query(F.data == "admin_migrate")
async def cb_admin_migrate(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await no_access_callback(callback)
        return
    
    await callback.message.edit_text(
        "🚀 <b>Миграция на новый сервер</b>\n\n"
        "Эта функция перенесет бота на другой сервер.\n"
        "Вам понадобятся:\n"
        "• IP-адрес нового сервера\n"
        "• Пароль root-пользователя\n\n"
        "Введите IP-адрес нового сервера:",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(MigrationStates.waiting_for_ip)
    await callback.answer()


@router.message(MigrationStates.waiting_for_ip)
async def process_ip(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    
    ip = message.text.strip()
    
    # Простая проверка IP
    parts = ip.split('.')
    if len(parts) != 4:
        await message.answer(
            "❌ Неверный формат IP-адреса.\n"
            "Пример: 192.168.1.1\n\n"
            "Попробуйте снова:"
        )
        return
    
    try:
        for part in parts:
            num = int(part)
            if num < 0 or num > 255:
                raise ValueError
    except ValueError:
        await message.answer(
            "❌ Неверный формат IP-адреса.\n"
            "Пример: 192.168.1.1\n\n"
            "Попробуйте снова:"
        )
        return
    
    await state.update_data(server_ip=ip)
    
    await message.answer(
        f"✅ IP-адрес: <code>{ip}</code>\n\n"
        "Теперь введите пароль root-пользователя нового сервера:",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(MigrationStates.waiting_for_root_password)


@router.message(MigrationStates.waiting_for_root_password)
async def process_password(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    
    password = message.text.strip()
    
    if len(password) < 4:
        await message.answer(
            "❌ Пароль слишком короткий.\n"
            "Попробуйте снова:"
        )
        return
    
    await state.update_data(root_password=password)
    data = await state.get_data()
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить миграцию", callback_data="confirm_migration")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_migration")]
    ])
    
    await message.answer(
        f"📋 <b>Подтверждение миграции</b>\n\n"
        f"IP-адрес: <code>{data['server_ip']}</code>\n"
        f"Пароль root: <code>{'•' * len(password)}</code>\n\n"
        f"Будут перенесены:\n"
        f"• Все файлы бота\n"
        f"• База данных\n"
        f"• Конфигурация\n"
        f"• Настроена автозагрузка\n\n"
        f"После миграции бот на этом сервере остановится.\n"
        f"Для запуска на новом сервере введите команду:\n"
        f"<code>systemctl start drebolbot</code>\n\n"
        f"Продолжить?",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )
    await state.set_state(MigrationStates.waiting_for_confirmation)


@router.callback_query(F.data == "cancel_migration")
async def cb_cancel_migration(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "❌ Миграция отменена.",
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_migration")
async def cb_confirm_migration(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await no_access_callback(callback)
        return
    
    data = await state.get_data()
    server_ip = data.get('server_ip')
    root_password = data.get('root_password')
    
    if not server_ip or not root_password:
        await callback.message.edit_text(
            "❌ Ошибка: не хватает данных для миграции.",
            parse_mode=ParseMode.HTML
        )
        await state.clear()
        await callback.answer()
        return
    
    await callback.message.edit_text(
        "🚀 <b>Начинаю миграцию...</b>\n\n"
        "Это может занять несколько минут.\n"
        "Не отключайте бота!",
        parse_mode=ParseMode.HTML
    )
    
    # Запускаем миграцию в отдельной задаче
    asyncio.create_task(perform_migration(callback, server_ip, root_password))
    await state.clear()
    await callback.answer()


async def perform_migration(callback: types.CallbackQuery, server_ip: str, root_password: str):
    """Выполняет миграцию бота на новый сервер"""
    try:
        # Импортируем paramiko динамически
        import paramiko
        
        # Подключаемся к новому серверу
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        await callback.message.edit_text(
            "🔌 Подключаюсь к новому серверу...",
            parse_mode=ParseMode.HTML
        )
        
        ssh.connect(
            hostname=server_ip,
            username='root',
            password=root_password,
            timeout=30
        )
        
        await callback.message.edit_text(
            "✅ Подключено!\n\n"
            "📦 Создаю структуру проекта...",
            parse_mode=ParseMode.HTML
        )
        
        # Создаем директорию на новом сервере
        ssh.exec_command('mkdir -p /root/drebolbot')
        
        # Передаем файлы
        sftp = ssh.open_sftp()
        
        total_items = len(MIGRATION_ITEMS)
        current_item = 0
        
        for item in MIGRATION_ITEMS:
            current_item += 1
            source_path = PROJECT_ROOT / item
            dest_path = f"/root/drebolbot/{item}"
            
            if not source_path.exists():
                continue
            
            try:
                if source_path.is_dir():
                    # Передаем директорию рекурсивно
                    await transfer_directory(sftp, str(source_path), dest_path)
                else:
                    # Передаем файл
                    sftp.put(str(source_path), dest_path)
                
                progress = int((current_item / total_items) * 100)
                await callback.message.edit_text(
                    f"📦 Передаю файлы... {progress}%\n\n"
                    f"Текущий: {item}",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                print(f"Ошибка при передаче {item}: {e}")
        
        sftp.close()
        
        await callback.message.edit_text(
            "✅ Файлы переданы!\n\n"
            "📦 Устанавливаю зависимости...",
            parse_mode=ParseMode.HTML
        )
        
        # Устанавливаем зависимости
        stdin, stdout, stderr = ssh.exec_command(
            'cd /root/drebolbot && pip3 install -r requirements.txt',
            timeout=300
        )
        stdout.read()
        
        await callback.message.edit_text(
            "✅ Зависимости установлены!\n\n"
            "⚙️ Настраиваю автозагрузку...",
            parse_mode=ParseMode.HTML
        )
        
        # Создаем systemd сервис
        service_content = f"""[Unit]
Description=Drebol Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/drebolbot
ExecStart=/usr/bin/python3 /root/drebolbot/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""
        
        stdin, stdout, stderr = ssh.exec_command('cat > /etc/systemd/system/drebolbot.service', timeout=10)
        stdin.write(service_content)
        stdin.channel.shutdown_write()
        stdout.read()
        
        # Перезагружаем systemd и включаем автозагрузку
        ssh.exec_command('systemctl daemon-reload')
        ssh.exec_command('systemctl enable drebolbot')
        
        await callback.message.edit_text(
            "✅ Автозагрузка настроена!\n\n"
            "🔐 Запускаю первоначальную настройку...",
            parse_mode=ParseMode.HTML
        )
        
        # Запускаем install.py для первоначальной настройки
        stdin, stdout, stderr = ssh.exec_command(
            'cd /root/drebolbot && python3 install.py',
            timeout=60
        )
        
        # Отправляем данные для настройки
        # Читаем .env с текущего сервера
        env_path = PROJECT_ROOT / '.env'
        if env_path.exists():
            with open(env_path, 'r') as f:
                env_content = f.read()
            
            # Парсим значения
            env_vars = {}
            for line in env_content.split('\n'):
                if '=' in line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    env_vars[key.strip()] = value.strip()
            
            # Отправляем данные в install.py
            stdin.write(env_vars.get('TOKEN', '') + '\n')
            stdin.write(env_vars.get('ADMIN_ID', '') + '\n')
            stdin.channel.shutdown_write()
            stdout.read()
        
        await callback.message.edit_text(
            "✅ Настройка завершена!\n\n"
            "🛑 Останавливаю бота на этом сервере...",
            parse_mode=ParseMode.HTML
        )
        
        # Копируем .env если он есть
        if env_path.exists():
            try:
                sftp = ssh.open_sftp()
                sftp.put(str(env_path), '/root/drebolbot/.env')
                sftp.close()
            except Exception as e:
                print(f"Ошибка при копировании .env: {e}")
        
        # Копируем authorized.json если он есть
        auth_path = PROJECT_ROOT / 'authorized.json'
        if auth_path.exists():
            try:
                sftp = ssh.open_sftp()
                sftp.put(str(auth_path), '/root/drebolbot/authorized.json')
                sftp.close()
            except Exception as e:
                print(f"Ошибка при копировании authorized.json: {e}")
        
        ssh.close()
        
        # Останавливаем бота на текущем сервере
        await callback.message.edit_text(
            "✅ <b>Миграция завершена!</b>\n\n"
            "Бот перенесен на новый сервер.\n"
            "Автозагрузка настроена.\n\n"
            "🛑 <b>Для остановки бота на этом сервере введите:</b>\n"
            "<code>systemctl stop drebolbot</code>\n\n"
            "🚀 <b>Для запуска на новом сервере введите:</b>\n"
            f"<code>ssh root@{server_ip} 'systemctl start drebolbot'</code>\n\n"
            "Или подключитесь к новому серверу и выполните:\n"
            "<code>systemctl start drebolbot</code>",
            parse_mode=ParseMode.HTML
        )
        
    except ImportError:
        await callback.message.edit_text(
            "❌ <b>Ошибка</b>\n\n"
            "Для миграции необходим модуль paramiko.\n"
            "Установите его командой:\n"
            "<code>pip3 install paramiko</code>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await callback.message.edit_text(
            f"❌ <b>Ошибка миграции</b>\n\n"
            f"<code>{str(e)}</code>\n\n"
            f"Проверьте:\n"
            f"• Правильность IP-адреса\n"
            f"• Доступность сервера\n"
            f"• Правильность пароля root",
            parse_mode=ParseMode.HTML
        )


async def transfer_directory(sftp, local_dir: str, remote_dir: str):
    """Рекурсивно передает директорию"""
    try:
        sftp.stat(remote_dir)
    except FileNotFoundError:
        sftp.mkdir(remote_dir)
    
    for item in os.listdir(local_dir):
        local_path = os.path.join(local_dir, item)
        remote_path = f"{remote_dir}/{item}"
        
        if os.path.isdir(local_path):
            # Пропускаем __pycache__ и .git
            if item in ['__pycache__', '.git', '.gitignore']:
                continue
            await transfer_directory(sftp, local_path, remote_path)
        else:
            sftp.put(local_path, remote_path)
