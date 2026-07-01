from __future__ import annotations

import asyncio
import os
import shlex
import tarfile
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

import paramiko


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET_DIR = "/root/drebol-vpn"
DEFAULT_SERVICE_NAME = "drebol-vpn"
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


@dataclass
class MigrationTarget:
    host: str
    port: int = 22
    username: str = "root"
    password: str = ""
    target_dir: str = DEFAULT_TARGET_DIR
    service_name: str = DEFAULT_SERVICE_NAME


@dataclass
class MigrationResult:
    ok: bool
    message: str
    commands: list[str]


def _should_include(path: Path) -> bool:
    relative = path.relative_to(PROJECT_ROOT)
    if any(part in EXCLUDED_PARTS for part in relative.parts):
        return False
    if path.suffix in EXCLUDED_SUFFIXES:
        return False
    return True


def _build_archive() -> Path:
    temp_file = NamedTemporaryFile(prefix="drebol-migration-", suffix=".tar.gz", delete=False)
    temp_file.close()
    archive_path = Path(temp_file.name)
    with tarfile.open(archive_path, "w:gz") as archive:
        for root, dirs, files in os.walk(PROJECT_ROOT):
            root_path = Path(root)
            dirs[:] = [
                item
                for item in dirs
                if _should_include(root_path / item)
            ]
            for file_name in files:
                path = root_path / file_name
                if not _should_include(path):
                    continue
                archive.add(path, arcname=path.relative_to(PROJECT_ROOT))
    return archive_path


def _write_remote_file(sftp: paramiko.SFTPClient, remote_path: str, content: str, mode: int = 0o644) -> None:
    with sftp.file(remote_path, "w") as remote_file:
        remote_file.write(content)
    sftp.chmod(remote_path, mode)


def _exec_blocking(ssh: paramiko.SSHClient, command: str) -> tuple[int, str, str]:
    stdin, stdout, stderr = ssh.exec_command(command)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return exit_status, out, err


def _remote_service_content(target_dir: str, service_name: str) -> str:
    target_dir_q = shlex.quote(target_dir)
    python_exec = f"{target_dir}/.venv/bin/python"
    main_py = f"{target_dir}/main.py"
    return (
        "[Unit]\n"
        "Description=Drebol Telegram Bot\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={target_dir_q}\n"
        f"ExecStart={python_exec} {main_py}\n"
        "Restart=always\n"
        "RestartSec=3\n"
        "Environment=PYTHONUNBUFFERED=1\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


async def migrate_bot_to_server(target: MigrationTarget, *, progress_cb=None) -> MigrationResult:
    if not target.host.strip():
        return MigrationResult(False, "Хост не задан.", [])
    if not target.password:
        return MigrationResult(False, "Пароль не задан.", [])

    async def progress(text: str) -> None:
        if progress_cb:
            await progress_cb(text)

    await progress("📦 <b>Собираю архив проекта...</b>")
    archive_path = await asyncio.to_thread(_build_archive)
    remote_bundle = f"/tmp/{target.service_name}-bundle.tar.gz"
    remote_install_dir = target.target_dir
    commands: list[str] = []

    try:
        await progress("🔌 <b>Подключаюсь к новому серверу...</b>")

        def _run() -> MigrationResult:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=target.host.strip(),
                port=int(target.port or 22),
                username=target.username.strip() or "root",
                password=target.password,
                look_for_keys=False,
                allow_agent=False,
                timeout=20,
            )
            try:
                sftp = ssh.open_sftp()
                try:
                    commands.append(f"mkdir -p {remote_install_dir}")
                    _exec_blocking(ssh, f"mkdir -p {shlex.quote(remote_install_dir)}")
                    commands.append(f"upload {archive_path.name} -> {remote_bundle}")
                    sftp.put(str(archive_path), remote_bundle)
                    commands.append(f"tar -xzf {remote_bundle} -C {remote_install_dir}")
                    exit_status, out, err = _exec_blocking(
                        ssh,
                        f"tar -xzf {shlex.quote(remote_bundle)} -C {shlex.quote(remote_install_dir)}",
                    )
                    if exit_status != 0:
                        return MigrationResult(False, f"Не удалось распаковать архив: {err or out}".strip(), commands)

                    commands.append("install python3/python3-venv/python3-pip")
                    exit_status, out, err = _exec_blocking(
                        ssh,
                        "apt-get update && apt-get install -y python3 python3-venv python3-pip",
                    )
                    if exit_status != 0:
                        return MigrationResult(False, f"Не удалось установить системные пакеты: {err or out}".strip(), commands)

                    commands.append("python3 -m venv .venv")
                    exit_status, out, err = _exec_blocking(
                        ssh,
                        f"cd {shlex.quote(remote_install_dir)} && python3 -m venv .venv",
                    )
                    if exit_status != 0:
                        return MigrationResult(False, f"Не удалось создать virtualenv: {err or out}".strip(), commands)

                    commands.append("pip install -r requirements.txt")
                    exit_status, out, err = _exec_blocking(
                        ssh,
                        f"cd {shlex.quote(remote_install_dir)} && .venv/bin/python -m pip install --upgrade pip setuptools wheel && .venv/bin/python -m pip install -r requirements.txt",
                    )
                    if exit_status != 0:
                        return MigrationResult(False, f"Не удалось установить зависимости: {err or out}".strip(), commands)

                    service_path = f"/etc/systemd/system/{target.service_name}.service"
                    _write_remote_file(sftp, service_path, _remote_service_content(remote_install_dir, target.service_name), 0o644)
                    commands.append(f"write {service_path}")

                    exit_status, out, err = _exec_blocking(ssh, "systemctl daemon-reload")
                    if exit_status != 0:
                        return MigrationResult(False, f"Не удалось перечитать systemd: {err or out}".strip(), commands)

                    exit_status, out, err = _exec_blocking(ssh, f"systemctl enable {shlex.quote(target.service_name)}")
                    if exit_status != 0:
                        return MigrationResult(False, f"Не удалось включить автозапуск: {err or out}".strip(), commands)

                    exit_status, out, err = _exec_blocking(ssh, f"systemctl restart {shlex.quote(target.service_name)}")
                    if exit_status != 0:
                        return MigrationResult(False, f"Не удалось запустить сервис: {err or out}".strip(), commands)

                    commands.append(f"systemctl enable/restart {target.service_name}")
                    _exec_blocking(ssh, f"rm -f {shlex.quote(remote_bundle)}")
                    return MigrationResult(True, "Перенос завершён успешно.", commands)
                finally:
                    sftp.close()
            finally:
                ssh.close()

        await progress("⏳ <b>Выполняю удалённую установку...</b>")
        return await asyncio.to_thread(_run)
    finally:
        try:
            os.unlink(archive_path)
        except Exception:
            pass
