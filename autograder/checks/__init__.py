"""Типы проверок. Каждая проверка — функция (ctx, check) -> CheckResult.

Пути в спецификации проверки:
  * обычный путь              — относительно репозитория студента (с учётом подпапки)
  * 'private:data/x.csv'      — относительно приватной папки задания
  * 'public:tests_public'     — относительно публичной папки задания
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import Assignment, Check


@dataclass
class CheckContext:
    repo_dir: Path          # рабочая копия студента (уже с подкладанными данными)
    assignment: Assignment
    python: str = "python3"
    log: list[str] = field(default_factory=list)

    def resolve(self, path: str) -> Path:
        if path.startswith("private:"):
            return self.assignment.private_dir / path[len("private:"):]
        if path.startswith("public:"):
            return self.assignment.public_dir / path[len("public:"):]
        return self.repo_dir / path


@dataclass
class CheckResult:
    id: str
    points: float
    max_points: float
    passed: bool
    message: str = ""
    manual_review: bool = False
    details: dict = field(default_factory=dict)

    def to_row(self) -> dict:
        return {"id": self.id, "points": round(self.points, 3), "max_points": self.max_points,
                "passed": self.passed, "message": self.message, "manual_review": self.manual_review}


CheckFn = Callable[[CheckContext, Check], CheckResult]
CHECK_TYPES: dict[str, CheckFn] = {}


def register(name: str):
    def deco(fn: CheckFn) -> CheckFn:
        CHECK_TYPES[name] = fn
        return fn
    return deco


def run_check(ctx: CheckContext, check: Check) -> CheckResult:
    fn = CHECK_TYPES.get(check.type)
    if fn is None:
        return CheckResult(check.id, 0, check.points, False, f"неизвестный тип проверки '{check.type}'")
    try:
        res = fn(ctx, check)
    except subprocess.TimeoutExpired:
        res = CheckResult(check.id, 0, check.points, False, "превышено время выполнения")
    except Exception as e:  # проверка не должна валить весь прогон
        res = CheckResult(check.id, 0, check.points, False, f"ошибка проверки: {type(e).__name__}: {e}")
    res.manual_review = res.manual_review or check.manual_review
    return res


def points_from_scale(value: float, scale: list[dict], higher_is_better: bool = True) -> tuple[float, str]:
    """scale: [{min: 0.8, points: 4}, {min: 0.7, points: 3}, ...] (или max для метрик 'меньше — лучше')."""
    key = "min" if higher_is_better else "max"
    ordered = sorted(scale, key=lambda s: float(s[key]), reverse=higher_is_better)
    for step in ordered:
        thr = float(step[key])
        if (higher_is_better and value >= thr) or (not higher_is_better and value <= thr):
            return float(step["points"]), f"{value:.4f} {'>=' if higher_is_better else '<='} {thr:g}"
    return 0.0, f"{value:.4f} не достигает ни одного порога"


# регистрация встроенных типов
from . import files, notebook, metrics, pytest_check, command  # noqa: E402,F401
