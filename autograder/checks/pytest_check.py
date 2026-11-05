"""Проверка через pytest: баллы берутся из маркеров @pytest.mark.points(n) и суммируются по пройденным тестам."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from . import CheckContext, CheckResult, register
from ..config import Check


@register("pytest")
def pytest_check(ctx: CheckContext, check: Check) -> CheckResult:
    """spec:
        path: private:tests_hidden          — папка или файл с тестами (private:/public:/в репозитории)
        timeout: 300
        points: сумма баллов по маркерам (для контроля; если не задано — считается из тестов)
    Тестам доступны фикстуры repo_dir / private_dir / public_dir (см. autograder.pytest_plugin).
    """
    tests_path = ctx.resolve(check.spec["path"])
    if not tests_path.exists():
        return CheckResult(check.id, 0, check.points, False, f"нет тестов {check.spec['path']}")
    report = Path(tempfile.mkstemp(suffix=".json", prefix="pytest_report_")[1])
    env = dict(os.environ)
    env.update({
        "GRADER_REPO_DIR": str(ctx.repo_dir),
        "GRADER_PRIVATE_DIR": str(ctx.assignment.private_dir),
        "GRADER_PUBLIC_DIR": str(ctx.assignment.public_dir),
        "GRADER_REPORT": str(report),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "autograder.pytest_plugin", "-p", "no:cacheprovider",
           "-o", "addopts=", "--rootdir", str(tests_path if tests_path.is_dir() else tests_path.parent)]
    if check.spec.get("noconftest"):
        cmd.append("--noconftest")
    cmd.append(str(tests_path))
    try:
        proc = subprocess.run(cmd, cwd=ctx.repo_dir, env=env, capture_output=True, text=True,
                              timeout=int(check.spec.get("timeout", 300)))
    except subprocess.TimeoutExpired:
        report.unlink(missing_ok=True)
        return CheckResult(check.id, 0, check.points, False, "тесты выполнялись слишком долго")

    if not report.exists() or report.stat().st_size == 0:
        report.unlink(missing_ok=True)
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-5:]
        return CheckResult(check.id, 0, check.points, False, "pytest не запустился: " + " | ".join(tail)[:400])
    results = json.loads(report.read_text(encoding="utf-8"))
    report.unlink(missing_ok=True)

    earned = sum(r["points"] for r in results if r["outcome"] == "passed")
    total = sum(r["points"] for r in results)
    max_points = check.points or total
    if check.points and abs(total - check.points) > 1e-9:
        ctx.log.append(f"[{check.id}] сумма баллов в тестах ({total:g}) не равна points в yaml ({check.points:g}); масштабируем")
        earned = earned * check.points / total if total else 0.0
    failed = [f"{r['name']}: {r['message']}" if r["message"] else r["name"] for r in results if r["outcome"] != "passed"]
    n_pass = sum(1 for r in results if r["outcome"] == "passed")
    msg = f"пройдено {n_pass}/{len(results)} тестов" + (f"; не пройдено: {'; '.join(failed)}" if failed else "")
    return CheckResult(check.id, earned, max_points, not failed, msg[:600],
                       details={"tests": results})
