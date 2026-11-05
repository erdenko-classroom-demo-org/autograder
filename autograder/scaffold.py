"""`grader new hwNN` — заготовка задания сразу в двух репозиториях."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from .config import Course

ASSIGNMENT_YAML = """\
id: {aid}
title: "{title}"
max_points: 10

deadline:
  soft: "{soft}"          # после него начисляется штраф
  hard: "{hard}"          # после него работа не принимается (0 баллов)
  penalty_per_day: 1      # балл за каждые начатые сутки опоздания

# Публичные проверки — студенты видят их и могут гонять у себя (CI в starter/). Баллы за них тоже идут в оценку.
checks:
  - id: files_present
    type: file_exists
    points: 1
    paths: [outputs/answers.json]
  - id: report_written
    type: file_min_length
    points: 1
    path: report.md
    min_chars: 300
    manual_review: true     # проверяющий читает текст; балл ставится автоматически, но помечается к просмотру
"""

GRADING_YAML = """\
# Приватная часть задания {aid}: данные, скрытые проверки, антиплагиат.
# Сумма points здесь + в assignment.yaml должна равняться max_points.

data:                      # подкладываются в рабочую копию студента перед проверкой (поверх его файлов)
  - {{src: data/train.csv, dst: data/train.csv}}

checks:
  - id: notebook_runs
    type: notebook_executes
    points: 1
    path: {aid}.ipynb
    timeout: 600
    outputs: [outputs/answers.json]
  - id: answers
    type: json_values
    points: 3
    path: outputs/answers.json
    expected: private:tests_hidden/expected.json
    rel_tol: 0.01
  # - id: model_quality
  #   type: metric_threshold
  #   points: 4
  #   predictions: outputs/predictions.csv
  #   truth: private:data/test_target.csv
  #   id_column: id
  #   target_column: target
  #   metric: r2
  #   scale: [{{min: 0.8, points: 4}}, {{min: 0.7, points: 3}}, {{min: 0.6, points: 2}}, {{min: 0.0, points: 1}}]
  - id: hidden_tests
    type: pytest
    points: 4
    path: private:tests_hidden

plagiarism:
  files: ["{aid}.ipynb"]
"""

README_PUBLIC = """\
# {title}

**Дедлайн:** мягкий — {soft}, жёсткий — {hard}. Штраф: 1 балл за каждые начатые сутки после мягкого.

## Что сделать

1. Скопируйте папку `starter/` в свой **публичный** репозиторий (см. README курса).
2. Данные (`data/`) получите в канале курса и положите рядом с ноутбуком — в git они не попадают.
3. Выполните задания в `{aid}.ipynb`. Ноутбук должен создавать `outputs/answers.json`.
4. Напишите выводы в `report.md` (не меньше 300 символов).
5. Закоммитьте и запушьте. Убедитесь, что CI зелёный.
6. Заполните форму сдачи: ссылка на репозиторий и хэш коммита.

## Как оценивается

| Проверка | Баллы |
|---|---|
| файлы на месте | 1 |
| ноутбук выполняется с нуля | 1 |
| ответы на вопросы | 3 |
| скрытые тесты | 4 |
| отчёт | 1 |
"""

STARTER_CI = """\
name: public-checks
on: [push, pull_request]
jobs:
  checks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.11"}
      - run: pip install -r requirements.txt pytest
      - run: pytest -q tests_public
"""

STARTER_CONFTEST = '''\
"""Фикстуры для публичных тестов — работают и без грейдера (в CI студента)."""
import os
from pathlib import Path

import pytest


@pytest.fixture
def repo_dir() -> Path:
    return Path(os.environ.get("GRADER_REPO_DIR", Path(__file__).resolve().parent.parent))


def pytest_configure(config):
    config.addinivalue_line("markers", "points(n): сколько баллов даёт тест")
'''

STARTER_TEST = '''\
import json

import pytest


@pytest.mark.points(1)
def test_answers_json_exists(repo_dir):
    p = repo_dir / "outputs" / "answers.json"
    assert p.exists(), "ноутбук должен создать outputs/answers.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
'''

HIDDEN_TEST = '''\
import json

import pytest


@pytest.mark.points(2)
def test_answers_have_all_keys(repo_dir, private_dir):
    expected = json.loads((private_dir / "tests_hidden" / "expected.json").read_text(encoding="utf-8"))
    actual = json.loads((repo_dir / "outputs" / "answers.json").read_text(encoding="utf-8"))
    missing = set(expected) - set(actual)
    assert not missing, f"нет ключей: {sorted(missing)}"


@pytest.mark.points(2)
def test_placeholder(repo_dir):
    """Замените на содержательные проверки."""
    assert (repo_dir / "report.md").exists()
'''


def _write(path: Path, text: str, created: list[Path]):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(text, encoding="utf-8")
        created.append(path)


def create_assignment(course: Course, aid: str, title: str) -> list[Path]:
    created: list[Path] = []
    pub = course.public_root / "assignments" / aid
    priv = course.root / "assignments" / aid
    soft = (datetime.now(course.tz) + timedelta(days=14)).replace(hour=23, minute=59, second=0, microsecond=0)
    hard = soft + timedelta(days=7)
    fmt = "%Y-%m-%d %H:%M"
    _write(pub / "assignment.yaml", ASSIGNMENT_YAML.format(aid=aid, title=title, soft=soft.strftime(fmt), hard=hard.strftime(fmt)), created)
    _write(pub / "README.md", README_PUBLIC.format(aid=aid, title=title, soft=soft.strftime("%d.%m.%Y %H:%M"), hard=hard.strftime("%d.%m.%Y %H:%M")), created)
    _write(pub / "starter" / "README.md", f"# {title}\n\nСм. условие в репозитории курса: `assignments/{aid}/README.md`.\n", created)
    _write(pub / "starter" / "requirements.txt", "pandas\nnumpy\nscikit-learn\njupyter\n", created)
    _write(pub / "starter" / ".gitignore", "data/\n.ipynb_checkpoints/\n__pycache__/\n.grader/\n", created)
    _write(pub / "starter" / "report.md", "# Отчёт\n\n_Ваши выводы здесь._\n", created)
    _write(pub / "starter" / ".github" / "workflows" / "ci.yml", STARTER_CI, created)
    _write(pub / "starter" / "tests_public" / "conftest.py", STARTER_CONFTEST, created)
    _write(pub / "starter" / "tests_public" / "test_public.py", STARTER_TEST, created)
    nb = ('{"cells":[{"cell_type":"markdown","metadata":{},"source":["# ' + title + '\\n","\\n","Условие — в репозитории курса."]},'
          '{"cell_type":"code","execution_count":null,"metadata":{},"outputs":[],"source":["import json, os\\n","import pandas as pd\\n","\\n",'
          '"train = pd.read_csv(\'data/train.csv\')\\n","answers = {}\\n","# YOUR CODE HERE\\n","\\n","os.makedirs(\'outputs\', exist_ok=True)\\n",'
          '"json.dump(answers, open(\'outputs/answers.json\', \'w\'), indent=2)"]}],'
          '"metadata":{"kernelspec":{"display_name":"Python 3","language":"python","name":"python3"}},"nbformat":4,"nbformat_minor":5}\n')
    _write(pub / "starter" / f"{aid}.ipynb", nb, created)

    _write(priv / "grading.yaml", GRADING_YAML.format(aid=aid), created)
    _write(priv / "data" / "README.md", "Положите сюда данные задания (train.csv и т.п.). Они подкладываются студенту при проверке и раздаются отдельно.\n", created)
    _write(priv / "tests_hidden" / "expected.json", "{}\n", created)
    _write(priv / "tests_hidden" / "test_hidden.py", HIDDEN_TEST, created)
    _write(priv / "solution" / "README.md", "Эталонное решение: скопируйте сюда starter/ и решите задание. `grader selftest` требует полного балла.\n", created)
    return created
