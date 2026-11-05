"""Структурные проверки файлов: наличие, длина текста, значения в JSON, колонки CSV."""
from __future__ import annotations

import json
import math

from . import CheckContext, CheckResult, register
from ..config import Check


@register("file_exists")
def file_exists(ctx: CheckContext, check: Check) -> CheckResult:
    """spec: path (или paths: [..]) — все файлы должны существовать и быть непустыми."""
    paths = check.spec.get("paths") or [check.spec["path"]]
    missing = [p for p in paths if not ctx.resolve(p).is_file() or ctx.resolve(p).stat().st_size == 0]
    if missing:
        return CheckResult(check.id, 0, check.points, False, f"нет файла(ов): {', '.join(missing)}")
    return CheckResult(check.id, check.points, check.points, True, "файлы на месте")


@register("file_min_length")
def file_min_length(ctx: CheckContext, check: Check) -> CheckResult:
    """spec: path, min_chars (без пробелов). Обычно вместе с manual_review: true."""
    p = ctx.resolve(check.spec["path"])
    if not p.is_file():
        return CheckResult(check.id, 0, check.points, False, f"нет файла {check.spec['path']}")
    text = p.read_text(encoding="utf-8", errors="replace")
    n = len("".join(text.split()))
    need = int(check.spec.get("min_chars", 1))
    if n < need:
        return CheckResult(check.id, 0, check.points, False, f"в {check.spec['path']} {n} символов, нужно не меньше {need}")
    return CheckResult(check.id, check.points, check.points, True, f"{n} символов")


def _close(actual, expected, rel_tol: float, abs_tol: float) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual == expected
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isclose(float(actual), float(expected), rel_tol=rel_tol, abs_tol=abs_tol)
    if isinstance(expected, str) and isinstance(actual, str):
        return actual.strip().lower() == expected.strip().lower()
    return actual == expected


@register("json_values")
def json_values(ctx: CheckContext, check: Check) -> CheckResult:
    """Сравнение значений в JSON студента с эталоном.

    spec:
      path: outputs/answers.json
      expected: private:tests_hidden/expected.json   (файл) или словарь прямо в yaml
      keys: [..]            — какие ключи проверять (по умолчанию все ключи эталона)
      rel_tol: 0.01, abs_tol: 0.0
      partial: true         — баллы делятся поровну между ключами (иначе всё или ничего)
    """
    p = ctx.resolve(check.spec["path"])
    if not p.is_file():
        return CheckResult(check.id, 0, check.points, False, f"нет файла {check.spec['path']}")
    try:
        actual = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return CheckResult(check.id, 0, check.points, False, f"{check.spec['path']} не является корректным JSON: {e}")
    exp = check.spec["expected"]
    if isinstance(exp, str):
        exp = json.loads(ctx.resolve(exp).read_text(encoding="utf-8"))
    keys = check.spec.get("keys") or list(exp.keys())
    rel = float(check.spec.get("rel_tol", 0.01))
    abs_ = float(check.spec.get("abs_tol", 0.0))
    ok, bad = [], []
    for k in keys:
        if k not in actual:
            bad.append(f"{k}: отсутствует")
        elif _close(actual[k], exp[k], rel, abs_):
            ok.append(k)
        else:
            bad.append(f"{k}: получено {actual[k]!r}")
    if check.spec.get("partial", True):
        pts = check.points * len(ok) / max(len(keys), 1)
    else:
        pts = check.points if not bad else 0.0
    msg = f"верно {len(ok)}/{len(keys)}" + (f"; неверно: {'; '.join(bad)}" if bad else "")
    return CheckResult(check.id, pts, check.points, not bad, msg, details={"ok": ok, "bad": bad})


@register("csv_columns")
def csv_columns(ctx: CheckContext, check: Check) -> CheckResult:
    """spec: path, columns: [..], min_rows (опц.), no_nan: true (опц.)"""
    import pandas as pd

    p = ctx.resolve(check.spec["path"])
    if not p.is_file():
        return CheckResult(check.id, 0, check.points, False, f"нет файла {check.spec['path']}")
    try:
        df = pd.read_csv(p)
    except Exception as e:
        return CheckResult(check.id, 0, check.points, False, f"не читается как CSV: {e}")
    problems = []
    missing = [c for c in check.spec.get("columns", []) if c not in df.columns]
    if missing:
        problems.append(f"нет колонок {missing}")
    if "min_rows" in check.spec and len(df) < int(check.spec["min_rows"]):
        problems.append(f"строк {len(df)} < {check.spec['min_rows']}")
    if check.spec.get("no_nan") and df.isna().any().any():
        problems.append("есть пропуски")
    if problems:
        return CheckResult(check.id, 0, check.points, False, "; ".join(problems))
    return CheckResult(check.id, check.points, check.points, True, f"{len(df)} строк, колонки на месте")
