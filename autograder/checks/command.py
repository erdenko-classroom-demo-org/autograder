"""Произвольная команда в рабочей копии студента: сборка Docker-образа, запуск скрипта, линтер и т.п."""
from __future__ import annotations

import os
import shlex
import subprocess

from . import CheckContext, CheckResult, register
from ..config import Check


@register("command")
def command(ctx: CheckContext, check: Check) -> CheckResult:
    """spec:
        cmd: "docker build -t hw-check ."      — строка (shell) или список аргументов
        timeout: 600
        expect_in_output: "OK"                 — опционально: подстрока, которая должна быть в stdout
    Проходит, если код возврата 0 (и подстрока найдена).
    Запускать такие проверки имеет смысл локально или в CI публичного репозитория студента —
    в бесплатных минутах приватного репозитория сборки Docker дорогие.
    """
    cmd = check.spec["cmd"]
    args = shlex.split(cmd) if isinstance(cmd, str) else [str(a) for a in cmd]
    env = dict(os.environ)
    env["GRADER_PRIVATE_DIR"] = str(ctx.assignment.private_dir)
    try:
        proc = subprocess.run(args, cwd=ctx.repo_dir, env=env, capture_output=True, text=True,
                              timeout=int(check.spec.get("timeout", 600)))
    except FileNotFoundError:
        return CheckResult(check.id, 0, check.points, False, f"команда не найдена: {args[0]}")
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        tail = " | ".join(out.strip().splitlines()[-3:])
        return CheckResult(check.id, 0, check.points, False, f"код возврата {proc.returncode}: {tail}"[:400])
    need = check.spec.get("expect_in_output")
    if need and need not in out:
        return CheckResult(check.id, 0, check.points, False, f"в выводе нет '{need}'")
    return CheckResult(check.id, check.points, check.points, True, "команда выполнилась успешно")
