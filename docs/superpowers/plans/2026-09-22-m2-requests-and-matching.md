# M2. Заявки и матчинг: план реализации

> **Для агента-исполнителя:** одна фаза — одна сессия. В начале сессии читаешь
> «Общие ограничения», «Принятые решения» и свою фазу; чужие фазы не трогаешь.
> В конце сессии дописываешь в этот файл раздел «Результат фазы N» и стартовый
> промпт для следующей фазы, затем делаешь коммит. Шаги помечены `- [x]` —
> отмечай по ходу.

**Цель:** брокер заводит заявку покупателя во внешней таблице и получает из базы
на 20 826 объявлений ранжированный список подходящих вариантов — в обе стороны:
новая заявка по всей базе и новые объявления по всем активным заявкам.

**Архитектура:** порт `RequestsSource` отдаёт сырые строки, домен превращает их
в `Request`, домен же считает кластеры и баллы, оркестратор `listam/matching.py`
складывает результат в `matches`. Три новых модуля домена — чистые функции без
базы и без сети; всё внешнее по-прежнему за портами, выбор адаптера — в конфиге.

**Стек:** Python 3.12, SQLite, pytest, openpyxl, PyYAML. Новых зависимостей
план не вводит: `gsheet` использует уже объявленные в `requirements-gdrive.txt`
`google-api-python-client` и `google-auth`.

**Спека:** `docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md` —
читается вместе с планом, решения 1–11 в фазах не пересматриваются.

## Исходное состояние (снято 22.09.2026)

| Что | Значение |
| --- | --- |
| HEAD | `5f165f9` (коммит спеки), дерево чистое |
| Батарея | 427 passed, 12 skipped |
| Схема базы | 6 |
| Боевая база | 20 826 объявлений: 20 619 активных, 207 снятых; прогонов 8 |
| `requests`, `matches` | таблицы есть с миграции 001, обе пусты |
| `listings.cluster_id` | колонка есть, заполнена у 0 из 20 826 |
| `street` | заполнена у 16 602 из 20 826 (79,7%) — это основание решения 4 |

## Global Constraints (нарушать нельзя)

- Схема базы меняется **только** новой версионированной миграцией в `listam/migrations/`.
  В M2 миграция ровно одна — 007, целиком в фазе 1.
- Новый метод порта — это новый контрактный тест в `tests/contracts/`.
- Ни один путь, ключ, идентификатор и имя адаптера не зашит в код: внешнее — за портом,
  выбор — в конфиге, секреты — только в `.env`, подключение — в `listam/wiring.py`.
- Все отметки времени в UTC (`to_iso`/`from_iso` из `listam/adapters/db_sqlite.py`),
  пути относительные от корня проекта.
- **Пороги в конфиге не поднимаются, чтобы тест позеленел.** Порог, который мешает, —
  это находка или решение; и то и другое записывается в отчёт фазы, а не обходится.
- Ноль в пороге значит «ноль», а не «выключено»; выключается `null`. Читать пороги —
  только через `listam.config.threshold`, не через `config.get(...) or default`.
- Бессмысленный ввод командной строки отклоняется на входе кодом возврата 2.
- Тесты — только `.venv/Scripts/python.exe -m pytest -q`. Системный python не годится:
  в нём нет `openpyxl`.
- Любой прогон CLI из скрипта — только с `PYTHONIOENCODING=utf-8`, иначе дочерний
  процесс кодирует stdout в cp1252 и глотает кириллицу целиком.
- Телефоны (`contacts`) — M4, уведомления — M3. Не трогаем. `listam/crawler.py`
  в M2 не меняется (решение 9).
- Числа «ожидается N passed» — арифметика от 427 плюс тесты фазы. Разошлось
  на один-два — не повод подгонять: сверь, что именно добавилось, и поправь
  число в плане.

## Карта файлов

| Файл | Ответственность | Фаза |
| --- | --- | --- |
| `listam/migrations/007_requests_and_matches.sql` | схема 6 → 7 | 1 |
| `listam/domain/models.py` | `Request`, `Match` рядом с `Listing` | 1 |
| `listam/domain/requests.py` | разбор и нормализация строк заявки | 1 |
| `listam/adapters/db_sqlite.py` | хранение заявок, кластеров, матчей | 1, 3, 5 |
| `listam/ports/database.py` | новые методы порта | 1, 3, 5 |
| `listam/ports/requests_source.py` | `rows()`, `describe()`, общий `active_requests()` | 2 |
| `listam/adapters/requests_csv.py` | заявки из CSV-файла | 2 |
| `listam/adapters/requests_gsheet.py` | заявки из Google Sheet | 2 |
| `listam/domain/clustering.py` | кластеры-дубли | 3 |
| `listam/domain/scoring.py` | баллы и жёсткие критерии | 4 |
| `listam/matching.py` | оркестрация матчинга | 5 |
| `listam/adapters/exporter_xlsx.py` | лист «Матчи» | 6 |
| `listam/cli.py` | команды `requests`, `cluster`, `match`, `matches` | 2, 3, 5, 6 |
| `listam/doctor.py` | проверка источника заявок и секции `match` | 2, 4 |
| `listam/wiring.py` | сборка `csv`/`gsheet` | 2 |
| `config/dev.yaml`, `config/prod.yaml` | секции `requests` и `match` | 2, 4 |

---

# Фаза 1. Заявка как сущность

**Одна сессия.** Схема, доменная модель заявки, разбор строк, хранение.
Источника заявок ещё нет — строки в тестах кладутся руками.

**Ожидается после фазы:** ~455 passed, 12 skipped, схема базы 7.

### Задача 1.1. Миграция 007

**Файлы:**
- Создать: `listam/migrations/007_requests_and_matches.sql`
- Тест: `tests/test_migrations.py`, `tests/contracts/test_database_contract.py`

**Produces:** схема 7 — колонки, на которые опираются все последующие фазы.

- [x] **Шаг 1: падающий тест на версию схемы и колонки**

```python
# tests/test_migrations.py — дописать
def test_migration_007_adds_request_and_match_columns(tmp_path):
    db = SqliteDatabase(tmp_path / "m7.sqlite")
    db.connect()
    db.migrate()
    assert db.schema_version() == 7
    requests_columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(requests)")}
    assert {"districts_priority", "floor_min", "floor_max",
            "no_first_floor", "no_last_floor", "updated_at", "source_row"} <= requests_columns
    matches_columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(matches)")}
    assert {"run_id", "breakdown", "cluster_id", "cluster_size",
            "cluster_spread_usd", "first_matched_at"} <= matches_columns
    runs_columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(runs)")}
    assert "new_matches" in runs_columns
    db.close()
```

- [x] **Шаг 2: убедиться, что тест падает**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_migrations.py -k 007`
Ожидается: FAIL — `schema_version() == 6`.

- [x] **Шаг 3: написать миграцию**

```sql
-- Версия 7: заявки покупателей и матчи.
--
-- requests.districts_priority — приоритетные районы отдельно от допустимых:
--   фактор «район» весом 20 различает «согласен» и «хочет именно там».
-- requests.floor_min/floor_max/no_first_floor/no_last_floor — этажные правила
--   колонками, а не строкой floor_rules: скоринг читает их без разбора
--   свободного текста, а floor_rules остаётся человеческой заметкой.
-- requests.source_row — сырая строка источника целиком (JSON). Спорную заявку
--   надо уметь предъявить ровно в том виде, в каком её ввёл человек.
-- matches.breakdown — разбор балла по факторам (JSON). Без него на вопрос
--   «почему 68, а не 71» нечего ответить ни в отладке, ни клиенту.
-- matches.cluster_* — снимок кластера на момент матча: сколько объявлений
--   в нём было и какой разброс цен. Единица показа — кластер, а не карточка.
-- matches.first_matched_at — когда матч появился впервые. matched_at двигает
--   каждый пересчёт, и по нему «когда мы это нашли» уже не узнать.
-- runs.new_matches — счётчик назван в спеке с M0 и до сих пор не заведён.

ALTER TABLE requests ADD COLUMN districts_priority TEXT;
ALTER TABLE requests ADD COLUMN floor_min INTEGER;
ALTER TABLE requests ADD COLUMN floor_max INTEGER;
ALTER TABLE requests ADD COLUMN no_first_floor INTEGER DEFAULT 0;
ALTER TABLE requests ADD COLUMN no_last_floor INTEGER DEFAULT 0;
ALTER TABLE requests ADD COLUMN updated_at TEXT;
ALTER TABLE requests ADD COLUMN source_row TEXT;

ALTER TABLE matches ADD COLUMN run_id INTEGER;
ALTER TABLE matches ADD COLUMN breakdown TEXT;
ALTER TABLE matches ADD COLUMN cluster_id TEXT;
ALTER TABLE matches ADD COLUMN cluster_size INTEGER DEFAULT 1;
ALTER TABLE matches ADD COLUMN cluster_spread_usd REAL;
ALTER TABLE matches ADD COLUMN first_matched_at TEXT;

ALTER TABLE runs ADD COLUMN new_matches INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_matches_request_score ON matches(request_id, score DESC);
CREATE INDEX IF NOT EXISTS idx_matches_listing ON matches(listing_id);
CREATE INDEX IF NOT EXISTS idx_listings_cluster ON listings(cluster_id);
CREATE INDEX IF NOT EXISTS idx_listings_status_district ON listings(status, district);
CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
```

- [x] **Шаг 4: тест проходит, вся батарея тоже**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: 428 passed, 12 skipped. Если падает `test_docs.py` или тест,
проверяющий число миграций, — поправь его: это ожидаемое следствие, а не находка.

- [x] **Шаг 5: накат на копию боевой базы**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "import shutil; shutil.copy('data/listam.sqlite', 'data/listam-before-m2-007.sqlite')"
```

Затем в Python: открыть копию `SqliteDatabase`, `migrate()`, проверить
`schema_version() == 7`, `SELECT COUNT(*) FROM listings` = 20 826,
`SELECT COUNT(*) FROM runs` = 8. Боевую базу `data/listam.sqlite` фаза 1
**не трогает** — она мигрируется в фазе 7.

- [x] **Шаг 6: коммит**

```bash
git add listam/migrations/007_requests_and_matches.sql tests/test_migrations.py
git commit -m "feat(db): миграция 007 — колонки заявок, матчей и счётчик new_matches"
```

### Задача 1.2. Доменная модель заявки и матча

**Файлы:**
- Изменить: `listam/domain/models.py`
- Тест: `tests/test_requests.py` (создать)

**Interfaces — Produces:**
```python
@dataclass
class Request:
    id: int | None = None
    external_id: str | None = None
    client_name: str | None = None
    client_phone: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    status: str = "active"
    budget_max: float | None = None
    budget_stretch: float | None = None
    districts: list[str] = field(default_factory=list)
    districts_priority: list[str] = field(default_factory=list)
    rooms: list[int] = field(default_factory=list)
    area_min: float | None = None
    area_max: float | None = None
    floor_min: int | None = None
    floor_max: int | None = None
    no_first_floor: bool = False
    no_last_floor: bool = False
    must_have: str | None = None
    nice_to_have: str | None = None
    floor_rules: str | None = None
    notes: str | None = None
    source_row: str | None = None

    def stretch(self, percent: float) -> float | None: ...

@dataclass
class Match:
    id: int | None = None
    request_id: int | None = None
    listing_id: str | None = None
    score: float | None = None
    matched_at: datetime | None = None
    first_matched_at: datetime | None = None
    status: str = "new"
    reject_reason: str | None = None
    run_id: int | None = None
    breakdown: dict | None = None
    cluster_id: str | None = None
    cluster_size: int = 1
    cluster_spread_usd: float | None = None
```

- [x] **Шаг 1: падающий тест на растянутый бюджет**

```python
# tests/test_requests.py
from listam.domain.models import Request


def test_stretch_defaults_to_a_percent_above_the_budget():
    assert Request(budget_max=100_000).stretch(10) == 110_000


def test_an_explicit_stretch_wins_over_the_percent():
    assert Request(budget_max=100_000, budget_stretch=105_000).stretch(10) == 105_000


def test_a_request_without_a_budget_has_no_stretch():
    assert Request().stretch(10) is None
```

- [x] **Шаг 2: убедиться, что тест падает**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_requests.py`
Ожидается: FAIL — `ImportError: cannot import name 'Request'`.

- [x] **Шаг 3: добавить `Request` и `Match` в `listam/domain/models.py`**

```python
@dataclass
class Request:
    """Заявка покупателя — ядро системы. Фильтр производен от неё, а не наоборот."""

    # ... поля из блока Produces выше ...

    def stretch(self, percent: float) -> float | None:
        """Растянутый потолок бюджета.

        Вариант на 5 000 дороже бюджета всё равно стоит звонка: жёсткий
        критерий отсекает по нему, а не по `budget_max`.
        """
        if self.budget_stretch is not None:
            return self.budget_stretch
        if self.budget_max is None:
            return None
        return self.budget_max * (1 + float(percent) / 100)
```

- [x] **Шаг 4: тест проходит**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_requests.py`
Ожидается: 3 passed.

- [x] **Шаг 5: коммит**

```bash
git add listam/domain/models.py tests/test_requests.py
git commit -m "feat(domain): заявка и матч как доменные сущности"
```

### Задача 1.3. Разбор строки заявки

**Файлы:**
- Создать: `listam/domain/requests.py`
- Тест: `tests/test_requests.py` (дописать)

**Interfaces — Produces:**
```python
COLUMNS: tuple[str, ...]          # имена колонок источника в каноническом порядке

@dataclass
class RequestError:
    row_number: int
    external_id: str | None
    column: str | None
    value: str | None
    message: str

    def render(self) -> str: ...   # «строка 3 (заявка R-2): budget_max = «примерно 100к» — не число»

def parse_row(row: dict, row_number: int = 0) -> Request      # бросает RequestParseError
def parse_rows(rows: Iterable[dict]) -> tuple[list[Request], list[RequestError]]
```

- [x] **Шаг 1: падающие тесты на нормализацию и на отказы**

```python
# tests/test_requests.py — дописать
import pytest

from listam.domain.requests import RequestParseError, parse_row, parse_rows


def row(**over) -> dict:
    fields = {
        "id": "R-1", "client_name": "Ани", "client_phone": "+374 00 000000",
        "status": "active", "budget_max": "120 000 $", "budget_stretch": "",
        "districts": "Кентрон, Арабкир", "districts_priority": "Кентрон",
        "rooms": "2-3", "area_min": "60", "area_max": "95",
        "floor_min": "2", "floor_max": "", "no_first_floor": "да", "no_last_floor": "нет",
        "must_have": "балкон", "nice_to_have": "ремонт", "notes": "", "floor_rules": "",
    }
    fields.update(over)
    return fields


def test_money_is_read_without_spaces_and_currency_signs():
    assert parse_row(row()).budget_max == 120_000


def test_rooms_are_read_both_as_a_list_and_as_a_range():
    assert parse_row(row(rooms="2-3")).rooms == [2, 3]
    assert parse_row(row(rooms="2, 4")).rooms == [2, 4]


def test_districts_are_split_and_trimmed():
    parsed = parse_row(row())
    assert parsed.districts == ["Кентрон", "Арабкир"]
    assert parsed.districts_priority == ["Кентрон"]


def test_yes_and_no_are_read_in_any_of_the_usual_spellings():
    for yes in ("да", "Да", "yes", "1", "+", "true"):
        assert parse_row(row(no_first_floor=yes)).no_first_floor is True
    for no in ("нет", "no", "0", "-", "", "false"):
        assert parse_row(row(no_first_floor=no)).no_first_floor is False


def test_an_empty_optional_column_stays_empty_and_is_not_guessed():
    parsed = parse_row(row(area_max="", floor_max=""))
    assert parsed.area_max is None
    assert parsed.floor_max is None


def test_the_whole_source_row_is_kept_as_written():
    parsed = parse_row(row(notes="звонить после шести"))
    assert "звонить после шести" in parsed.source_row


def test_a_budget_that_is_not_a_number_is_a_refusal_naming_the_column():
    with pytest.raises(RequestParseError) as exc:
        parse_row(row(budget_max="примерно 100к"), row_number=3)
    assert exc.value.column == "budget_max"
    assert "примерно 100к" in exc.value.value


def test_a_stretch_below_the_budget_is_a_refusal():
    with pytest.raises(RequestParseError) as exc:
        parse_row(row(budget_max="100000", budget_stretch="90000"))
    assert exc.value.column == "budget_stretch"


def test_an_area_range_upside_down_is_a_refusal():
    with pytest.raises(RequestParseError) as exc:
        parse_row(row(area_min="95", area_max="60"))
    assert exc.value.column == "area_max"


def test_an_unknown_status_is_a_refusal_and_not_read_as_active():
    with pytest.raises(RequestParseError) as exc:
        parse_row(row(status="в работе"))
    assert exc.value.column == "status"


def test_a_request_without_an_id_is_a_refusal():
    with pytest.raises(RequestParseError) as exc:
        parse_row(row(id=""))
    assert exc.value.column == "id"


def test_a_priority_district_outside_the_allowed_list_is_a_refusal():
    with pytest.raises(RequestParseError) as exc:
        parse_row(row(districts="Кентрон", districts_priority="Давташен"))
    assert exc.value.column == "districts_priority"


def test_one_broken_row_does_not_cost_the_others():
    parsed, errors = parse_rows([row(id="R-1"), row(id="R-2", budget_max="сколько-то"),
                                 row(id="R-3")])
    assert [item.external_id for item in parsed] == ["R-1", "R-3"]
    assert len(errors) == 1
    assert errors[0].external_id == "R-2"
    assert "budget_max" in errors[0].render()
```

- [x] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_requests.py`
Ожидается: FAIL — модуля `listam.domain.requests` нет.

- [x] **Шаг 3: написать разбор**

```python
"""Разбор строки заявки: нестрогий по форме, строгий по смыслу.

Форма человеческая: пробелы, знак валюты, «да»/«yes»/«+» — всё читается.
Смысл — нет: «примерно 100к» в бюджете, «много» в комнатах и перевёрнутый
диапазон площади не истолковываются, а отклоняют строку целиком (решение 2).
Истолкованная наугад заявка ошибётся молча и покажет клиенту не то.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable

from listam.domain.models import Request

COLUMNS = (
    "id", "client_name", "client_phone", "status",
    "budget_max", "budget_stretch", "districts", "districts_priority", "rooms",
    "area_min", "area_max", "floor_min", "floor_max",
    "no_first_floor", "no_last_floor",
    "must_have", "nice_to_have", "floor_rules", "notes",
)

STATUSES = ("active", "paused", "closed")

TRUE_WORDS = {"да", "yes", "y", "1", "+", "true", "истина"}
FALSE_WORDS = {"нет", "no", "n", "0", "-", "false", "ложь", ""}

# Разделители в списках: запятая, точка с запятой, перевод строки.
SPLIT = re.compile(r"[,;\n]+")
# Всё, что не цифра и не разделитель дробной части: пробелы, $, знак валюты.
NOT_A_NUMBER = re.compile(r"[^\d.,-]")


class RequestParseError(Exception):
    def __init__(self, column: str, value: str | None, message: str):
        super().__init__(f"{column} = {value!r} — {message}")
        self.column = column
        self.value = "" if value is None else str(value)
        self.message = message


@dataclass
class RequestError:
    row_number: int
    external_id: str | None
    column: str | None
    value: str | None
    message: str

    def render(self) -> str:
        who = f" (заявка {self.external_id})" if self.external_id else ""
        return (f"строка {self.row_number}{who}: "
                f"{self.column} = {self.value!r} — {self.message}")


def _text(row: dict, column: str) -> str:
    return str(row.get(column, "") or "").strip()


def _number(row: dict, column: str) -> float | None:
    raw = _text(row, column)
    if not raw:
        return None
    cleaned = NOT_A_NUMBER.sub("", raw).replace(",", ".").replace(" ", "")
    # Тысячи через точку («120.000») от дробной части не отличить, поэтому
    # точка, после которой ровно три цифры и ничего больше, — это разряд.
    if re.fullmatch(r"-?\d+\.\d{3}", cleaned):
        cleaned = cleaned.replace(".", "")
    try:
        return float(cleaned)
    except ValueError:
        raise RequestParseError(column, raw, "не число")


def _integer(row: dict, column: str) -> int | None:
    value = _number(row, column)
    if value is None:
        return None
    if value != int(value):
        raise RequestParseError(column, _text(row, column), "не целое число")
    return int(value)


def _flag(row: dict, column: str) -> bool:
    raw = _text(row, column).lower()
    if raw in TRUE_WORDS:
        return True
    if raw in FALSE_WORDS:
        return False
    raise RequestParseError(column, raw, "не да и не нет")


def _list(row: dict, column: str) -> list[str]:
    raw = _text(row, column)
    return [part.strip() for part in SPLIT.split(raw) if part.strip()]


def _rooms(row: dict) -> list[int]:
    raw = _text(row, "rooms")
    if not raw:
        return []
    values: set[int] = set()
    for part in SPLIT.split(raw):
        part = part.strip()
        if not part:
            continue
        span = re.fullmatch(r"(\d+)\s*[-–—]\s*(\d+)", part)
        if span:
            low, high = int(span.group(1)), int(span.group(2))
            if low > high:
                raise RequestParseError("rooms", part, "диапазон задом наперёд")
            values.update(range(low, high + 1))
            continue
        if not part.isdigit():
            raise RequestParseError("rooms", part, "не число комнат")
        values.add(int(part))
    return sorted(values)


def parse_row(row: dict, row_number: int = 0) -> Request:
    external_id = _text(row, "id")
    if not external_id:
        raise RequestParseError("id", external_id, "заявка без идентификатора")

    status = _text(row, "status").lower() or "active"
    if status not in STATUSES:
        raise RequestParseError("status", status,
                                f"неизвестный статус; бывают: {', '.join(STATUSES)}")

    budget_max = _number(row, "budget_max")
    budget_stretch = _number(row, "budget_stretch")
    if budget_max is not None and budget_stretch is not None and budget_stretch < budget_max:
        raise RequestParseError("budget_stretch", _text(row, "budget_stretch"),
                                "растянутый бюджет меньше основного")

    area_min = _number(row, "area_min")
    area_max = _number(row, "area_max")
    if area_min is not None and area_max is not None and area_max < area_min:
        raise RequestParseError("area_max", _text(row, "area_max"),
                                "верхняя граница площади ниже нижней")

    floor_min = _integer(row, "floor_min")
    floor_max = _integer(row, "floor_max")
    if floor_min is not None and floor_max is not None and floor_max < floor_min:
        raise RequestParseError("floor_max", _text(row, "floor_max"),
                                "верхний этаж ниже нижнего")

    districts = _list(row, "districts")
    priority = _list(row, "districts_priority")
    outside = [item for item in priority if districts and item not in districts]
    if outside:
        raise RequestParseError("districts_priority", ", ".join(outside),
                                "приоритетный район не входит в список допустимых")

    return Request(
        external_id=external_id,
        client_name=_text(row, "client_name") or None,
        client_phone=_text(row, "client_phone") or None,
        status=status,
        budget_max=budget_max,
        budget_stretch=budget_stretch,
        districts=districts,
        districts_priority=priority,
        rooms=_rooms(row),
        area_min=area_min,
        area_max=area_max,
        floor_min=floor_min,
        floor_max=floor_max,
        no_first_floor=_flag(row, "no_first_floor"),
        no_last_floor=_flag(row, "no_last_floor"),
        must_have=_text(row, "must_have") or None,
        nice_to_have=_text(row, "nice_to_have") or None,
        floor_rules=_text(row, "floor_rules") or None,
        notes=_text(row, "notes") or None,
        source_row=json.dumps(row, ensure_ascii=False, sort_keys=True),
    )


def parse_rows(rows: Iterable[dict]) -> tuple[list[Request], list[RequestError]]:
    """Разобранные заявки и отклонённые строки. Одна опечатка не стоит остальных."""
    parsed: list[Request] = []
    errors: list[RequestError] = []
    for number, row in enumerate(rows, start=1):
        try:
            parsed.append(parse_row(row, row_number=number))
        except RequestParseError as exc:
            errors.append(RequestError(
                row_number=number,
                external_id=str(row.get("id") or "").strip() or None,
                column=exc.column, value=exc.value, message=exc.message,
            ))
    return parsed, errors
```

- [x] **Шаг 4: тесты проходят**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_requests.py`
Ожидается: все зелёные (около 16 штук).

- [x] **Шаг 5: коммит**

```bash
git add listam/domain/requests.py tests/test_requests.py
git commit -m "feat(domain): разбор строки заявки — форма человеческая, смысл строгий"
```

### Задача 1.4. Хранение заявок: новые методы порта

**Файлы:**
- Изменить: `listam/ports/database.py`, `listam/adapters/db_sqlite.py`
- Тест: `tests/contracts/test_database_contract.py`

**Interfaces — Produces:**
```python
Database.upsert_request(request: Request, now: datetime) -> str   # new | updated | unchanged
Database.iter_requests(status: str | None = "active") -> Iterator[Request]
Database.get_request(external_id: str) -> Request | None
```

- [x] **Шаг 1: контрактные тесты (новый метод порта — новый контрактный тест)**

```python
# tests/contracts/test_database_contract.py — дописать
from listam.domain.models import Request


def make_request(external_id="R-1", **over) -> Request:
    fields = dict(
        external_id=external_id, client_name="Ани", client_phone="+374 00 000000",
        status="active", budget_max=120_000.0, districts=["Кентрон", "Арабкир"],
        districts_priority=["Кентрон"], rooms=[2, 3], area_min=60.0, area_max=95.0,
        floor_min=2, no_first_floor=True,
    )
    fields.update(over)
    return Request(**fields)


def test_a_new_request_is_stored_and_read_back_whole(db):
    assert db.upsert_request(make_request(), NOW) == "new"
    stored = db.get_request("R-1")
    assert stored.districts == ["Кентрон", "Арабкир"]
    assert stored.districts_priority == ["Кентрон"]
    assert stored.rooms == [2, 3]
    assert stored.no_first_floor is True
    assert stored.created_at == NOW


def test_the_same_request_read_twice_is_not_a_change(db):
    db.upsert_request(make_request(), NOW)
    assert db.upsert_request(make_request(), LATER) == "unchanged"


def test_a_changed_request_is_an_update_and_keeps_its_birthday(db):
    db.upsert_request(make_request(), NOW)
    assert db.upsert_request(make_request(budget_max=150_000.0), LATER) == "updated"
    stored = db.get_request("R-1")
    assert stored.budget_max == 150_000.0
    assert stored.created_at == NOW
    assert stored.updated_at == LATER


def test_only_active_requests_are_iterated_by_default(db):
    db.upsert_request(make_request("R-1"), NOW)
    db.upsert_request(make_request("R-2", status="paused"), NOW)
    assert [item.external_id for item in db.iter_requests()] == ["R-1"]
    assert len(list(db.iter_requests(status=None))) == 2


def test_an_unknown_request_is_none_and_not_an_error(db):
    assert db.get_request("R-404") is None
```

- [x] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py -k request`
Ожидается: FAIL — `AttributeError: 'SqliteDatabase' object has no attribute 'upsert_request'`.

- [x] **Шаг 3: объявить методы в порте и реализовать в адаптере**

В `listam/ports/database.py` — три `@abstractmethod` с теми же сигнатурами
и docstring на каждую, по образцу соседних методов.

В `listam/adapters/db_sqlite.py` — раздел `# --- заявки ---`:

```python
# Поля заявки, изменение которых считаем содержательным: всё, что влияет
# на матчинг и на разговор с клиентом. `updated_at` и `source_row` сюда
# не входят — иначе перечитывание одной и той же таблицы каждый час
# выглядело бы как правка всех пятидесяти заявок разом.
REQUEST_FIELDS = (
    "client_name", "client_phone", "status", "budget_max", "budget_stretch",
    "districts", "districts_priority", "rooms", "area_min", "area_max",
    "floor_min", "floor_max", "no_first_floor", "no_last_floor",
    "must_have", "nice_to_have", "floor_rules", "notes",
)

LIST_FIELDS = ("districts", "districts_priority", "rooms")


def _join(values) -> str | None:
    """Список в TEXT-колонку. Пустой список и None — одно и то же: «не задано»."""
    if not values:
        return None
    return ",".join(str(value) for value in values)


def _split(raw: str | None, cast=str) -> list:
    if not raw:
        return []
    return [cast(part.strip()) for part in raw.split(",") if part.strip()]
```

`upsert_request` сравнивает существующую заявку по `REQUEST_FIELDS`
(списки — после `_join`, флаги — через `_to_int`), при совпадении возвращает
`"unchanged"` и **не двигает** `updated_at`; иначе пишет и возвращает
`"new"`/`"updated"`. `created_at` ставится только при вставке.

`_row_to_request` разбирает списки через `_split(raw)` и `_split(raw, int)`
для `rooms`, флаги — через `bool(row["no_first_floor"])`, даты — через `from_iso`.

- [x] **Шаг 4: тесты проходят, батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: ~455 passed, 12 skipped.

- [x] **Шаг 5: коммит**

```bash
git add listam/ports/database.py listam/adapters/db_sqlite.py tests/contracts/test_database_contract.py
git commit -m "feat(db): заявки хранятся, читаются и не переписываются от перечитывания"
```

### Конец фазы 1

- [x] Дописать в этот файл раздел «Результат фазы 1»: что сделано, числа батареи
      (`passed`/`skipped`), версия схемы, что разошлось с планом и почему.
- [x] Дописать стартовый промпт для фазы 2 по шаблону из раздела
      «Шаблон стартового промпта» в конце плана.
- [x] `git status --short` — чисто; коммит сделан.

---

## Результат фазы 1

**Сделано.** Схема доехала до 7, заявка стала доменной сущностью, строка
источника превращается в `Request`, заявки лежат в базе и переживают
перечитывание.

| Что | Значение |
| --- | --- |
| Батарея | **452 passed, 12 skipped** |
| Схема базы | **7** |
| Боевая база `data/listam.sqlite` | не тронута, схема 6 — мигрируется в фазе 7 |
| Копия `data/listam-before-m2-007.sqlite` | схема 7, 20 826 объявлений (20 619 активных), 8 прогонов |
| Коммиты | `dec4739`, `2712a64`, `4f38e8e`, `0c658be` |

Что появилось:

- `listam/migrations/007_requests_and_matches.sql` — единственная миграция M2:
  7 колонок в `requests`, 6 в `matches`, `runs.new_matches`, 5 индексов.
- `Request` и `Match` в `listam/domain/models.py`.
- `listam/domain/requests.py` — `COLUMNS`, `parse_row`, `parse_rows`,
  `RequestParseError`, `RequestError`.
- `Database.upsert_request` / `iter_requests` / `get_request` в порте и в
  SQLite-адаптере, с контрактными тестами.

### Что разошлось с планом и почему

1. **`_number` из плана не отклонял «примерно 100к», а читал его как 100.**
   Плановый `NOT_A_NUMBER.sub("", raw)` выбрасывает все нецифровые символы и
   отдаёт остаток во `float`: «примерно 100к» → `100.0`, «100к» → `100.0`.
   Тест «бюджет не число — это отказ» проходил случайно, потому что
   «сколько-то» цифр не содержит вовсе. Это молчаливое занижение бюджета
   вчетверо — ровно то, что решение 2 спеки запрещает. Разбор переписан
   наоборот: сначала снимаются украшения (пробелы, `$`, `€`, `₽`, `֏`), затем
   остаток обязан целиком совпасть с одним из трёх образцов — разряды тысяч,
   дробное, целое; иначе отказ. Правило «точка с тремя цифрами после неё — это
   разряд» из плана сохранено. Добавлены два теста: на формы числа и на то, что
   «100к» до 100 не обрезается. **Это находка, а не смягчение порога.**
2. **`stretch` считается как `budget_max + budget_max * percent / 100`.**
   Плановая формула `budget_max * (1 + percent/100)` на 100 000 и 10% даёт
   `110000.00000000001`, и плановый же тест `== 110_000` на ней падает.
   Арифметика та же, артефакта нет.
3. **`test_migrations_005_and_006_...` ослаблен с `== 6` до `>= 6`.**
   Ожидаемое следствие новой миграции, план это прямо разрешает (задача 1.1,
   шаг 4).
4. **452 passed вместо «~455».** Не расхождение, а точная арифметика:
   427 + 1 (миграция) + 18 (`test_requests.py`: 3 на модель, 15 на разбор,
   включая 2 добавленных) + 6 (контракт заявок) = 452. Оценка в плане была
   прикидкой.
5. **Контрактных тестов заявок шесть, а не пять.** Добавлен
   `test_rereading_the_same_table_does_not_move_the_update_stamp`: план требует
   «`unchanged` не двигает `updated_at`» словами в задаче 1.4, шаг 3, но ни
   один тест этого не проверял — а именно на это опирается будущий дайджест
   «что изменилось со вчера».

### Стартовый промпт для фазы 2

```
Ты продолжаешь работу над инструментом мониторинга list.am в C:\Users\Admin\Downloads\list.

Прочитай docs/superpowers/plans/2026-09-22-m2-requests-and-matching.md:
разделы «Global Constraints», «Карта файлов», «Результат фазы 1» и свою
«Фазу 2». Чужие фазы не трогай. Спека рядом:
docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md —
из неё читаются «Принятые решения», они в фазах не пересматриваются.

Исходное состояние: HEAD 0c658be (плюс коммит плана), дерево чистое, батарея
452 passed, 12 skipped, схема базы 7, боевая база 20 826 объявлений
(20 619 активных, 207 снятых), прогонов 8. Боевая база ещё на схеме 6 —
она мигрируется в фазе 7; мигрированная копия лежит в
data/listam-before-m2-007.sqlite. Заявки уже разбираются
(listam/domain/requests.py: COLUMNS, parse_row, parse_rows) и уже хранятся
(Database.upsert_request / iter_requests / get_request), но источника,
откуда брать строки, ещё нет — в тестах фазы 1 они кладутся руками.

Твоя задача — фаза 2: источник заявок. Порт RequestsSource получает rows()
и describe(), а разбор и отсев остаются в базовом классе — csv и gsheet
обязаны давать одну и ту же заявку из одной и той же строки (решение 1).
Появляются адаптеры csv и gsheet, секция requests в конфиге, сборка
в wiring, команда listam requests и проверка источника в doctor.
gsheet пишется сразу, но живьём не проверяется: контрактный тест под skipif,
как у gdrive (решение 11) — отсюда и новый skipped.

Работай по шагам задач: на каждое поведение — падающий тест ДО правки.
Тесты гоняй только .venv/Scripts/python.exe -m pytest -q, любой прогон CLI
из скрипта — только с PYTHONIOENCODING=utf-8. Пороги в конфиге не поднимай.
Новый метод порта — это новый контрактный тест в tests/contracts/.
Схему меняет только миграция 007 из фазы 1, новых миграций в M2 нет.

В конце сессии допиши в план раздел «Результат фазы 2»: что сделано, числа
батареи, что разошлось с планом и почему, и стартовый промпт для фазы 3.
Сделай коммит.
```

---

# Фаза 2. Источник заявок

**Одна сессия.** Порт получает `rows()`, появляются адаптеры `csv` и `gsheet`,
конфиг и команда `listam requests`.

**Ожидается после фазы:** ~475 passed, 13 skipped (новый `gsheet`-скип).

### Задача 2.1. Порт `RequestsSource`

**Файлы:**
- Изменить: `listam/ports/requests_source.py`
- Тест: `tests/contracts/test_requests_source_contract.py` (создать)

**Interfaces — Produces:**
```python
RequestsSource.rows() -> list[dict]            # абстрактный
RequestsSource.describe() -> str               # абстрактный: что за источник, для doctor
RequestsSource.active_requests() -> list[Request]   # реализован в базовом классе
RequestsSource.read() -> tuple[list[Request], list[RequestError]]  # реализован в базовом классе
```

- [x] **Шаг 1: контрактный тест порта**

```python
"""Контрактный тест порта RequestsSource: одинаков для любой реализации."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from listam.adapters.requests_csv import CsvRequestsSource
from listam.domain.requests import COLUMNS
from listam.ports.requests_source import EmptyRequestsSource, RequestsSource

ROW = {
    "id": "R-1", "client_name": "Ани", "client_phone": "+374 00 000000",
    "status": "active", "budget_max": "120000", "budget_stretch": "",
    "districts": "Кентрон", "districts_priority": "", "rooms": "3",
    "area_min": "60", "area_max": "95", "floor_min": "", "floor_max": "",
    "no_first_floor": "да", "no_last_floor": "нет",
    "must_have": "", "nice_to_have": "", "floor_rules": "", "notes": "",
}


def write_csv(path: Path, rows: list[dict]) -> Path:
    import csv
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


@pytest.fixture(params=["none", "csv", pytest.param("gsheet", marks=pytest.mark.skipif(
    not os.environ.get("REQUESTS_SHEET"),
    reason="нет REQUESTS_SHEET: живой Google Sheet не проверить"))])
def source(request, tmp_path):
    if request.param == "none":
        return EmptyRequestsSource()
    if request.param == "csv":
        return CsvRequestsSource(path=write_csv(tmp_path / "requests.csv", [ROW]))
    from listam.adapters.requests_gsheet import GSheetRequestsSource
    return GSheetRequestsSource(
        sheet_id=os.environ["REQUESTS_SHEET"],
        credentials_file=os.environ.get("GDRIVE_CREDENTIALS_FILE"),
    )


def test_is_a_requests_source(source):
    assert isinstance(source, RequestsSource)


def test_rows_are_plain_dictionaries(source):
    for row in source.rows():
        assert isinstance(row, dict)


def test_describe_says_what_the_source_is(source):
    assert source.describe().strip()


def test_reading_gives_requests_and_the_list_of_refusals(source):
    parsed, errors = source.read()
    assert isinstance(parsed, list) and isinstance(errors, list)
    for item in parsed:
        assert item.external_id


def test_active_requests_are_only_the_active_ones(source):
    assert all(item.status == "active" for item in source.active_requests())


def test_reading_twice_gives_the_same_thing(source):
    first, _ = source.read()
    second, _ = source.read()
    assert [item.external_id for item in first] == [item.external_id for item in second]
```

- [x] **Шаг 2: убедиться, что тест падает**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/contracts/test_requests_source_contract.py`
Ожидается: FAIL — `listam.adapters.requests_csv` не существует.

- [x] **Шаг 3: переписать порт**

```python
"""Порт RequestsSource: откуда берутся заявки покупателей.

Адаптер отвечает ровно за одно — достать сырые строки. Превращение строки
в заявку живёт в домене и одно на всех (решение 1): `csv` и `gsheet`
обязаны дать одну и ту же заявку из одной и той же строки, иначе
контрактный тест ничего не гарантирует.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from listam.domain.models import Request
from listam.domain.requests import RequestError, parse_rows


class RequestsSource(ABC):
    @abstractmethod
    def rows(self) -> list[dict]:
        """Сырые строки источника: колонка → значение, всё текстом."""

    @abstractmethod
    def describe(self) -> str:
        """Чем является источник — для `doctor` и для отчёта команды."""

    def read(self) -> tuple[list[Request], list[RequestError]]:
        """Разобранные заявки и отклонённые строки. Разбор один на все адаптеры."""
        return parse_rows(self.rows())

    def active_requests(self) -> list[Request]:
        parsed, _ = self.read()
        return [item for item in parsed if item.status == "active"]


class EmptyRequestsSource(RequestsSource):
    """`requests.kind: none` — источник не настроен, и это не ошибка."""

    def rows(self) -> list[dict]:
        return []

    def describe(self) -> str:
        return "источник заявок не настроен (requests.kind: none)"
```

- [x] **Шаг 4: написать `listam/adapters/requests_csv.py`**

```python
"""Заявки из CSV-файла: отладочный источник и он же запасной для боевого.

Путь — из конфига (`requests.path`), в коде его нет и быть не может.
"""
from __future__ import annotations

import csv
from pathlib import Path

from listam.ports.requests_source import RequestsSource


class MissingRequestsFile(Exception):
    """Файла заявок нет по указанному пути."""


class CsvRequestsSource(RequestsSource):
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def rows(self) -> list[dict]:
        if not self.path.exists():
            raise MissingRequestsFile(
                f"Файла заявок нет: {self.path}. Путь задаётся ключом requests.path "
                f"в config/<env>.yaml."
            )
        # utf-8-sig: таблица, сохранённая Excel'ем, начинается с BOM, и без
        # него первая колонка называется '\ufeffid' и не находится никогда.
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

    def describe(self) -> str:
        return f"CSV: {self.path}"
```

- [x] **Шаг 5: написать `listam/adapters/requests_gsheet.py`**

```python
"""Заявки из Google Sheet: брокер ведёт таблицу руками, скрипт её читает.

Идентификатор таблицы и ключ сервисного аккаунта — из конфига и .env,
в коде их нет. Адаптер достаёт значения и отдаёт строки словарями;
разбор — общий, в домене.
"""
from __future__ import annotations

from listam.ports.requests_source import RequestsSource

SCOPES = ("https://www.googleapis.com/auth/spreadsheets.readonly",)


class GSheetUnavailable(Exception):
    """Нет библиотек Google или ключа сервисного аккаунта."""


class GSheetRequestsSource(RequestsSource):
    def __init__(self, sheet_id: str, credentials_file: str | None = None,
                 range_name: str = "A1:Z1000"):
        self.sheet_id = sheet_id
        self.credentials_file = credentials_file
        self.range_name = range_name

    def _service(self):
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as exc:      # pragma: no cover — зависит от окружения
            raise GSheetUnavailable(
                "Нет библиотек Google: pip install -r requirements-gdrive.txt"
            ) from exc
        if not self.credentials_file:
            raise GSheetUnavailable(
                "Не задан ключ сервисного аккаунта: requests.credentials_file "
                "в config/<env>.yaml (значение — из .env)"
            )
        credentials = service_account.Credentials.from_service_account_file(
            self.credentials_file, scopes=list(SCOPES)
        )
        return build("sheets", "v4", credentials=credentials, cache_discovery=False)

    def rows(self) -> list[dict]:
        service = self._service()
        values = service.spreadsheets().values().get(
            spreadsheetId=self.sheet_id, range=self.range_name
        ).execute().get("values", [])
        if not values:
            return []
        header = [str(name).strip() for name in values[0]]
        # Пустые хвостовые ячейки Sheets не присылает вовсе: короткую строку
        # дополняем пустыми, иначе колонки разъедутся на первой же заявке
        # без заметки.
        return [
            dict(zip(header, list(row) + [""] * (len(header) - len(row))))
            for row in values[1:]
            if any(str(cell).strip() for cell in row)
        ]

    def describe(self) -> str:
        return f"Google Sheet: {self.sheet_id}"
```

- [x] **Шаг 6: тесты проходят**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/contracts/test_requests_source_contract.py`
Ожидается: 12 passed, 6 skipped (шесть тестов параметра `gsheet`).

- [x] **Шаг 7: коммит**

```bash
git add listam/ports/requests_source.py listam/adapters/requests_csv.py listam/adapters/requests_gsheet.py tests/contracts/test_requests_source_contract.py
git commit -m "feat(requests): порт отдаёт строки, разбор общий, адаптеры csv и gsheet"
```

### Задача 2.2. Конфиг и сборка

**Файлы:**
- Изменить: `listam/wiring.py`, `config/dev.yaml`, `config/prod.yaml`, `.env.example`
- Тест: `tests/test_wiring.py`, `tests/test_config.py`

- [x] **Шаг 1: падающие тесты на сборку**

```python
# tests/test_wiring.py — дописать
def test_csv_source_is_built_from_the_config_path(tmp_path):
    config = make_config({"requests": {"kind": "csv", "path": str(tmp_path / "r.csv")}})
    source = build_requests_source(config)
    assert isinstance(source, CsvRequestsSource)
    assert source.path == tmp_path / "r.csv"


def test_csv_source_without_a_path_is_a_config_error():
    config = make_config({"requests": {"kind": "csv"}})
    with pytest.raises(ConfigError):
        build_requests_source(config)


def test_gsheet_source_is_built_from_the_config_sheet():
    config = make_config({"requests": {"kind": "gsheet", "sheet": "SHEET-ID",
                                       "credentials_file": "key.json"}})
    source = build_requests_source(config)
    assert source.sheet_id == "SHEET-ID"


def test_an_unknown_requests_kind_names_the_options():
    config = make_config({"requests": {"kind": "телепатия"}})
    with pytest.raises(UnknownAdapter) as exc:
        build_requests_source(config)
    assert "csv" in str(exc.value) and "gsheet" in str(exc.value)
```

(`make_config` — вспомогательная функция, которая уже есть в `tests/test_wiring.py`;
если её там нет, собери `Config(data, env="test", path=Path("config/test.yaml"))`.)

- [x] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_wiring.py -k requests`
Ожидается: FAIL — `UnknownAdapter: requests.kind = 'csv'`.

- [x] **Шаг 3: дописать `build_requests_source`**

```python
def build_requests_source(config: Config) -> RequestsSource:
    kind = _kind(config, "requests", "none")
    if kind in ("none", "empty"):
        return EmptyRequestsSource()
    if kind == "csv":
        from listam.adapters.requests_csv import CsvRequestsSource

        path = config.get("requests.path")
        if not path:
            raise ConfigError(
                "requests.kind = csv, но requests.path не задан: "
                "откуда читать заявки — решает конфиг, а не код."
            )
        return CsvRequestsSource(path=path)
    if kind == "gsheet":
        from listam.adapters.requests_gsheet import GSheetRequestsSource

        return GSheetRequestsSource(
            sheet_id=config.require("requests.sheet"),
            credentials_file=config.get("requests.credentials_file"),
            range_name=config.get("requests.range", "A1:Z1000"),
        )
    raise _unknown("requests", kind, ["none", "csv", "gsheet"])
```

- [x] **Шаг 4: конфиги**

В `config/dev.yaml` секция `requests` становится:

```yaml
requests:
  kind: csv                     # none | csv | gsheet
  path: ./data/requests.csv     # для kind: csv
  # sheet: ${REQUESTS_SHEET}          # для kind: gsheet
  # credentials_file: ${GDRIVE_CREDENTIALS_FILE}
  # range: A1:Z1000                   # сколько строк листа читаем
```

В `config/prod.yaml` — то же, но `kind: none` остаётся до тех пор, пока в `.env`
не появится `REQUESTS_SHEET`; закомментированный блок `gsheet` с пояснением,
что переключение — это правка одной строки. Пороги не трогаются.

`.env.example`: под `REQUESTS_SHEET=` дописать комментарий, что ключ сервисного
аккаунта берётся из `GDRIVE_CREDENTIALS_FILE`.

- [x] **Шаг 5: тесты проходят**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: ~467 passed, 18 skipped.

- [x] **Шаг 6: коммит**

```bash
git add listam/wiring.py config/dev.yaml config/prod.yaml .env.example tests/test_wiring.py
git commit -m "feat(config): источник заявок выбирается конфигом, csv и gsheet собираются"
```

### Задача 2.3. Команда `listam requests`

**Файлы:**
- Создать: `listam/requests_sync.py`
- Изменить: `listam/cli.py`, `listam/doctor.py`
- Тест: `tests/test_requests_sync.py` (создать), `tests/test_doctor.py`

**Interfaces — Produces:**
```python
@dataclass
class SyncReport:
    source: str
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: list[RequestError] = field(default_factory=list)
    errors: int = 0
    notes: str | None = None

    def render(self) -> str: ...

def run_requests_sync(config) -> SyncReport
```

- [x] **Шаг 1: падающие тесты**

```python
# tests/test_requests_sync.py
def test_requests_are_read_from_the_source_and_stored(tmp_path, csv_config):
    report = run_requests_sync(csv_config)
    assert (report.new, report.updated, report.rejected) == (2, 0, [])
    database = build_database(csv_config)
    database.connect()
    assert [item.external_id for item in database.iter_requests()] == ["R-1", "R-2"]
    database.close()


def test_reading_the_same_table_twice_changes_nothing(tmp_path, csv_config):
    run_requests_sync(csv_config)
    report = run_requests_sync(csv_config)
    assert (report.new, report.updated, report.unchanged) == (0, 0, 2)


def test_one_broken_row_does_not_stop_the_others(tmp_path, csv_config_with_one_bad_row):
    report = run_requests_sync(csv_config_with_one_bad_row)
    assert report.new == 1
    assert len(report.rejected) == 1
    assert "budget_max" in report.render()
    assert report.errors == 0        # опечатка в одной строке — не сбой прогона


def test_a_table_where_nothing_parses_is_an_error(tmp_path, csv_config_all_bad):
    report = run_requests_sync(csv_config_all_bad)
    assert report.errors == 1
    assert "формат" in report.render().lower()


def test_a_missing_file_is_an_error_and_the_base_is_untouched(tmp_path, csv_config_missing):
    report = run_requests_sync(csv_config_missing)
    assert report.errors == 1
    assert report.new == 0
```

Фикстуры собирают временный конфиг: `storage.kind: local`, `storage.work_dir`
и `storage.directory` в `tmp_path`, `requests.kind: csv`, `requests.path` —
на написанный тестом файл. Пороги в фикстурах те же, что в `config/dev.yaml`.

- [x] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_requests_sync.py`
Ожидается: FAIL — модуля `listam.requests_sync` нет.

- [x] **Шаг 3: написать `listam/requests_sync.py`**

Логика: собрать источник и базу через `wiring`; скачать базу из хранилища,
если локальной копии нет (как делает `_export` в `cli.py`); проверить, что
`schema_version() >= latest_schema_version()`, иначе `errors=1` с текстом про
миграции; прочитать источник (`source.read()`); если разобранных ноль, а строки
были — `errors=1` с текстом «формат таблицы не тот»; иначе `upsert_request`
каждую в одной транзакции, считая `new`/`updated`/`unchanged`; залить базу
обратно в хранилище, если что-то изменилось. Отклонённые — в `report.rejected`,
`errors` при этом ноль (решение 2).

- [x] **Шаг 4: подключить к CLI**

В `build_parser`:

```python
commands.add_parser("requests", help="прочитать заявки из источника и записать в базу")
```

В `main`:

```python
if args.command == "requests":
    return _requests(config)
```

```python
def _requests(config) -> int:
    from listam.requests_sync import run_requests_sync

    report = run_requests_sync(config)
    print(report.render())
    return 1 if report.errors else 0
```

`render()` печатает источник, три счётчика и список отклонённых строк
через `RequestError.render()`, каждая с новой строки под заголовком
`⚠ Не разобрано: N` — без `None` в тексте (правило, добытое F-09 в M1).

- [x] **Шаг 5: строка в `doctor`**

Новая проверка `requests_check(config)`: собирает источник, зовёт `describe()`,
пробует `rows()`. `kind: none` — это `OK` с текстом «источник не настроен,
матчинг работать не будет» и `warn=True`. Недоступный источник — `СБОЙ`.
Живой — `OK` с числом строк и числом неразобранных (неразобранные — `warn`).

- [x] **Шаг 6: батарея и живой прогон**

```bash
.venv/Scripts/python.exe -m pytest -q
```
Ожидается: ~475 passed, 18 skipped.

Затем вручную: положить `data/requests.csv` с одной строкой и прогнать

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam requests
```

на временной базе (`APP_ENV` с конфигом, чей `storage.work_dir` — во временной
папке). Боевую базу фаза 2 не трогает.

- [x] **Шаг 7: коммит**

```bash
git add listam/requests_sync.py listam/cli.py listam/doctor.py tests/test_requests_sync.py tests/test_doctor.py
git commit -m "feat(cli): команда requests читает источник, пишет заявки и называет отклонённые"
```

### Конец фазы 2

- [x] Раздел «Результат фазы 2» в этот файл: числа батареи, сколько скипов
      добавил `gsheet`, что разошлось с планом.
- [x] Стартовый промпт для фазы 3.
- [x] Коммит, `git status --short` — чисто.

## Результат фазы 2

**Сделано.** У заявок появился источник. Порт отдаёт строки, разбор остался
один на всех, `csv` и `gsheet` собираются конфигом, команда `listam requests`
читает таблицу и кладёт разобранное в базу, `doctor` говорит, заработает ли
матчинг.

| Что | Значение |
| --- | --- |
| Батарея | **487 passed, 18 skipped** |
| Скипов добавил `gsheet` | **6** (12 → 18): шесть контрактных тестов под `skipif` |
| Схема базы | 7, новых миграций нет |
| Боевая база `data/listam.sqlite` | не тронута, схема 6 — мигрируется в фазе 7 |
| Коммиты | `c71cf57`, `b648979`, `56ebf2c` |

Что появилось:

- `listam/ports/requests_source.py` — `rows()` и `describe()` абстрактные,
  `read()` и `active_requests()` реализованы в базовом классе через
  `parse_rows` (решение 1).
- `listam/adapters/requests_csv.py` (`CsvRequestsSource`, `MissingRequestsFile`)
  и `listam/adapters/requests_gsheet.py` (`GSheetRequestsSource`,
  `GSheetUnavailable`).
- `tests/contracts/test_requests_source_contract.py` — 6 тестов × 3 параметра.
- `build_requests_source` в `listam/wiring.py` собирает `none`/`csv`/`gsheet`;
  секция `requests` в `config/dev.yaml` (`kind: csv`) и `config/prod.yaml`
  (`kind: none`, закомментированный блок `gsheet`).
- `listam/requests_sync.py` — `SyncReport` и `run_requests_sync`.
- `listam requests` в `listam/cli.py`, `requests_check` в `listam/doctor.py`.
- `config/requests.example.csv` — образец таблицы заявок.

**Живой прогон** (временная база в scratchpad, боевая не тронута): таблица из
двух строк, одна с `budget_max = «примерно 100к»`. Первый прогон — «новых 1»,
`⚠ Не разобрано: 1` с названной колонкой и значением, код 0. Второй прогон —
«без изменений 1», заливки нет. `doctor` — жёлтая строка «Источник заявок» с
числом заявок и текстом отказа, код 0.

### Что разошлось с планом и почему

1. **`config/dev.yaml` с `kind: csv` делал `listam doctor` красным на чистом
   клоне.** Плановая секция указывает на `./data/requests.csv`, а `data/`
   целиком в `.gitignore` — файла нет ни у кого, кто только что склонировал
   репозиторий, и настроенный-но-недоступный источник по спеке это сбой
   (и остаётся сбоем: смягчать нечего). Заведён образец
   `config/requests.example.csv` — он вне `data/` и коммитится; комментарий
   в `dev.yaml` говорит, что его надо скопировать. **Это находка плана,
   а не смягчение проверки.**
2. **В `doctor` добавлено предупреждение «активных заявок нет».** План называет
   три исхода проверки (`none` — ⚠, недоступен — СБОЙ, живой — OK). Четвёртый
   исход — таблица читается, но активных заявок в ней ноль — по плану был бы
   зелёным, и человек узнал бы про пустой матчинг только по пустой витрине.
   Это ровно тот же смысл, что у `kind: none`, и помечается так же.
3. **Заявки пишутся не «в одной транзакции».** Шаг 3 задачи 2.3 просит обернуть
   запись одной транзакцией, но `upsert_request` из фазы 1 открывает свою
   транзакцию на каждый вызов, а вложенный `BEGIN` sqlite не допускает.
   Атомарна каждая заявка по отдельности; разбор целиком происходит до записи,
   поэтому «полтаблицы записалось, полтаблицы нет из-за опечатки» невозможно.
4. **Чтение заявок берёт замок прогона и заливает базу как `recheck`,
   а не как `export`.** План отсылает к `_export` («скачать, если локальной
   копии нет»), но `export` только читает, а `requests` пишет в общую базу.
   Взята схема `recheck`: `build_run_lock` → `take_the_fresher_copy` →
   запись → снимок → `rotate_backups` → `upload`. Без замка чтение заявок
   соревновалось бы с идущим обходом за один файл.
5. **Источник читается до базы.** Спека требует «источник недоступен — база не
   трогается»; чтобы это было буквально так, `source.read()` стоит раньше
   замка и подключения. Отсюда усиленные тесты: при недоступном источнике и
   при уехавшем формате файла базы не существует вовсе.
6. **487 passed вместо «~475».** 452 + 12 (контракт: 18 тестов, 6 под skip)
   + 4 (`test_wiring.py`) + 11 (`test_requests_sync.py`) + 5 (`test_doctor.py`)
   + 3 (`test_cli.py`) = 487. Тестов в задачах 2.3 написано больше плановых
   пяти: добавлены «источник назван в отчёте», «правка заявки — это updated»,
   «отклонённая заявка в базу не попала», «пустая таблица — не ошибка»,
   «`kind: none` не падает», «в отчёте нет `None`».
7. **Заголовок фазы обещал «13 skipped», шаги задачи 2.1 — «6 skipped».**
   Правы шаги: параметр `gsheet` даёт по скипу на каждый из шести контрактных
   тестов, итого 12 + 6 = 18. Число в заголовке фазы было прикидкой; в фазах
   3–6 оно уже написано верно (18).

### Стартовый промпт для фазы 3

```
Ты продолжаешь работу над инструментом мониторинга list.am в C:\Users\Admin\Downloads\list.

Прочитай docs/superpowers/plans/2026-09-22-m2-requests-and-matching.md:
разделы «Global Constraints», «Карта файлов», «Результат фазы 1»,
«Результат фазы 2» и свою «Фазу 3». Чужие фазы не трогай. Спека рядом:
docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md —
из неё читаются «Принятые решения», они в фазах не пересматриваются.

Исходное состояние: HEAD на коммите отчёта фазы 2, дерево чистое, батарея
487 passed, 18 skipped, схема базы 7, новых миграций в M2 нет. Боевая база
20 826 объявлений (20 619 активных, 207 снятых), прогонов 8, ещё на схеме 6 —
она мигрируется в фазе 7; мигрированная копия лежит в
data/listam-before-m2-007.sqlite. Улица заполнена у 16 602 из 20 826 (79,7%) —
это основание решения 4: объявление без улицы в кластер не сливается.

Заявки уже читаются целиком: порт RequestsSource отдаёт rows() и describe(),
разбор общий в listam/domain/requests.py, адаптеры csv и gsheet собираются
конфигом (build_requests_source), команда listam requests кладёт заявки
в базу, doctor показывает строку «Источник заявок». Кластеров ещё нет:
listings.cluster_id пуст у всех 20 826.

Твоя задача — фаза 3: дедуп кластеров. Домен listam/domain/clustering.py
(чистые функции, без базы и без сети), методы базы для cluster_id с
контрактными тестами, команда listam cluster со сводкой. Ключ и допуск ±2 м² —
по решениям 4 и 5 спеки: допуск это объединение соседних площадей внутри
группы, а не бакет; объявление без улицы, района или площади получает кластер
из самого себя.

Работай по шагам задач: на каждое поведение — падающий тест ДО правки.
Тесты гоняй только .venv/Scripts/python.exe -m pytest -q, любой прогон CLI
из скрипта — только с PYTHONIOENCODING=utf-8. Пороги в конфиге не поднимай.
Новый метод порта — это новый контрактный тест в tests/contracts/.
Схему меняет только миграция 007 из фазы 1, новых миграций в M2 нет.
Боевую базу фаза 3 не трогает: пробуй на копии.

В конце сессии допиши в план раздел «Результат фазы 3»: что сделано, числа
батареи, что разошлось с планом и почему, и стартовый промпт для фазы 4.
Сделай коммит.
```

---

# Фаза 3. Кластеры-дубли

**Одна сессия.** Дедуп: одна квартира у трёх агентств — один кластер.

**Ожидается после фазы:** ~495 passed, 18 skipped.

### Задача 3.1. Домен кластеризации

**Файлы:**
- Создать: `listam/domain/clustering.py`
- Тест: `tests/test_clustering.py` (создать)

**Interfaces — Produces:**
```python
DEFAULT_AREA_TOLERANCE = 2.0

@dataclass
class Cluster:
    cluster_id: str
    listing_ids: list[str]        # отсортированы, первый — самый дешёвый
    cheapest_id: str
    size: int
    spread_usd: float | None      # max(price_usd) - min(price_usd); один член — None

def assign(listings: Iterable[Listing], area_tolerance: float = DEFAULT_AREA_TOLERANCE) -> dict[str, str]
def clusters(listings: Iterable[Listing], area_tolerance: float = DEFAULT_AREA_TOLERANCE) -> list[Cluster]
def normalize_street(street: str | None) -> str | None
```

- [x] **Шаг 1: падающие тесты**

```python
# tests/test_clustering.py
from listam.domain.clustering import assign, clusters, normalize_street

from tests.contracts.test_database_contract import make_listing


def test_the_same_flat_from_three_agencies_is_one_cluster():
    items = [
        make_listing("1", price_usd=132_000.0, area=85.0),
        make_listing("2", price_usd=139_000.0, area=85.0),
        make_listing("3", price_usd=128_000.0, area=86.0),
    ]
    mapping = assign(items)
    assert len({mapping[item.id] for item in items}) == 1


def test_areas_two_metres_apart_join_and_three_metres_apart_do_not():
    close = assign([make_listing("1", area=85.0), make_listing("2", area=87.0)])
    assert len(set(close.values())) == 1
    far = assign([make_listing("1", area=85.0), make_listing("2", area=88.0)])
    assert len(set(far.values())) == 2


def test_a_chain_of_close_areas_stays_one_cluster():
    # 85 — 87 — 89: соседи в пределах допуска, края — нет. Это одна квартира,
    # обмеренная тремя агентствами, а не две разные.
    items = [make_listing("1", area=85.0), make_listing("2", area=87.0),
             make_listing("3", area=89.0)]
    assert len(set(assign(items).values())) == 1


def test_a_listing_without_a_street_is_a_cluster_of_its_own():
    # Решение 4: улицы нет у 20% базы, и без неё ключ склеивает разные
    # квартиры одного района. Прятать вариант хуже, чем позвонить дважды.
    items = [make_listing("1", street=None), make_listing("2", street=None)]
    assert len(set(assign(items).values())) == 2


def test_different_floors_are_different_flats():
    items = [make_listing("1", floor=4), make_listing("2", floor=5)]
    assert len(set(assign(items).values())) == 2


def test_the_street_is_read_the_same_however_it_is_written():
    assert normalize_street("ул. Туманяна") == normalize_street("Туманяна  ")
    assert normalize_street("улица Туманяна") == normalize_street("ТУМАНЯНА")


def test_the_cluster_id_does_not_depend_on_the_order_of_reading():
    items = [make_listing("1", area=85.0), make_listing("2", area=86.0)]
    assert set(assign(items).values()) == set(assign(list(reversed(items))).values())


def test_a_cluster_knows_its_cheapest_member_and_the_spread():
    found = clusters([make_listing("1", price_usd=132_000.0),
                      make_listing("2", price_usd=139_000.0)])
    assert len(found) == 1
    assert found[0].cheapest_id == "1"
    assert found[0].size == 2
    assert found[0].spread_usd == 7_000.0


def test_a_lonely_listing_has_no_spread():
    assert clusters([make_listing("1")])[0].spread_usd is None
```

- [x] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_clustering.py`
Ожидается: FAIL — модуля нет.

- [x] **Шаг 3: написать `listam/domain/clustering.py`**

```python
"""Дедуп: одна квартира, выставленная несколькими агентствами, — один кластер.

Клиенту нельзя звонить дважды про одну и ту же квартиру, это выглядит как
непрофессионализм. Но и склеить две разные квартиры нельзя: склейка прячет
от клиента вариант, о котором он никогда не узнает. Отсюда два правила:
ключ строгий (решение 4 — без улицы объявление остаётся само по себе),
а допуск по площади — объединение соседей, а не бакет (решение 5).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable

from listam.domain.models import Listing

DEFAULT_AREA_TOLERANCE = 2.0

# «ул.», «улица», «փող.» — это не часть названия, а способ его записать.
STREET_NOISE = re.compile(r"^(ул\.?|улица|пр\.?|проспект|փող\.?|փողոց)\s+", re.IGNORECASE)
SPACES = re.compile(r"\s+")


def normalize_street(street: str | None) -> str | None:
    if not street:
        return None
    cleaned = SPACES.sub(" ", str(street).strip())
    cleaned = STREET_NOISE.sub("", cleaned)
    return cleaned.casefold() or None


@dataclass
class Cluster:
    cluster_id: str
    listing_ids: list[str]
    cheapest_id: str
    size: int
    spread_usd: float | None


def _key(listing: Listing) -> tuple | None:
    """Ключ группы до учёта площади. None — объявление не кластеризуется."""
    street = normalize_street(listing.street)
    if not (listing.district and street and listing.area):
        return None
    return (listing.district.strip().casefold(), street,
            listing.rooms, listing.floor, listing.floors_total)


def _cluster_id(key: tuple, low: float, high: float) -> str:
    raw = "|".join(str(part) for part in key) + f"|{low:.1f}-{high:.1f}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _groups(listings, area_tolerance: float) -> list[tuple[tuple, list[Listing]]]:
    buckets: dict[tuple, list[Listing]] = {}
    lonely: list[tuple[tuple, list[Listing]]] = []
    for listing in listings:
        key = _key(listing)
        if key is None:
            lonely.append((("одиночка", listing.id), [listing]))
            continue
        buckets.setdefault(key, []).append(listing)

    groups: list[tuple[tuple, list[Listing]]] = []
    for key, members in buckets.items():
        # Сортировка по площади, затем по id: порядок чтения базы не должен
        # влиять ни на состав кластеров, ни на их идентификаторы.
        members.sort(key=lambda item: (item.area, item.id))
        chain = [members[0]]
        for listing in members[1:]:
            if listing.area - chain[-1].area <= area_tolerance:
                chain.append(listing)
                continue
            groups.append((key, chain))
            chain = [listing]
        groups.append((key, chain))
    return groups + lonely


def clusters(listings: Iterable[Listing],
             area_tolerance: float = DEFAULT_AREA_TOLERANCE) -> list[Cluster]:
    found: list[Cluster] = []
    for key, members in _groups(list(listings), area_tolerance):
        areas = [item.area for item in members if item.area is not None] or [0.0]
        cluster_id = _cluster_id(key, min(areas), max(areas))
        priced = [item for item in members if item.price_usd is not None]
        ordered = sorted(members, key=lambda item: (item.price_usd is None,
                                                    item.price_usd or 0.0, item.id))
        prices = [item.price_usd for item in priced]
        found.append(Cluster(
            cluster_id=cluster_id,
            listing_ids=[item.id for item in ordered],
            cheapest_id=ordered[0].id,
            size=len(members),
            spread_usd=round(max(prices) - min(prices), 2) if len(prices) > 1 else None,
        ))
    return found


def assign(listings: Iterable[Listing],
           area_tolerance: float = DEFAULT_AREA_TOLERANCE) -> dict[str, str]:
    """Объявление → идентификатор его кластера."""
    return {
        listing_id: cluster.cluster_id
        for cluster in clusters(listings, area_tolerance)
        for listing_id in cluster.listing_ids
    }
```

- [x] **Шаг 4: тесты проходят**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_clustering.py`
Ожидается: 9 passed.

- [x] **Шаг 5: коммит**

```bash
git add listam/domain/clustering.py tests/test_clustering.py
git commit -m "feat(domain): кластеры-дубли — строгий ключ и объединение по площади"
```

### Задача 3.2. Кластеры в базе и команда `listam cluster`

**Файлы:**
- Изменить: `listam/ports/database.py`, `listam/adapters/db_sqlite.py`, `listam/cli.py`
- Создать: `listam/clustering_run.py`
- Тест: `tests/contracts/test_database_contract.py`, `tests/test_clustering_run.py`

**Interfaces — Produces:**
```python
Database.set_cluster_ids(mapping: dict[str, str]) -> int      # сколько строк изменилось
Database.listings_for_matching(since: datetime | None = None) -> list[Listing]

@dataclass
class ClusterReport:
    listings: int
    clusters: int
    multi: int              # кластеров больше одного члена
    largest: int            # размер самого большого кластера
    without_street: int     # объявлений без улицы: они кластеризованы поодиночке
    changed: int

def run_clustering(config) -> ClusterReport
```

- [x] **Шаг 1: контрактные тесты**

```python
# tests/contracts/test_database_contract.py — дописать
def test_cluster_ids_are_stored_and_only_changed_rows_count(db):
    db.upsert_listing(make_listing("1"), NOW)
    db.upsert_listing(make_listing("2"), NOW)
    assert db.set_cluster_ids({"1": "abc", "2": "abc"}) == 2
    assert db.set_cluster_ids({"1": "abc", "2": "abc"}) == 0
    assert db.get_listing("1").cluster_id == "abc"


def test_matching_takes_only_active_and_clean_listings(db):
    db.upsert_listing(make_listing("1"), NOW)
    db.upsert_listing(make_listing("2", anomaly="цена за метр вне порога"), NOW)
    db.upsert_listing(make_listing("3"), NOW)
    db.mark_gone(["3"], LATER)
    assert [item.id for item in db.listings_for_matching()] == ["1"]


def test_matching_since_a_mark_takes_only_what_appeared_after_it(db):
    db.upsert_listing(make_listing("1"), NOW)
    db.upsert_listing(make_listing("2"), EVEN_LATER)
    assert [item.id for item in db.listings_for_matching(since=LATER)] == ["2"]
```

- [x] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py -k cluster`
Ожидается: FAIL — метода нет.

- [x] **Шаг 3: реализовать методы**

`set_cluster_ids` — одной транзакцией, `UPDATE listings SET cluster_id = ?
WHERE id = ? AND (cluster_id IS NULL OR cluster_id <> ?)`, складывая `rowcount`.

`listings_for_matching` — `SELECT * FROM listings WHERE status = 'active'
AND (anomaly IS NULL OR anomaly = '')`, плюс `AND first_seen >= ?` при `since`,
`ORDER BY first_seen DESC, id DESC`.

- [x] **Шаг 4: написать `listam/clustering_run.py` и команду**

`run_clustering(config)`: открыть базу (проверка схемы, как в `_export`),
прочитать `listings_for_matching()`, посчитать `clusters(...)` с допуском
`threshold(config, "match.cluster.area_tolerance", DEFAULT_AREA_TOLERANCE)`,
записать `set_cluster_ids(assign_map)`, вернуть `ClusterReport`.

В CLI — `commands.add_parser("cluster", help="пересчитать кластеры-дубли по базе")`
и печать отчёта: объявлений, кластеров, из них многочленных, крупнейший,
сколько объявлений без улицы (они кластеризованы поодиночке — это надо
говорить вслух, иначе число кластеров выглядит подозрительно большим).

- [x] **Шаг 5: тест на команду**

```python
# tests/test_clustering_run.py
def test_clustering_writes_ids_and_counts_the_lonely_ones(tmp_config_with_db):
    report = run_clustering(tmp_config_with_db)
    assert report.clusters >= 1
    assert report.without_street >= 0
    assert report.changed == report.listings   # первый прогон проставляет всем


def test_running_it_twice_changes_nothing(tmp_config_with_db):
    run_clustering(tmp_config_with_db)
    assert run_clustering(tmp_config_with_db).changed == 0
```

- [x] **Шаг 6: батарея**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: ~495 passed, 18 skipped.

- [x] **Шаг 7: коммит**

```bash
git add listam/ports/database.py listam/adapters/db_sqlite.py listam/clustering_run.py listam/cli.py tests/
git commit -m "feat(cluster): кластеры считаются по базе и ложатся в listings.cluster_id"
```

### Конец фазы 3

- [x] «Результат фазы 3» с числами батареи и — обязательно — прогон
      `listam cluster` на **копии** боевой базы (`data/listam-before-m2-007.sqlite`)
      с числами: сколько кластеров, сколько многочленных, крупнейший,
      сколько объявлений без улицы. Боевую базу не трогать.
- [x] Стартовый промпт для фазы 4. Коммит.

---

## Результат фазы 3

**Сделано.** Дедуп поехал: домен считает кластеры, база их хранит,
`listam cluster` пересчитывает базу целиком и называет числа, по которым
видно, что дедуп работает, а не сломался.

| Что | Значение |
| --- | --- |
| Батарея | **512 passed, 18 skipped** |
| Схема базы | 7, новых миграций нет |
| Боевая база `data/listam.sqlite` | не тронута, схема 6, `cluster_id` пуст у всех |
| Прогон | на копии `data/listam-before-m2-007.sqlite` в scratchpad; сама копия тоже не тронута |
| Коммиты | `aa33da1`, `d9de737` |

Что появилось:

- `listam/domain/clustering.py` — `normalize_street`, `Cluster`, `clusters`,
  `assign`, `DEFAULT_AREA_TOLERANCE`. Чистые функции: ни базы, ни сети.
- `Database.set_cluster_ids` и `Database.listings_for_matching` в порте и
  в SQLite-адаптере, с контрактными тестами.
- `listam/clustering_run.py` — `ClusterReport`, `cluster_database`,
  `run_clustering`, `area_tolerance`.
- `listam cluster` в `listam/cli.py` со сводкой.

### Прогон по копии боевой базы

`listam cluster` на копии `data/listam-before-m2-007.sqlite` (5 секунд):

| Что | Значение |
| --- | --- |
| Объявлений в выборке | **20 595** = 20 619 активных минус 24 с пометкой `anomaly` |
| Кластеров | **14 232** |
| Многочленных | **2 777** |
| Объявлений в них | **9 140** — дедуп убирает 6 363 лишних звонка |
| Крупнейший кластер | **36** |
| Без улицы | **4 174 (20,3%)** — каждое кластер из самого себя |
| Второй прогон | изменено строк **0** |

Крупнейший кластер проверен руками: Арабкир, Вагаршяна, 3 комнаты, 13-й этаж
из 14, 80 м², новостройка, цены $255 000–$275 000, 35 агентств и один
собственник. Это не ложное слияние, а ровно тот случай, ради которого
затевался дедуп: одна квартира, выставленная тридцатью шестью подряд.
Цепочка площадей за пределы 80–82 м² не ушла — разгона по допуску нет.

### Что разошлось с планом и почему

1. **Плановое `STREET_NOISE` съедало начало настоящих названий.** В
   `^(ул\.?|улица|...)` альтернатива `ул\.?` стоит первой и в «улица Туманяна»
   матчится на «ул», оставляя «ица туманяна». Плановый же тест
   `normalize_street("улица Туманяна") == normalize_street("ТУМАНЯНА")`
   на этом падает — это и была первая красная лампа. Хуже, чем разъехавшаяся
   пара: «Улучшенная» превратилась бы в «учшенная», и две разные улицы
   сошлись бы в одну — то самое ложное слияние, которое запрещает решение 4.
   Сокращение теперь снимается, только когда оно и есть отдельное слово:
   с точкой или с пробелом следом. Добавлен тест на имя, которое лишь
   начинается как сокращение. **Это находка, а не смягчение.**
2. **`listam cluster` берёт замок прогона и заливает базу в хранилище.**
   План говорит «открыть базу, как в `_export`», но `_export` только читает,
   а `cluster` переписывает `cluster_id` у двадцати тысяч строк. Это ровно
   расхождение 4 фазы 2, и вывод тот же: взята схема `recheck` — замок →
   `take_the_fresher_copy` → запись → снимок → `rotate_backups` → `upload`.
   Без замка пересчёт соревновался бы с идущим обходом за один файл, а без
   заливки проставленные кластеры пережили бы ровно до первой выкачки
   свежей копии из хранилища.
3. **Пересчёт разделён надвое: `cluster_database(database, tolerance)` и
   `run_clustering(config)`.** Первый работает по уже открытой базе и замка
   не берёт — его позовёт матчинг фазы 5 изнутри своего замка (спека:
   «автоматически перед матчингом, если в выборке есть объявления без
   `cluster_id`»). Второй берёт замок и заливает — это команда. Иначе
   автокластеризация перед матчингом упёрлась бы в собственный замок.
4. **Открытие базы ловит `Exception`, а не `OSError`, как в `recheck`.**
   Битый файл базы — это `sqlite3.DatabaseError`, а не `OSError`: с плановым
   `OSError` команда падала бы трассировкой вместо «файл базы недоступен».
   Тест `test_a_broken_database_is_an_error_and_not_a_crash` это держит.
5. **Секция `match` в конфиг не добавлена.** Допуск читается через
   `threshold(config, "match.cluster.area_tolerance", DEFAULT_AREA_TOLERANCE)`,
   и пока секции нет, берётся 2.0. Наполнение секции — задача 4.2, и лезть
   в чужую фазу за одним ключом значило бы дважды править один файл.
   Тест `test_the_tolerance_is_read_from_the_config` уже проверяет, что
   ключ читается и что ноль значит ноль.
6. **512 passed вместо «~495».** Арифметика: 487 + 13 (`test_clustering.py`)
   + 4 (контракт базы) + 6 (`test_clustering_run.py`) + 2 (`test_cli.py`) = 512.
   Сверх плановых написаны: «без района или площади — тоже одиночка», «пустая
   улица — это отсутствие улицы», «имя, похожее на сокращение, не трогаем»,
   «член без цены не бывает самым дешёвым» (иначе клиенту показывается
   карточка без цены как лучший вариант), «ушедшие и помеченные в счёт не
   идут», «допуск читается из конфига», «битая база — ошибка, а не падение»,
   «кластеры правда легли в базу» и две на команду.

### Стартовый промпт для фазы 4

```
Ты продолжаешь работу над инструментом мониторинга list.am в C:\Users\Admin\Downloads\list.

Прочитай docs/superpowers/plans/2026-09-22-m2-requests-and-matching.md:
разделы «Global Constraints», «Карта файлов», «Результат фазы 1»,
«Результат фазы 2», «Результат фазы 3» и свою «Фазу 4». Чужие фазы не трогай.
Спека рядом: docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md —
из неё читаются «Принятые решения», они в фазах не пересматриваются.

Исходное состояние: HEAD на коммите отчёта фазы 3, дерево чистое, батарея
512 passed, 18 skipped, схема базы 7, новых миграций в M2 нет. Боевая база
20 826 объявлений (20 619 активных, 207 снятых), прогонов 8, ещё на схеме 6 —
она мигрируется в фазе 7; мигрированная копия лежит в
data/listam-before-m2-007.sqlite, и она тоже чистая: прогоны фазы 3 шли
на копии копии в scratchpad.

Заявки читаются и лежат в базе (фазы 1–2). Кластеры считаются: домен
listam/domain/clustering.py, методы базы set_cluster_ids и
listings_for_matching, команда listam cluster. На копии боевой базы она даёт
20 595 объявлений → 14 232 кластера, из них 2 777 многочленных, крупнейший 36,
без улицы 4 174 (20,3%); второй прогон меняет 0 строк.

Твоя задача — фаза 4: движок скоринга. Домен listam/domain/scoring.py
(чистые функции, без базы и без сети): жёсткие критерии с причиной в
rejected_by, шесть факторов с весами из конфига, разбор балла. Фактор, для
которого не хватает данных, из знаменателя исключается — заявка без диапазона
площади не должна получать систематически меньший балл. Выгодность считается
против медианы района (решение 6), а не кластера. Плюс секция match в
config/dev.yaml и config/prod.yaml и её проверка в doctor.

Секции match в конфиге пока нет вовсе: фаза 3 читает допуск кластеров через
threshold(config, "match.cluster.area_tolerance", 2.0) и живёт на значении
по умолчанию. Заводя секцию, не забудь cluster.area_tolerance: 2 — иначе
ключ останется только в коде.

Работай по шагам задач: на каждое поведение — падающий тест ДО правки.
Тесты гоняй только .venv/Scripts/python.exe -m pytest -q, любой прогон CLI
из скрипта — только с PYTHONIOENCODING=utf-8. Пороги в конфиге не поднимай.
Новый метод порта — это новый контрактный тест в tests/contracts/.
Схему меняет только миграция 007 из фазы 1, новых миграций в M2 нет.
Боевую базу фаза 4 не трогает.

В конце сессии допиши в план раздел «Результат фазы 4»: что сделано, числа
батареи, что разошлось с планом и почему, и стартовый промпт для фазы 5.
Сделай коммит.
```

---

# Фаза 4. Движок скоринга

**Одна сессия.** Чистая функция: заявка плюс объявление плюс медианы района —
балл и его разбор. Базы и сети здесь нет.

**Ожидается после фазы:** ~520 passed, 18 skipped.

### Задача 4.1. Жёсткие критерии и баллы

**Файлы:**
- Создать: `listam/domain/scoring.py`
- Тест: `tests/test_scoring.py` (создать)

**Interfaces — Produces:**
```python
DEFAULT_WEIGHTS = {"budget": 30, "district": 20, "price_per_sqm": 20,
                   "area_rooms": 15, "floor": 10, "seller_type": 5}
DEFAULT_STRETCH_PERCENT = 10.0
CHEAP_SATURATION = 0.25        # −25% к медиане района — полный балл за выгодность

@dataclass
class Score:
    value: int                              # 0–100
    breakdown: dict[str, tuple[float, float]]   # фактор → (набрано, вес)
    rejected_by: str | None = None

    @property
    def matched(self) -> bool: ...          # rejected_by is None

def rejection(request: Request, listing: Listing, stretch_percent: float) -> str | None
def score(request: Request, listing: Listing, *,
          median_by_district: dict[str, float] | None = None,
          weights: dict[str, float] | None = None,
          stretch_percent: float = DEFAULT_STRETCH_PERCENT) -> Score
```

- [ ] **Шаг 1: падающие тесты на жёсткие критерии**

```python
# tests/test_scoring.py
from listam.domain.scoring import rejection, score

from tests.contracts.test_database_contract import make_listing
from tests.test_requests import row      # если row() там — иначе собери Request прямо


def a_request(**over):
    from listam.domain.models import Request
    fields = dict(external_id="R-1", budget_max=140_000.0,
                  districts=["Центр"], districts_priority=["Центр"],
                  rooms=[3], area_min=70.0, area_max=100.0)
    fields.update(over)
    return Request(**fields)


def test_a_district_outside_the_list_is_refused():
    assert rejection(a_request(), make_listing(district="Давташен"), 10) == "район"


def test_a_request_without_districts_accepts_any_district():
    assert rejection(a_request(districts=[]), make_listing(district="Давташен"), 10) is None


def test_fewer_rooms_than_asked_is_refused():
    assert rejection(a_request(rooms=[3, 4]), make_listing(rooms=2), 10) == "комнаты"


def test_more_rooms_than_asked_is_not_refused_but_scores_lower():
    # Четыре комнаты вместо трёх — это всё ещё звонок: клиенту может подойти.
    assert rejection(a_request(rooms=[3]), make_listing(rooms=4), 10) is None


def test_a_price_above_the_stretched_budget_is_refused():
    assert rejection(a_request(budget_max=100_000.0), make_listing(price_usd=120_000.0), 10) \
        == "бюджет"


def test_a_price_inside_the_stretch_is_not_refused():
    assert rejection(a_request(budget_max=100_000.0), make_listing(price_usd=108_000.0), 10) is None


def test_a_listing_without_a_price_cannot_pass_the_budget_test():
    assert rejection(a_request(), make_listing(price_usd=None), 10) == "цена неизвестна"


def test_an_area_below_the_minimum_is_refused():
    assert rejection(a_request(area_min=70.0), make_listing(area=55.0), 10) == "площадь"
```

- [ ] **Шаг 2: падающие тесты на баллы**

```python
def test_a_perfect_fit_scores_a_hundred():
    listing = make_listing(district="Центр", rooms=3, area=85.0, floor=4, floors_total=9,
                           price_usd=120_000.0, price_per_sqm=1_000.0, seller_type="owner")
    result = score(a_request(), listing, median_by_district={"Центр": 1_400.0})
    assert result.value == 100
    assert result.matched


def test_a_price_inside_the_stretch_scores_less_than_one_inside_the_budget():
    inside = score(a_request(budget_max=140_000.0), make_listing(price_usd=130_000.0))
    stretched = score(a_request(budget_max=140_000.0), make_listing(price_usd=150_000.0))
    assert stretched.value < inside.value


def test_a_priority_district_beats_a_merely_allowed_one():
    request = a_request(districts=["Центр", "Арабкир"], districts_priority=["Центр"])
    top = score(request, make_listing(district="Центр"))
    ok = score(request, make_listing(district="Арабкир"))
    assert top.value > ok.value


def test_below_the_district_median_scores_higher_than_above_it():
    medians = {"Центр": 1_400.0}
    cheap = score(a_request(), make_listing(price_per_sqm=1_000.0), median_by_district=medians)
    dear = score(a_request(), make_listing(price_per_sqm=1_800.0), median_by_district=medians)
    assert cheap.value > dear.value


def test_the_first_floor_is_penalised_only_when_the_client_said_so():
    listing = make_listing(floor=1)
    assert score(a_request(no_first_floor=True), listing).value < \
           score(a_request(no_first_floor=False), listing).value


def test_the_last_floor_is_penalised_only_when_the_client_said_so():
    listing = make_listing(floor=9, floors_total=9)
    assert score(a_request(no_last_floor=True), listing).value < \
           score(a_request(no_last_floor=False), listing).value


def test_an_owner_scores_above_an_agency():
    assert score(a_request(), make_listing(seller_type="owner")).value > \
           score(a_request(), make_listing(seller_type="agency")).value


def test_a_factor_without_data_is_dropped_from_the_denominator_and_not_a_penalty():
    # Нет медианы района — фактор «выгодность» не считается вовсе. Иначе
    # объявление в районе без медианы всегда проигрывало бы двадцать баллов
    # ни за что.
    without = score(a_request(), make_listing(price_per_sqm=1_000.0), median_by_district={})
    assert "price_per_sqm" not in without.breakdown
    assert without.value == 100


def test_the_breakdown_names_every_factor_that_counted():
    result = score(a_request(), make_listing(), median_by_district={"Центр": 1_400.0})
    assert set(result.breakdown) <= {"budget", "district", "price_per_sqm",
                                     "area_rooms", "floor", "seller_type"}
    for got, weight in result.breakdown.values():
        assert 0 <= got <= weight


def test_a_refused_listing_scores_zero_and_says_why():
    result = score(a_request(), make_listing(district="Давташен"))
    assert result.value == 0
    assert result.rejected_by == "район"
    assert not result.matched


def test_weights_come_from_the_caller_and_are_not_wired_into_the_code():
    listing = make_listing(seller_type="agency")
    only_seller = score(a_request(), listing, weights={"seller_type": 5})
    assert set(only_seller.breakdown) == {"seller_type"}
    assert only_seller.value == 0
```

- [ ] **Шаг 3: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_scoring.py`
Ожидается: FAIL — модуля нет.

- [ ] **Шаг 4: написать `listam/domain/scoring.py`**

Правила реализации, которые нельзя нарушить:

- `rejection` возвращает короткую причину словом: `"район"`, `"комнаты"`,
  `"бюджет"`, `"площадь"`, `"цена неизвестна"` — она ложится в `matches.reject_reason`
  и читается человеком.
- Каждый фактор — своя функция `_budget(...) -> float | None`, возвращающая
  долю от 0 до 1 или `None`, если данных не хватает. `None` исключает вес
  фактора из знаменателя целиком.
- `value = round(100 * сумма(доля × вес) / сумма(весов посчитанных факторов))`;
  посчитанных факторов ноль — `value = 0` и `breakdown` пуст.
- Веса приходят аргументом; `DEFAULT_WEIGHTS` — значения спеки и используются
  только когда вызывающий ничего не передал. В конфиг за ними модуль не ходит:
  домен про конфиг не знает.
- Формула выгодности: `(median - price_per_sqm) / median`, зажатая в
  `[-CHEAP_SATURATION, CHEAP_SATURATION]` и переведённая в `[0, 1]`.
- Фактор `area_rooms`: половина за попадание площади в диапазон, половина
  за попадание комнат в список; незаданный диапазон — эта половина не считается.

- [ ] **Шаг 5: тесты проходят**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_scoring.py`
Ожидается: 19 passed.

- [ ] **Шаг 6: коммит**

```bash
git add listam/domain/scoring.py tests/test_scoring.py
git commit -m "feat(domain): скоринг — жёсткие критерии, веса аргументом, разбор балла"
```

### Задача 4.2. Секция `match` в конфиге и в `doctor`

**Файлы:**
- Изменить: `config/dev.yaml`, `config/prod.yaml`, `listam/doctor.py`
- Тест: `tests/test_config.py`, `tests/test_doctor.py`

- [ ] **Шаг 1: падающие тесты**

```python
# tests/test_doctor.py — дописать
def test_doctor_shows_the_matching_weights_and_thresholds(dev_config):
    report = run_doctor(dev_config, check_network=False)
    line = next(c for c in report.checks if "матчинг" in c.name.lower())
    assert "70" in line.details and "40" in line.details


def test_a_hot_threshold_below_the_digest_one_is_a_warning(config_with_bad_thresholds):
    report = run_doctor(config_with_bad_thresholds, check_network=False)
    line = next(c for c in report.checks if "матчинг" in c.name.lower())
    assert line.warn or not line.ok


def test_zero_weights_everywhere_is_a_failure_not_a_silent_zero_score(config_zero_weights):
    # Ноль значит ноль: все веса по нулю — это не «выключено», это матчинг,
    # который всегда отдаёт 0 баллов. Такое надо показать, а не проглотить.
    report = run_doctor(config_zero_weights, check_network=False)
    assert not report.ok
```

- [ ] **Шаг 2: убедиться, что тесты падают**, затем дописать секцию в оба конфига:

```yaml
match:                          # движок подбора: `python -m listam match`
  weights:                      # веса факторов, сумма значения не имеет
    budget: 30                  # попадание в бюджет
    district: 20                # район: приоритетный выше просто допустимого
    price_per_sqm: 20           # выгодность против медианы района
    area_rooms: 15              # площадь и комнаты
    floor: 10                   # штраф за первый и последний, если клиент против
    seller_type: 5              # собственник выше агентства
  thresholds:
    hot: 70                     # немедленное уведомление (заработает на M3)
    digest: 40                  # дневной дайджест
  budget_stretch_percent: 10    # на столько растягиваем бюджет, если заявка молчит
  cluster:
    area_tolerance: 2           # м²: на столько могут разойтись обмеры одной квартиры
  limit: 50                     # сколько строк показывает `matches`
```

- [ ] **Шаг 3: проверка `match_check(config)` в `doctor.py`**

Печатает веса и пороги; `hot < digest` — `warn`; сумма весов ноль — `СБОЙ`
с текстом, что балл всегда будет нулевым. Конфиг `doctor` не правит (решение 7
плана M1 продолжает действовать).

- [ ] **Шаг 4: батарея и живой `doctor`**

```bash
.venv/Scripts/python.exe -m pytest -q
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam doctor --no-network
```
Ожидается: ~520 passed, 18 skipped; в отчёте строка «Матчинг» с весами и порогами.

- [ ] **Шаг 5: коммит**

```bash
git add config/dev.yaml config/prod.yaml listam/doctor.py tests/test_doctor.py
git commit -m "feat(config): веса и пороги матчинга в конфиге, doctor их показывает"
```

### Конец фазы 4

- [ ] «Результат фазы 4»: числа батареи, что разошлось. Если какой-то вес
      или порог пришлось бы поднять ради зелёного теста — **не поднимать**,
      а записать находкой.
- [ ] Стартовый промпт для фазы 5. Коммит.

---

# Фаза 5. Матчи: запись и команда `match`

**Одна сессия.** Оркестратор, хранение матчей, оба направления.

**Ожидается после фазы:** ~550 passed, 18 skipped.

### Задача 5.1. Хранение матчей

**Файлы:**
- Изменить: `listam/ports/database.py`, `listam/adapters/db_sqlite.py`
- Тест: `tests/contracts/test_database_contract.py`

**Interfaces — Produces:**
```python
Database.upsert_match(match: Match, now: datetime) -> str   # new | updated | unchanged
Database.matches_for_request(request_id: int, min_score: float | None = None,
                             limit: int | None = None) -> list[Match]
Database.set_match_status(match_id: int, status: str, reject_reason: str | None = None) -> None
Database.count_matches(request_id: int | None = None) -> int
```

- [ ] **Шаг 1: контрактные тесты**

```python
# tests/contracts/test_database_contract.py — дописать
from listam.domain.models import Match


def stored_request(db, external_id="R-1"):
    db.upsert_request(make_request(external_id), NOW)
    return db.get_request(external_id)


def test_a_new_match_is_stored_with_both_timestamps(db):
    request = stored_request(db)
    db.upsert_listing(make_listing("1"), NOW)
    assert db.upsert_match(Match(request_id=request.id, listing_id="1", score=82.0,
                                 breakdown={"budget": [30, 30]}), NOW) == "new"
    match = db.matches_for_request(request.id)[0]
    assert match.first_matched_at == NOW and match.matched_at == NOW
    assert match.breakdown == {"budget": [30, 30]}


def test_rematching_updates_the_score_and_keeps_the_birthday(db):
    request = stored_request(db)
    db.upsert_listing(make_listing("1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="1", score=82.0), NOW)
    assert db.upsert_match(Match(request_id=request.id, listing_id="1", score=91.0),
                           LATER) == "updated"
    match = db.matches_for_request(request.id)[0]
    assert match.score == 91.0
    assert match.first_matched_at == NOW
    assert match.matched_at == LATER


def test_a_status_set_by_a_human_survives_the_recount(db):
    # Решение 7: статус — это след звонка, а не вычисленное значение.
    request = stored_request(db)
    db.upsert_listing(make_listing("1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="1", score=82.0), NOW)
    match = db.matches_for_request(request.id)[0]
    db.set_match_status(match.id, "called", reject_reason="первый этаж не смотрим")
    db.upsert_match(Match(request_id=request.id, listing_id="1", score=91.0), LATER)
    after = db.matches_for_request(request.id)[0]
    assert after.status == "called"
    assert after.reject_reason == "первый этаж не смотрим"
    assert after.score == 91.0


def test_the_same_match_twice_is_not_a_change(db):
    request = stored_request(db)
    db.upsert_listing(make_listing("1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="1", score=82.0), NOW)
    assert db.upsert_match(Match(request_id=request.id, listing_id="1", score=82.0),
                           LATER) == "unchanged"


def test_matches_come_back_ranked_and_can_be_cut_by_score_and_count(db):
    request = stored_request(db)
    for number, value in (("1", 55.0), ("2", 91.0), ("3", 73.0)):
        db.upsert_listing(make_listing(number), NOW)
        db.upsert_match(Match(request_id=request.id, listing_id=number, score=value), NOW)
    assert [m.listing_id for m in db.matches_for_request(request.id)] == ["2", "3", "1"]
    assert [m.listing_id for m in db.matches_for_request(request.id, min_score=70)] == ["2", "3"]
    assert len(db.matches_for_request(request.id, limit=1)) == 1


def test_a_match_on_a_listing_that_went_away_is_kept(db):
    # Решение 8: «мы звонили по этой квартире» переживает снятие объявления.
    request = stored_request(db)
    db.upsert_listing(make_listing("1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="1", score=82.0), NOW)
    db.mark_gone(["1"], LATER)
    assert len(db.matches_for_request(request.id)) == 1
```

- [ ] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py -k match`
Ожидается: FAIL — метода `upsert_match` нет.

- [ ] **Шаг 3: реализовать**

`upsert_match` ищет строку по `(request_id, listing_id)`. Нет — вставляет
с `first_matched_at = matched_at = now`, `status='new'`. Есть — сравнивает
`score`, `breakdown` (сериализованный `json.dumps(..., sort_keys=True)`),
`cluster_id`, `cluster_size`, `cluster_spread_usd`. Совпало — `"unchanged"`,
`matched_at` не двигается. Иначе `UPDATE`, который перечисляет **только**
вычисляемые колонки: `status` и `reject_reason` в списке присвоений
отсутствуют физически, а не «сохраняются по условию» — так их нельзя
затереть случайной правкой.

`breakdown` в базу — `json.dumps`, из базы — `json.loads`; пустой — `None`.
Кортежи `(набрано, вес)` из `Score.breakdown` после JSON возвращаются списками —
именно поэтому контрактный тест сверяет с `{"budget": [30, 30]}`, а не с кортежем.
Приводить их обратно к кортежам не надо: в базе это данные для человека, а не
структура, по которой считают.

`matches_for_request` — `ORDER BY score DESC, listing_id`, с `AND score >= ?`
и `LIMIT ?`, когда они заданы.

- [ ] **Шаг 4: тесты проходят**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py`
Ожидается: все зелёные.

- [ ] **Шаг 5: коммит**

```bash
git add listam/ports/database.py listam/adapters/db_sqlite.py tests/contracts/test_database_contract.py
git commit -m "feat(db): матчи пишутся идемпотентно и не затирают след звонка"
```

### Задача 5.2. Оркестратор и команда `match`

**Файлы:**
- Создать: `listam/matching.py`
- Изменить: `listam/cli.py`
- Тест: `tests/test_matching.py` (создать), `tests/test_cli.py`

**Interfaces — Produces:**
```python
@dataclass
class MatchReport:
    scope: str                 # «заявка R-1», «новые объявления прогона 8», «вся база»
    requests: int = 0
    listings: int = 0
    considered: int = 0        # кластеров-представителей
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    hot: int = 0               # score >= match.thresholds.hot
    digest: int = 0            # hot > score >= digest
    errors: int = 0
    notes: str | None = None

    def render(self) -> str: ...

def run_match(config, *, external_id: str | None = None,
              only_new: bool = False, recount_all: bool = False) -> MatchReport
```

- [ ] **Шаг 1: падающие тесты**

```python
# tests/test_matching.py
def test_a_new_request_is_matched_against_the_whole_base(matching_config):
    report = run_match(matching_config, external_id="R-1")
    assert report.requests == 1
    assert report.new > 0
    assert "R-1" in report.scope


def test_only_the_cheapest_listing_of_a_cluster_becomes_a_match(matching_config_with_duplicates):
    run_match(matching_config_with_duplicates, external_id="R-1")
    database = build_database(matching_config_with_duplicates)
    database.connect()
    request = database.get_request("R-1")
    matches = database.matches_for_request(request.id)
    assert [m.listing_id for m in matches] == ["cheap"]
    assert matches[0].cluster_size == 3
    assert matches[0].cluster_spread_usd == 11_000.0
    database.close()


def test_new_only_looks_at_listings_from_the_last_run(matching_config_with_two_runs):
    report = run_match(matching_config_with_two_runs, only_new=True)
    assert report.listings == 2          # столько принёс последний прогон
    assert "прогон" in report.scope


def test_recounting_everything_creates_no_duplicates(matching_config):
    run_match(matching_config, recount_all=True)
    second = run_match(matching_config, recount_all=True)
    assert second.new == 0
    assert second.unchanged > 0


def test_a_request_that_does_not_exist_is_an_error_naming_it(matching_config):
    report = run_match(matching_config, external_id="R-404")
    assert report.errors == 1
    assert "R-404" in report.notes


def test_a_paused_request_is_not_matched(matching_config_with_paused_request):
    report = run_match(matching_config_with_paused_request, recount_all=True)
    assert report.requests == 0
    assert "нет активных заявок" in report.notes.lower()


def test_hot_and_digest_are_counted_by_the_config_thresholds(matching_config):
    report = run_match(matching_config, external_id="R-1")
    assert report.hot + report.digest <= report.new + report.updated + report.unchanged
```

- [ ] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_matching.py`
Ожидается: FAIL — модуля `listam.matching` нет.

- [ ] **Шаг 3: написать `listam/matching.py`**

Порядок работы `run_match`:

1. Открыть базу через `wiring` (скачать из хранилища, если локальной копии нет),
   проверить `schema_version() >= latest_schema_version()` — иначе `errors=1`.
2. Собрать заявки: `external_id` задан — одна, из базы, и она обязана быть
   `active` (иначе `errors=1` с текстом, называющим заявку и её статус);
   иначе все активные. Активных ноль — `notes="нет активных заявок"`, `errors=0`.
3. Собрать объявления: `only_new` — `listings_for_matching(since=начало последнего
   прогона)` (мерка берётся тем же способом, что в `changes.since_point`);
   иначе `listings_for_matching()`.
4. Кластеры: `clusters(все активные объявления, допуск из конфига)` — считаются
   **по всей базе**, а не по выборке, иначе `--new` не узнает, что у свежего
   объявления уже есть двойники. Из выборки оставить только представителей
   (`cheapest_id` своего кластера); `considered` — их число.
5. Медианы: `median_price_per_sqm_by_district(все активные)`.
6. Для каждой пары «заявка × представитель» — `score(...)`. `rejected_by` не
   `None` — матч не пишется вовсе (отказы в базе не нужны: их миллионы).
7. `upsert_match` с `cluster_id`, `cluster_size`, `cluster_spread_usd`, `run_id`
   последнего прогона; счётчики `new`/`updated`/`unchanged`, `hot`/`digest`
   по порогам из конфига.
8. Залить базу в хранилище, если что-то изменилось.

- [ ] **Шаг 4: команда в CLI**

```python
match = commands.add_parser("match", help="подобрать объявления под заявки")
match.add_argument("--request", help="внешний идентификатор заявки: подобрать по всей базе")
match.add_argument("--new", action="store_true",
                   help="только объявления, которые принёс последний прогон")
match.add_argument("--all", action="store_true",
                   help="пересчитать все активные заявки по всей базе")
```

Разбор на входе (правило «бессмысленный ввод отклоняется, а не истолковывается»):

```python
if args.command == "match":
    chosen = [name for name, on in
              (("--request", bool(args.request)), ("--new", args.new), ("--all", args.all)) if on]
    if len(chosen) > 1:
        print(f"{' и '.join(chosen)} вместе не работают: это три разных выборки. "
              "Выбери одно.", file=sys.stderr)
        return 2
    if not chosen:
        print("Нечего подбирать: укажи --request <id> для одной заявки, "
              "--new для объявлений последнего прогона или --all для полного пересчёта.",
              file=sys.stderr)
        return 2
    return _match(config, external_id=args.request, only_new=args.new, recount_all=args.all)
```

- [ ] **Шаг 5: тесты CLI**

```python
# tests/test_cli.py — дописать
def test_match_refuses_two_scopes_at_once(capsys):
    assert main(["match", "--new", "--all"]) == 2


def test_match_without_a_scope_explains_the_three_options(capsys):
    assert main(["match"]) == 2
    assert "--request" in capsys.readouterr().err
```

- [ ] **Шаг 6: батарея**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: ~550 passed, 18 skipped.

- [ ] **Шаг 7: коммит**

```bash
git add listam/matching.py listam/cli.py tests/test_matching.py tests/test_cli.py
git commit -m "feat(match): подбор в обе стороны — заявка по базе и новое по заявкам"
```

### Конец фазы 5

- [ ] «Результат фазы 5»: числа батареи, сколько матчей дала тестовая заявка
      на фикстурах, что разошлось с планом.
- [ ] Стартовый промпт для фазы 6. Коммит.

---

# Фаза 6. Витрина

**Одна сессия.** `listam matches`, лист «Матчи» в `.xlsx`, README.

**Ожидается после фазы:** ~570 passed, 18 skipped.

### Задача 6.1. Команда `listam matches`

**Файлы:**
- Изменить: `listam/matching.py` (функция `render_matches`), `listam/cli.py`
- Тест: `tests/test_matching.py`

**Interfaces — Produces:**
```python
MatchRow = tuple[Request, Match, Listing]

def collect_matches(config, *, external_id: str | None = None,
                    min_score: float | None = None,
                    limit: int | None = None) -> list[MatchRow]
    """Матчи для витрины: заявка, матч и объявление одной строкой.

    Объявление берётся `get_listing` даже когда оно снято (решение 8):
    витрина его помечает, а не прячет. `external_id` не задан — все
    активные заявки, по убыванию балла внутри каждой.
    """

def render_matches(rows: list[MatchRow], limit: int) -> str
```

**Consumes:** `Database.matches_for_request`, `Database.iter_requests`,
`Database.get_listing` (фазы 1 и 5); пороги `match.thresholds.digest`
и `match.limit` из конфига (фаза 4).

- [ ] **Шаг 1: падающие тесты**

```python
def test_the_shop_window_shows_score_price_district_and_the_cluster(matching_config):
    run_match(matching_config, external_id="R-1")
    printed = render_matches(collect_matches(matching_config, external_id="R-1"), limit=50)
    assert "балл" in printed.lower()
    assert "None" not in printed          # правило, добытое F-09 в M1
    assert "$" in printed


def test_a_cluster_of_three_says_so_and_names_the_spread(matching_config_with_duplicates):
    run_match(matching_config_with_duplicates, external_id="R-1")
    printed = render_matches(collect_matches(matching_config_with_duplicates,
                                             external_id="R-1"), limit=50)
    assert "3 объявления" in printed


def test_a_match_whose_listing_went_away_is_marked_and_not_hidden(matching_config_gone):
    printed = render_matches(collect_matches(matching_config_gone, external_id="R-1"), limit=50)
    assert "снято" in printed


def test_the_limit_comes_from_the_config_and_zero_is_refused_on_the_command_line(capsys):
    assert main(["matches", "--limit", "0"]) == 2
```

- [ ] **Шаг 2: убедиться, что падает**, затем написать `render_matches`.

Строка витрины: балл, цена в USD, $/м², район, улица, комнаты, площадь,
этаж/этажность, тип продавца, ссылка; пометка кластера «3 объявления,
разброс $11 000» и пометка «снято», если объявление больше не на ленте.
Прочерк `—` вместо пустого значения (константа `DASH` уже есть в `changes.py` —
вынести её в общее место или продублировать с тем же именем). Заголовок
называет заявку и мерку: «Заявка R-1 (Ани), порог дайджеста 40».

- [ ] **Шаг 3: команда**

```python
matches = commands.add_parser("matches", help="ранжированный список подобранных вариантов")
matches.add_argument("--request", help="внешний идентификатор заявки")
matches.add_argument("--limit", type=int, help="сколько строк показать (по умолчанию — из конфига)")
matches.add_argument("--min-score", type=float, help="показывать от этого балла и выше")
```

`--limit <= 0` и `--min-score` вне `0…100` — код возврата 2 с внятным текстом.

- [ ] **Шаг 4: батарея и коммит**

```bash
.venv/Scripts/python.exe -m pytest -q
git add listam/matching.py listam/cli.py tests/
git commit -m "feat(cli): витрина matches — ранжированный список с кластером и пометками"
```

### Задача 6.2. Лист «Матчи» в выгрузке

**Файлы:**
- Изменить: `listam/ports/exporter.py`, `listam/adapters/exporter_xlsx.py`, `listam/cli.py`
- Тест: `tests/contracts/test_exporter_contract.py`

**Interfaces — Produces:**
```python
Exporter.export(listings, name=None, matches: list[MatchRow] | None = None) -> Path
# MatchRow = tuple[Request, Match, Listing]
```

Метод порта меняется — значит, дополняется контрактный тест (правило проекта).

- [ ] **Шаг 1: контрактный тест**

```python
def test_the_workbook_has_a_matches_sheet_when_matches_are_given(exporter, tmp_path):
    path = exporter.export([make_listing("1")], matches=[(request, match, listing)])
    book = openpyxl.load_workbook(path)
    assert "Матчи" in book.sheetnames
    sheet = book["Матчи"]
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref.startswith("A1")
    assert sheet.cell(row=2, column=1).value == "R-1"


def test_without_matches_the_workbook_is_exactly_what_it_was(exporter):
    path = exporter.export([make_listing("1")])
    book = openpyxl.load_workbook(path)
    assert "Матчи" not in book.sheetnames
```

- [ ] **Шаг 2: убедиться, что падает**, затем реализовать.

Колонки листа: заявка, клиент, балл, статус матча, цена USD, $/м², район,
улица, комнаты, площадь, этаж, этажность, продавец, объявлений в кластере,
разброс, статус объявления, ссылка. Шапка заморожена, автофильтр по всему
диапазону, числа — числами, ссылка кликабельна — ровно как на листе объявлений.
Статусы по-русски (правило, добытое F-12 в M1): «новый», «отправлен»,
«звонили», «отказ».

- [ ] **Шаг 3: `export` собирает матчи**

`_export` в `cli.py` читает матчи активных заявок (`matches_for_request` по
каждой, с порогом `match.thresholds.digest`) и передаёт их в `export(...)`.
Матчей нет — лист не создаётся, и это не ошибка.

- [ ] **Шаг 4: батарея, живая выгрузка, коммит**

```bash
.venv/Scripts/python.exe -m pytest -q
git add listam/ports/exporter.py listam/adapters/exporter_xlsx.py listam/cli.py tests/contracts/test_exporter_contract.py
git commit -m "feat(export): лист «Матчи» в выгрузке"
```

### Задача 6.3. README

**Файлы:** `README.md`, `tests/test_docs.py`

- [ ] Раздел про заявки: формат таблицы (все 19 колонок с примерами значений),
      где лежит файл, как переключиться на Google Sheet.
- [ ] Раздел про матчинг: что такое кластер, из чего складывается балл,
      что значат пороги 70 и 40.
- [ ] Расписание: `scrape --fresh && match --new` раз в час,
      `scrape && match --all` раз в сутки.
- [ ] `tests/test_docs.py` — проверка, что README упоминает все команды
      (`requests`, `cluster`, `match`, `matches`); дописать их в существующий
      список, если он там есть.
- [ ] Батарея, коммит.

### Конец фазы 6

- [ ] «Результат фазы 6», стартовый промпт для фазы 7, коммит.

---

# Фаза 7. Боевая приёмка

**Одна сессия.** Единственная фаза, которая трогает `data/listam.sqlite`.
Кода эта фаза по замыслу не пишет — она предъявляет написанное на живых данных.
Если что-то всё же пришлось починить — это находка, и она записывается.

### Шаг 1. Копия базы до работ

- [ ] `data/listam-before-m2-<дата>-<время>.sqlite`, `PRAGMA quick_check` = ok,
      число строк и прогонов записаны в отчёт.

### Шаг 2. Накат схемы 7 на боевую базу

- [ ] `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam recheck`
- [ ] Проверить: `schema_version() == 7`, число объявлений не изменилось (20 826),
      `runs.new_matches` = 0 у всех восьми прогонов, `matches` пуст.

### Шаг 3. Реальная заявка

- [ ] Завести `data/requests.csv` с одной настоящей заявкой (район, бюджет,
      комнаты, площадь — как у живого клиента).
- [ ] `python -m listam requests` — заявка прочитана и лежит в базе;
      отклонённых нет.
- [ ] Отдельно проверить отказ: вторая строка с `budget_max: «примерно 100к»`
      отклонена, первая записана, код возврата 0.

### Шаг 4. Кластеры по боевой базе

- [ ] `python -m listam cluster` — записать в отчёт: объявлений, кластеров,
      многочленных, крупнейший кластер, объявлений без улицы.
- [ ] Посмотреть глазами на три самых больших кластера: это действительно
      одна квартира у нескольких агентств или склейка? Если склейка —
      **не трогать порог в конфиге**, а записать находкой с примером.

### Шаг 5. Матчинг в обе стороны

- [ ] `python -m listam match --request <id>` — сколько матчей, сколько `hot`,
      сколько в дайджесте, за сколько секунд.
- [ ] `python -m listam matches --request <id>` — верхние пять проверить руками:
      балл объясним разбором, район и бюджет соответствуют заявке.
- [ ] `python -m listam scrape --fresh` (живая лента, обычный инкрементальный
      прогон), затем `python -m listam match --new` — матчи только среди новых.
- [ ] Второй `python -m listam match --all` — `new = 0`, дублей нет.
- [ ] Проставить руками статус одному матчу (`set_match_status`), пересчитать,
      убедиться, что статус на месте.

### Шаг 6. Выгрузка и doctor

- [ ] `python -m listam export` — лист «Матчи» есть, числа сходятся с витриной.
- [ ] `python -m listam doctor` — зелёный, строки про источник заявок и матчинг
      на месте.

### Шаг 7. Отчёт и закрытие M2

- [ ] Раздел «Результат фазы 7» со всеми числами шагов 2–6.
- [ ] Контрольный список приёмки из спеки (шесть пунктов) — с колонкой «Вышло».
- [ ] Раздел «M2 закрыт» с таблицей фаз и батарей.
- [ ] Стартовый промпт для M3 (уведомления).
- [ ] Коммит, `git status --short` — чисто.

---

## Шаблон стартового промпта (каждая фаза дописывает свой)

```
Ты продолжаешь работу над инструментом мониторинга list.am в C:\Users\Admin\Downloads\list.

Прочитай docs/superpowers/plans/2026-09-22-m2-requests-and-matching.md:
разделы «Global Constraints», «Карта файлов», «Результат фазы <N-1>» и свою
«Фазу <N>». Чужие фазы не трогай. Спека рядом:
docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md —
из неё читаются «Принятые решения», они в фазах не пересматриваются.

Исходное состояние: HEAD <хэш>, дерево чистое, батарея <N> passed, <M> skipped,
схема базы <версия>, боевая база 20 826 объявлений, прогонов 8.

Твоя задача — фаза <N>: <одна фраза о смысле фазы>.
<Три-четыре строки о том, что именно делается и почему это одно целое.>

Работай по шагам задач: на каждое поведение — падающий тест ДО правки.
Тесты гоняй только .venv/Scripts/python.exe -m pytest -q, любой прогон CLI
из скрипта — только с PYTHONIOENCODING=utf-8. Пороги в конфиге не поднимай.
Новый метод порта — это новый контрактный тест в tests/contracts/.
Схему меняет только миграция 007 из фазы 1, новых миграций в M2 нет.

В конце сессии допиши в план раздел «Результат фазы <N>»: что сделано, числа
батареи, что разошлось с планом и почему, и стартовый промпт для фазы <N+1>.
Сделай коммит.
```

## Что в этот план не входит

- Уведомления и дайджест (M3), телефоны продавцов (M4), дашборд (M5).
- Автоматическое сужение будущих матчей по `reject_reason`: поле заполняется
  руками, механики обучения в M2 нет.
- Скоринг `must_have` и `nice_to_have` свободным текстом.
- Живая проверка `gsheet`: адаптер пишется и покрывается контрактным тестом
  под `skipif`, приёмка идёт на `csv` (решение 11 спеки).
- Правка `listam/crawler.py`: связка со `scrape` делается расписанием
  (решение 9 спеки).
- Курсы EUR и RUB, формат отметок времени в базе — как и в плане M1,
  трогать незачем.
