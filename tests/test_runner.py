"""Проверки requires / on_fail: stop и общая форма сдачи — на временных папках, без git и ноутбуков."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from autograder.config import Assignment, Check, Course, Deadline
from autograder.runner import run_checks_on_dir
from autograder.submissions import read_submissions

TZ = ZoneInfo("Europe/Moscow")


def make_assignment(tmp_path: Path, checks: list[Check]) -> Assignment:
    priv = tmp_path / "priv"
    priv.mkdir(exist_ok=True)
    return Assignment(id="hw01", title="hw01", max_points=sum(c.points for c in checks),
                      deadline=Deadline(datetime(2027, 1, 1, tzinfo=TZ), datetime(2027, 1, 8, tzinfo=TZ), 1),
                      checks=checks, data=[], plagiarism_files=[],
                      public_dir=tmp_path / "pub", private_dir=priv, starter_dir=tmp_path / "pub" / "starter",
                      solution_dir=priv / "solution")


def test_on_fail_stop_skips_the_rest(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "b.txt").write_text("x")
    checks = [
        Check("gate", "file_exists", 2, spec={"path": "a.txt", "on_fail": "stop"}),
        Check("later", "file_exists", 3, spec={"path": "b.txt"}),
    ]
    results, _ = run_checks_on_dir(make_assignment(tmp_path, checks), repo)
    by_id = {r.id: r for r in results}
    assert by_id["gate"].points == 0
    assert by_id["later"].points == 0 and "не проверялось" in by_id["later"].message

    (repo / "a.txt").write_text("x")
    results, _ = run_checks_on_dir(make_assignment(tmp_path, checks), repo)
    assert sum(r.points for r in results) == 5


def test_requires_zeroes_points_when_dependency_fails(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("x")
    checks = [
        Check("a", "file_exists", 1, spec={"path": "a.txt", "requires": ["b"]}),  # ссылка вперёд — допустимо
        Check("b", "file_exists", 1, spec={"path": "b.txt"}),
    ]
    results, _ = run_checks_on_dir(make_assignment(tmp_path, checks), repo)
    by_id = {r.id: r for r in results}
    assert by_id["a"].points == 0 and "не пройдено: b" in by_id["a"].message


def test_single_form_for_all_assignments_and_missing_sha(tmp_path: Path):
    course = Course(name="c", root=tmp_path, missing_sha="head")
    csv = tmp_path / "all.csv"
    csv.write_text(
        "Отметка времени,Адрес электронной почты,Задание,Логин GitHub,Ссылка на репозиторий,Хэш коммита\n"
        "15.02.2027 20:00:00,a@x.ru,ДЗ 1 (hw01),alice,https://github.com/alice/hw01,abc1234\n"
        "15.02.2027 21:00:00,a@x.ru,hw02,alice,https://github.com/alice/hw02,abc1234\n"
        "15.02.2027 21:00:00,b@x.ru,HW01,bob,https://github.com/bob/hw01,\n",
        encoding="utf-8")
    subs, _ = read_submissions(course, csv, assignment_id="hw01")
    assert set(subs) == {"alice", "bob"}
    assert subs["alice"].repo_url.endswith("/hw01")
    assert subs["bob"].sha == "HEAD" and not subs["bob"].problems

    strict = Course(name="c", root=tmp_path, missing_sha="reject")
    subs, _ = read_submissions(strict, csv, assignment_id="hw01")
    assert subs["bob"].problems == ["не указан коммит (хэш или ссылка на коммит)"]
