"""Оркестрация проверки: сдачи → клонирование → данные → проверки → баллы."""
from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .checks import CheckContext, CheckResult, run_check
from .config import Assignment, Course
from .fetch import FetchError, fetch_repo
from .scoring import StudentResult, effective_deadline, finalize
from .submissions import (Student, Submission, read_adjustments, read_extensions, read_roster,
                          read_submissions)


def work_dir(course: Course, assignment: Assignment) -> Path:
    return course.path(course.work_dir) / assignment.id


def inject_data(assignment: Assignment, repo_dir: Path) -> list[str]:
    """Копирует данные из приватной папки в рабочую копию (поверх того, что мог закоммитить студент)."""
    notes = []
    for item in assignment.data:
        src = assignment.private_dir / item["src"]
        dst = repo_dir / item.get("dst", item["src"])
        if not src.exists():
            notes.append(f"нет файла данных {src}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    return notes


def run_checks_on_dir(assignment: Assignment, repo_dir: Path, only_public: bool = False) -> tuple[list[CheckResult], list[str]]:
    inject_data(assignment, repo_dir)
    ctx = CheckContext(repo_dir=repo_dir, assignment=assignment)
    # Сначала выполняем ноутбуки/команды, пересоздающие файлы студента, потом всё, что эти файлы читает.
    # Внутри каждой группы — порядок как в yaml. Явный приоритет можно задать ключом `order` в проверке.
    def order(c):
        default = 0 if c.type in ("notebook_executes", "command") else 1
        return int(c.spec.get("order", default))

    results_by_id: dict[str, CheckResult] = {}
    stopped_by: str | None = None
    for check in sorted(assignment.checks, key=order):
        if only_public and check.visibility != "public":
            continue
        if stopped_by:
            results_by_id[check.id] = CheckResult(check.id, 0.0, check.points, False,
                                                  f"не проверялось: не пройдена проверка «{stopped_by}»",
                                                  manual_review=False)
            continue
        res = run_check(ctx, check)
        results_by_id[check.id] = res
        # on_fail: stop — «ворота»: если эта проверка не пройдена, дальше не проверяем (0 за остальное)
        if check.spec.get("on_fail") == "stop" and not res.passed:
            stopped_by = check.id

    # requires: [ids] — баллы за проверку даются только если все перечисленные проверки пройдены
    # (можно ссылаться и на проверки, идущие позже по списку)
    for check in assignment.checks:
        req = check.spec.get("requires") or []
        res = results_by_id.get(check.id)
        if not res or not req or res.points == 0:
            continue
        failed = [r for r in req if r not in results_by_id or not results_by_id[r].passed]
        if failed:
            res.points = 0.0
            res.passed = False
            res.message += f" — но 0 б., так как не пройдено: {', '.join(failed)}"
    results = [results_by_id[c.id] for c in assignment.checks if c.id in results_by_id]  # отчёт — в порядке yaml
    return results, ctx.log


def grade_one(course: Course, assignment: Assignment, key: str, student: Student | None,
              submission: Submission | None, wd: Path, extensions: list[dict], adjustments: list[dict]) -> StudentResult:
    dl = effective_deadline(course, assignment, key, extensions)
    if student is None and submission is not None:
        student = submission.student
    if submission is None:
        res = StudentResult(key, student, None, "not_submitted", notes=["форма сдачи не заполнена"])
        return finalize(course, assignment, res, dl, adjustments)
    if submission.problems:
        res = StudentResult(key, student, submission, "invalid_submission", notes=list(submission.problems))
        return finalize(course, assignment, res, dl, adjustments)

    repo_dir = wd / "repos" / key
    try:
        fr = fetch_repo(submission.repo_url, submission.sha, repo_dir)
    except FetchError as e:
        res = StudentResult(key, student, submission, "fetch_error", notes=[str(e)])
        return finalize(course, assignment, res, dl, adjustments)
    except Exception as e:
        res = StudentResult(key, student, submission, "fetch_error", notes=[f"{type(e).__name__}: {e}"])
        return finalize(course, assignment, res, dl, adjustments)

    target = repo_dir / submission.subdir if submission.subdir else repo_dir
    if not target.is_dir():
        res = StudentResult(key, student, submission, "invalid_submission",
                            notes=[f"в репозитории нет папки '{submission.subdir}'"], sha=fr.sha)
        return finalize(course, assignment, res, dl, adjustments)

    checks, log = run_checks_on_dir(assignment, target)
    notes = list(log)
    if submission.sha.upper() == "HEAD":
        notes.insert(0, f"коммит не указан — проверен последний {fr.sha[:7]} (дата коммита {fr.committed_at[:16]})")
    if submission.comment:
        notes.append(f"комментарий студента: {submission.comment}")
    res = StudentResult(key, student, submission, "ok", checks=checks, sha=fr.sha, committed_at=fr.committed_at,
                        notes=notes)
    return finalize(course, assignment, res, dl, adjustments)


def grade_assignment(course: Course, assignment: Assignment, submissions_csv: Path | None = None,
                     only: set[str] | None = None, workers: int = 4,
                     progress=None) -> tuple[list[StudentResult], list[str]]:
    roster = read_roster(course)
    csv_path = submissions_csv or assignment.submission_form_csv
    subs, warnings = read_submissions(course, csv_path, roster, assignment_id=assignment.id)
    extensions = read_extensions(course, roster)
    adjustments = read_adjustments(course, roster)

    keys = set(roster) | set(subs)
    if only:
        keys &= {roster.resolve(o) for o in only}
    wd = work_dir(course, assignment)
    (wd / "repos").mkdir(parents=True, exist_ok=True)

    results: list[StudentResult] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(grade_one, course, assignment, key, roster.get(key), subs.get(key),
                            wd, extensions, adjustments): key for key in sorted(keys)}
        for fut in as_completed(futs):
            res = fut.result()
            results.append(res)
            if progress:
                progress(res)
    results.sort(key=lambda r: ((r.student.group if r.student else "~"), (r.student.name if r.student else r.key)))
    return results, warnings
