"""Загрузка конфигурации курса и заданий.

Два репозитория:
  * публичный  — <public>/assignments/<id>/assignment.yaml  (метаданные, дедлайны, публичные проверки)
  * приватный  — <private>/course.yaml и <private>/assignments/<id>/grading.yaml
                 (данные для подкладывания, скрытые проверки, антиплагиат)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

DEFAULT_FORM_COLUMNS = {
    # обязательные
    "timestamp": "Отметка времени",
    "repo": "Ссылка на репозиторий",
    # хотя бы одна из трёх — чтобы узнать студента
    "name": "ФИО",
    "group": "Группа",
    "email": "Адрес электронной почты",
    "login": "Логин GitHub",
    # необязательные
    "comment": "Комментарий",
    "sha": "Хэш коммита",             # можно вместо этого дать ссылку на коммит в поле «Ссылка»
    "subdir": "Папка в репозитории",
    "assignment": "Задание",          # если одна общая форма сдачи на все ДЗ
}


class ConfigError(Exception):
    pass


@dataclass
class Course:
    name: str
    timezone: str = "Europe/Moscow"
    salt: str = "change-me"
    public_repo_path: str = "../classroom-demo"
    public_repo_url: str = ""
    roster_path: str = "roster.csv"
    submissions_dir: str = "submissions"
    submissions_file: str = ""  # если задано — одна общая выгрузка формы на все задания (фильтр по колонке «Задание»)
    missing_sha: str = "head"   # head — без хэша берём последний коммит ветки по умолчанию; reject — ошибка сдачи
    results_dir: str = "results"
    work_dir: str = ".work"
    adjustments_path: str = "adjustments.csv"
    extensions_path: str = "extensions.csv"
    form_columns: dict = field(default_factory=lambda: dict(DEFAULT_FORM_COLUMNS))
    form_timestamp_format: str = "%d.%m.%Y %H:%M:%S"
    after_hard_deadline: str = "zero"  # zero | ignore
    plagiarism_threshold: float = 0.85
    root: Path = field(default=Path("."), repr=False)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else (self.root / p)

    @property
    def public_root(self) -> Path:
        env = os.environ.get("GRADER_PUBLIC")
        return Path(env).resolve() if env else self.path(self.public_repo_path).resolve()


@dataclass
class Deadline:
    soft: datetime
    hard: datetime
    penalty_per_day: float = 0.0


@dataclass
class Check:
    id: str
    type: str
    points: float = 0.0
    manual_review: bool = False
    visibility: str = "hidden"  # public | hidden
    spec: dict = field(default_factory=dict)


@dataclass
class Assignment:
    id: str
    title: str
    max_points: float
    deadline: Deadline
    checks: list[Check]
    data: list[dict]
    plagiarism_files: list[str]
    public_dir: Path
    private_dir: Path
    starter_dir: Path
    solution_dir: Path
    submission_form_csv: Path | None = None


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"Файл не найден: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Ожидался словарь в {path}")
    return data


def parse_dt(value, tz: ZoneInfo) -> datetime:
    """'2027-02-15 23:59' / ISO / datetime -> aware datetime в часовом поясе курса."""
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        dt = None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def load_course(private_root: Path) -> Course:
    private_root = Path(private_root).resolve()
    raw = _read_yaml(private_root / "course.yaml")
    known = {f for f in Course.__dataclass_fields__ if f != "root"}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"course.yaml: неизвестные поля {sorted(unknown)}")
    course = Course(root=private_root, **raw)
    if not isinstance(course.form_columns, dict):
        raise ConfigError("course.yaml: form_columns должен быть словарём")
    cols = dict(DEFAULT_FORM_COLUMNS)
    cols.update(course.form_columns)
    course.form_columns = cols
    if course.missing_sha not in ("head", "reject"):
        raise ConfigError("course.yaml: missing_sha должен быть 'head' или 'reject'")
    return course


def _parse_checks(items, visibility: str) -> list[Check]:
    checks = []
    for i, raw in enumerate(items or []):
        if not isinstance(raw, dict):
            raise ConfigError(f"Проверка #{i} должна быть словарём")
        raw = dict(raw)
        cid = raw.pop("id", None)
        ctype = raw.pop("type", None)
        if not cid or not ctype:
            raise ConfigError(f"Проверка #{i}: нужны поля id и type")
        points = float(raw.pop("points", 0) or 0)
        manual = bool(raw.pop("manual_review", False))
        checks.append(Check(id=str(cid), type=str(ctype), points=points,
                            manual_review=manual, visibility=visibility, spec=raw))
    ids = [c.id for c in checks]
    dups = {x for x in ids if ids.count(x) > 1}
    if dups:
        raise ConfigError(f"Повторяющиеся id проверок: {sorted(dups)}")
    return checks


def load_assignment(course: Course, assignment_id: str, require_private: bool = True) -> Assignment:
    public_dir = course.public_root / "assignments" / assignment_id
    private_dir = course.root / "assignments" / assignment_id
    pub = _read_yaml(public_dir / "assignment.yaml")
    if pub.get("id") != assignment_id:
        raise ConfigError(f"{public_dir / 'assignment.yaml'}: id '{pub.get('id')}' не совпадает с папкой '{assignment_id}'")
    dl = pub.get("deadline") or {}
    for key in ("soft", "hard"):
        if key not in dl:
            raise ConfigError(f"{assignment_id}: в deadline нет поля '{key}'")
    deadline = Deadline(
        soft=parse_dt(dl["soft"], course.tz),
        hard=parse_dt(dl["hard"], course.tz),
        penalty_per_day=float(dl.get("penalty_per_day", 0) or 0),
    )
    if deadline.hard < deadline.soft:
        raise ConfigError(f"{assignment_id}: жёсткий дедлайн раньше мягкого")

    checks = _parse_checks(pub.get("checks"), "public")
    data: list[dict] = []
    plag_files: list[str] = []
    grading_path = private_dir / "grading.yaml"
    if grading_path.exists():
        priv = _read_yaml(grading_path)
        checks += _parse_checks(priv.get("checks"), "hidden")
        data = list(priv.get("data") or [])
        plag_files = list((priv.get("plagiarism") or {}).get("files") or [])
    elif require_private:
        raise ConfigError(f"Нет файла {grading_path}")

    ids = [c.id for c in checks]
    dups = {x for x in ids if ids.count(x) > 1}
    if dups:
        raise ConfigError(f"{assignment_id}: id проверок пересекаются между assignment.yaml и grading.yaml: {sorted(dups)}")

    max_points = float(pub.get("max_points") or sum(c.points for c in checks))
    if course.submissions_file:
        form_csv = course.path(course.submissions_file)
    else:
        form_csv = course.path(course.submissions_dir) / f"{assignment_id}.csv"
    return Assignment(
        id=assignment_id,
        title=str(pub.get("title") or assignment_id),
        max_points=max_points,
        deadline=deadline,
        checks=checks,
        data=data,
        plagiarism_files=plag_files,
        public_dir=public_dir,
        private_dir=private_dir,
        starter_dir=public_dir / "starter",
        solution_dir=private_dir / "solution",
        submission_form_csv=form_csv,
    )


def list_assignments(course: Course) -> list[str]:
    base = course.public_root / "assignments"
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir() if (p / "assignment.yaml").exists())


def validate_assignment(course: Course, assignment_id: str) -> list[str]:
    """Возвращает список проблем (пустой — всё в порядке)."""
    problems: list[str] = []
    try:
        a = load_assignment(course, assignment_id, require_private=False)
    except ConfigError as e:
        return [str(e)]
    from .checks import CHECK_TYPES  # локальный импорт, чтобы избежать цикла

    ids = {c.id for c in a.checks}
    for c in a.checks:
        if c.type not in CHECK_TYPES:
            problems.append(f"проверка '{c.id}': неизвестный тип '{c.type}' (есть: {', '.join(sorted(CHECK_TYPES))})")
        if c.points < 0:
            problems.append(f"проверка '{c.id}': отрицательные баллы")
        for r in c.spec.get("requires") or []:
            if r not in ids:
                problems.append(f"проверка '{c.id}': requires ссылается на несуществующую проверку '{r}'")
        if c.spec.get("on_fail") not in (None, "stop", "continue"):
            problems.append(f"проверка '{c.id}': on_fail должен быть 'stop' или 'continue'")
    total = sum(c.points for c in a.checks)
    has_private = (a.private_dir / "grading.yaml").exists()
    if has_private and a.checks and abs(total - a.max_points) > 1e-9:
        problems.append(f"сумма баллов по проверкам ({total:g}) не равна max_points ({a.max_points:g})")
    if not has_private and total > a.max_points + 1e-9:
        problems.append(f"публичные проверки дают {total:g} баллов — больше max_points ({a.max_points:g})")
    if not a.starter_dir.exists():
        problems.append(f"нет папки starter: {a.starter_dir}")
    if not (a.public_dir / "README.md").exists():
        problems.append("нет README.md с условием задания")
    if a.deadline.soft < datetime.now(course.tz) - timedelta(days=365):
        problems.append("мягкий дедлайн больше года в прошлом — опечатка в годе?")
    # утечки в публичной части
    leak_words = ("solution", "expected", "target", "hidden", "answers_ref")
    for p in a.public_dir.rglob("*"):
        if p.is_file() and any(w in p.name.lower() for w in leak_words):
            problems.append(f"подозрительный файл в публичной части: {p.relative_to(a.public_dir)}")
    return problems
