from __future__ import annotations

import json
import subprocess
from pathlib import Path
import os


ROOT_DIR = Path(__file__).resolve().parent
SERVICE_NAME = "drebolbot"
ADMIN_CHAT_ID_FILE = ROOT_DIR / "data" / "admin_chat_id.txt"
RESTART_NOTICE_FILE = ROOT_DIR / "data" / "restart_notice.json"


def _run_git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(ROOT_DIR), *args],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )


def _current_branch() -> str | None:
    res = _run_git("rev-parse", "--abbrev-ref", "HEAD")
    branch = (res.stdout or "").strip()
    return branch if branch and branch != "HEAD" else None


def _local_commit() -> str | None:
    res = _run_git("rev-parse", "HEAD")
    commit = (res.stdout or "").strip()
    return commit or None


def _remote_commit(branch: str) -> str | None:
    res = _run_git("ls-remote", "origin", f"refs/heads/{branch}")
    if res.returncode != 0:
        return None
    line = (res.stdout or "").strip().splitlines()
    if not line:
        return None
    return line[0].split()[0]


def get_update_status() -> tuple[str, bool]:
    branch = _current_branch()
    if not branch:
        return ("н/д", False)

    local = _local_commit()
    remote = _remote_commit(branch)
    if not local or not remote:
        return ("н/д", False)
    if local == remote:
        return ("нет новой версии", False)
    return ("есть новая версия", True)


def update_from_git() -> tuple[bool, str]:
    branch = _current_branch()
    if not branch:
        return False, "Не удалось определить ветку"

    fetch = _run_git("fetch", "origin", branch)
    if fetch.returncode != 0:
        return False, (fetch.stderr or fetch.stdout or "git fetch failed").strip()

    reset = _run_git("reset", "--hard", f"origin/{branch}")
    if reset.returncode != 0:
        return False, (reset.stderr or reset.stdout or "git reset failed").strip()

    return True, "Обновление завершено"


def restart_service() -> None:
    subprocess.Popen(
        ["systemctl", "restart", SERVICE_NAME],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def save_admin_chat_id(chat_id: int) -> None:
    try:
        ADMIN_CHAT_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        ADMIN_CHAT_ID_FILE.write_text(str(int(chat_id)), encoding="utf-8")
    except Exception:
        pass


def load_admin_chat_id() -> int | None:
    try:
        if not ADMIN_CHAT_ID_FILE.exists():
            return None
        raw = ADMIN_CHAT_ID_FILE.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except Exception:
        return None


def save_restart_notice(chat_id: int, text: str) -> None:
    try:
        RESTART_NOTICE_FILE.parent.mkdir(parents=True, exist_ok=True)
        RESTART_NOTICE_FILE.write_text(
            json.dumps({"chat_id": int(chat_id), "text": text}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def load_restart_notice() -> dict | None:
    try:
        if not RESTART_NOTICE_FILE.exists():
            return None
        raw = RESTART_NOTICE_FILE.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        return None


def clear_restart_notice() -> None:
    try:
        if RESTART_NOTICE_FILE.exists():
            RESTART_NOTICE_FILE.unlink()
    except Exception:
        pass
