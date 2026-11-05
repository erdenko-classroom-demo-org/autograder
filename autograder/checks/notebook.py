"""Выполнение ноутбука студента «с нуля» в его рабочей копии (данные уже подложены)."""
from __future__ import annotations

import asyncio
import re
import shutil
import tempfile
from pathlib import Path

import nbformat
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError, CellTimeoutError

from . import CheckContext, CheckResult, register
from ..config import Check


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _short_error(err: Exception, limit: int = 400) -> str:
    text = _ANSI.sub("", str(err))
    # у CellExecutionError последняя непустая строка — «ТипИсключения: сообщение», это и показываем студенту
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    tail = lines[-1] if lines else type(err).__name__
    return tail[:limit]


@register("notebook_executes")
def notebook_executes(ctx: CheckContext, check: Check) -> CheckResult:
    """spec:
        path: hw01.ipynb
        timeout: 600                — на весь ноутбук, секунд
        cell_timeout: 300           — на одну ячейку
        kernel: python3
        outputs: [outputs/answers.json, outputs/predictions.csv]
            — файлы, которые ноутбук должен создать. Перед запуском закоммиченные версии
              убираются в сторону; если ноутбук упал — возвращаются обратно, чтобы остальные
              проверки шли по тому, что сдал студент (а эта проверка — 0 баллов).
    """
    nb_path = ctx.resolve(check.spec["path"])
    if not nb_path.is_file():
        return CheckResult(check.id, 0, check.points, False, f"нет ноутбука {check.spec['path']}")

    outputs = [ctx.repo_dir / p for p in check.spec.get("outputs", [])]
    backup = Path(tempfile.mkdtemp(prefix="nb_backup_"))
    saved: dict[Path, Path] = {}
    for p in outputs:
        if p.exists():
            dst = backup / p.name
            shutil.move(str(p), dst)
            saved[p] = dst

    nb = nbformat.read(nb_path, as_version=4)
    client = NotebookClient(
        nb,
        timeout=int(check.spec.get("cell_timeout", 300)),
        kernel_name=check.spec.get("kernel", "python3"),
        resources={"metadata": {"path": str(nb_path.parent)}},
        allow_errors=False,
        extra_arguments=["--IPKernelApp.log_level=ERROR"],  # глушит предупреждения ядра в выводе грейдера
    )
    ok, msg = True, "ноутбук выполнился без ошибок"
    try:
        # проверки идут в пуле потоков, а nbclient работает через asyncio — потоку нужен свой event loop
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        client.execute()
    except CellTimeoutError:
        ok, msg = False, "ячейка выполнялась слишком долго"
    except CellExecutionError as e:
        ok, msg = False, "ошибка при выполнении: " + _short_error(e)
    except Exception as e:  # например, нет ядра
        ok, msg = False, f"не удалось выполнить ноутбук: {type(e).__name__}: {_short_error(e)}"
    finally:
        try:
            dbg = ctx.repo_dir / ".grader"
            dbg.mkdir(exist_ok=True)
            nbformat.write(nb, dbg / "executed.ipynb")
        except Exception:
            pass

    if ok:
        missing = [str(p.relative_to(ctx.repo_dir)) for p in outputs if not p.exists()]
        if missing:
            ok, msg = False, f"ноутбук выполнился, но не создал файлы: {', '.join(missing)}"
    if not ok:
        for p, dst in saved.items():  # вернуть то, что сдал студент
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                shutil.move(str(dst), p)
    shutil.rmtree(backup, ignore_errors=True)
    return CheckResult(check.id, check.points if ok else 0.0, check.points, ok, msg)
