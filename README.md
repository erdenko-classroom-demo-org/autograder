# autograder

Проверка учебных заданий, сданных как публичные GitHub-репозитории через Google-форму. Не привязан к конкретному курсу: курс описывается двумя репозиториями с фиксированной структурой папок (публичный с заданиями и приватный с тестами), грейдер их читает.

Что умеет: забрать указанный в форме коммит каждого студента, подложить данные, выполнить ноутбук, прогнать декларативные и pytest-проверки, посчитать штраф за опоздание по времени формы, применить ручные правки и продления, выдать CSV под ведомость, отзыв каждому студенту и публичную таблицу по кодам, сравнить решения на схожесть.

## Установка

```bash
pip install git+https://github.com/ORG/autograder          # или из папки: pip install -e autograder
grader --version
```

Требуется Python ≥ 3.10 и git. Для выполнения ноутбуков студентов нужны библиотеки, которыми они пользуются (`requirements-grading.txt` в приватном репозитории курса).

## Команды

Запускаются из корня приватного репозитория курса (`course.yaml` рядом). Публичный репозиторий ищется по `public_repo_path` из `course.yaml`, переменной `GRADER_PUBLIC` или флагу `--public`.

| команда | что делает |
|---|---|
| `grader run hw01 [--only \"ФИО\" ...] [--workers 4] [--clean]` | проверить задание по `submissions/hw01.csv`; результаты в `results/hw01/` (`--only` пишет в `results/hw01/partial/`) |
| `grader plagiarism hw01 [--files ...]` | сравнить решения между собой → `results/hw01/plagiarism/` |
| `grader selftest [hw01]` | эталон набирает максимум, стартер — нет; код возврата 1 при провале (для CI) |
| `grader validate [hw01]` | yaml валиден, баллы сходятся, нет утечек в публичную часть; работает и без приватного репозитория: `grader --public . validate` |
| `grader roster-check hw01 [--ping]` | кто не сдал, ошибки в форме, доступность репозиториев |
| `grader codes` | персональные коды студентов для публичной таблицы (CSV) |
| `grader new hw05 --title "..."` | заготовка задания в обоих репозиториях |

## Структура курса

```
<public>/assignments/<id>/assignment.yaml      метаданные, дедлайны, публичные проверки
<public>/assignments/<id>/README.md            условие
<public>/assignments/<id>/starter/             шаблон репозитория студента
<private>/course.yaml                          настройки курса
<private>/roster.csv                           name,group,email,grader[,login]
<private>/submissions/<id>.csv                 выгрузка Google-формы сдачи
<private>/assignments/<id>/grading.yaml        данные, скрытые проверки, антиплагиат
<private>/assignments/<id>/{data,tests_hidden,solution}/
<private>/adjustments.csv  extensions.csv      ручные правки и продления
<private>/results/<id>/                        результаты прогонов
```

### course.yaml

```yaml
name: "Course"
timezone: "Europe/Moscow"                 # все дедлайны и времена формы — в этом поясе
public_repo_path: "../classroom-demo"
salt: "random-string"                     # для персональных кодов студентов
roster_path: roster.csv
submissions_dir: submissions              # форма на задание: submissions/<id>.csv
submissions_file: ""                      # или одна общая форма: submissions/all.csv (фильтр по колонке «Задание»)
missing_sha: head                         # head | reject — что делать, если хэш коммита в форме пуст
results_dir: results
work_dir: .work
adjustments_path: adjustments.csv
extensions_path: extensions.csv
form_columns:                             # названия вопросов в Google-форме
  timestamp: "Отметка времени"            # обязательно
  repo: "Ссылка на репозиторий"           # обязательно; ссылка на репозиторий или на коммит (.../commit/<sha>)
  name: "ФИО"                             # студент узнаётся по ФИО + группе, email или логину — что есть
  group: "Группа"
  email: "Адрес электронной почты"
  login: "Логин GitHub"
  comment: "Комментарий"                  # необязательно
  sha: "Хэш коммита"                      # необязательно
  subdir: "Папка в репозитории"           # необязательно: если один репозиторий на весь курс
  assignment: "Задание"                   # необязательно: при одной общей форме
form_timestamp_format: "%d.%m.%Y %H:%M:%S"
after_hard_deadline: zero                 # zero | ignore
plagiarism_threshold: 0.85
```

### assignment.yaml (публичный)

```yaml
id: hw01
title: "..."
max_points: 10
deadline:
  soft: "2027-02-15 23:59"
  hard: "2027-02-22 23:59"
  penalty_per_day: 1
checks:                                   # visibility: public
  - {id: public_tests, type: pytest, points: 1, path: tests_public}
  - {id: report_written, type: file_min_length, points: 1, path: report.md, min_chars: 300, manual_review: true}
```

### grading.yaml (приватный)

```yaml
data:
  - {src: data/train.csv, dst: data/train.csv}       # копируется из приватной папки задания в репозиторий студента
checks:                                               # visibility: hidden
  - {id: notebook_runs, type: notebook_executes, points: 1, path: hw01.ipynb, outputs: [outputs/answers.json]}
  - {id: answers, type: json_values, points: 3, path: outputs/answers.json, expected: "private:tests_hidden/expected.json", rel_tol: 0.01}
  - id: model_quality
    type: metric_threshold
    points: 3
    predictions: outputs/predictions.csv
    truth: private:data/test_target.csv
    id_column: id
    target_column: price
    metric: r2
    scale: [{min: 0.9, points: 3}, {min: 0.8, points: 2}, {min: 0.6, points: 1}]
  - {id: hidden_tests, type: pytest, points: 1, path: "private:tests_hidden"}
plagiarism:
  files: ["hw01.ipynb"]
```

Сумма `points` по обоим файлам должна равняться `max_points`. Каждая проверка: `id`, `type`, `points`, опционально:

- `manual_review: true` — балл ставится автоматически, строка помечается «к просмотру»;
- `order: N` — порядок выполнения (по умолчанию `notebook_executes`/`command` идут первыми, остальные — в порядке записи);
- `on_fail: stop` — если проверка не пройдена, остальные не выполняются и получают 0 («не выполнил часть 1 — дальше не проверяем»);
- `requires: [id, ...]` — баллы только если перечисленные проверки пройдены.

## Типы проверок

| type | поля | баллы |
|---|---|---|
| `file_exists` | `path` / `paths` | всё или ничего |
| `file_min_length` | `path`, `min_chars` | всё или ничего |
| `csv_columns` | `path`, `columns`, `min_rows`, `no_nan` | всё или ничего |
| `json_values` | `path`, `expected` (путь или словарь), `keys`, `rel_tol`, `abs_tol`, `partial` | по ключам (partial) или всё/ничего |
| `metric_threshold` | `predictions`, `truth`, `id_column`, `target_column`, `metric` (r2, mae, rmse, mse, mape, accuracy, f1, f1_macro, roc_auc, logloss), `scale`, `higher_is_better` | по шкале |
| `notebook_executes` | `path`, `timeout`, `cell_timeout`, `kernel`, `outputs` | всё или ничего; при падении возвращает файлы из коммита студента |
| `command` | `cmd`, `timeout`, `expect_in_output` | всё или ничего |
| `pytest` | `path`, `timeout`, `noconftest` | сумма `@pytest.mark.points(n)` пройденных тестов (по умолчанию 1 за тест); при расхождении с `points` масштабируется |

Префиксы путей: `private:` — приватная папка задания, `public:` — публичная, без префикса — репозиторий студента. Тестам pytest доступны фикстуры `repo_dir`, `private_dir`, `public_dir` (плагин `autograder.pytest_plugin`).

Новый тип проверки — функция с декоратором `@register("name")` в `autograder/checks/`, принимает `(ctx, check)` и возвращает `CheckResult`.

## Сдача и баллы

Сдача — строка выгрузки формы: время (ставит Google, не подделать), кто (ФИО + группа, email или логин GitHub — сопоставляется с `roster.csv`; ФИО можно без отчества и в другом порядке, если совпадение однозначно), ссылка на репозиторий или на коммит. Из нескольких строк одного студента берётся последняя. Форма — своя на каждое задание (`submissions/<id>.csv`) или одна общая (колонка «Задание», `submissions_file`). Коммит не указан: `missing_sha: head` — берётся последний коммит ветки по умолчанию с пометкой в `notes`; `reject` — ошибка сдачи. В `adjustments.csv`/`extensions.csv` студент указывается ФИО (или «ФИО, группа»), логином или ключом. Грейдер делает `git fetch --depth 1 origin <sha>` (GitHub это разрешает) или полный клон и проверяет ровно этот коммит.

`raw` = сумма проверок, не больше `max_points`. Опоздание — начатые сутки после мягкого дедлайна (с учётом `extensions.csv`): `penalty = min(raw, days × penalty_per_day)`; после жёсткого дедлайна — 0 (`after_hard_deadline: zero`). `adjustments.csv`: `delta` прибавляется, `set_total` заменяет итог. Итог не ниже 0; правки могут поднять его выше максимума (бонус).

## Результаты

`results/<id>/results.csv` (`;`, UTF-8-BOM — под ведомость), `results.json` (машиночитаемо), `summary.md` (сводка для проверяющего), `details/<студент>.md` (отзыв), `public_results.md` (таблица по кодам `sha256(salt:login)[:8]`), `plagiarism/` (`pairs.csv`, `report.md`, `sources/` — обезличенные .py для JPlag).

## Антиплагиат

Ноутбуки → код (магии закомментированы) → токены с обезличенными именами и константами → доля совпадений (difflib). Пары выше порога помечаются; решение — за человеком, через `adjustments.csv`. При наличии `tools/jplag.jar` (или `JPLAG_JAR`) дополнительно запускается JPlag.

## Безопасность

Код студентов выполняется. В GitHub Actions это одноразовая виртуальная машина без секретов курса в окружении (данные и тесты лежат в файлах, но результатов это не искажает — проверяемый коммит зафиксирован формой). Локально запускайте в отдельном пользователе/контейнере, если не доверяете коду, или ограничивайтесь публичным CI студентов для тяжёлых вещей вроде Docker.

## Разработка

```bash
pip install -e . && python -m pytest -q tests
```
