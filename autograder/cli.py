"""Командная строка: grader <команда> [аргументы].

Запускать из корня приватного репозитория курса (или передать --private). Публичный репозиторий
ищется по course.yaml → public_repo_path (или переменная GRADER_PUBLIC).
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, list_assignments, load_assignment, load_course, validate_assignment


def _course(args):
    import os

    if args.public:
        os.environ["GRADER_PUBLIC"] = str(Path(args.public).resolve())
    private = Path(args.private)
    if not (private / "course.yaml").exists() and args.public and args.cmd == "validate":
        # запуск из публичного репозитория без приватного (CI публичного репозитория): проверяем только описания
        import tempfile

        from .config import Course

        return Course(name="public-only", root=Path(tempfile.mkdtemp(prefix="grader_public_")))
    try:
        course = load_course(private)
    except ConfigError as e:
        sys.exit(f"Ошибка конфигурации: {e}")
    if not (course.public_root / "assignments").exists():
        sys.exit(f"Не найден публичный репозиторий курса: {course.public_root} (поправьте public_repo_path в course.yaml или --public)")
    return course


def _assignment(course, aid):
    try:
        return load_assignment(course, aid)
    except ConfigError as e:
        sys.exit(f"Ошибка в описании задания {aid}: {e}")


def cmd_run(args):
    from .report import write_all
    from .runner import grade_assignment

    course = _course(args)
    a = _assignment(course, args.assignment)
    print(f"{a.title}: мягкий дедлайн {a.deadline.soft:%d.%m.%Y %H:%M}, жёсткий {a.deadline.hard:%d.%m.%Y %H:%M}, "
          f"штраф {a.deadline.penalty_per_day:g} б./день, проверок {len(a.checks)}, максимум {a.max_points:g} б.")

    def progress(r):
        who = (r.student.name if r.student and r.student.name else r.key)[:24]
        print(f"  {who:<24} {r.status:<20} {r.total:>5g}" + (f"  ({'; '.join(r.notes)[:80]})" if r.notes else ""))

    results, warnings = grade_assignment(course, a, Path(args.submissions) if args.submissions else None,
                                         only=set(args.only) if args.only else None, workers=args.workers,
                                         progress=progress)
    out = write_all(course, a, results, warnings, partial=bool(args.only))
    for w in warnings:
        print("  ! " + w)
    print(f"\nГотово: {len(results)} студентов. Результаты в {out}")
    if args.only:
        print("  (прогон по части студентов: записан в подпапку partial/, полный результат не тронут)")
    print(f"  results.csv — в ведомость, summary.md — сводка, details/ — отзывы, public_results.md — студентам")
    if args.clean:
        shutil.rmtree(course.path(course.work_dir) / a.id / "repos", ignore_errors=True)


def cmd_plagiarism(args):
    from . import plagiarism
    from .fetch import fetch_repo
    from .runner import work_dir
    from .submissions import read_roster, read_submissions

    course = _course(args)
    a = _assignment(course, args.assignment)
    files = args.files or a.plagiarism_files
    if not files:
        sys.exit("В grading.yaml не задан раздел plagiarism.files и не передан --files")
    wd = work_dir(course, a) / "repos"
    roster = read_roster(course)
    subs, _ = read_submissions(course, a.submission_form_csv, roster, assignment_id=a.id)
    repos = {}
    for key, sub in sorted(subs.items()):
        if sub.problems:
            continue
        d = wd / key
        if not d.exists():
            try:
                fetch_repo(sub.repo_url, sub.sha, d)
            except Exception as e:
                print(f"  {sub.student.name or key}: не удалось получить репозиторий ({e})")
                continue
        label = sub.student.name or key
        repos[label] = d / sub.subdir if sub.subdir else d
    out = course.path(course.results_dir) / a.id / "plagiarism"
    info = plagiarism.run(course, a.id, repos, files, out)
    print(f"Сравнено {info['compared']} решений, подозрительных пар: {len(info['suspicious'])}. Отчёт: {out / 'report.md'}")
    for p in info["suspicious"]:
        print(f"  ⚠️ {p.a} ~ {p.b}: {p.score:.2f}")
    if info["jplag"]:
        print("  " + info["jplag"])


def cmd_selftest(args):
    """Эталон должен набирать максимум, голый стартер — (почти) ничего."""
    from .runner import run_checks_on_dir

    course = _course(args)
    ids = [args.assignment] if args.assignment else list_assignments(course)
    failed = False
    for aid in ids:
        a = _assignment(course, aid)
        problems = validate_assignment(course, aid)
        for p in problems:
            print(f"[{aid}] конфигурация: {p}")
        failed |= bool(problems)
        wd = course.path(course.work_dir) / aid / "selftest"
        for label, src, expect_full in (("эталон", a.solution_dir, True), ("стартер", a.starter_dir, False)):
            if not src.exists():
                print(f"[{aid}] нет папки {label}: {src}")
                failed |= expect_full
                continue
            dst = wd / label
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git", "data", "__pycache__"))
            results, log = run_checks_on_dir(a, dst)
            total = sum(r.points for r in results)
            print(f"[{aid}] {label}: {total:g} / {a.max_points:g}")
            for r in results:
                mark = "✅" if r.passed else "❌"
                print(f"      {mark} {r.id:<22} {r.points:>5g}/{r.max_points:<4g} {r.message[:110]}")
            for ln in log:
                print(f"      ! {ln}")
            if expect_full and abs(total - a.max_points) > 1e-9:
                print(f"[{aid}] ОШИБКА: эталонное решение не набирает максимум")
                failed = True
            if not expect_full and total > a.max_points * 0.3:
                print(f"[{aid}] ПРЕДУПРЕЖДЕНИЕ: пустой стартер набирает {total:g} б. — проверки слишком мягкие?")
    sys.exit(1 if failed else 0)


def cmd_validate(args):
    course = _course(args)
    ids = [args.assignment] if args.assignment else list_assignments(course)
    bad = 0
    for aid in ids:
        problems = validate_assignment(course, aid)
        if problems:
            bad += 1
            print(f"[{aid}] ✗")
            for p in problems:
                print(f"    - {p}")
        else:
            print(f"[{aid}] ✓")
    sys.exit(1 if bad else 0)


def cmd_roster_check(args):
    """Кто не сдал, у кого проблемы в форме, доступен ли репозиторий — без запуска проверок."""
    import os
    import subprocess

    from .submissions import read_roster, read_submissions

    course = _course(args)
    a = _assignment(course, args.assignment)
    roster = read_roster(course)
    subs, warnings = read_submissions(course, a.submission_form_csv, roster, assignment_id=a.id)
    for w in warnings:
        print("  ! " + w)
    def who(key):
        st = roster.get(key) or (subs[key].student if key in subs else None)
        return (st.name if st and st.name else key)

    missing = sorted(set(roster) - set(subs))
    unknown = sorted(set(subs) - set(roster))
    print(f"В списке {len(roster)}, сдали {len(subs)}, не сдали {len(missing)}, не из списка {len(unknown)}")
    if missing:
        print("Не сдали: " + ", ".join(who(k) for k in missing))
    if unknown:
        print("Не найдены в roster.csv: " + ", ".join(who(k) for k in unknown))
    for key, s in sorted(subs.items()):
        login = who(key)
        probs = list(s.problems)
        if not probs and args.ping:
            r = subprocess.run(["git", "ls-remote", "--exit-code", s.repo_url, "HEAD"], capture_output=True, text=True, timeout=60,
                               env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
            if r.returncode:
                probs.append("репозиторий недоступен (приватный? опечатка?)")
        if probs:
            print(f"  {login}: {'; '.join(probs)}")


def cmd_codes(args):
    """Персональные коды студентов для публичной таблицы результатов."""
    from .scoring import public_id
    from .submissions import read_roster

    course = _course(args)
    roster = read_roster(course)
    w = csv.writer(sys.stdout, delimiter=";")
    w.writerow(["name", "group", "email", "login", "code"])
    for s in sorted(roster.values(), key=lambda s: (s.group, s.name)):
        w.writerow([s.name, s.group, s.email, s.login, public_id(course, s.key)])


def cmd_new(args):
    from .scaffold import create_assignment

    course = _course(args)
    created = create_assignment(course, args.assignment, args.title or args.assignment)
    print("Создано:")
    for p in created:
        print("  " + str(p))
    print("Дальше: заполните README.md и starter/ в публичном репозитории, grading.yaml, solution/ и tests_hidden/ в приватном,"
          " затем `grader selftest " + args.assignment + "`.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="grader", description="Автопроверка заданий курса")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--private", default=".", help="корень приватного репозитория курса (по умолчанию текущая папка)")
    p.add_argument("--public", default=None, help="корень публичного репозитория (по умолчанию из course.yaml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("run", help="проверить задание по выгрузке формы сдачи")
    s.add_argument("assignment")
    s.add_argument("--submissions", help="CSV выгрузки формы (по умолчанию submissions/<id>.csv)")
    s.add_argument("--only", nargs="*", help="только эти студенты (ФИО, логин GitHub или ключ)")
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--clean", action="store_true", help="удалить рабочие копии после прогона (иначе остаются в .work/ для plagiarism и разбора)")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("plagiarism", help="сравнить решения между собой")
    s.add_argument("assignment")
    s.add_argument("--files", nargs="*", help="какие файлы сравнивать (по умолчанию из grading.yaml)")
    s.set_defaults(fn=cmd_plagiarism)

    s = sub.add_parser("selftest", help="эталон набирает максимум, стартер — нет")
    s.add_argument("assignment", nargs="?")
    s.set_defaults(fn=cmd_selftest)

    s = sub.add_parser("validate", help="проверить описание задания (yaml, баллы, утечки)")
    s.add_argument("assignment", nargs="?")
    s.set_defaults(fn=cmd_validate)

    s = sub.add_parser("roster-check", help="кто сдал/не сдал, ошибки в форме")
    s.add_argument("assignment")
    s.add_argument("--ping", action="store_true", help="проверить доступность каждого репозитория")
    s.set_defaults(fn=cmd_roster_check)

    s = sub.add_parser("codes", help="персональные коды студентов (CSV в stdout)")
    s.set_defaults(fn=cmd_codes)

    s = sub.add_parser("new", help="создать заготовку нового задания в обоих репозиториях")
    s.add_argument("assignment")
    s.add_argument("--title")
    s.set_defaults(fn=cmd_new)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
