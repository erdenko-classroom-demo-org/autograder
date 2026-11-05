"""Выгрузка результатов: CSV под ведомость, отзыв каждому студенту, публичная таблица по кодам."""
from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from .config import Assignment, Course
from .scoring import StudentResult, public_id

STATUS_RU = {
    "ok": "сдано",
    "late": "сдано с опозданием",
    "late_hard": "после жёсткого дедлайна",
    "not_submitted": "не сдано",
    "fetch_error": "репозиторий/коммит недоступен",
    "invalid_submission": "ошибка в форме сдачи",
}


def results_dir(course: Course, assignment: Assignment) -> Path:
    return course.path(course.results_dir) / assignment.id


def _fmt(x: float) -> str:
    return f"{x:g}"


def _safe(key: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in key)


def _who(r: StudentResult) -> str:
    return r.student.name if r.student and r.student.name else r.key


def write_all(course: Course, assignment: Assignment, results: list[StudentResult], warnings: list[str],
              partial: bool = False) -> Path:
    out = results_dir(course, assignment)
    if partial:  # прогон по части студентов не должен затирать полный результат
        out = out / "partial"
    out.mkdir(parents=True, exist_ok=True)
    (out / "details").mkdir(exist_ok=True)
    now = datetime.now(course.tz)
    check_ids = [c.id for c in assignment.checks]

    # 1. results.csv — строка на студента, колонки под ведомость
    with (out / "results.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["name", "group", "grader", "github", "status", "submitted_at", "sha", "days_late",
                    *check_ids, "raw", "penalty", "adjustment", "total", "manual_review", "notes", "student_key"])
        for r in results:
            s = r.student
            pts = {c.id: c.points for c in r.checks}
            github = (r.submission.github_login if r.submission and r.submission.github_login else (s.login if s else ""))
            w.writerow([
                s.name if s else r.key, s.group if s else "", s.grader if s else "", github,
                STATUS_RU.get(r.status, r.status),
                r.submission.submitted_at.strftime("%d.%m.%Y %H:%M") if r.submission else "",
                r.sha[:7] if r.sha else (r.submission.sha[:7] if r.submission else ""),
                r.days_late or "",
                *[_fmt(pts.get(cid, 0.0)) if r.checks else "" for cid in check_ids],
                _fmt(r.raw), _fmt(r.penalty), _fmt(r.adjustment), _fmt(r.total),
                "да" if r.manual_review and r.checks else "",
                " | ".join(r.notes),
                r.key,
            ])

    # 2. results.json — машиночитаемо, для пересчётов и сравнения прогонов
    (out / "results.json").write_text(json.dumps({
        "assignment": assignment.id, "generated_at": now.isoformat(),
        "deadline": {"soft": assignment.deadline.soft.isoformat(), "hard": assignment.deadline.hard.isoformat(),
                     "penalty_per_day": assignment.deadline.penalty_per_day},
        "results": [{
            "key": r.key, "name": r.student.name if r.student else "", "group": r.student.group if r.student else "",
            "status": r.status, "sha": r.sha, "committed_at": r.committed_at,
            "submitted_at": r.submission.submitted_at.isoformat() if r.submission else None,
            "repo": r.submission.repo_url if r.submission else None,
            "checks": [c.to_row() for c in r.checks], "raw": r.raw, "penalty": r.penalty,
            "days_late": r.days_late, "adjustment": r.adjustment, "total": r.total, "notes": r.notes,
        } for r in results],
        "warnings": warnings,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    # 3. details/<login>.md — развёрнутый отзыв (для апелляций и ручного просмотра)
    for r in results:
        title = r.student.name if r.student and r.student.name else r.key
        lines = [f"# {assignment.title} — {title}", ""]
        if r.student:
            lines.append(f"{r.student.group}" + (f", проверяющий: {r.student.grader}" if r.student.grader else "") + f", код {public_id(course, r.key)}")
        lines.append(f"Статус: **{STATUS_RU.get(r.status, r.status)}**")
        if r.submission:
            lines.append(f"Сдано: {r.submission.submitted_at:%d.%m.%Y %H:%M}, репозиторий {r.submission.repo_url}, коммит `{r.sha[:7] if r.sha else r.submission.sha}`")
        lines.append("")
        if r.checks:
            lines += ["| Проверка | Баллы | Результат |", "|---|---|---|"]
            for c in r.checks:
                flag = " _(нужен просмотр проверяющего)_" if c.manual_review else ""
                mark = "✅" if c.points >= c.max_points - 1e-9 else ("◐" if c.points > 0 else "❌")
                lines.append(f"| {c.id} | {_fmt(c.points)} / {_fmt(c.max_points)} | {mark} {c.message}{flag} |")
            lines.append("")
        lines.append(f"Сумма проверок: {_fmt(r.raw)}; штраф: −{_fmt(r.penalty)}; правки: {r.adjustment:+g}; **итог: {_fmt(r.total)} / {_fmt(assignment.max_points)}**")
        if r.notes:
            lines += ["", "Примечания:"] + [f"- {n}" for n in r.notes]
        (out / "details" / f"{_safe(r.key)}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 4. public_results.md — для публикации студентам (коды вместо логинов)
    pub = [f"# Результаты {assignment.title}", "",
           f"Обновлено {now:%d.%m.%Y %H:%M}. Код — ваш персональный код курса, его выдаёт преподаватель после регистрации.",
           "Колонки проверок: ✅ пройдена, ◐ частично, ❌ нет. Подробный разбор — у проверяющего.", "",
           "| Код | " + " | ".join(check_ids) + " | Опоздание | Итог |",
           "|---|" + "---|" * len(check_ids) + "---|---|"]
    for r in sorted(results, key=lambda r: public_id(course, r.key)):
        if r.status == "not_submitted":
            continue
        code = public_id(course, r.key)
        if r.status in ("fetch_error", "invalid_submission"):
            pub.append(f"| {code} | " + " | ".join(["—"] * len(check_ids)) + " | ошибка в форме сдачи или репозиторий недоступен — напишите преподавателю | **0** |")
            continue
        cells = []
        by_id = {c.id: c for c in r.checks}
        for cid in check_ids:
            c = by_id.get(cid)
            if c is None:
                cells.append("—")
            elif c.points >= c.max_points - 1e-9 and c.max_points > 0:
                cells.append("✅")
            elif c.points > 0:
                cells.append("◐")
            else:
                cells.append("❌")
        if r.status == "late_hard":
            late = "после жёсткого дедлайна"
        elif r.penalty:
            late = f"{r.days_late} дн., −{_fmt(r.penalty)}"
        else:
            late = ""
        pub.append(f"| {code} | " + " | ".join(cells) + f" | {late} | **{_fmt(r.total)}** |")
    (out / "public_results.md").write_text("\n".join(pub) + "\n", encoding="utf-8")

    # 5. summary.md — для проверяющего: распределение и что требует внимания
    statuses = Counter(r.status for r in results)
    graded = [r for r in results if r.checks]
    manual = [r for r in results if r.manual_review and r.checks]
    attention = [r for r in results if r.status in ("fetch_error", "invalid_submission")]
    lines = [f"# {assignment.title} — сводка прогона {now:%d.%m.%Y %H:%M}", "",
             f"Студентов в списке/сдавших: {len(results)} / {len(results) - statuses.get('not_submitted', 0)}", ""]
    lines += [f"- {STATUS_RU.get(k, k)}: {v}" for k, v in statuses.most_common()]
    if graded:
        totals = sorted(r.total for r in graded)
        mean = sum(totals) / len(totals)
        lines += ["", f"Средний балл по проверенным: {mean:.2f} / {_fmt(assignment.max_points)}; медиана {totals[len(totals)//2]:g}; "
                      f"минимум {totals[0]:g}; максимум {totals[-1]:g}", ""]
        lines += ["Доля прошедших каждую проверку:"]
        for cid in check_ids:
            n_full = sum(1 for r in graded for c in r.checks if c.id == cid and c.passed)
            lines.append(f"- {cid}: {n_full}/{len(graded)}")
    if manual:
        lines += ["", f"Нужен просмотр проверяющего ({len(manual)}): " + ", ".join(_who(r) for r in manual)]
    if attention:
        lines += ["", "Проблемы со сдачей (написать студентам):"]
        lines += [f"- {_who(r)}: {'; '.join(r.notes)}" for r in attention]
    if warnings:
        lines += ["", "Предупреждения при чтении формы:"] + [f"- {w}" for w in warnings]
    lines += ["", "Файлы: `results.csv` (вставить в ведомость), `details/` (отзывы), `public_results.md` (опубликовать студентам), `plagiarism/` (после `grader plagiarism`)."]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
