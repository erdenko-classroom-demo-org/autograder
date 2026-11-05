"""Получение репозитория студента на конкретном коммите."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class FetchError(Exception):
    pass


@dataclass
class FetchResult:
    path: Path
    sha: str
    committed_at: str  # ISO-строка даты коммита (информационно; штраф считается по времени формы)


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # не зависать на запросе логина для несуществующего/приватного репозитория
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)


def fetch_repo(url: str, sha: str, dest: Path, timeout: int = 300) -> FetchResult:
    """Клонирует url и переключается на sha. Сначала пытается неглубоко забрать только нужный коммит
    (GitHub это разрешает), при неудаче делает полный клон."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    if sha.upper() == "HEAD":  # коммит не указан — берём последний коммит ветки по умолчанию
        r = _run(["git", "clone", "-q", "--depth", "1", url, str(dest)], timeout=timeout)
        if r.returncode:
            shutil.rmtree(dest, ignore_errors=True)
            raise FetchError(f"не удалось клонировать {url}: {r.stderr.strip()[:300]}")
        full = _run(["git", "rev-parse", "HEAD"], cwd=dest).stdout.strip()
        date = _run(["git", "log", "-1", "--format=%cI"], cwd=dest).stdout.strip()
        shutil.rmtree(dest / ".git", ignore_errors=True)
        return FetchResult(path=dest, sha=full, committed_at=date)

    def shallow() -> bool:
        r = _run(["git", "init", "-q"], cwd=dest, timeout=timeout)
        if r.returncode:
            return False
        _run(["git", "remote", "add", "origin", url], cwd=dest, timeout=timeout)
        r = _run(["git", "fetch", "-q", "--depth", "1", "origin", sha], cwd=dest, timeout=timeout)
        if r.returncode:
            return False
        r = _run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=dest, timeout=timeout)
        return r.returncode == 0

    if not shallow():
        shutil.rmtree(dest)
        r = _run(["git", "clone", "-q", url, str(dest)], timeout=timeout)
        if r.returncode:
            shutil.rmtree(dest, ignore_errors=True)
            raise FetchError(f"не удалось клонировать {url}: {r.stderr.strip()[:300]}")
        r = _run(["git", "checkout", "-q", sha], cwd=dest, timeout=timeout)
        if r.returncode:
            shutil.rmtree(dest, ignore_errors=True)  # не оставлять рабочую копию на «каком-то» коммите
            raise FetchError(f"коммит {sha} не найден в {url}: {r.stderr.strip()[:300]}")

    full = _run(["git", "rev-parse", "HEAD"], cwd=dest).stdout.strip()
    if re.fullmatch(r"[0-9a-fA-F]{7,40}", sha) and not full.startswith(sha.lower()):
        raise FetchError(f"после checkout HEAD={full[:12]}, ожидался {sha}")
    date = _run(["git", "log", "-1", "--format=%cI"], cwd=dest).stdout.strip()
    shutil.rmtree(dest / ".git", ignore_errors=True)  # студенту хватит рабочей копии; история не нужна
    return FetchResult(path=dest, sha=full, committed_at=date)
