from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from autograder.checks import CheckResult, points_from_scale
from autograder.config import Assignment, Course, Deadline
from autograder.scoring import StudentResult, effective_deadline, finalize, lateness, public_id
from autograder.submissions import Student, Submission

TZ = ZoneInfo("Europe/Moscow")
SOFT = datetime(2027, 2, 15, 23, 59, tzinfo=TZ)
HARD = SOFT + timedelta(days=7)


def make_assignment(penalty=1.0):
    return Assignment(id="hw01", title="hw01", max_points=10,
                      deadline=Deadline(SOFT, HARD, penalty), checks=[], data=[], plagiarism_files=[],
                      public_dir=Path("."), private_dir=Path("."), starter_dir=Path("."), solution_dir=Path("."))


def make_result(key, submitted_at, points):
    st = Student(key=key, name=key)
    sub = Submission(key=key, repo_url="x", sha="abcdef1", submitted_at=submitted_at, student=st)
    res = StudentResult(key, st, sub, "ok", checks=[CheckResult("c1", points, 10, True)])
    return res


def test_lateness_boundaries():
    dl = Deadline(SOFT, HARD, 1)
    assert lateness(SOFT, dl) == (0, False)
    assert lateness(SOFT + timedelta(minutes=1), dl) == (1, False)
    assert lateness(SOFT + timedelta(days=1), dl) == (1, False)
    assert lateness(SOFT + timedelta(days=1, seconds=1), dl) == (2, False)
    assert lateness(HARD + timedelta(seconds=1), dl) == (8, True)


def test_penalty_and_cap():
    course = Course(name="c")
    a = make_assignment(penalty=1.5)
    res = finalize(course, a, make_result("a", SOFT + timedelta(days=2), 9), a.deadline, [])
    assert res.status == "late" and res.days_late == 2 and res.penalty == 3 and res.total == 6
    res = finalize(course, a, make_result("b", HARD + timedelta(hours=1), 9), a.deadline, [])
    assert res.status == "late_hard" and res.total == 0
    res = finalize(course, a, make_result("c", SOFT, 12), a.deadline, [])
    assert res.raw == 10 and res.total == 10  # сумма проверок обрезается до максимума


def test_adjustments_and_extensions():
    course = Course(name="c")
    a = make_assignment()
    ext = [{"key": "late_ok", "assignment": "hw01", "soft": "2027-02-20 23:59", "hard": ""}]
    dl = effective_deadline(course, a, "late_ok", ext)
    assert dl.soft == datetime(2027, 2, 20, 23, 59, tzinfo=TZ) and dl.hard == HARD
    res = finalize(course, a, make_result("late_ok", SOFT + timedelta(days=3), 8), dl, [])
    assert res.status == "ok" and res.total == 8

    adj = [{"key": "bonus", "assignment": "", "delta": "1", "set_total": "", "reason": "нашёл ошибку"},
           {"key": "cheat", "assignment": "hw01", "delta": "", "set_total": "0", "reason": "плагиат"}]
    assert finalize(course, a, make_result("bonus", SOFT, 10), a.deadline, adj).total == 11
    assert finalize(course, a, make_result("cheat", SOFT, 10), a.deadline, adj).total == 0


def test_not_submitted():
    course = Course(name="c")
    a = make_assignment()
    res = finalize(course, a, StudentResult("x", None, None, "not_submitted"), a.deadline, [])
    assert res.total == 0 and res.status == "not_submitted"


def test_scale():
    scale = [{"min": 0.8, "points": 4}, {"min": 0.7, "points": 3}, {"min": 0.0, "points": 1}]
    assert points_from_scale(0.85, scale)[0] == 4
    assert points_from_scale(0.75, scale)[0] == 3
    assert points_from_scale(0.1, scale)[0] == 1
    assert points_from_scale(-0.5, scale)[0] == 0
    mae = [{"max": 10, "points": 3}, {"max": 20, "points": 1}]
    assert points_from_scale(5, mae, higher_is_better=False)[0] == 3
    assert points_from_scale(15, mae, higher_is_better=False)[0] == 1
    assert points_from_scale(25, mae, higher_is_better=False)[0] == 0


def test_public_id_stable_and_private():
    c1 = Course(name="c", salt="s1")
    c2 = Course(name="c", salt="s2")
    assert public_id(c1, "Ivanov") == public_id(c1, "ivanov")
    assert public_id(c1, "ivanov") != public_id(c2, "ivanov")
    assert len(public_id(c1, "ivanov")) == 8
