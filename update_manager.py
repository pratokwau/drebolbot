from __future__ import annotations

import subprocess
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
SERVICE_NAME = "drebolbot"


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
