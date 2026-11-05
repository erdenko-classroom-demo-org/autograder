"""Поиск похожих решений.

1. Ноутбуки превращаются в .py (только код, магии закомментированы), скрипты берутся как есть.
2. Встроенная проверка: код разбивается на токены, имена переменных/функций и литералы обезличиваются,
   пары сравниваются по доле совпадающих токенов. Ловит переименование переменных и правку констант,
   не ловит глубокую переработку структуры — для этого есть JPlag.
3. Если найден jplag.jar (переменная JPLAG_JAR или tools/jplag.jar в приватном репозитории),
   дополнительно запускается JPlag с HTML-отчётом.

Результат — только список подозрительных пар для проверяющего. Решение о плагиате принимает человек
и фиксирует его в adjustments.csv.
"""
from __future__ import annotations

import io
import itertools
import json
import os
import shutil
import subprocess
import tokenize
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import nbformat

from .config import Course


def notebook_to_code(path: Path) -> str:
    nb = nbformat.read(path, as_version=4)
    parts = []
    for cell in nb.cells:
        if cell.cell_type != "code":
            continue
        lines = []
        for ln in cell.source.splitlines():
            s = ln.lstrip()
            if s.startswith(("%", "!", "?")) or s.endswith("?"):
                lines.append("# " + ln)
            else:
                lines.append(ln)
        parts.append("\n".join(lines))
    return "\n\n".join(parts) + "\n"


def collect_sources(repo_dir: Path, files: list[str]) -> str:
    """Склеивает указанные файлы студента в один текст кода."""
    chunks = []
    for rel in files:
        for p in sorted(repo_dir.glob(rel)):
            if not p.is_file():
                continue
            try:
                if p.suffix == ".ipynb":
                    chunks.append(notebook_to_code(p))
                else:
                    chunks.append(p.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
    return "\n".join(chunks)


def normalize_tokens(code: str) -> list[str]:
    """Имена → N, числа → 0, строки → S, комментарии и переводы строк выбрасываются."""
    out: list[str] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(code).readline):
            t = tok.type
            if t in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                     tokenize.ENCODING, tokenize.ENDMARKER):
                continue
            if t == tokenize.NAME:
                import keyword

                out.append(tok.string if keyword.iskeyword(tok.string) else "N")
            elif t == tokenize.NUMBER:
                out.append("0")
            elif t == tokenize.STRING:
                out.append("S")
            else:
                out.append(tok.string)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # битый код — сравниваем как есть, по словам
        out = code.split()
    return out


def similarity(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    sm = SequenceMatcher(None, a, b, autojunk=False)
    if sm.real_quick_ratio() < 0.5 or sm.quick_ratio() < 0.5:
        return round(sm.quick_ratio(), 4)
    return round(sm.ratio(), 4)


@dataclass
class Pair:
    a: str
    b: str
    score: float
    tokens_a: int
    tokens_b: int


def builtin_report(sources: dict[str, str], threshold: float) -> list[Pair]:
    toks = {login: normalize_tokens(code) for login, code in sources.items()}
    pairs = []
    for a, b in itertools.combinations(sorted(toks), 2):
        s = similarity(toks[a], toks[b])
        pairs.append(Pair(a, b, s, len(toks[a]), len(toks[b])))
    pairs.sort(key=lambda p: p.score, reverse=True)
    return pairs


def find_jplag(course: Course) -> Path | None:
    env = os.environ.get("JPLAG_JAR")
    if env and Path(env).is_file():
        return Path(env)
    cand = course.path("tools/jplag.jar")
    return cand if cand.is_file() else None


def run_jplag(jar: Path, src_dir: Path, out_dir: Path, language: str = "python3", min_tokens: int = 12) -> str:
    if not shutil.which("java"):
        return "java не найден — JPlag пропущен"
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["java", "-jar", str(jar), "-l", language, "-t", str(min_tokens), "-r", str(out_dir / "jplag_result"),
           "-M", "RUN", str(src_dir)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode:
        return "JPlag завершился с ошибкой: " + (proc.stderr or proc.stdout).strip()[-400:]
    return f"JPlag: результат в {out_dir / 'jplag_result.jplag'} (открыть на https://jplag.github.io/JPlag/ или в jplag-web)"


def run(course: Course, assignment_id: str, repos: dict[str, Path], files: list[str], out_dir: Path) -> dict:
    """repos: {login: рабочая копия}. Пишет pairs.csv, report.md, папку sources/ для JPlag."""
    out_dir.mkdir(parents=True, exist_ok=True)
    src_dir = out_dir / "sources"
    if src_dir.exists():
        shutil.rmtree(src_dir)
    src_dir.mkdir()
    sources: dict[str, str] = {}
    for login, repo in sorted(repos.items()):
        code = collect_sources(repo, files)
        if not code.strip():
            continue
        sources[login] = code
        d = src_dir / login
        d.mkdir()
        (d / "solution.py").write_text(code, encoding="utf-8")

    threshold = course.plagiarism_threshold
    pairs = builtin_report(sources, threshold)
    with (out_dir / "pairs.csv").open("w", encoding="utf-8", newline="") as f:
        f.write("login_a,login_b,similarity,tokens_a,tokens_b,suspicious\n")
        for p in pairs:
            f.write(f"{p.a},{p.b},{p.score:.4f},{p.tokens_a},{p.tokens_b},{'yes' if p.score >= threshold else ''}\n")

    jplag_note = ""
    jar = find_jplag(course)
    if jar:
        jplag_note = run_jplag(jar, src_dir, out_dir)

    suspicious = [p for p in pairs if p.score >= threshold]
    lines = [f"# Похожие решения — {assignment_id}", "",
             f"Сравнено решений: {len(sources)}. Порог подозрения: {threshold:.2f}. "
             f"Пар выше порога: {len(suspicious)}.", "",
             "Сходство — доля совпадающих токенов после обезличивания имён и констант "
             "(1.00 — код идентичен с точностью до имён переменных). Это сигнал для проверяющего, не вердикт: "
             "откройте оба решения и решите сами; плагиат фиксируется в adjustments.csv (set_total=0).", ""]
    if jplag_note:
        lines += [jplag_note, ""]
    lines += ["| Студент A | Студент B | Сходство | Токенов A/B |", "|---|---|---|---|"]
    for p in (suspicious or pairs[:10]):
        flag = " ⚠️" if p.score >= threshold else ""
        lines.append(f"| {p.a} | {p.b} | {p.score:.2f}{flag} | {p.tokens_a}/{p.tokens_b} |")
    if not suspicious:
        lines.insert(len(lines) - len(pairs[:10]) - 2, "_Пар выше порога нет; ниже — десять самых похожих для ориентира._\n")
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps({
        "compared": len(sources), "threshold": threshold,
        "suspicious": [[p.a, p.b, p.score] for p in suspicious]}, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"compared": len(sources), "suspicious": suspicious, "pairs": pairs, "jplag": jplag_note}
