"""Плагин pytest для тестов заданий.

В тестах доступны:
    @pytest.mark.points(2)      — сколько баллов даёт тест (по умолчанию 1)
    fixture repo_dir            — Path к рабочей копии студента
    fixture private_dir         — Path к приватной папке задания (в публичных тестах не задан)
    fixture public_dir          — Path к публичной папке задания

Подключается грейдером автоматически (-p autograder.pytest_plugin); при локальном запуске
в репозитории студента (CI обратной связи) работает и без плагина — см. conftest.py в starter.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

_RESULTS: dict[str, dict] = {}


def pytest_configure(config):
    config.addinivalue_line("markers", "points(n): сколько баллов даёт тест")


@pytest.fixture
def repo_dir() -> Path:
    return Path(os.environ.get("GRADER_REPO_DIR", os.getcwd()))


@pytest.fixture
def private_dir() -> Path:
    v = os.environ.get("GRADER_PRIVATE_DIR")
    if not v:
        pytest.skip("приватная папка недоступна (публичный запуск)")
    return Path(v)


@pytest.fixture
def public_dir() -> Path:
    v = os.environ.get("GRADER_PUBLIC_DIR")
    if not v:
        pytest.skip("публичная папка задания не задана")
    return Path(v)


def _points(item) -> float:
    m = item.get_closest_marker("points")
    if m and m.args:
        return float(m.args[0])
    return 1.0


def pytest_runtest_logreport(report):
    rec = _RESULTS.setdefault(report.nodeid, {"nodeid": report.nodeid, "outcome": "passed", "message": "", "points": 1.0})
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        if report.outcome == "failed":
            rec["outcome"] = "failed"
            msg = report.longreprtext.strip().splitlines()
            # первая строка с 'E ' — текст assert (сообщение для студента); дальше идут пояснения pytest
            e_lines = [ln[2:].strip() for ln in msg if ln.startswith("E ")]
            text = e_lines[0] if e_lines else (msg[-1] if msg else "")
            text = text.removeprefix("AssertionError:").strip()
            rec["message"] = text[:300]
        elif report.outcome == "skipped":
            rec["outcome"] = "skipped"
            rec["message"] = str(report.longrepr)[-200:] if report.longrepr else ""


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _RESULTS.setdefault(item.nodeid, {"nodeid": item.nodeid, "outcome": "passed", "message": "", "points": 1.0})
    _RESULTS[item.nodeid]["points"] = _points(item)
    _RESULTS[item.nodeid]["name"] = item.name
    yield


def pytest_sessionfinish(session, exitstatus):
    out = os.environ.get("GRADER_REPORT")
    if out:
        Path(out).write_text(json.dumps(list(_RESULTS.values()), ensure_ascii=False, indent=1), encoding="utf-8")
