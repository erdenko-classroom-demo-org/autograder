"""Итоговый балл: сумма проверок → штраф за опоздание → индивидуальные правки."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime

from .checks import CheckResult
from .config import Assignment, Course, Deadline, parse_dt
from .submissions import Student, Submission


@dataclass
class StudentResult:
    key: str
    student: Student | None
    submission: Submission | None
    status: str  # ok | late | late_hard | not_submitted | fetch_error | invalid_submission
    checks: list[CheckResult] = field(default_factory=list)
    raw: float = 0.0
    penalty: float = 0.0
    days_late: int = 0
    adjustment: float = 0.0
    total: float = 0.0
    notes: list[str] = field(default_factory=list)
    sha: str = ""
    committed_at: str = ""

    @property
    def manual_review(self) -> bool:
        return any(c.manual_review for c in self.checks)


def public_id(course: Course, key: str) -> str:
    """Короткий код студента для публичной таблицы результатов (не раскрывает ни ФИО, ни логин)."""
    return hashlib.sha256(f"{course.salt}:{key.lower()}".encode()).hexdigest()[:8]


def effective_deadline(course: Course, assignment: Assignment, key: str, extensions: list[dict]) -> Deadline:
    dl = assignment.deadline
    soft, hard = dl.soft, dl.hard
    for row in extensions:
        if row["key"] == key and row.get("assignment", "") in ("", assignment.id):
            if row.get("soft"):
                soft = parse_dt(row["soft"], course.tz)
            if row.get("hard"):
                hard = parse_dt(row["hard"], course.tz)
    if hard < soft:
        hard = soft
    return Deadline(soft=soft, hard=hard, penalty_per_day=dl.penalty_per_day)


def lateness(submitted_at: datetime, dl: Deadline) -> tuple[int, bool]:
    """(число начатых суток опоздания после мягкого дедлайна, сдано после жёсткого?)"""
    if submitted_at <= dl.soft:
        return 0, False
    days = math.ceil((submitted_at - dl.soft).total_seconds() / 86400)
    return max(days, 1), submitted_at > dl.hard


def finalize(course: Course, assignment: Assignment, res: StudentResult, dl: Deadline, adjustments: list[dict]) -> StudentResult:
    res.raw = min(sum(c.points for c in res.checks), assignment.max_points)
    res.penalty = 0.0
    if res.submission is not None and res.status in ("ok", "late", "late_hard"):
        days, after_hard = lateness(res.submission.submitted_at, dl)
        res.days_late = days
        if after_hard and course.after_hard_deadline == "zero":
            res.status = "late_hard"
            res.penalty = res.raw
            res.notes.append(f"сдано после жёсткого дедлайна ({dl.hard:%d.%m %H:%M})")
        elif days > 0:
            res.status = "late"
            res.penalty = min(res.raw, days * dl.penalty_per_day)
            res.notes.append(f"опоздание {days} дн. → −{res.penalty:g} б.")
        else:
            res.status = "ok"
    total = res.raw - res.penalty
    set_total = None
    for row in adjustments:
        if row["key"] != res.key or row.get("assignment", "") not in ("", assignment.id):
            continue
        if row.get("set_total", "") != "":
            set_total = float(row["set_total"])
            res.notes.append(f"балл выставлен вручную: {set_total:g} ({row.get('reason', '')})")
        elif row.get("delta", "") != "":
            d = float(row["delta"])
            res.adjustment += d
            res.notes.append(f"правка {d:+g} ({row.get('reason', '')})")
    if set_total is not None:
        total = set_total
    else:
        total += res.adjustment
    res.total = round(max(0.0, total), 2)  # правки могут поднять балл выше max_points (бонус за найденную ошибку)
    return res
