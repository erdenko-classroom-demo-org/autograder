from pathlib import Path

from autograder.config import Course
from autograder.plagiarism import normalize_tokens, similarity
from autograder.submissions import Roster, Student, normalize_login, normalize_repo_url, parse_repo_link, read_submissions


def test_normalize_login():
    assert normalize_login("@Ivanov ") == "ivanov"
    assert normalize_login("https://github.com/Ivanov/") == "ivanov"
    assert normalize_login("github.com/ivanov/some-repo") == "ivanov"


def test_normalize_repo_url():
    assert normalize_repo_url("https://github.com/u/r.git") == "https://github.com/u/r"
    assert normalize_repo_url("https://github.com/u/r/tree/main/hw01") == "https://github.com/u/r"
    assert normalize_repo_url("git@github.com:u/r.git") == "https://github.com/u/r"
    assert normalize_repo_url("github.com/u/r/") == "https://github.com/u/r"
    assert normalize_repo_url("file:///tmp/x") == "file:///tmp/x"


def test_parse_repo_link_extracts_commit_branch_and_subdir():
    assert parse_repo_link("https://github.com/U/R/commit/abc1234def") == ("https://github.com/U/R", "u", "abc1234def", "")
    assert parse_repo_link("https://github.com/u/r/tree/dev/hw01") == ("https://github.com/u/r", "u", "dev", "hw01")
    assert parse_repo_link("https://github.com/u/r/blob/main/hw01/hw01.ipynb") == ("https://github.com/u/r", "u", "main", "hw01")
    assert parse_repo_link("https://github.com/u/r") == ("https://github.com/u/r", "u", "", "")


def test_read_submissions_latest_wins_and_email_fallback(tmp_path: Path):
    course = Course(name="c", root=tmp_path, missing_sha="reject")
    csv = tmp_path / "hw01.csv"
    csv.write_text(
        "Отметка времени,Адрес электронной почты,Логин GitHub,Ссылка на репозиторий,Хэш коммита\n"
        "15.02.2027 20:00:00,a@x.ru,Alice,https://github.com/alice/hw01,abc1234\n"
        "15.02.2027 22:00:00,a@x.ru,alice,https://github.com/alice/hw01,abc9999\n"
        "15.02.2027 21:00:00,b@x.ru,WRONGLOGIN,https://github.com/bob/hw01,def5678\n"
        "15.02.2027 21:00:00,c@x.ru,carol,https://github.com/carol/hw01,not a sha!\n",
        encoding="utf-8")
    roster = Roster([Student("alice", login="alice", email="a@x.ru"), Student("bob", login="bob", email="b@x.ru"),
                     Student("carol", login="carol")])
    subs, warnings = read_submissions(course, csv, roster)
    assert subs["alice"].sha == "abc9999"
    assert any("несколько отправок" in w for w in warnings)
    assert "bob" in subs and subs["bob"].repo_url == "https://github.com/bob/hw01"  # сопоставлен по email
    assert subs["carol"].problems and "не похож" in subs["carol"].problems[0]


def test_form_with_name_and_group_only(tmp_path: Path):
    """Форма без логина и хэша: ФИО, группа, ссылка, комментарий."""
    course = Course(name="c", root=tmp_path)
    csv = tmp_path / "hw01.csv"
    csv.write_text(
        "Отметка времени,ФИО,Группа,Ссылка на репозиторий,Комментарий\n"
        "15.02.2027 20:00:00,Иванов Алексей Игоревич,БИ-301,https://github.com/ivanov-ai/hw01,\n"
        "15.02.2027 20:10:00,Петрова Мария,би 301,https://github.com/mpetrova/hw01/commit/0123abc,сделала часть 2 не до конца\n"
        "15.02.2027 20:20:00,Сидоров Дмитрий,БИ-302,https://github.com/sid/hw01/tree/main/hw01,\n"
        "15.02.2027 20:30:00,Неизвестный Студент,БИ-301,https://github.com/xx/hw01,\n",
        encoding="utf-8")
    roster = Roster([Student("иванов-алексей-игоревич-би301", name="Иванов Алексей Игоревич", group="БИ-301"),
                     Student("петрова-мария-сергеевна-би301", name="Петрова Мария Сергеевна", group="БИ-301"),
                     Student("сидоров-дмитрий-би302", name="Сидоров Дмитрий", group="БИ-302")])
    subs, warnings = read_submissions(course, csv, roster)
    iv = subs["иванов-алексей-игоревич-би301"]
    assert iv.sha == "HEAD" and iv.github_login == "ivanov-ai" and not iv.problems
    pe = subs["петрова-мария-сергеевна-би301"]          # без отчества и с «би 301» — всё равно нашли
    assert pe.sha == "0123abc" and pe.comment.startswith("сделала")
    si = subs["сидоров-дмитрий-би302"]
    assert si.sha == "main" and si.subdir == "hw01"
    unknown = [s for k, s in subs.items() if k.startswith("неизвестный")]
    assert unknown and "не найден в roster.csv" in unknown[0].problems[0]
    assert roster.resolve("Петрова Мария") == "петрова-мария-сергеевна-би301"
    assert roster.resolve("Иванов Алексей Игоревич, БИ-301") == "иванов-алексей-игоревич-би301"


def test_token_similarity_ignores_renames():
    a = "x = df['price'].mean()\nprint(x)\n"
    b = "avg_price = data['price'].mean()\nprint(avg_price)\n"
    c = "import numpy as np\nfor i in range(10):\n    print(i ** 2)\n"
    assert similarity(normalize_tokens(a), normalize_tokens(b)) > 0.95
    # на крошечных фрагментах базовое сходство высокое (мало разных токенов); важно, что оно заметно ниже
    assert similarity(normalize_tokens(a), normalize_tokens(c)) < 0.7
