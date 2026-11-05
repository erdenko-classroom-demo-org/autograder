"""Список студентов (roster.csv) и выгрузка Google-формы сдачи (submissions/<hw>.csv).

Студент узнаётся по тому, что есть: логин GitHub → email → ФИО + группа → ФИО. Внутренний ключ студента
(`key`) — логин GitHub, если он известен из roster, иначе «фамилия-имя-отчество-группа».
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Course

SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
GITHUB_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s#?]+?)(?:\.git)?"
    r"(?:/(?P<kind>commit|tree|blob|commits)/(?P<ref>[^/\s#?]+)(?:/(?P<path>[^#?\s]*))?)?/?$", re.I)


@dataclass
class Student:
    key: str
    login: str = ""
    name: str = ""
    group: str = ""
    email: str = ""
    grader: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Submission:
    key: str
    repo_url: str
    sha: str                       # хэш, имя ветки или 'HEAD'
    submitted_at: datetime
    student: Student               # из roster или собранный из формы
    subdir: str = ""
    comment: str = ""
    github_login: str = ""         # владелец репозитория по ссылке
    problems: list[str] = field(default_factory=list)


# ---------- нормализация ----------

def normalize_login(value: str) -> str:
    """'@Ivanov', 'https://github.com/Ivanov/', 'ivanov ' -> 'ivanov'."""
    s = (value or "").strip().lstrip("@")
    m = re.match(r"^(?:https?://)?(?:www\.)?github\.com/([^/\s]+)/?", s, re.I)
    if m:
        s = m.group(1)
    return s.lower()


def name_tokens(name: str) -> tuple[str, ...]:
    s = (name or "").lower().replace("ё", "е")
    s = re.sub(r"[^\w\s-]", " ", s)
    return tuple(t for t in s.split() if t)


def normalize_group(group: str) -> str:
    return re.sub(r"[\s_]+", "", (group or "").lower().replace("ё", "е").replace("-", ""))


def make_key(name: str, group: str) -> str:
    toks = name_tokens(name)
    g = re.sub(r"[^\w]+", "", (group or "").lower().replace("ё", "е"))
    return "-".join(toks + ((g,) if g else ()))


def parse_repo_link(value: str) -> tuple[str, str, str, str]:
    """Ссылка студента -> (url репозитория, владелец, ref/sha или '', подпапка).

    Понимает https://github.com/u/r, .../r.git, .../r/commit/<sha>, .../r/tree/<branch>/<папка>, .../r/blob/<branch>/<файл>,
    git@github.com:u/r.git, github.com/u/r и любые другие url (file://, gitlab) — как есть.
    """
    s = (value or "").strip()
    s = re.sub(r"[?#].*$", "", s)
    if s.startswith("git@github.com:"):
        s = "https://github.com/" + s[len("git@github.com:"):]
    if re.match(r"^(www\.)?github\.com/", s, re.I):
        s = "https://" + s
    m = GITHUB_RE.match(s)
    if not m:
        return s.rstrip("/"), "", "", ""
    url = f"https://github.com/{m['owner']}/{m['repo']}"
    ref, subdir = "", ""
    kind = (m["kind"] or "").lower()
    if kind == "commit":
        ref = m["ref"]
    elif kind in ("tree", "blob"):
        ref = m["ref"]
        path = (m["path"] or "").strip("/")
        if kind == "blob":
            path = path.rsplit("/", 1)[0] if "/" in path else ""
        subdir = path
    return url, m["owner"].lower(), ref, subdir


def normalize_repo_url(value: str) -> str:
    return parse_repo_link(value)[0]


# ---------- roster ----------

class Roster:
    def __init__(self, students: list[Student]):
        self.students: dict[str, Student] = {s.key: s for s in students}
        self.by_login = {s.login: s for s in students if s.login}
        self.by_email = {s.email: s for s in students if s.email}

    def __len__(self):
        return len(self.students)

    def __contains__(self, key):
        return key in self.students

    def __iter__(self):
        return iter(self.students)

    def get(self, key: str) -> Student | None:
        return self.students.get(key)

    def values(self):
        return self.students.values()

    def find(self, login: str = "", email: str = "", name: str = "", group: str = "") -> Student | None:
        if login and login in self.by_login:
            return self.by_login[login]
        if email and email in self.by_email:
            return self.by_email[email]
        toks = set(name_tokens(name))
        if not toks:
            return None
        # ФИО: в форме могут написать «Иванов Алексей» без отчества или в другом порядке
        cands = [s for s in self.students.values()
                 if toks == set(name_tokens(s.name)) or (len(toks) >= 2 and toks <= set(name_tokens(s.name)))]
        if group:
            g = normalize_group(group)
            same_group = [s for s in cands if normalize_group(s.group) == g]
            if same_group:
                cands = same_group
        if len(cands) == 1:
            return cands[0]
        exact = [s for s in cands if toks == set(name_tokens(s.name))]
        return exact[0] if len(exact) == 1 else None

    def resolve(self, identifier: str) -> str:
        """Ключ, логин GitHub или ФИО (можно 'ФИО, группа') из adjustments/extensions -> key."""
        ident = (identifier or "").strip()
        if ident in self.students:
            return ident
        low = normalize_login(ident)
        if low in self.students:
            return low
        if low in self.by_login:
            return self.by_login[low].key
        name, _, group = ident.partition(",")
        s = self.find(name=name, group=group.strip())
        return s.key if s else ident


def read_roster(course: Course) -> Roster:
    path = course.path(course.roster_path)
    students: list[Student] = []
    if not path.exists():
        return Roster(students)
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            login = normalize_login(row.get("login", ""))
            name, group = row.get("name", ""), row.get("group", "")
            if not login and not name:
                continue
            key = login or make_key(name, group)
            students.append(Student(key=key, login=login, name=name, group=group,
                                    email=row.get("email", "").lower(), grader=row.get("grader", ""),
                                    extra={k: v for k, v in row.items() if k not in {"login", "name", "group", "email", "grader"}}))
    return Roster(students)


# ---------- форма сдачи ----------

def _parse_form_timestamp(value: str, course: Course) -> datetime:
    s = value.strip()
    for fmt in (course.form_timestamp_format, "%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=course.tz)
        except ValueError:
            continue
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=course.tz)


def _assignment_matches(cell: str, assignment_id: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(assignment_id.lower())}(?![a-z0-9])", cell.lower()) is not None


def read_submissions(course: Course, csv_path: Path, roster: Roster | None = None,
                     assignment_id: str | None = None) -> tuple[dict[str, Submission], list[str]]:
    """Читает выгрузку формы. Возвращает {key: последняя сдача студента} и список предупреждений.

    Обязательные колонки: время и ссылка. Студент определяется по логину GitHub / email / ФИО + группе —
    по тем колонкам, которые есть в форме. Хэш коммита можно передать отдельной колонкой или ссылкой
    вида .../commit/<sha>; без него берётся последний коммит (missing_sha: head) или сдача отклоняется (reject).
    Из нескольких отправок одного студента берётся последняя по времени формы.
    """
    cols = course.form_columns
    warnings: list[str] = []
    latest: dict[str, Submission] = {}
    roster = roster or Roster([])

    if not csv_path.exists():
        return latest, [f"Нет файла выгрузки формы: {csv_path}"]

    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = [h.strip() for h in (reader.fieldnames or [])]
        for key in ("timestamp", "repo"):
            if cols[key] not in header:
                raise ValueError(f"В выгрузке формы нет колонки '{cols[key]}' (ключ {key}); есть: {header}")
        if not any(cols[k] in header for k in ("login", "email", "name")):
            raise ValueError("В выгрузке формы нет ни логина GitHub, ни email, ни ФИО — некого сопоставлять со списком")
        a_col = cols.get("assignment", "")
        has_assignment_col = bool(a_col) and a_col in header
        if has_assignment_col and not assignment_id:
            raise ValueError(f"В выгрузке есть колонка '{a_col}', но не указано, какое задание проверяем")

        def cell(row, k):
            return row.get(cols.get(k, ""), "").strip()

        for i, row in enumerate(reader, start=2):
            row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            if has_assignment_col and not _assignment_matches(row.get(a_col, ""), assignment_id):
                continue
            problems: list[str] = []
            try:
                ts = _parse_form_timestamp(row[cols["timestamp"]], course)
            except ValueError:
                warnings.append(f"строка {i}: непонятное время '{row[cols['timestamp']]}'")
                continue

            repo_url, owner, ref, subdir = parse_repo_link(cell(row, "repo"))
            login = normalize_login(cell(row, "login"))
            email = cell(row, "email").lower()
            name, group = cell(row, "name"), cell(row, "group")

            student = roster.find(login=login, email=email, name=name, group=group) if len(roster) else None
            if student is None:
                who = name or login or owner or email
                if len(roster):
                    problems.append(f"«{who}» ({group}) не найден в roster.csv — проверьте написание ФИО/группы")
                key = login or (make_key(name, group) if name else owner) or email
                if not key:
                    warnings.append(f"строка {i}: не удалось определить студента")
                    continue
                student = Student(key=key, login=login or owner, name=name, group=group, email=email)

            if not repo_url:
                problems.append("пустая ссылка на репозиторий")
            sha = (cell(row, "sha") or ref).lower()
            if not sha:
                if course.missing_sha == "head":
                    sha = "HEAD"
                else:
                    problems.append("не указан коммит (хэш или ссылка на коммит)")
            elif not SHA_RE.match(sha) and sha != "head" and not re.match(r"^[\w./-]+$", sha):
                problems.append(f"'{sha}' не похож ни на хэш, ни на имя ветки")
            sub_dir = (cell(row, "subdir") or subdir).strip("/ ")
            sub = Submission(key=student.key, repo_url=repo_url, sha=sha, submitted_at=ts, student=student,
                             subdir=sub_dir, comment=cell(row, "comment"), github_login=owner, problems=problems)
            prev = latest.get(student.key)
            if prev is None or ts >= prev.submitted_at:
                if prev is not None:
                    warnings.append(f"{student.name or student.key}: несколько отправок формы, берём последнюю ({ts:%d.%m %H:%M})")
                latest[student.key] = sub
    return latest, warnings


# ---------- ручные правки ----------

def _read_rows(path: Path, roster: Roster) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            ident = row.get("student") or row.get("login") or row.get("name") or ""
            if not ident or ident.startswith("#"):
                continue
            row["key"] = roster.resolve(ident)
            rows.append(row)
    return rows


def read_adjustments(course: Course, roster: Roster) -> list[dict]:
    """adjustments.csv: student,assignment,delta,set_total,reason,author  (student — ФИО, логин или ключ)"""
    return _read_rows(course.path(course.adjustments_path), roster)


def read_extensions(course: Course, roster: Roster) -> list[dict]:
    """extensions.csv: student,assignment,soft,hard,reason,author"""
    return _read_rows(course.path(course.extensions_path), roster)
