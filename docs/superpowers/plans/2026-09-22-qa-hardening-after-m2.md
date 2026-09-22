# QA-ужесточение после M2: план исправлений

> **Для агента-исполнителя:** одна фаза — одна сессия. В начале сессии читаешь
> «Что нашёл аудит», «Global Constraints», «Карта файлов» и свою фазу; чужие
> фазы не трогаешь. В конце сессии дописываешь в этот файл раздел «Результат
> фазы N» и стартовый промпт для следующей фазы, затем делаешь коммит.
> Шаги помечены `- [ ]` — отмечай по ходу.

**Нумерация.** M0 — парсер и обход, M1 — дельта и история, M2 — заявки и
матчинг (закрыт 22.09.2026, семь фаз). Этот план — не новый этап, а разбор
внешнего QA по всему, что построено к этому дню. Следующий этап (уведомления
и дайджест) в файлах зовётся **M3**; его план пишется после фазы 8 этого
документа.

**Цель:** брокер, который смотрит в витрину матчей, видит там квартиры,
по которым можно звонить сегодня, — а не снимок вчерашнего рынка, дубли одной
квартиры и заявки клиентов, ушедших месяц назад.

**Архитектура:** ничего не переписывается заново. Матч получает жизненный цикл
(появился → подтверждается → закрылся), кластер — устойчивый идентификатор,
выборка `--new` — честную мерку, источник заявок — двусторонний договор
(появление и исчезновение), оркестраторы — общий каркас вместо пяти копий.

**Стек:** Python 3.12, SQLite, pytest, openpyxl, PyYAML. Новых зависимостей
план не вводит.

**Исходное состояние (снято 22.09.2026):**

| Что | Значение |
| --- | --- |
| HEAD | `046858c`, дерево чистое |
| Батарея | **604 passed, 18 skipped** (37,5 с) |
| Схема базы | 7 |
| Боевая база | на машине аудита отсутствует (`data/` в `.gitignore`); замеры сделаны на синтетической базе того же масштаба — 20 826 объявлений, 50 заявок |

---

## Что нашёл аудит

Каждая находка ниже **воспроизведена запуском**, а не вычитана глазами.
Номера используются в задачах фаз.

### Блокеры

**B-1. Матч, переставший быть правдой, живёт вечно.**
Подбор не пишет отказы — и правильно делает. Но строка, записанная вчера,
никогда не закрывается. Объявление подорожало со 100 000 до 400 000 $ —
жёсткий критерий теперь отвечает «бюджет», `_write_matches` делает `continue`,
и в `matches` остаётся строка с баллом 86. Витрина и лист «Матчи» показывают
клиенту с бюджетом 120 000 $ квартиру за 400 000 $.
*Воспроизведено:* матч записан (балл 86) → цена поднята → `rejection` = «бюджет»
→ `matches_for_request` возвращает 1 строку, балл 86.

**B-2. Смена представителя кластера плодит дубли одной квартиры.**
В `matches` ложится самый дешёвый член кластера. Появилось объявление дешевле —
представителем стал он, а строка на прежнего представителя осталась. Клиент
получает две карточки одной квартиры: ровно то, ради чего дедуп и затевался.
*Воспроизведено:* `p1` (100 000 $) и `p2` (90 000 $) на одном адресе →
`cheapest_id` сменился с `p1` на `p2` → в базе 2 строки матча на одну квартиру.

**B-3. Заявка, удалённая из таблицы источника, остаётся активной навсегда.**
`run_requests_sync` знает только про появление и правку. Брокер удалил строку —
заявка продолжает матчиться, лезть в витрину и в выгрузку.
*Воспроизведено:* `R-1` и `R-2` в базе, источник отдаёт только `R-1` →
после повторного чтения активны обе.

### Высокие

**H-1. `cluster_id` меняется от появления соседа.**
Идентификатор считается по `min`/`max` площади состава. Новое объявление внутри
допуска сдвигает границы — и кластер получает другой `cluster_id`. Снимок
`matches.cluster_id` перестаёт совпадать с базой, «тот же кластер» не опознаётся
ничем.
*Воспроизведено:* `{a:60, b:61}` → `4572d79ac1c01415`; после добавления `c:62.5`
→ `babbed2035e04559`.

**H-2. `match --new` не видит подешевевших квартир.**
Выборка мерится по `first_seen`. Квартира, которая вчера стоила 130 000 $, а
сегодня стоит 118 000 $ и наконец влезла в бюджет, новой не считается и в
`--new` не попадает никогда. Ночной сценарий `scrape && match --new`
систематически теряет главное событие рынка — снижение цены.

**H-3. Транзитивная цепочка склеивает разные квартиры.**
Цепочка растёт от соседа к соседу: при допуске 2 м² четыре объявления
60/62/64/66 м² становятся одним кластером шириной 6 м². Показывается один —
самый дешёвый; остальные три квартиры клиент не увидит вовсе.
*Воспроизведено:* 4 объявления, допуск 2.0 → 1 кластер размера 4.

**H-4. `gsheet` и `csv` читают одну строку по-разному.**
Решение 1 спеки: адаптеры обязаны давать одну и ту же заявку из одной и той же
строки. `csv` отклоняет строку, где значений больше, чем колонок. `gsheet`
собирает строку через `zip(header, row)` — лишние значения молча отбрасываются.
Разъехавшаяся заявка в Google Sheet будет прочитана как исправная.
*Воспроизведено:* строка `[R-7, 100000, заметка, хвост, ещё хвост]` при шапке из
трёх колонок → `gsheet` вернул три поля, потерял два; `csv` ту же строку
отклонил с внятным объяснением.

**H-5. Один `match --all` стоит минуту и четыре чтения всей базы.**
Замеры на 20 826 объявлений / 50 заявок:

| Что | Числа |
| --- | --- |
| `listings_for_matching()` за прогон | **4 вызова**, каждый ~1,15 с |
| Кластеры считаются | **дважды** (в `_count_clusters_if_needed` и следом) |
| Пар «заявка × представитель» | 1 030 800 |
| Скоринг всех пар | ~1 с (не узкое место) |
| Запись матчей | 6 743 строки за 5,8 с на 5 заявках → **~67 000 строк, ~58 с** на 50 |

Запись идёт по **одной транзакции на строку** — вот вся минута.

### Средние

**M-1. Опечатка в имени веса молча выкидывает фактор из балла.**
`score()` берёт факторы через `shares.get(name)` и неизвестное имя пропускает.
`budjet: 30` вместо `budget: 30` — и фактор «бюджет» весом 30 не участвует.
*Воспроизведено:* при весах `{budjet: 30, district: 20}` балл 50 вместо 86,
разбор состоит из одного фактора. `doctor` про это только предупреждает, подбор
едет и пишет в базу.

**M-2. `match.limit: 0` в конфиге даёт витрину из одной строки «…и ещё 30».**
CLI отклоняет `--limit 0` кодом 2, конфиг то же число принимает.
*Воспроизведено:* `display_limit` = 0 → «Заявка R-1 — подобрано 30 / …и ещё 30».

**M-3. `doctor` проверяет схему временного пробника, а не рабочей базы.**
Проверка создаёт пустую базу во временной папке, мигрирует её и печатает версию
7 — при том, что рабочий файл может быть на версии 4 и любая команда откажется
работать.
*Воспроизведено:* рабочая база откачена до схемы 4 → `doctor` говорит
«OK, версия 7».

**M-4. Витрина читает объявления по одному.**
`collect_matches` зовёт `get_listing` на каждый матч. На 50 заявках это
**66 910 отдельных запросов**.

**M-5. Отрицательные числа в заявке принимаются молча.**
`budget_max = -5000` → `stretch(10) = -5500` → все объявления отклоняются с
причиной «бюджет», и брокер видит пустую витрину без объяснений. Так же
проходят `area_min = -10` и `floor_min = -3`.
*Воспроизведено.*

**M-6. Две строки с одним `id` — вторая молча затирает первую.**
Ни отказа, ни предупреждения: в базе остаётся последняя.
*Воспроизведено:* `R-5` с бюджетом 100 000 и 999 999 → в базе 999 999.

**M-7. `set_match_status` принимает любое слово.**
`"ПОЖАЛУЙ НЕТ"` ложится в базу и попадает в выгрузку как есть.
*Воспроизведено.*

**M-8. `requests_sync` ловит `OSError` там, где соседи ловят `Exception`.**
Битый файл базы даёт `sqlite3.DatabaseError` — `requests` падает трейсбеком,
`match` и `cluster` в той же ситуации отвечают человеку.

**M-9. Заливка в хранилище не обёрнута.**
`storage.upload(...)` и `rotate_backups(...)` в `matching._upload` стоят вне
`try`: сбой сети даёт трейсбек поверх уже записанной базы, и отчёт прогона
до человека не доходит.

**M-10. Лист «Матчи» в выгрузке без потолка.**
`_export` зовёт `collect_matches(config)` без `limit`: на боевых числах это
десятки тысяч строк в .xlsx.

### Низкие

- **L-1.** `iter_requests` сортирует по `external_id` как по строке:
  `R-0, R-1, R-10, R-11, R-12, R-2`. *Воспроизведено.*
- **L-2.** `run_match(recount_all=...)` принимается и нигде не используется.
- **L-3.** `render_matches` группирует по `request.id`; заявка с `id = None`
  схлопывает все такие заявки в одну группу.
- **L-4.** `gsheet` читает `A1:Z1000`: заявка в 1000-й строке молча не читается.
- **L-5.** `rooms: "0"` разбирается в `[0]` — квартир с нулём комнат не бывает.

### Долг и рефакторинг

- **R-1.** Пять оркестраторов (`crawler`, `recheck`, `clustering_run`,
  `requests_sync`, `matching`) повторяют один блок: замок → свежая копия →
  миграция → проверка схемы → снимок → ротация → заливка. Пять копий уже
  разошлись (см. M-8, M-9).
- **R-2.** `listam/matching.py` — 496 строк: оркестрация подбора и печать
  витрины в одном файле.
- **R-3.** `SELLER_TYPES` и `MATCH_STATUSES` продублированы в `matching.py` и
  `adapters/exporter_xlsx.py` — два места для одного словаря подписей.

---

## Global Constraints (нарушать нельзя)

- Схема базы меняется **только** новой версионированной миграцией в
  `listam/migrations/`. В этом плане миграции две — **008** (фаза 1) и
  **009** (фаза 3). Больше никаких.
- Новый метод порта — это новый контрактный тест в `tests/contracts/`.
- Ни один путь, ключ, идентификатор и имя адаптера не зашит в код: внешнее —
  за портом, выбор — в конфиге, секреты — только в `.env`, подключение —
  в `listam/wiring.py`.
- Все отметки времени в UTC (`to_iso`/`from_iso` из
  `listam/adapters/db_sqlite.py`), пути относительные от корня проекта.
- **Пороги в конфиге не поднимаются, чтобы тест позеленел.** Порог, который
  мешает, — это находка или решение; и то и другое записывается в отчёт фазы.
- Ноль в пороге значит «ноль», а не «выключено»; выключается `null`. Читать
  пороги — только через `listam.config.threshold`.
- Бессмысленный ввод командной строки отклоняется на входе кодом возврата 2.
  **Бессмысленное значение в конфиге отклоняется так же** — это новое правило
  этого плана, оно вводится в фазе 5.
- След звонка (`matches.status`, `matches.reject_reason`) пишет только человек.
  Ни одна правка этого плана не имеет права его трогать — включая закрытие
  протухших матчей в фазе 1.
- Тесты — только `.venv/Scripts/python.exe -m pytest -q`. Системный python
  не годится: в нём нет `openpyxl`.
- Любой прогон CLI из скрипта — только с `PYTHONIOENCODING=utf-8`.
- Уведомления — M3, телефоны продавцов — M4, дашборд — M5. Не трогаем.
- `listam/crawler.py` правится только в фазе 7 и только в части общего каркаса.
- Числа «ожидается N passed» — арифметика от 604 плюс тесты фазы. Разошлось
  на один-два — не повод подгонять: сверь, что именно добавилось, и поправь
  число в плане.

## Карта файлов

| Файл | Ответственность | Фаза |
| --- | --- | --- |
| `listam/migrations/008_match_lifecycle.sql` | схема 7 → 8: закрытие матчей | 1 |
| `listam/migrations/009_match_marks.sql` | схема 8 → 9: отметка подбора у заявки | 3 |
| `listam/ports/database.py` | новые методы порта | 1, 3, 4, 6 |
| `listam/adapters/db_sqlite.py` | реализация, пакетная запись, JOIN витрины | 1, 3, 4, 6, 7 |
| `listam/matching.py` | закрытие протухших, честная выборка, один проход | 1, 3, 6, 7 |
| `listam/matches_view.py` | витрина матчей отдельно от подбора | 7 |
| `listam/domain/clustering.py` | устойчивый `cluster_id`, ширина кластера | 2 |
| `listam/domain/requests.py` | отрицательные, дубли `id`, `rooms: 0` | 4 |
| `listam/adapters/requests_gsheet.py` | разъехавшаяся строка как у `csv` | 4 |
| `listam/requests_sync.py` | закрытие исчезнувших заявок | 4 |
| `listam/config.py` | `positive`, отказ по бессмысленному порогу | 5 |
| `listam/doctor.py` | проверка рабочей базы, веса как сбой | 5 |
| `listam/adapters/exporter_xlsx.py` | потолок листа «Матчи» | 6 |
| `listam/runner.py` | общий каркас оркестрации | 7 |
| `listam/domain/labels.py` | подписи статусов в одном месте | 7 |
| `config/dev.yaml`, `config/prod.yaml` | новые пороги | 3, 5, 6 |

---

# Фаза 1. Матч, который перестал быть правдой

**Одна сессия.** Закрывает **B-1** и **B-2**. У матча появляется конец жизни:
полный проход по заявке закрывает те матчи, которые в этом проходе не
подтвердились. След звонка при этом не трогается — закрытый матч остаётся
в базе со своим `status` и `reject_reason`, он просто уходит из витрины.

**Ожидается после фазы:** ~617 passed, 18 skipped, схема базы 8.

### Задача 1.1. Миграция 008

**Файлы:**
- Создать: `listam/migrations/008_match_lifecycle.sql`
- Тест: `tests/test_migrations.py`

- [x] **Шаг 1: падающий тест на версию схемы и колонки**

```python
# tests/test_migrations.py — дописать
def test_migration_008_adds_the_end_of_a_match_life(tmp_path):
    db = SqliteDatabase(tmp_path / "m8.sqlite")
    db.connect()
    db.migrate()
    assert db.schema_version() == 8
    columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(matches)")}
    assert {"retired_at", "retired_reason"} <= columns
    db.close()
```

- [x] **Шаг 2: убедиться, что тест падает**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_migrations.py -k 008`
Ожидается: FAIL — `assert 7 == 8`.

- [x] **Шаг 3: написать миграцию**

```sql
-- Версия 8: у матча появляется конец жизни.
--
-- matches.retired_at — когда полный проход по заявке перестал подтверждать
--   этот матч. Удалять строку нельзя: в ней след звонка (status,
--   reject_reason), и «мы звонили по этой квартире» не должно исчезать
--   вместе с подорожавшим объявлением.
-- matches.retired_reason — почему перестал: «бюджет», «район», «не
--   представитель кластера». Читает человек, а не программа.
--
-- Индекс по (request_id, retired_at) — витрина спрашивает только живые
-- матчи одной заявки, и это её главный запрос.

ALTER TABLE matches ADD COLUMN retired_at TEXT;
ALTER TABLE matches ADD COLUMN retired_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_matches_alive ON matches(request_id, retired_at);
```

- [x] **Шаг 4: тест проходит, вся батарея тоже**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: 605 passed, 18 skipped. Если падает тест, проверяющий число
миграций или версию схемы, — поправь его: это ожидаемое следствие, а не
находка.

- [x] **Шаг 5: коммит**

```bash
git add listam/migrations/008_match_lifecycle.sql tests/test_migrations.py
git commit -m "feat(db): миграция 008 — у матча появляется конец жизни"
```

### Задача 1.2. Модель и порт: закрытие матча

**Файлы:**
- Изменить: `listam/domain/models.py`, `listam/ports/database.py`,
  `listam/adapters/db_sqlite.py`
- Тест: `tests/contracts/test_database_contract.py`

**Interfaces — Produces:**
```python
# listam/domain/models.py — два поля в Match
retired_at: datetime | None = None
retired_reason: str | None = None

# listam/ports/database.py
Database.retire_matches(request_id: int, keep: set[str],
                        now: datetime, reason: str) -> int
Database.matches_for_request(request_id: int, min_score: float | None = None,
                             limit: int | None = None,
                             include_retired: bool = False) -> list[Match]
```

- [x] **Шаг 1: контрактные тесты**

```python
# tests/contracts/test_database_contract.py — дописать
def test_a_match_that_stopped_matching_is_retired_and_leaves_the_window(db):
    db.upsert_request(make_request(), NOW)
    request = db.get_request("R-1")
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_listing(make_listing("L-2"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="L-1", score=80.0), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="L-2", score=70.0), NOW)

    closed = db.retire_matches(request.id, keep={"L-1"}, now=LATER, reason="бюджет")

    assert closed == 1
    alive = [item.listing_id for item in db.matches_for_request(request.id)]
    assert alive == ["L-1"]
    everything = db.matches_for_request(request.id, include_retired=True)
    assert {item.listing_id for item in everything} == {"L-1", "L-2"}


def test_retiring_a_match_does_not_touch_the_call_trace(db):
    db.upsert_request(make_request(), NOW)
    request = db.get_request("R-1")
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="L-1", score=80.0), NOW)
    stored = db.matches_for_request(request.id)[0]
    db.set_match_status(stored.id, "called", "дорого")

    db.retire_matches(request.id, keep=set(), now=LATER, reason="бюджет")

    closed = db.matches_for_request(request.id, include_retired=True)[0]
    assert closed.status == "called"
    assert closed.reject_reason == "дорого"
    assert closed.retired_at == LATER
    assert closed.retired_reason == "бюджет"


def test_a_match_that_matches_again_comes_back_to_the_window(db):
    db.upsert_request(make_request(), NOW)
    request = db.get_request("R-1")
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="L-1", score=80.0), NOW)
    db.retire_matches(request.id, keep=set(), now=LATER, reason="бюджет")

    db.upsert_match(Match(request_id=request.id, listing_id="L-1", score=75.0), LATER)

    alive = db.matches_for_request(request.id)
    assert [item.listing_id for item in alive] == ["L-1"]
    assert alive[0].retired_at is None


def test_retiring_twice_closes_nothing_the_second_time(db):
    db.upsert_request(make_request(), NOW)
    request = db.get_request("R-1")
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="L-1", score=80.0), NOW)
    assert db.retire_matches(request.id, keep=set(), now=LATER, reason="бюджет") == 1
    assert db.retire_matches(request.id, keep=set(), now=LATER, reason="бюджет") == 0


def test_retiring_one_request_does_not_touch_another(db):
    db.upsert_request(make_request("R-1"), NOW)
    db.upsert_request(make_request("R-2"), NOW)
    first, second = db.get_request("R-1"), db.get_request("R-2")
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_match(Match(request_id=first.id, listing_id="L-1", score=80.0), NOW)
    db.upsert_match(Match(request_id=second.id, listing_id="L-1", score=80.0), NOW)

    db.retire_matches(first.id, keep=set(), now=LATER, reason="бюджет")

    assert len(db.matches_for_request(second.id)) == 1
```

Если в файле ещё нет хелпера `make_listing`, добавь его рядом с `make_request`:

```python
def make_listing(listing_id="L-1", **over) -> Listing:
    fields = dict(
        id=listing_id, url=f"https://www.list.am/ru/item/{listing_id}",
        district="Кентрон", street="Абовяна", area=70.0, rooms=3, floor=3,
        floors_total=9, price_usd=100_000.0, price_per_sqm=1428.0,
        seller_type="owner",
    )
    fields.update(over)
    return Listing(**fields)
```

- [x] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py -k retir`
Ожидается: FAIL — `AttributeError: 'SqliteDatabase' object has no attribute 'retire_matches'`.

- [x] **Шаг 3: два поля в `Match`**

```python
# listam/domain/models.py — в dataclass Match, после cluster_spread_usd
    retired_at: datetime | None = None      # когда проход перестал его подтверждать
    retired_reason: str | None = None       # почему: «бюджет», «район», «не представитель»
```

- [x] **Шаг 4: объявить метод в порте**

```python
# listam/ports/database.py — рядом с upsert_match
    @abstractmethod
    def retire_matches(self, request_id: int, keep: set[str],
                       now: datetime, reason: str) -> int:
        """Закрывает матчи заявки, которых нет в `keep`. Отдаёт, сколько закрыл.

        Закрытие — не удаление: в строке лежит след звонка, и он переживает
        подорожавшее объявление. Уже закрытые повторно не трогаются, иначе
        `retired_at` двигался бы каждым прогоном и переставал отвечать на
        вопрос «когда вариант отпал».
        """

    @abstractmethod
    def matches_for_request(self, request_id: int, min_score: float | None = None,
                            limit: int | None = None,
                            include_retired: bool = False) -> list["Match"]:
        """Матчи заявки от лучшего к худшему. По умолчанию — только живые."""
```

- [x] **Шаг 5: реализация в адаптере**

```python
# listam/adapters/db_sqlite.py — в разделе «--- матчи ---»
    def retire_matches(self, request_id: int, keep: set[str],
                       now: datetime, reason: str) -> int:
        """Закрывает всё, что этот проход не подтвердил. См. порт."""
        alive = [
            row["id"] for row in self.conn.execute(
                "SELECT id, listing_id FROM matches "
                "WHERE request_id = ? AND retired_at IS NULL",
                (request_id,),
            ) if row["listing_id"] not in keep
        ]
        if not alive:
            return 0
        stamp = to_iso(now)
        with self.transaction():
            self.conn.executemany(
                "UPDATE matches SET retired_at = ?, retired_reason = ? WHERE id = ?",
                [(stamp, reason, match_id) for match_id in alive],
            )
        return len(alive)
```

В `matches_for_request` — новый аргумент и условие:

```python
    def matches_for_request(self, request_id: int, min_score: float | None = None,
                            limit: int | None = None,
                            include_retired: bool = False) -> list[Match]:
        query = "SELECT * FROM matches WHERE request_id = ?"
        params: list = [request_id]
        if not include_retired:
            query += " AND retired_at IS NULL"
        if min_score is not None:
            query += " AND score >= ?"
            params.append(float(min_score))
        query += " ORDER BY score DESC, listing_id"
        if limit is not None:
            query += " LIMIT ?"
            params.append(int(limit))
        return [_row_to_match(row) for row in self.conn.execute(query, tuple(params))]
```

В `upsert_match` — воскрешение. В ветке существующей строки `retired_at`
обязан гаситься **и в сравнении, и в записи**: иначе закрытый матч,
подтвердившийся снова с тем же баллом, остался бы закрытым как «unchanged».

```python
        same = all(
            _normalize(values[name]) == _normalize(existing[name])
            for name in MATCH_COMPARED
        )
        if same and existing["retired_at"] is None:
            return "unchanged"

        updates = dict(values)
        updates["matched_at"] = to_iso(now)
        # Подтвердился снова — значит, живой. Гасим закрытие вместе с причиной:
        # причина без даты читалась бы как «закрыт неизвестно когда».
        updates["retired_at"] = None
        updates["retired_reason"] = None
        updates["id"] = existing["id"]
```

И в `_row_to_match` — два поля:

```python
        retired_at=from_iso(row["retired_at"]),
        retired_reason=row["retired_reason"],
```

- [x] **Шаг 6: тесты проходят, батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: ~610 passed, 18 skipped.

- [x] **Шаг 7: коммит**

```bash
git add listam/domain/models.py listam/ports/database.py listam/adapters/db_sqlite.py tests/contracts/test_database_contract.py
git commit -m "feat(db): матч закрывается, а не исчезает — след звонка переживает его"
```

### Задача 1.3. Подбор закрывает то, что не подтвердил

**Файлы:**
- Изменить: `listam/matching.py:247-276` (`_write_matches`),
  `listam/matching.py:42-70` (`MatchReport`), `listam/matching.py:132-226` (`run_match`)
- Тест: `tests/test_matching.py`

- [x] **Шаг 1: падающие тесты на закрытие**

```python
# tests/test_matching.py — дописать
def test_a_listing_that_left_the_budget_leaves_the_window(matching_config):
    run_match(matching_config)
    config, db_path = matching_config, database_path(matching_config)

    database = SqliteDatabase(db_path)
    database.connect()
    listing = database.get_listing("L-1")
    database.upsert_listing(
        replace(listing, price_usd=900_000.0, price_raw="900000 $"),
        datetime(2026, 9, 23, tzinfo=timezone.utc),
    )
    database.close()

    report = run_match(config)

    assert report.retired == 1
    rows = collect_matches(config)
    assert "L-1" not in [listing.id for _, _, listing in rows]


def test_a_cheaper_twin_replaces_the_old_representative_and_not_doubles_it(
        matching_config_with_duplicates):
    config = matching_config_with_duplicates
    run_match(config)
    before = {listing.id for _, _, listing in collect_matches(config)}

    database = SqliteDatabase(database_path(config))
    database.connect()
    twin = database.get_listing(sorted(before)[0])
    database.upsert_listing(
        replace(twin, id="L-cheap", url="https://www.list.am/ru/item/L-cheap",
                price_usd=(twin.price_usd or 0) - 5_000),
        datetime(2026, 9, 23, tzinfo=timezone.utc),
    )
    database.close()

    run_match(config)

    after = [listing for _, _, listing in collect_matches(config)]
    clusters_shown = [match.cluster_id for _, match, _ in collect_matches(config)]
    assert len(clusters_shown) == len(set(clusters_shown)), \
        "одна квартира не может стоять в витрине дважды"
    assert "L-cheap" in [listing.id for listing in after]


def test_matching_only_the_new_ones_closes_nothing(matching_config_with_two_runs):
    config = matching_config_with_two_runs
    run_match(config)
    report = run_match(config, only_new=True)
    assert report.retired == 0, \
        "выборка --new неполна: закрывать по ней — значит выкинуть всё, чего в ней нет"
```

Импорты в начале файла дополнить: `from dataclasses import replace`,
`from listam.adapters.db_sqlite import SqliteDatabase`,
`from listam.wiring import database_path`, `from listam.matching import collect_matches`
(проверь, что уже импортировано, и не дублируй).

- [x] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_matching.py -k "leaves_the_window or replaces_the_old or closes_nothing"`
Ожидается: FAIL — `AttributeError: 'MatchReport' object has no attribute 'retired'`.

- [x] **Шаг 3: счётчик в отчёте**

```python
# listam/matching.py — в MatchReport, после unchanged
    retired: int = 0            # матчей закрыто: проход их больше не подтверждает
```

и строка в `render`, сразу за строкой «Матчи:»:

```python
        if self.retired:
            lines.append(f"Закрыто матчей: {self.retired} — вариант больше не подходит")
```

- [x] **Шаг 4: закрытие в `_write_matches`**

Целиком заменить тело функции:

```python
def _write_matches(database: Database, report: MatchReport, requests, candidates,
                   representatives: dict, medians: dict[str, float],
                   run_id: int | None, tuning: Settings,
                   full_sweep: bool) -> None:
    """Пара «заявка × представитель» → балл → строка в `matches`.

    `full_sweep` — прошли ли по всей базе. Только полный проход имеет право
    закрывать матчи: по выборке `--new` «не подтвердился» значит «его не было
    в выборке», и закрытие выкинуло бы из витрины всё, кроме свежего.
    """
    now = datetime.now(timezone.utc)
    for request in requests:
        confirmed: set[str] = set()
        for listing in candidates:
            result = score(request, listing, median_by_district=medians,
                           weights=tuning.weights,
                           stretch_percent=tuning.stretch_percent)
            if result.rejected_by is not None:
                # Отказ в базу не пишется: их миллионы, и звонить по ним некуда.
                continue
            cluster = representatives[listing.id]
            outcome = database.upsert_match(Match(
                request_id=request.id,
                listing_id=listing.id,
                score=float(result.value),
                run_id=run_id,
                breakdown=result.breakdown or None,
                cluster_id=cluster.cluster_id,
                cluster_size=cluster.size,
                cluster_spread_usd=cluster.spread_usd,
            ), now)
            confirmed.add(listing.id)
            setattr(report, outcome, getattr(report, outcome) + 1)
            if tuning.hot is not None and result.value >= tuning.hot:
                report.hot += 1
            elif tuning.digest is not None and result.value >= tuning.digest:
                report.digest += 1
        if full_sweep:
            report.retired += database.retire_matches(
                request.id, keep=confirmed, now=now,
                reason="проход больше не подтверждает этот вариант",
            )
```

- [x] **Шаг 5: вызов в `run_match`**

```python
        _write_matches(database, report, requests, candidates, representatives,
                       medians, run_id, settings(config), full_sweep=not only_new)
```

и заливка теперь нужна и после одного только закрытия:

```python
        if report.new or report.updated or report.retired:
```

- [x] **Шаг 6: тесты проходят, батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: ~617 passed, 18 skipped. Если падает
`test_a_match_whose_listing_went_away_is_marked_and_not_hidden` — разберись,
а не правь: снятое с ленты объявление не участвует в `listings_for_matching`,
значит полный проход его не подтвердит и закроет матч. Решение 8 спеки
(«витрина помечает снятое, а не прячет») это запрещает. Правка — в
`retire_matches` звать только с теми заявками, чьи кандидаты считались, а
снятые объявления исключать из закрытия: собери в `run_match` множество
`id` **всех** объявлений базы, включая снятые, и передавай в `keep` те
закрываемые, что просто ушли с ленты. Что именно ты выбрал — запиши в отчёт
фазы.

- [x] **Шаг 7: коммит**

```bash
git add listam/matching.py tests/test_matching.py
git commit -m "fix(match): вариант, переставший подходить, уходит из витрины"
```

### Конец фазы 1

- [x] Дописать в этот файл раздел «Результат фазы 1»: что сделано, числа
      батареи (`passed`/`skipped`), версия схемы, как решён вопрос со снятыми
      объявлениями из шага 6, что разошлось с планом и почему.
- [x] Дописать стартовый промпт для фазы 2 по шаблону из раздела
      «Шаблон стартового промпта» в конце плана.
- [x] `git status --short` — чисто; коммит сделан.

## Результат фазы 1

**Сделано.** У матча появился конец жизни. **B-1** и **B-2** закрыты.

| Что | Значение |
| --- | --- |
| HEAD | `1489ede`, дерево чистое |
| Батарея | **615 passed, 18 skipped** (37,9 с) |
| Схема базы | **8** |
| Коммитов | 3 (`722af4d`, `3888a29`, `1489ede`) |

**Что именно изменилось:**

- Миграция `008_match_lifecycle.sql`: `matches.retired_at`,
  `matches.retired_reason`, индекс `idx_matches_alive(request_id, retired_at)`.
- Порт: `Database.retire_matches(request_id, keep, now, reason) -> int` и
  новый аргумент `include_retired` у `matches_for_request` (по умолчанию
  витрина видит только живые).
- `upsert_match` воскрешает закрытый матч: `retired_at` гасится и в записи,
  и в сравнении — иначе подтвердившийся снова матч с тем же баллом остался бы
  закрытым как `unchanged`.
- `MatchReport.retired` и строка отчёта «Закрыто матчей: N — вариант больше
  не подходит». Заливка в хранилище теперь идёт и после одного только
  закрытия (`report.new or report.updated or report.retired`).
- `_write_matches` собирает `confirmed` по заявке и закрывает остальное —
  но только при `full_sweep=not only_new`.

**Вопрос со снятыми объявлениями (шаг 6 задачи 1.3) — как решён.**
Снятое с ленты объявление не попадает в `listings_for_matching`, значит
полный проход его не подтвердит и закрыл бы матч — а решение 8 спеки велит
витрине снятое **помечать, а не прятать**. Решение: в `run_match` считается
`off_the_feed = database.known_ids() - {id живых объявлений выборки}` —
это снятые и отложенные аномалией, то есть всё, чего проход не видел вовсе.
Множество уходит в `_write_matches` и подмешивается в `keep`:
`retire_matches(..., keep=confirmed | off_the_feed, ...)`.

Почему так, а не условием внутри адаптера: `retire_matches` — тупая операция
над множеством, и прятать в ней знание «какие объявления живые» значило бы
сделать контракт порта непредсказуемым. `known_ids()` — чтение одной колонки,
уже существующий метод порта; отдельного прохода по таблице оно не добавляет
в том смысле, в каком это важно для **H-5** (там речь про четыре чтения
`listings_for_matching` целиком). Поведение закрыто тестом
`test_a_match_whose_listing_left_the_feed_is_not_retired`.

**Что разошлось с планом.**

- **Числа батареи: 615, а не ~617.** Арифметика: 604 + 1 (миграция)
  + 6 (контрактные) + 4 (матчинг) = 615. В плане было заложено 5 контрактных
  и 3 матчинговых теста; я добавил по одному сверх:
  `test_a_match_confirmed_with_the_very_same_score_comes_back_too` (без него
  правка `same and existing["retired_at"] is None` не была бы покрыта — это
  как раз та ошибка, от которой план предостерегает словами) и
  `test_a_match_whose_listing_left_the_feed_is_not_retired` (решение выше
  обязано быть закрыто тестом, а не отчётом).
- **`test_migration_007_adds_request_and_match_columns` правлен:** `== 7`
  стало `>= 7`, как и у теста миграции 006. Ожидаемое следствие новой
  миграции, а не находка.
- **Идентификаторы в тестах фазы 1.3.** В плане тесты написаны на `L-1`;
  фикстура `matching_config` наполняется объявлениями `1`, `2`, `3`
  (`suitable`), поэтому в тесте про подорожание стоит `"1"`. Смысл теста
  не изменился.
- **Хелпер `make_listing` уже был** в `tests/contracts/test_database_contract.py`
  (с другим набором полей по умолчанию) — второй не заводил; заявки берутся
  через существующий `stored_request`.
- **Порядок строк в `MatchReport.render`.** Строка «Закрыто матчей» вставлена
  между «Матчи:» и «Из них горячих», как велит план; для этого список строк
  собирается не одним литералом, а с `append`.

**Чего фаза не трогала.** След звонка: `status` и `reject_reason` не входят
ни в `MATCH_FIELDS`, ни в `UPDATE` закрытия — `retire_matches` пишет ровно две
колонки. Тест `test_retiring_a_match_does_not_touch_the_call_trace` это держит.

## Стартовый промпт для фазы 2

```
Ты продолжаешь работу над инструментом мониторинга list.am
в C:\Users\Artur.A.Gevorgyan\Downloads\new_project.

Прочитай docs/superpowers/plans/2026-09-22-qa-hardening-after-m2.md:
разделы «Что нашёл аудит», «Global Constraints», «Карта файлов»,
«Результат фазы 1» и свою «Фазу 2». Чужие фазы не трогай.
Рядом лежат план и спека M2 — из них читаются «Принятые решения»:
docs/superpowers/plans/2026-09-22-m2-requests-and-matching.md,
docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md.
Решения 1–11 в фазах не пересматриваются; если фаза одно из них уточняет,
это прямо написано в её заголовке — у фазы 2 уточняется решение 5.

Исходное состояние: HEAD 1489ede, дерево чистое, батарея 615 passed,
18 skipped, схема базы 8.

Твоя задача — фаза 2: кластер, который не разваливается.
Идентификатор кластера перестаёт зависеть от состава (H-1): сегодня он
считается по min/max площади, и новый сосед внутри допуска молча меняет
cluster_id всему кластеру — снимок в matches.cluster_id перестаёт совпадать
с базой. Заодно цепочка по площади перестаёт растягиваться без предела (H-3):
при допуске 2 м² объявления 60/62/64/66 склеиваются в один кластер шириной
6 м², и три квартиры из четырёх клиент не видит вовсе.

Работай по шагам задач: на каждое поведение — падающий тест ДО правки.
Тесты гоняй только .venv/Scripts/python.exe -m pytest -q, любой прогон CLI
из скрипта — только с PYTHONIOENCODING=utf-8. Пороги в конфиге не поднимай.
Новый метод порта — это новый контрактный тест в tests/contracts/.
Схему меняют только миграции 008 (сделана в фазе 1) и 009 (фаза 3);
своих не заводи — фаза 2 схему не трогает вовсе.
След звонка (matches.status, matches.reject_reason) не трогает ничто.

В конце сессии допиши в план раздел «Результат фазы 2»: что сделано, числа
батареи, что разошлось с планом и почему, и стартовый промпт для фазы 3.
Сделай коммит.
```

---

# Фаза 2. Кластер, который не разваливается

**Одна сессия.** Закрывает **H-1** и **H-3**. Идентификатор кластера перестаёт
зависеть от состава, а цепочка по площади перестаёт растягиваться без предела.

**Ожидается после фазы:** ~625 passed, 18 skipped, схема базы 8.

**Решение фазы (записывается в отчёт):** ширина кластера по площади не
превышает допуск. Решение 5 спеки («объединение соседей, а не бакет») этим не
отменяется: 60,0 и 61,9 при допуске 2 по-прежнему в одном кластере, где бакет
их бы разрезал. Отменяется только транзитивный разгон: 60 и 66 — разные
квартиры, и склеивать их допуск в 2 м² не разрешал никогда.

### Задача 2.1. Ширина кластера ограничена допуском

**Файлы:**
- Изменить: `listam/domain/clustering.py:73-97` (`_groups`)
- Тест: `tests/test_clustering.py`

- [x] **Шаг 1: падающие тесты**

```python
# tests/test_clustering.py — дописать
def test_a_chain_does_not_stretch_past_the_tolerance():
    found = clusters([
        listing("a", area=60.0), listing("b", area=62.0),
        listing("c", area=64.0), listing("d", area=66.0),
    ], area_tolerance=2.0)
    sizes = sorted(cluster.size for cluster in found)
    assert sizes == [1, 1, 2], (
        "60 и 66 — разные квартиры: допуск 2 м² не разрешал их склеивать"
    )


def test_neighbours_inside_the_tolerance_still_meet():
    found = clusters([listing("a", area=60.0), listing("b", area=61.9)],
                     area_tolerance=2.0)
    assert [cluster.size for cluster in found] == [2], (
        "бакет разрезал бы их по границе — объединение соседей этого не делает"
    )


def test_the_widest_cluster_is_no_wider_than_the_tolerance():
    found = clusters([listing(f"x{n}", area=60.0 + n * 0.5) for n in range(12)],
                     area_tolerance=2.0)
    for cluster in found:
        areas = [60.0 + int(item[1:]) * 0.5 for item in cluster.listing_ids]
        assert max(areas) - min(areas) <= 2.0
```

Хелпер `listing(...)` в этом файле уже есть — используй его; если имя другое,
возьми то, что в файле.

- [x] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_clustering.py -k "stretch or widest"`
Ожидается: FAIL — `assert [1, 1, 2] == [4]` (цепочка склеила всё).

- [x] **Шаг 3: мерить от начала цепочки, а не от соседа**

```python
# listam/domain/clustering.py — в _groups, цикл по members
        chain = [members[0]]
        for listing in members[1:]:
            # Мерка — от первого члена цепочки, а не от предыдущего. Иначе
            # цепочка разгоняется: 60→62→64→66 при допуске 2 даёт кластер
            # шириной 6 м², в котором три квартиры из четырёх клиент никогда
            # не увидит — показывается только самая дешёвая.
            if listing.area - chain[0].area <= area_tolerance:
                chain.append(listing)
                continue
            groups.append((key, chain))
            chain = [listing]
        groups.append((key, chain))
```

- [x] **Шаг 4: тесты проходят**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_clustering.py`
Ожидается: все зелёные.

- [x] **Шаг 5: коммит**

```bash
git add listam/domain/clustering.py tests/test_clustering.py
git commit -m "fix(cluster): ширина кластера не больше допуска — 60 и 66 м² разные квартиры"
```

### Задача 2.2. Устойчивый `cluster_id`

**Файлы:**
- Изменить: `listam/domain/clustering.py:68-70` (`_cluster_id`),
  `listam/domain/clustering.py:100-119` (`clusters`)
- Тест: `tests/test_clustering.py`

**Interfaces — Produces:**
```python
def _anchor(listing_ids: list[str]) -> str   # самый старший id кластера
def _cluster_id(key: tuple, anchor: str) -> str
```

- [x] **Шаг 1: падающие тесты на устойчивость**

```python
# tests/test_clustering.py — дописать
def test_a_newcomer_does_not_rename_the_cluster():
    before = clusters([listing("100", area=60.0), listing("101", area=61.0)],
                      area_tolerance=2.0)[0]
    after = clusters([listing("100", area=60.0), listing("101", area=61.0),
                      listing("102", area=61.5)], area_tolerance=2.0)[0]
    assert before.cluster_id == after.cluster_id, (
        "идентификатор кластера назван по самому старому объявлению в нём "
        "и от прихода соседа не меняется"
    )


def test_the_anchor_is_the_oldest_id_and_not_the_shortest_string():
    found = clusters([listing("9", area=60.0), listing("10", area=60.5)],
                     area_tolerance=2.0)[0]
    alone = clusters([listing("9", area=60.0)], area_tolerance=2.0)[0]
    assert found.cluster_id == alone.cluster_id, (
        "id list.am растут числами: 9 старше 10, хотя как строка — больше"
    )


def test_two_different_addresses_are_two_different_clusters():
    found = clusters([listing("100", area=60.0, street="Абовяна"),
                      listing("101", area=60.0, street="Маштоца")],
                     area_tolerance=2.0)
    assert len({cluster.cluster_id for cluster in found}) == 2
```

- [x] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_clustering.py -k "newcomer or anchor"`
Ожидается: FAIL — идентификаторы разные.

- [x] **Шаг 3: якорь вместо границ площади**

```python
# listam/domain/clustering.py — заменить _cluster_id целиком
def _anchor(listing_ids: Iterable[str]) -> str:
    """Самое старое объявление кластера — по нему кластер и называется.

    Считать идентификатор по границам площади состава нельзя: сосед, попавший
    внутрь допуска, сдвигает min/max — и кластер получает новое имя, хотя
    квартира та же. Снимок `matches.cluster_id` после этого не совпадает
    с базой ничем.

    `(len, id)` вместо просто `id`: идентификаторы list.am — числа строками,
    и лексикографически «9» больше «10», а по возрасту — младше.
    """
    return min(listing_ids, key=lambda item: (len(str(item)), str(item)))


def _cluster_id(key: tuple, anchor: str) -> str:
    raw = "|".join(str(part) for part in key) + f"|{anchor}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
```

В `clusters` — считать якорь по составу:

```python
    for key, members in _groups(list(listings), area_tolerance):
        cluster_id = _cluster_id(key, _anchor(item.id for item in members))
        prices = [item.price_usd for item in members if item.price_usd is not None]
```

Строка со `areas` из старой версии больше не нужна — удали её.

- [x] **Шаг 4: тесты проходят, батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: ~625 passed, 18 skipped. Тесты, в которых проверялись конкретные
шестнадцатеричные `cluster_id`, теперь ждут другие значения — это ожидаемое
следствие, пересними их. Тесты, проверяющие **состав** кластера, меняться
не должны; если такой поехал — это находка, разбирайся.

- [x] **Шаг 5: коммит**

```bash
git add listam/domain/clustering.py tests/test_clustering.py
git commit -m "fix(cluster): идентификатор назван по старшему объявлению и не плывёт"
```

### Задача 2.3. Пересчёт кластеров не пропускает изменившийся состав

**Файлы:**
- Изменить: `listam/matching.py:229-244` (`_count_clusters_if_needed`)
- Тест: `tests/test_matching.py`

Проверка «есть ли непроставленные `cluster_id`» ловит только новые строки.
Объявление, ушедшее с ленты, уменьшает кластер — а `cluster_size` в матче
остаётся прежним, пока в базе не появится хоть одна строка без кластера.

- [x] **Шаг 1: падающий тест**

```python
# tests/test_matching.py — дописать
def test_a_cluster_that_lost_a_member_says_the_new_size(
        matching_config_with_duplicates):
    config = matching_config_with_duplicates
    run_match(config)
    sizes_before = {match.cluster_size for _, match, _ in collect_matches(config)}
    assert max(sizes_before) > 1

    database = SqliteDatabase(database_path(config))
    database.connect()
    gone = database.matches_for_request(
        next(iter(database.iter_requests())).id)[0].listing_id
    database.mark_gone([gone], datetime(2026, 9, 23, tzinfo=timezone.utc))
    database.close()

    run_match(config)

    sizes_after = [match.cluster_size for _, match, _ in collect_matches(config)]
    assert max(sizes_after) < max(sizes_before), (
        "объявление ушло с ленты — кластер стал меньше, и витрина обязана "
        "показывать новый размер, а не вчерашний"
    )
```

- [x] **Шаг 2: убедиться, что тест падает**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_matching.py -k lost_a_member`
Ожидается: FAIL — размер прежний.

- [x] **Шаг 3: считать кластеры всегда, а не по признаку пустой колонки**

```python
def _count_clusters(database: Database, config: Config, notes: list[str]) -> None:
    """Кластеры перед подбором — каждый раз, а не когда в базе есть пустые.

    Признак «есть объявление без cluster_id» ловит только появление. Уход
    с ленты состав кластера тоже меняет, колонок не трогая: кластер из трёх
    становится кластером из двух, а витрина, пока её не пересчитали, обещает
    клиенту три предложения по одной квартире. Пересчёт стоит 0,1 с на
    двадцати тысячах строк — дешевле, чем неверный размер.

    Замка здесь второго нет: `cluster_database` работает по уже открытой базе.
    """
    counted = cluster_database(database, area_tolerance(config))
    notes.append(
        f"кластеры пересчитаны: объявлений {counted.listings}, "
        f"кластеров {counted.clusters}, изменено строк {counted.changed}"
    )
```

В `run_match` заменить вызов `_count_clusters_if_needed(...)` на
`_count_clusters(database, config, notes)`.

- [x] **Шаг 4: тесты проходят, батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: ~626 passed, 18 skipped. Тест
`test_clusters_are_counted_before_matching_if_the_base_has_none` поменял смысл:
кластеры считаются всегда — перепиши его под новое поведение и переименуй
в `test_clusters_are_counted_before_every_matching`.

- [x] **Шаг 5: коммит**

```bash
git add listam/matching.py tests/test_matching.py
git commit -m "fix(match): кластеры считаются каждым подбором — ушедший член меняет размер"
```

### Конец фазы 2

- [x] Дописать раздел «Результат фазы 2»: числа батареи, сколько кластеров
      дал новый допуск против старого (посчитай на синтетической базе или на
      фикстурах), что разошлось с планом и почему.
- [x] Дописать стартовый промпт для фазы 3.
- [x] `git status --short` — чисто; коммит сделан.

## Результат фазы 2

**Сделано.** Кластер получил устойчивое имя и конечную ширину. **H-1** и
**H-3** закрыты.

| Что | Значение |
| --- | --- |
| HEAD | `03a6302`, дерево чистое |
| Батарея | **622 passed, 18 skipped** (44,6 с) |
| Схема базы | **8** (фаза схему не трогала) |
| Коммитов | 3 (`e1ca8e2`, `7f0eb6e`, `03a6302`) |

**Что именно изменилось:**

- `_groups` мерит цепочку от её первого члена, а не от предыдущего соседа.
  Ширина кластера по площади теперь не превышает допуск.
- `_cluster_id(key, anchor)` вместо `_cluster_id(key, low, high)`; новый
  `_anchor(listing_ids)` — самое старое объявление кластера, порядок по
  `(len(id), id)`, потому что идентификаторы list.am — числа строками.
  Приход соседа кластер больше не переименовывает.
- `_count_clusters_if_needed` → `_count_clusters`: пересчёт перед каждым
  подбором, а не только когда в базе есть строка без `cluster_id`.

**Сколько кластеров дал новый допуск против старого.**
Замер на синтетике (`scratchpad/count_clusters.py`, сид 20260922), допуск
2 м², тот же масштаб, что у аудита:

| База | Старое (от соседа) | Новое (от начала) |
| --- | --- | --- |
| разреженная, 20 826 объявлений | 20 490 кластеров, крупнейший 4, ширина до 3,5 м² | 20 498 кластеров, крупнейший 3, ширина до **2,0 м²** |
| плотная, 20 965 объявлений | 8 855 кластеров, крупнейший 10, ширина до 9,5 м² | 10 791 кластера, крупнейший 7, ширина до **2,0 м²** |

На разреженной базе разница восемь кластеров: цепочки там почти не
складываются. На плотной — **+1 936 кластеров (+21,9 %)**, и это ровно то
число квартир, которые клиент раньше не видел вовсе: они стояли в чужом
кластере, а показывается из кластера один. Ширина в обоих замерах ровно
2,0 м² — потолок держится.

Полный пересчёт кластеров на 20 826 объявлениях — **0,080 с**. Это цена
безусловного пересчёта из задачи 2.3; на фоне минуты, которую `match --all`
стоит по **H-5**, она не видна.

**Что разошлось с планом.**

- **Задача 2.1, ожидаемое `[1, 1, 2]` — арифметическая описка плана.**
  Код, который план сам же и предписывает (мерка от `chain[0]`), на входе
  60/62/64/66 при допуске 2 даёт пары 60+62 и 64+66, то есть `[2, 2]`.
  Допуск включающий — это уже закреплено тестом
  `test_areas_two_metres_apart_join_and_three_metres_apart_do_not` (85 и 87
  сходятся), — так что пара шириной ровно 2 м² законна. Решение фазы
  («ширина не превышает допуск») выполняется и там и там; в тесте стоит
  `[2, 2]`. Порогов при этом никто не трогал.
- **`test_a_chain_of_close_areas_stays_one_cluster` переписан.** Тест
  утверждал, что 85—87—89 при допуске 2 — один кластер, то есть кодировал
  ровно ту транзитивную склейку, которую фаза убирает. По правилу «тест
  состава поехал — это находка» разобрался: находка в том, что поведение
  было закреплено тестом как желаемое. Теперь
  `test_a_chain_of_close_areas_breaks_where_the_tolerance_ends`: 85 и 87
  вместе, 89 отдельно.
- **Задача 2.3: посылка плана на HEAD неверна, тест написан другой.**
  План ждал, что у матча останется вчерашний `cluster_size`. Это не так:
  `run_match` считает `clusters(everything, ...)` заново каждым прогоном
  (`listam/matching.py:204`) и кладёт в матч свежие `cluster_id`,
  `cluster_size`, `cluster_spread_usd`. Тест из плана на HEAD **зеленеет
  сразу** — я это проверил, прежде чем править код.
  Устаревает другое — колонка `listings.cluster_id`: её пишет только
  `cluster_database`, а его звали лишь при наличии строки без кластера.
  После ухода объявления с ленты кластер меняет состав и (с задачей 2.2)
  имя, колонок не обнуляя, — и база начинает спорить со снимком в матче.
  Это тот же **H-1** с другой стороны, поэтому тест написан на расхождение:
  `test_a_cluster_that_lost_a_member_is_the_same_in_the_base_and_in_the_match`
  — падал (`76191133b62713fa` против `7d8e502fb1877801`), после правки
  зелёный.
- **`test_clusters_are_counted_before_matching_if_the_base_has_none`**
  переименован в `test_clusters_are_counted_before_every_matching`, как
  велит план, и дополнен вторым прогоном: в `report.notes` обязана быть
  строка «кластеры пересчитаны».
- **Числа батареи: 622, а не ~626.** Арифметика: 615 + 3 (задача 2.1)
  + 3 (задача 2.2) + 1 (задача 2.3) = 622. План закладывал на 2.3 ещё и
  сохранённый старый тест; он не добавлен, а переписан на месте.
- **Хелпер в `tests/test_clustering.py` зовётся `make_listing`**
  (импортируется из `tests/contracts/test_database_contract.py`), а не
  `listing` — взял тот, что в файле, как план и велит.
- **Строчку про «0,1 с на двадцати тысячах» в докстроке `_count_clusters`
  не оставлял**: замер дал 0,080 с, и держать в коде число, которое никто
  не перепроверяет, незачем. Число записано здесь, в отчёте.

**Остаточный риск (не находка, а свойство решения).** Якорь — самое старое
объявление кластера. Когда уходит с ленты **сам якорь**, кластер
переименовывается: имя переходит к следующему по старшинству. Это на
порядок реже прежнего (сосед приходил в кластер постоянно, якорь уходит
один раз за его жизнь) и, главное, теперь заметно — безусловный пересчёт
задачи 2.3 приводит `listings.cluster_id` и снимок в матче к согласию тем
же прогоном. Полностью развязать имя от состава можно было бы только
отдельной таблицей кластеров; схему фаза 2 не трогает, и в план это не
входило.

**Чего фаза не трогала.** Схему (осталась 8), пороги конфига,
`matches.status` и `matches.reject_reason`, `listam/crawler.py`, чужие
фазы. Портов не добавлялось — новых контрактных тестов нет.

## Стартовый промпт для фазы 3

```
Ты продолжаешь работу над инструментом мониторинга list.am
в C:\Users\Artur.A.Gevorgyan\Downloads\new_project.

Прочитай docs/superpowers/plans/2026-09-22-qa-hardening-after-m2.md:
разделы «Что нашёл аудит», «Global Constraints», «Карта файлов»,
«Результат фазы 1», «Результат фазы 2» и свою «Фазу 3». Чужие фазы
не трогай. Рядом лежат план и спека M2 — из них читаются «Принятые
решения»: docs/superpowers/plans/2026-09-22-m2-requests-and-matching.md,
docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md.
Решения 1–11 в фазах не пересматриваются; если фаза одно из них уточняет,
это прямо написано в её заголовке.

Исходное состояние: HEAD 03a6302, дерево чистое, батарея 622 passed,
18 skipped, схема базы 8.

Твоя задача — фаза 3: выборка, которая видит подешевевшее.
Сегодня `--new` мерится одним first_seen (H-2). Квартира, которая вчера
стоила 130 000 $, а сегодня 118 000 $ и наконец влезла в бюджет, новой
не считается и в выборку не попадает никогда — ночной сценарий
`scrape && match --new` систематически теряет главное событие рынка.
Правленая заявка («бюджет вырос на десять тысяч») по той же причине ждёт
полного ночного пересчёта: выборка мерится объявлениями и про заявку
не знает ничего.

Работай по шагам задач: на каждое поведение — падающий тест ДО правки.
Прежде чем править код, убедись, что тест из плана действительно падает
на твоём HEAD: в фазе 2 один такой зеленел сразу, и посылка плана
оказалась неверна (см. «Результат фазы 2»). Тесты гоняй только
.venv/Scripts/python.exe -m pytest -q, любой прогон CLI из скрипта —
только с PYTHONIOENCODING=utf-8. Пороги в конфиге не поднимай.
Новый метод порта — это новый контрактный тест в tests/contracts/.
Схему меняет только миграция 009 (твоя, 8 → 9); своих сверх неё
не заводи. След звонка (matches.status, matches.reject_reason)
не трогает ничто.

В конце сессии допиши в план раздел «Результат фазы 3»: что сделано, числа
батареи, версию схемы, что разошлось с планом и почему, и стартовый промпт
для фазы 4. Сделай коммит.
```

---

# Фаза 3. Выборка, которая видит подешевевшее

**Одна сессия.** Закрывает **H-2**. `--new` перестаёт мериться одним
`first_seen`: подешевевшая квартира, вернувшееся объявление и правленая заявка
попадают в подбор без полного пересчёта.

**Ожидается после фазы:** ~640 passed, 18 skipped, схема базы 9.

### Задача 3.1. Миграция 009: когда заявку подбирали в последний раз

**Файлы:**
- Создать: `listam/migrations/009_match_marks.sql`
- Тест: `tests/test_migrations.py`

- [x] **Шаг 1: падающий тест**

```python
# tests/test_migrations.py — дописать
def test_migration_009_remembers_when_a_request_was_matched(tmp_path):
    db = SqliteDatabase(tmp_path / "m9.sqlite")
    db.connect()
    db.migrate()
    assert db.schema_version() == 9
    columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(requests)")}
    assert "matched_at" in columns
    db.close()
```

- [x] **Шаг 2: убедиться, что тест падает**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_migrations.py -k 009`
Ожидается: FAIL — `assert 8 == 9`.

- [x] **Шаг 3: миграция**

```sql
-- Версия 9: заявка помнит, когда её подбирали.
--
-- requests.matched_at — отметка последнего полного прохода по этой заявке.
-- Без неё правленая заявка («бюджет вырос на десять тысяч») ждала бы ночного
-- --all, чтобы увидеть то, что подходит ей уже сейчас: выборка --new мерится
-- объявлениями и про изменившуюся заявку не знает ничего.

ALTER TABLE requests ADD COLUMN matched_at TEXT;
```

- [x] **Шаг 4: тест проходит, батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: ~627 passed, 18 skipped.

- [x] **Шаг 5: коммит**

```bash
git add listam/migrations/009_match_marks.sql tests/test_migrations.py
git commit -m "feat(db): миграция 009 — заявка помнит свой последний подбор"
```

### Задача 3.2. Выборка «что тронулось с отметки»

**Файлы:**
- Изменить: `listam/ports/database.py`, `listam/adapters/db_sqlite.py`
- Тест: `tests/contracts/test_database_contract.py`

**Interfaces — Produces:**
```python
Database.listings_touched_since(since: datetime) -> list[Listing]
```

- [x] **Шаг 1: контрактные тесты**

```python
# tests/contracts/test_database_contract.py — дописать
def test_touched_since_finds_the_newcomer(db):
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_listing(make_listing("L-2"), LATER)
    found = {item.id for item in db.listings_touched_since(LATER)}
    assert found == {"L-2"}


def test_touched_since_finds_the_one_that_changed_its_price(db):
    db.upsert_listing(make_listing("L-1", price_usd=130_000.0,
                                   price_raw="130000 $", currency="USD"), NOW)
    db.upsert_listing(make_listing("L-1", price_usd=118_000.0,
                                   price_raw="118000 $", currency="USD"), LATER)
    found = {item.id for item in db.listings_touched_since(LATER)}
    assert found == {"L-1"}, (
        "квартира, которая наконец влезла в бюджет, — главное событие рынка "
        "и не имеет права ждать ночного полного пересчёта"
    )


def test_touched_since_finds_the_one_that_came_back(db):
    db.upsert_listing(make_listing("L-1"), NOW)
    db.mark_gone(["L-1"], NOW)
    db.upsert_listing(make_listing("L-1"), LATER)
    assert {item.id for item in db.listings_touched_since(LATER)} == {"L-1"}


def test_touched_since_skips_the_untouched(db):
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_listing(make_listing("L-1"), LATER)     # та же карточка, ничего не менялось
    assert db.listings_touched_since(LATER) == []


def test_touched_since_keeps_the_rules_of_the_matching_selection(db):
    db.upsert_listing(make_listing("L-1"), LATER)
    db.upsert_listing(make_listing("L-2"), LATER)
    db.set_computed("L-2", anomaly="цена", price_amount=1.0)
    assert {item.id for item in db.listings_touched_since(LATER)} == {"L-1"}
```

- [x] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py -k touched`
Ожидается: FAIL — метода нет.

- [x] **Шаг 3: метод в порте**

```python
# listam/ports/database.py
    @abstractmethod
    def listings_touched_since(self, since: datetime) -> list["Listing"]:
        """Активные без аномалии, которых с отметки что-то коснулось.

        Коснулось — это появилось, сменило цену или вернулось на ленту.
        Мерить одним `first_seen`, как `listings_for_matching(since=...)`,
        мало: подешевевшая квартира новой не становится, а подбор её ждёт.
        """
```

- [x] **Шаг 4: реализация**

```python
# listam/adapters/db_sqlite.py — рядом с listings_for_matching
    def listings_touched_since(self, since: datetime) -> list[Listing]:
        """См. порт. Правила выборки те же, что у `listings_for_matching`."""
        stamp = to_iso(since)
        rows = self.conn.execute(
            "SELECT * FROM listings "
            " WHERE status = 'active' AND (anomaly IS NULL OR anomaly = '') "
            "   AND (first_seen >= :since "
            "        OR returned_at >= :since "
            "        OR id IN (SELECT listing_id FROM price_history "
            "                   WHERE seen_at >= :since)) "
            " ORDER BY first_seen DESC, id DESC",
            {"since": stamp},
        )
        return [_row_to_listing(row) for row in rows]
```

Точка истории цен пишется и при первой встрече объявления, поэтому новое
объявление попадает в выборку дважды — по `first_seen` и по истории. `OR`
это схлопывает, дублей строк не будет.

- [x] **Шаг 5: тесты проходят**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py`
Ожидается: все зелёные.

- [x] **Шаг 6: коммит**

```bash
git add listam/ports/database.py listam/adapters/db_sqlite.py tests/contracts/test_database_contract.py
git commit -m "feat(db): выборка «что тронулось» — новое, подешевевшее и вернувшееся"
```

### Задача 3.3. `--new` берёт тронувшееся, а правленая заявка — всю базу

**Файлы:**
- Изменить: `listam/matching.py:119-129` (`_listings_scope`),
  `listam/matching.py:99-116` (`_requests_to_match`), `listam/matching.py` (`run_match`)
- Тест: `tests/test_matching.py`

- [x] **Шаг 1: падающие тесты**

```python
# tests/test_matching.py — дописать
def test_new_sees_the_one_that_got_cheaper(matching_config_with_two_runs):
    config = matching_config_with_two_runs
    database = SqliteDatabase(database_path(config))
    database.connect()
    listing = database.get_listing("L-1")
    database.upsert_listing(
        replace(listing, price_usd=(listing.price_usd or 0) / 2,
                price_raw="подешевело"),
        datetime(2026, 9, 23, tzinfo=timezone.utc),
    )
    database.close()

    report = run_match(config, only_new=True)

    assert report.listings >= 1
    assert "L-1" in [listing.id for _, _, listing in collect_matches(config)]


def test_a_request_edited_after_its_last_matching_is_swept_whole(matching_config):
    config = matching_config
    run_match(config)

    database = SqliteDatabase(database_path(config))
    database.connect()
    request = next(iter(database.iter_requests()))
    database.upsert_request(
        replace(request, budget_max=(request.budget_max or 0) * 3),
        datetime(2026, 9, 23, tzinfo=timezone.utc),
    )
    database.close()

    report = run_match(config, only_new=True)

    assert report.requests >= 1
    assert "правленых заявок" in (report.notes or ""), (
        "заявка, которую тронули после подбора, идёт по всей базе, "
        "а не по выборке последнего прогона"
    )
```

- [x] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_matching.py -k "got_cheaper or edited_after"`
Ожидается: FAIL — подешевевшее в выборку не попало.

- [x] **Шаг 3: выборка по «тронулось»**

```python
def _listings_scope(database: Database, only_new: bool, config: Config
                    ) -> tuple[list, str]:
    """Выборка объявлений и как она называется по-человечески."""
    if not only_new:
        return database.listings_for_matching(), "вся база"
    # Мерка та же, что у `changes`: начало последнего прогона. Но берём не
    # «появившееся с неё», а «тронувшееся с неё»: подешевевшая квартира новой
    # не стала, а звонить по ней надо сегодня.
    mark, note = since_point(
        database, None, fallback_hours=config.get("changes.fallback_hours", 24)
    )
    return (database.listings_touched_since(mark),
            f"новое и подешевевшее: {note}")
```

- [x] **Шаг 4: правленая заявка идёт по всей базе**

В `run_match`, сразу после того как получены `requests` и посчитаны кластеры,
разделить заявки на две группы. Вставить перед вызовом `_write_matches`:

```python
        selection, scope = _listings_scope(database, only_new, config)
        report.scope = f"заявка {external_id}" if external_id else scope
        report.listings = len(selection)
        candidates = [item for item in selection if item.id in representatives]
        report.considered = len(candidates)

        last_run = database.last_run()
        run_id = last_run.id if last_run else None
        tuning = settings(config)

        # Заявка, которую тронули после её последнего подбора, выборкой
        # объявлений не покрывается: изменился не рынок, а условия. Такую
        # ведём по всей базе — иначе поднятый бюджет заработает только ночью.
        edited = [request for request in requests
                  if only_new and _is_edited(request)]
        fresh = [request for request in requests if request not in edited]
        if edited:
            notes.append(f"правленых заявок: {len(edited)} — по всей базе")
            whole = [item for item in everything if item.id in representatives]
            _write_matches(database, report, edited, whole, representatives,
                           medians, run_id, tuning, full_sweep=True)
        if fresh:
            _write_matches(database, report, fresh, candidates, representatives,
                           medians, run_id, tuning, full_sweep=not only_new)
        database.mark_requests_matched([r.id for r in requests],
                                       datetime.now(timezone.utc))
```

и рядом — предикат:

```python
def _is_edited(request: Request) -> bool:
    """Заявку тронули после её последнего подбора?

    Ни разу не подбиравшаяся заявка — тоже «правленая»: по выборке последнего
    прогона она увидит три вчерашних объявления вместо всей базы.
    """
    if request.matched_at is None:
        return True
    return (request.updated_at or request.created_at or request.matched_at) \
        > request.matched_at
```

- [x] **Шаг 5: поле и метод отметки**

В `listam/domain/models.py`, в `Request`, после `updated_at`:

```python
    matched_at: datetime | None = None      # когда по ней последний раз шёл полный подбор
```

В `listam/ports/database.py`:

```python
    @abstractmethod
    def mark_requests_matched(self, request_ids: list[int], now: datetime) -> None:
        """Отмечает, что по этим заявкам только что шёл подбор."""
```

В `listam/adapters/db_sqlite.py`, раздел заявок:

```python
    def mark_requests_matched(self, request_ids: list[int], now: datetime) -> None:
        """См. порт. Отметка не содержательная правка — `updated_at` не трогаем."""
        if not request_ids:
            return
        stamp = to_iso(now)
        with self.transaction():
            self.conn.executemany(
                "UPDATE requests SET matched_at = ? WHERE id = ?",
                [(stamp, int(request_id)) for request_id in request_ids],
            )
```

и в `_row_to_request` — `data["matched_at"] = from_iso(data.get("matched_at"))`.

Контрактный тест на новый метод (новый метод порта — новый контрактный тест):

```python
# tests/contracts/test_database_contract.py — дописать
def test_marking_a_request_matched_is_not_an_edit_of_it(db):
    db.upsert_request(make_request(), NOW)
    request = db.get_request("R-1")
    db.mark_requests_matched([request.id], LATER)
    stored = db.get_request("R-1")
    assert stored.matched_at == LATER
    assert stored.updated_at == NOW, "подбор заявку не правит"
    assert db.upsert_request(make_request(), LATER) == "unchanged"
```

- [x] **Шаг 6: тесты проходят, батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: ~640 passed, 18 skipped. Тест
`test_new_only_looks_at_listings_from_the_last_run` поменял смысл —
перепиши его под «новое и тронувшееся» и переименуй.

- [x] **Шаг 7: коммит**

```bash
git add listam/matching.py listam/domain/models.py listam/ports/database.py listam/adapters/db_sqlite.py tests/
git commit -m "fix(match): --new видит подешевевшее, правленая заявка идёт по всей базе"
```

### Конец фазы 3

- [x] Дописать раздел «Результат фазы 3»: числа батареи, версия схемы,
      сколько объявлений даёт `listings_touched_since` против
      `listings_for_matching(since=...)` на фикстурах, что разошлось.
- [x] Дописать стартовый промпт для фазы 4.
- [x] `git status --short` — чисто; коммит сделан.

## Результат фазы 3

**Сделано.** `--new` перестал мериться одним `first_seen`. **H-2** закрыт.

| Что | Значение |
| --- | --- |
| HEAD | `f248cd0`, дерево чистое |
| Батарея | **631 passed, 18 skipped** (38,2 с) |
| Схема базы | **9** |
| Коммитов | 3 (`b4a04f7`, `89eab6c`, `f248cd0`) |

**Что именно изменилось:**

- Миграция `009_match_marks.sql`: `requests.matched_at`.
- Порт: `Database.listings_touched_since(since)` и
  `Database.mark_requests_matched(request_ids, now)`. Оба закрыты
  контрактными тестами (шесть новых в `tests/contracts/`).
- `listings_touched_since` мерит три события вместо одного: `first_seen`,
  точку в `price_history` и `returned_at`. Правила выборки те же, что у
  `listings_for_matching`: активные и без аномалии.
- `_listings_scope` при `only_new` берёт «тронувшееся с отметки»; подпись
  выборки стала «новое и подешевевшее: …».
- `run_match` делит заявки на правленые и свежие. Правленая (или ни разу
  не подбиравшаяся) идёт по всей базе полным проходом, свежая — по выборке.
  В конце `mark_requests_matched` ставит отметку по всем заявкам прохода.
- `Request.matched_at` в модели и в `_row_to_request`; в `REQUEST_FIELDS`
  поле нарочно не заведено — отметка не содержательная правка, и `upsert`
  её не сравнивает и не пишет.

**Сколько даёт новая выборка против старой.**
Фикстура того же вида, что в `tests/test_matching.py`
(`scratchpad/touched_vs_new.py`): 1 040 активных объявлений, отметка —
начало последнего прогона.

| Выборка | Объявлений |
| --- | --- |
| вся база | 1 040 |
| `first_seen >= отметки` (как было) | 40 |
| тронулось с отметки (как стало) | **75** |

Разница — 25 подешевевших и 10 вернувшихся на ленту: **+87,5 %** к выборке
ночного `scrape && match --new`, и это ровно те квартиры, по которым надо
звонить сегодня. На боевых числах доля будет другой, замерить её на этой
машине нечем: `data/listam.sqlite` здесь пустой файл (боевая база у
заказчика, `data/` в `.gitignore`).

**Что разошлось с планом.**

- **Числа батареи по задачам другие, итог — 631, а не ~640.** Арифметика:
  622 + 1 (миграция) + 5 (`touched_since`) + 1 (контракт на отметку)
  + 2 (матчинг) = 631. План считал от 604 — от состояния до фазы 1,
  а не от фактических 622 после фазы 2; ожидания «~627 после задачи 3.1»
  и «~640 после 3.3» на эту базу не ложатся. Тестов добавлено ровно
  столько, сколько план и перечисляет, плюс переписан один существующий.
- **`test_migration_008_adds_the_end_of_a_match_life` правлен:** `== 8`
  стало `>= 8` с тем же комментарием, что у 007. Ожидаемое следствие новой
  миграции, а не находка.
- **Тест `test_new_sees_the_one_that_got_cheaper` написан не по буквам
  плана — иначе он зеленел бы не по той причине.** В плане он идёт от
  фикстуры `matching_config_with_two_runs` и правит объявление `L-1`
  (в фикстуре такого нет — там `old-1`, `old-2`, `new-1`, `new-2`), а
  главное: заявка в этой фикстуре ни разу не подбиралась, то есть по
  предикату `_is_edited` она **правленая** и идёт по всей базе. Матч
  нашёлся бы и без единой правки `_listings_scope` — тест не проверял бы
  ничего. В моём варианте: объявление за 200 000 $ (в бюджет не влезает),
  полный проход `run_match(config)` — матчей ноль, отметка заявке
  проставлена, — затем цена падает до 118 000 $, и только после этого
  `--new`. Заявка при этом не правленая, так что попадание в витрину
  зависит ровно от выборки. На HEAD падал как надо: `listings == 0`.
- **`test_new_only_looks_at_listings_from_the_last_run` переписан и
  переименован** в `test_new_takes_everything_touched_since_the_last_run`,
  как велит план. На новом коде он зеленел бы и без правки (выборка
  «нового» и «тронувшегося» на той фикстуре совпадают), поэтому в него
  добавлено подешевевшее вчерашнее объявление: теперь выборка — 3, а не 2,
  и нетронутое вчерашнее в неё по-прежнему не входит.
- **Мелочи по тексту плана:** база в тестах берётся через
  `build_database(config)` (он уже импортирован в файле), а не через
  `SqliteDatabase(database_path(config))` — два лишних импорта ради одного
  и того же объекта. В `mark_requests_matched` передаются только заявки
  с непустым `id`.

**Чего фаза не трогала.** След звонка (`matches.status`,
`matches.reject_reason`), пороги конфига, `listam/crawler.py`, чужие фазы.
`listings_for_matching(since=...)` оставлен как есть: им пользуется не
только подбор, и менять смысл существующего метода фаза не просила.

**Остаточный риск (свойство решения, не находка).** Отметка
`matched_at` ставится по всем заявкам прохода, включая прогон `--new`,
где свежие заявки шли по выборке, а не по всей базе. Это и есть смысл
поля — «когда по заявке последний раз шёл подбор», — но название колонки
в миграции говорит «полный проход». Если фазе 6 или 7 понадобится
«последний **полный** проход» (например, чтобы раз в сутки прогонять
заявку по всей базе), отметку придётся разделить надвое.

## Стартовый промпт для фазы 4

```
Ты продолжаешь работу над инструментом мониторинга list.am
в C:\Users\Artur.A.Gevorgyan\Downloads\new_project.

Прочитай docs/superpowers/plans/2026-09-22-qa-hardening-after-m2.md:
разделы «Что нашёл аудит», «Global Constraints», «Карта файлов»,
«Результат фазы 3» и свою «Фазу 4». Чужие фазы не трогай.
Рядом лежат план и спека M2 — из них читаются «Принятые решения»:
docs/superpowers/plans/2026-09-22-m2-requests-and-matching.md,
docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md.
Решения 1–11 в фазах не пересматриваются; если фаза одно из них уточняет,
это прямо написано в её заголовке.

Исходное состояние: HEAD f248cd0, дерево чистое, батарея 631 passed,
18 skipped, схема базы 9. Заявка теперь помнит свой последний подбор
(requests.matched_at), выборка --new берёт тронувшееся, а не появившееся.

Твоя задача — фаза 4: источник заявок как двусторонний договор.
Закрывает B-3, H-4, M-5, M-6, L-4, L-5. Исчезновение строки в источнике —
такое же событие, как появление: заявка, которую брокер удалил из таблицы,
сегодня матчится вечно. Разбор перестаёт принимать бессмысленные числа
(отрицательный бюджет, rooms: 0) и молча терять вторую строку с тем же id,
а gsheet начинает отклонять ровно то, что отклоняет csv (решение 1 спеки:
одна строка — одна заявка, каким бы адаптером её ни читали).

Работай по шагам задач: на каждое поведение — падающий тест ДО правки.
Прежде чем править код, убедись, что тест из плана действительно падает
на твоём HEAD и падает по той причине, которую проверяет: в фазе 2 один
такой зеленел сразу, в фазе 3 другой зеленел бы по чужой ветке кода
(см. «Результат фазы 3»). Тесты гоняй только
.venv/Scripts/python.exe -m pytest -q, любой прогон CLI из скрипта —
только с PYTHONIOENCODING=utf-8. Пороги в конфиге не поднимай.
Новый метод порта — это новый контрактный тест в tests/contracts/.
Схему меняют только миграции 008 (фаза 1) и 009 (фаза 3); своих не заводи —
фаза 4 идёт на схеме 9. След звонка (matches.status, matches.reject_reason)
не трогает ничто.

Числа «ожидается N passed» в плане считались от 604 — от состояния до
фазы 1. Сверяйся с фактическими 631 и правь число в плане, а не подгоняй
тесты.

В конце сессии допиши в план раздел «Результат фазы 4»: что сделано, числа
батареи, версию схемы, что разошлось с планом и почему, и стартовый промпт
для фазы 5. Сделай коммит.
```

---

# Фаза 4. Источник заявок как двусторонний договор

**Одна сессия.** Закрывает **B-3**, **H-4**, **M-5**, **M-6**, **L-4**, **L-5**.
Источник перестаёт быть односторонним: исчезновение строки — такое же событие,
как появление. Разбор перестаёт принимать бессмысленные числа, а `gsheet`
начинает отклонять то же, что отклоняет `csv`.

**Ожидается после фазы:** ~660 passed, 18 skipped, схема базы 9.

### Задача 4.1. Разбор отклоняет бессмысленное

**Файлы:**
- Изменить: `listam/domain/requests.py:73-129`
- Тест: `tests/test_requests.py`

- [ ] **Шаг 1: падающие тесты**

```python
# tests/test_requests.py — дописать
def test_a_negative_budget_is_a_refusal():
    with pytest.raises(RequestParseError) as exc:
        parse_row(row(budget_max="-5000"))
    assert exc.value.column == "budget_max"


def test_a_negative_area_is_a_refusal():
    with pytest.raises(RequestParseError) as exc:
        parse_row(row(area_min="-10"))
    assert exc.value.column == "area_min"


def test_a_floor_below_the_ground_is_a_refusal():
    with pytest.raises(RequestParseError) as exc:
        parse_row(row(floor_min="-3"))
    assert exc.value.column == "floor_min"


def test_a_flat_with_no_rooms_is_a_refusal():
    with pytest.raises(RequestParseError) as exc:
        parse_row(row(rooms="0"))
    assert exc.value.column == "rooms"


def test_zero_budget_is_a_refusal_and_not_read_as_no_budget():
    with pytest.raises(RequestParseError) as exc:
        parse_row(row(budget_max="0"))
    assert exc.value.column == "budget_max"


def test_two_rows_with_one_id_are_a_refusal_naming_the_double():
    parsed, errors = parse_rows([row(id="R-5", budget_max="100000"),
                                 row(id="R-5", budget_max="999999")])
    assert [item.external_id for item in parsed] == ["R-5"]
    assert parsed[0].budget_max == 100_000
    assert len(errors) == 1
    assert errors[0].column == "id"
    assert "R-5" in errors[0].render()
```

- [ ] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_requests.py -k "negative or ground or no_rooms or zero_budget or one_id"`
Ожидается: FAIL — `DID NOT RAISE`.

- [ ] **Шаг 3: числа обязаны быть больше нуля**

```python
# listam/domain/requests.py — заменить _number и _integer
def _number(row: dict, column: str, minimum: float | None = None) -> float | None:
    """Число из человеческой записи. `minimum` — ниже чего значение бессмысленно.

    Отрицательный бюджет не «очень маленький бюджет», а опечатка: `stretch`
    от −5 000 даёт −5 500, каждое объявление получает отказ «бюджет»,
    и брокер видит пустую витрину без единого объяснения.
    """
    raw = _text(row, column)
    if not raw:
        return None
    cleaned = DECORATION.sub("", raw)
    if GROUPED.fullmatch(cleaned):
        value = float(re.sub(r"[.,]", "", cleaned))
    elif FRACTIONAL.fullmatch(cleaned):
        value = float(cleaned.replace(",", "."))
    elif PLAIN.fullmatch(cleaned):
        value = float(cleaned)
    else:
        raise RequestParseError(column, raw, "не число")
    if minimum is not None and value < minimum:
        raise RequestParseError(column, raw, f"должно быть не меньше {minimum:g}")
    return value


def _integer(row: dict, column: str, minimum: float | None = None) -> int | None:
    value = _number(row, column, minimum=minimum)
    if value is None:
        return None
    if value != int(value):
        raise RequestParseError(column, _text(row, column), "не целое число")
    return int(value)
```

В `_rooms` — нулю комнат отказ:

```python
        if not part.isdigit():
            raise RequestParseError("rooms", part, "не число комнат")
        if int(part) < 1:
            raise RequestParseError("rooms", part, "квартир без комнат не бывает")
        values.add(int(part))
```

и в диапазоне, сразу после разбора границ:

```python
            if low < 1:
                raise RequestParseError("rooms", part, "квартир без комнат не бывает")
```

В `parse_row` — пороги на местах вызова:

```python
    budget_max = _number(row, "budget_max", minimum=1)
    budget_stretch = _number(row, "budget_stretch", minimum=1)
    ...
    area_min = _number(row, "area_min", minimum=1)
    area_max = _number(row, "area_max", minimum=1)
    ...
    floor_min = _integer(row, "floor_min", minimum=1)
    floor_max = _integer(row, "floor_max", minimum=1)
```

- [ ] **Шаг 4: дубль `id` — отказ, а не молчаливая замена**

```python
def parse_rows(rows: Iterable[dict]) -> tuple[list[Request], list[RequestError]]:
    """Разобранные заявки и отклонённые строки. Одна опечатка не стоит остальных.

    Второй `id` — отказ второй строке, а не замена первой: какая из двух
    настоящая, знает человек, и молча взять последнюю значит однажды подобрать
    не тому клиенту.
    """
    parsed: list[Request] = []
    errors: list[RequestError] = []
    seen: set[str] = set()
    for number, row in enumerate(rows, start=1):
        try:
            request = parse_row(row, row_number=number)
            if request.external_id in seen:
                raise RequestParseError(
                    "id", request.external_id,
                    "такой идентификатор в таблице уже был — две строки на одну заявку",
                )
            seen.add(request.external_id)
            parsed.append(request)
        except RequestParseError as exc:
            errors.append(RequestError(
                row_number=number,
                external_id=str(row.get("id") or "").strip() or None,
                column=exc.column, value=exc.value, message=exc.message,
            ))
    return parsed, errors
```

- [ ] **Шаг 5: тесты проходят, батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: ~647 passed, 18 skipped. Если поехал тест на образцовый CSV
(`config/requests.example.csv`) — проверь, что в образце нет нулей и минусов;
образец правится, находка записывается.

- [ ] **Шаг 6: коммит**

```bash
git add listam/domain/requests.py tests/test_requests.py
git commit -m "fix(requests): отрицательный бюджет, ноль комнат и дубль id — отказы"
```

### Задача 4.2. `gsheet` отклоняет то же, что и `csv`

**Файлы:**
- Изменить: `listam/adapters/requests_gsheet.py:40-58`
- Тест: `tests/test_requests_source.py` (создать)

- [ ] **Шаг 1: падающий тест**

```python
# tests/test_requests_source.py
"""Решение 1 спеки: csv и gsheet дают одну и ту же заявку из одной и той же строки."""
from __future__ import annotations

from listam.adapters.requests_gsheet import GSheetRequestsSource
from listam.domain.requests import parse_rows


class FakeSheet(GSheetRequestsSource):
    """Тот же разбор значений, но без сети: ответ Sheets кладётся руками."""

    def __init__(self, values):
        super().__init__(sheet_id="fake")
        self.values = values

    def _service(self):                      # pragma: no cover — сеть не нужна
        raise AssertionError("тест не ходит в сеть")

    def rows(self):
        return self._rows_from(self.values)


HEADER = ["id", "budget_max", "notes"]


def test_a_short_row_is_padded_and_read():
    source = FakeSheet([HEADER, ["R-1", "100000"]])
    assert source.rows() == [{"id": "R-1", "budget_max": "100000", "notes": ""}]


def test_a_row_longer_than_the_header_is_not_silently_trimmed():
    source = FakeSheet([HEADER, ["R-1", "100000", "заметка", "хвост"]])
    parsed, errors = parse_rows(source.rows())
    assert parsed == []
    assert len(errors) == 1
    assert "больше, чем колонок" in errors[0].message, (
        "csv такую строку отклоняет; gsheet обязан вести себя так же, "
        "иначе контрактный тест источника ничего не гарантирует"
    )


def test_an_empty_row_is_skipped_and_not_a_refusal():
    source = FakeSheet([HEADER, ["", "", ""], ["R-1", "100000", ""]])
    assert [row["id"] for row in source.rows()] == ["R-1"]
```

- [ ] **Шаг 2: убедиться, что тест падает**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_requests_source.py`
Ожидается: FAIL — у `GSheetRequestsSource` нет `_rows_from`.

- [ ] **Шаг 3: сборка строк отдельным методом, лишнее — под ключ `None`**

```python
# listam/adapters/requests_gsheet.py
    def _rows_from(self, values: list[list]) -> list[dict]:
        """Ответ Sheets в строки словарями. Разбор значений — в домене.

        Значений в строке больше, чем колонок в шапке, — строка разъехалась,
        и читать её нельзя. `csv.DictReader` кладёт остаток под ключ `None`,
        и домен на этот ключ уже умеет отказывать (решение 2). Повторяем его
        договор ровно, а не «почти»: иначе одна и та же строка в двух
        источниках даст две разные заявки, и решение 1 перестанет что-либо
        значить.
        """
        if not values:
            return []
        header = [str(name).strip() for name in values[0]]
        rows: list[dict] = []
        for raw in values[1:]:
            cells = [str(cell) for cell in raw]
            if not any(cell.strip() for cell in cells):
                continue
            # Пустые хвостовые ячейки Sheets не присылает вовсе: короткую
            # строку дополняем пустыми, иначе колонки разъедутся на первой же
            # заявке без заметки.
            padded = cells + [""] * (len(header) - len(cells))
            row = dict(zip(header, padded))
            if len(cells) > len(header):
                row[None] = cells[len(header):]
            rows.append(row)
        return rows

    def rows(self) -> list[dict]:
        service = self._service()
        values = service.spreadsheets().values().get(
            spreadsheetId=self.sheet_id, range=self.range_name
        ).execute().get("values", [])
        return self._rows_from(values)
```

- [ ] **Шаг 4: предупреждение про упёршийся диапазон**

В `describe` — назвать диапазон, чтобы упёршееся чтение было видно человеку:

```python
    def describe(self) -> str:
        return f"Google Sheet: {self.sheet_id} (диапазон {self.range_name})"
```

и тест на это:

```python
def test_describe_names_the_range_so_a_full_sheet_is_visible():
    assert "A1:Z1000" in GSheetRequestsSource(sheet_id="fake").describe()
```

- [ ] **Шаг 5: тесты проходят, батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: ~651 passed, 18 skipped.

- [ ] **Шаг 6: коммит**

```bash
git add listam/adapters/requests_gsheet.py tests/test_requests_source.py
git commit -m "fix(requests): gsheet отклоняет разъехавшуюся строку, как и csv"
```

### Задача 4.3. Заявка, исчезнувшая из источника, закрывается

**Файлы:**
- Изменить: `listam/ports/database.py`, `listam/adapters/db_sqlite.py`,
  `listam/requests_sync.py:57-158`
- Тест: `tests/contracts/test_database_contract.py`, `tests/test_requests_sync.py`

**Interfaces — Produces:**
```python
Database.close_requests_missing_from(external_ids: set[str], now: datetime) -> int
```

- [ ] **Шаг 1: контрактный тест**

```python
# tests/contracts/test_database_contract.py — дописать
def test_a_request_gone_from_the_source_is_closed(db):
    db.upsert_request(make_request("R-1"), NOW)
    db.upsert_request(make_request("R-2"), NOW)

    closed = db.close_requests_missing_from({"R-1"}, LATER)

    assert closed == 1
    assert [item.external_id for item in db.iter_requests()] == ["R-1"]
    gone = db.get_request("R-2")
    assert gone.status == "closed"
    assert gone.updated_at == LATER


def test_closing_twice_closes_nothing_the_second_time(db):
    db.upsert_request(make_request("R-1"), NOW)
    assert db.close_requests_missing_from(set(), LATER) == 1
    assert db.close_requests_missing_from(set(), LATER) == 0


def test_a_request_the_human_paused_is_not_closed_by_absence(db):
    db.upsert_request(make_request("R-1", status="paused"), NOW)
    assert db.close_requests_missing_from(set(), LATER) == 0, (
        "закрываются только активные: приостановленную заявку человек "
        "мог убрать из таблицы нарочно, и её статус — его решение"
    )
```

- [ ] **Шаг 2: убедиться, что тест падает**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py -k missing_from`
Ожидается: FAIL — метода нет.

- [ ] **Шаг 3: порт и реализация**

```python
# listam/ports/database.py
    @abstractmethod
    def close_requests_missing_from(self, external_ids: set[str],
                                    now: datetime) -> int:
        """Закрывает активные заявки, которых нет в этом списке. Отдаёт, сколько закрыл.

        Брокер удалил строку из таблицы — значит клиент ушёл. Удалять заявку
        нельзя: на ней висят матчи и след звонков. Трогаются только активные:
        `paused` и `closed` — решение человека, а не источника.
        """
```

```python
# listam/adapters/db_sqlite.py — в разделе заявок
    def close_requests_missing_from(self, external_ids: set[str],
                                    now: datetime) -> int:
        """См. порт."""
        alive = [
            row["external_id"] for row in self.conn.execute(
                "SELECT external_id FROM requests WHERE status = 'active'"
            ) if row["external_id"] not in external_ids
        ]
        if not alive:
            return 0
        stamp = to_iso(now)
        with self.transaction():
            self.conn.executemany(
                "UPDATE requests SET status = 'closed', updated_at = ? "
                " WHERE external_id = ?",
                [(stamp, external_id) for external_id in alive],
            )
        return len(alive)
```

- [ ] **Шаг 4: падающие тесты на команду**

```python
# tests/test_requests_sync.py — дописать
def test_a_row_deleted_from_the_table_closes_the_request(tmp_path, requests_config):
    config, csv_path = requests_config
    write_requests(csv_path, [request_row("R-1"), request_row("R-2")])
    run_requests_sync(config)

    write_requests(csv_path, [request_row("R-1")])
    report = run_requests_sync(config)

    assert report.closed == 1
    assert "R-2" in report.render()
    database = SqliteDatabase(database_path(config))
    database.connect()
    assert [item.external_id for item in database.iter_requests()] == ["R-1"]
    database.close()


def test_an_empty_table_closes_nothing_and_says_why(tmp_path, requests_config):
    config, csv_path = requests_config
    write_requests(csv_path, [request_row("R-1")])
    run_requests_sync(config)

    write_requests(csv_path, [])
    report = run_requests_sync(config)

    assert report.closed == 0
    assert "ни одной заявки" in (report.notes or ""), (
        "пустая таблица — это чаще сбой доступа, чем «все клиенты ушли»; "
        "закрывать по ней всю базу заявок нельзя"
    )
```

Хелперы `requests_config`, `write_requests`, `request_row` в этом файле уже
есть — используй их; если названия другие, возьми те, что в файле.

- [ ] **Шаг 5: закрытие в команде**

В `SyncReport` — счётчик и строка отчёта:

```python
    closed: int = 0             # заявок закрыто: строки в источнике больше нет
    closed_ids: list[str] = field(default_factory=list)
```

```python
        lines.append(
            f"Заявки: новых {self.new}, обновлённых {self.updated}, "
            f"без изменений {self.unchanged}, закрытых {self.closed}"
        )
        if self.closed_ids:
            lines.append(f"  закрыты (нет в источнике): {', '.join(self.closed_ids)}")
```

В `run_requests_sync`, сразу после цикла `upsert_request`:

```python
        # Пустой источник — почти всегда сбой доступа, а не «все клиенты ушли».
        # Закрыть по нему всю базу заявок означало бы потерять работу месяца
        # из-за одной недоступной таблицы.
        if parsed:
            present = {request.external_id for request in parsed}
            before = {item.external_id for item in database.iter_requests()}
            report.closed = database.close_requests_missing_from(present, now)
            report.closed_ids = sorted(before - present)
        else:
            notes.append("источник не отдал ни одной заявки — "
                         "ничего не закрываем, это похоже на сбой доступа")
```

И заливка теперь нужна и после одного закрытия:

```python
        if report.new or report.updated or report.closed:
```

- [ ] **Шаг 6: тесты проходят, батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: ~660 passed, 18 skipped.

- [ ] **Шаг 7: коммит**

```bash
git add listam/ports/database.py listam/adapters/db_sqlite.py listam/requests_sync.py tests/
git commit -m "fix(requests): удалённая из таблицы заявка закрывается, а не живёт вечно"
```

### Конец фазы 4

- [ ] Дописать раздел «Результат фазы 4»: числа батареи, что разошлось,
      и отдельно — что стало с `config/requests.example.csv`.
- [ ] Дописать стартовый промпт для фазы 5.
- [ ] `git status --short` — чисто; коммит сделан.

---

# Фаза 5. Конфиг, который не врёт

**Одна сессия.** Закрывает **M-1**, **M-2**, **M-3**, **M-7**, **M-8**, **M-9**.
Правило «бессмысленный ввод отклоняется на входе» распространяется с командной
строки на конфиг: опечатка в имени веса и нулевой потолок витрины перестают
быть молчаливыми.

**Ожидается после фазы:** ~678 passed, 18 skipped, схема базы 9.

### Задача 5.1. Неизвестный вес — отказ, а не молчание

**Файлы:**
- Изменить: `listam/matching.py:83-96` (`settings`), `listam/doctor.py:134-183`
- Тест: `tests/test_matching.py`, `tests/test_doctor.py`

- [ ] **Шаг 1: падающие тесты**

```python
# tests/test_matching.py — дописать
def test_a_misspelled_weight_is_refused_and_not_dropped_from_the_score(tmp_path):
    config = make_config(tmp_path, match={"weights": {"budjet": 30, "district": 20}})
    with pytest.raises(ConfigError) as exc:
        settings(config)
    assert "budjet" in str(exc.value)
    assert "budget" in str(exc.value), "отказ обязан назвать, как правильно"


def test_a_missing_weight_is_refused_too(tmp_path):
    config = make_config(tmp_path, match={"weights": {"budget": 30}})
    with pytest.raises(ConfigError) as exc:
        settings(config)
    assert "district" in str(exc.value)


def test_a_weight_of_zero_is_a_weight_and_not_an_absence(tmp_path):
    config = make_config(tmp_path, match={"weights": dict(
        DEFAULT_WEIGHTS, seller_type=0)})
    assert settings(config).weights["seller_type"] == 0
```

`make_config` — хелпер этого файла; если его нет, напиши по образцу фикстуры
`matching_config`, принимая секции словарём.

- [ ] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_matching.py -k weight`
Ожидается: FAIL — `DID NOT RAISE`.

- [ ] **Шаг 3: проверка имён в `settings`**

```python
def settings(config: Config) -> Settings:
    """Читает секцию `match`. Ноль значит ноль, `null` — выключено.

    Имена факторов проверяются на входе. Опечатка `budjet` вместо `budget`
    стоит фактору веса 30 и не видна ничем: балл считается, пишется в базу
    и выглядит правдоподобно — просто он другой. Молчать про это нельзя,
    как нельзя молчать про `--limit 0`.
    """
    weights = config.get("match.weights", None)
    if weights:
        known = set(DEFAULT_WEIGHTS)
        unknown = sorted(set(weights) - known)
        missing = sorted(known - set(weights))
        if unknown or missing:
            trouble = []
            if unknown:
                trouble.append(f"таких факторов нет: {', '.join(unknown)}")
            if missing:
                trouble.append(f"не названы: {', '.join(missing)}")
            raise ConfigError(
                f"match.weights — {'; '.join(trouble)}. "
                f"Факторы балла: {', '.join(sorted(known))}. "
                f"Вес 0 выключает фактор; убирать его из списка нельзя — "
                f"молча выпавший фактор меняет балл и не виден ничем."
            )
    stretch = threshold(config, "match.budget_stretch_percent", DEFAULT_STRETCH_PERCENT)
    hot = threshold(config, "match.thresholds.hot", DEFAULT_HOT)
    digest = threshold(config, "match.thresholds.digest", DEFAULT_DIGEST)
    return Settings(
        weights=dict(weights) if weights else dict(DEFAULT_WEIGHTS),
        stretch_percent=0.0 if stretch is None else float(stretch),
        hot=None if hot is None else float(hot),
        digest=None if digest is None else float(digest),
    )
```

Импорт `ConfigError` из `listam.config` добавить в начало `matching.py`.

- [ ] **Шаг 4: `doctor` считает это сбоем, а не предупреждением**

В `listam/doctor.py`, в `match_check`, перенести проверку неизвестных весов
из `warn` в `harm` и добавить пропущенные:

```python
    unknown = sorted(set(weights) - set(DEFAULT_WEIGHTS))
    missing = sorted(set(DEFAULT_WEIGHTS) - set(weights))
    if unknown:
        harm.append(
            f"таких факторов нет, и их вес в балл не войдёт: {', '.join(unknown)}"
        )
    if missing:
        harm.append(
            f"фактор не назван и молча выпадет из балла: {', '.join(missing)}. "
            f"Чтобы выключить — ставь вес 0"
        )
```

Тест в `tests/test_doctor.py`:

```python
def test_doctor_calls_a_misspelled_weight_a_failure_and_not_a_warning(tmp_path):
    config = doctor_config(tmp_path, match={"weights": {"budjet": 30}})
    check = match_check(config)
    assert check.ok is False
    assert "budjet" in check.details
```

- [ ] **Шаг 5: тесты проходят, батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: ~666 passed, 18 skipped. Проверь, что `config/dev.yaml` и
`config/prod.yaml` содержат все шесть факторов — иначе после этой правки
`match` в обоих окружениях откажется стартовать.

- [ ] **Шаг 6: коммит**

```bash
git add listam/matching.py listam/doctor.py tests/
git commit -m "fix(config): опечатка в имени веса — отказ на входе, а не тихий другой балл"
```

### Задача 5.2. Бессмысленный порог в конфиге отклоняется

**Файлы:**
- Изменить: `listam/config.py`, `listam/matching.py:325-328` (`display_limit`)
- Тест: `tests/test_config.py`, `tests/test_matching.py`

**Interfaces — Produces:**
```python
# listam/config.py
def positive(config: Config, key: str, default: Any) -> Any
```

- [ ] **Шаг 1: падающие тесты**

```python
# tests/test_config.py — дописать
def test_a_positive_threshold_refuses_zero(tmp_path):
    config = write_config(tmp_path, {"match": {"limit": 0}})
    with pytest.raises(ConfigError) as exc:
        positive(config, "match.limit", 50)
    assert "match.limit" in str(exc.value)


def test_a_positive_threshold_refuses_a_negative_number(tmp_path):
    config = write_config(tmp_path, {"match": {"limit": -5}})
    with pytest.raises(ConfigError):
        positive(config, "match.limit", 50)


def test_a_positive_threshold_lets_null_through_as_off(tmp_path):
    config = write_config(tmp_path, {"match": {"limit": None}})
    assert positive(config, "match.limit", 50) is None


def test_a_missing_key_gets_the_default(tmp_path):
    config = write_config(tmp_path, {})
    assert positive(config, "match.limit", 50) == 50
```

```python
# tests/test_matching.py — дописать
def test_a_zero_display_limit_is_refused_by_name(tmp_path):
    config = make_config(tmp_path, match={"limit": 0})
    with pytest.raises(ConfigError) as exc:
        display_limit(config)
    assert "match.limit" in str(exc.value)
```

- [ ] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_config.py -k positive`
Ожидается: FAIL — `ImportError: cannot import name 'positive'`.

- [ ] **Шаг 3: `positive` в конфиге**

```python
# listam/config.py — после threshold
def positive(config: Config, key: str, default: Any) -> Any:
    """Порог, который обязан быть больше нуля. `null` по-прежнему «выключено».

    Правило командной строки («--limit 0 не годится: меньше одной строки
    показывать нечего») ровно так же верно для конфига. Ноль, пришедший
    из yaml, до сих пор давал витрину из одной строки «…и ещё 30» — то есть
    молча прятал весь ответ. Отклонять такое нужно там же, где читают.
    """
    value = threshold(config, key, default)
    if value is None:
        return None
    number = float(value)
    if number <= 0:
        raise ConfigError(
            f"{key} = {value} не годится: это счётчик, и меньше единицы он "
            f"ничего не показывает. Чтобы снять ограничение, ставят null."
        )
    return value
```

- [ ] **Шаг 4: `display_limit` читает через `positive`**

```python
def display_limit(config: Config) -> int:
    """Сколько строк показывает витрина. Порог из конфига, а не число в коде."""
    value = positive(config, "match.limit", DEFAULT_LIMIT)
    return DEFAULT_LIMIT if value is None else int(value)
```

Импорт: `from listam.config import Config, ConfigError, positive, threshold`.

Так же перевести на `positive` чтение `changes.limit` в `listam/cli.py:323`:

```python
    if limit is None:
        limit = positive(config, "changes.limit", 50) or 50
```

и обернуть команду так, чтобы `ConfigError` давала код 2, а не трейсбек —
в `main`, вокруг ветки команды:

```python
    try:
        return _dispatch(args, config)
    except ConfigError as exc:
        print(f"Конфигурация не годится: {exc}", file=sys.stderr)
        return 2
```

где `_dispatch` — вынесенное тело разбора команд из `main` (перенос без
изменения логики).

- [ ] **Шаг 5: тесты проходят, батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: ~672 passed, 18 skipped.

- [ ] **Шаг 6: коммит**

```bash
git add listam/config.py listam/matching.py listam/cli.py tests/
git commit -m "fix(config): ноль в счётчике отклоняется так же, как в командной строке"
```

### Задача 5.3. `doctor` смотрит рабочую базу, а не пробник

**Файлы:**
- Изменить: `listam/doctor.py:236-252`
- Тест: `tests/test_doctor.py`

- [ ] **Шаг 1: падающий тест**

```python
# tests/test_doctor.py — дописать
def test_doctor_names_the_version_of_the_working_database(tmp_path):
    config = doctor_config(tmp_path)
    database = SqliteDatabase(database_path(config))
    database.connect()
    database.migrate()
    database.conn.execute("DELETE FROM schema_version WHERE version >= 5")
    database.conn.commit()
    database.close()

    report = run_doctor(config, check_network=False)
    schema = next(check for check in report.checks if "Схема" in check.name)

    assert schema.ok is False
    assert "4" in schema.details, (
        "версия рабочего файла, а не временного пробника: команда откажется "
        "работать именно с ним"
    )


def test_doctor_does_not_complain_when_there_is_no_working_database_yet(tmp_path):
    config = doctor_config(tmp_path)
    report = run_doctor(config, check_network=False)
    schema = next(check for check in report.checks if "Схема" in check.name)
    assert schema.ok is True
    assert "ещё нет" in schema.details
```

- [ ] **Шаг 2: убедиться, что тест падает**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_doctor.py -k working_database`
Ожидается: FAIL — `assert True is False`.

- [ ] **Шаг 3: проверка рабочего файла**

Заменить блок «схема базы» в `run_doctor` целиком:

```python
    # --- схема базы ---------------------------------------------------
    # Пробник во временной папке отвечает на вопрос «накатываются ли миграции
    # этим кодом», и это нужно. Но команда работает не с ним: рабочая база
    # может стоять на версии 4, и тогда `match`, `export` и `matches` откажутся
    # работать, а doctor до этой правки отвечал «OK, версия 7».
    try:
        from listam.adapters.db_sqlite import SqliteDatabase, latest_schema_version

        required = latest_schema_version()
        with tempfile.TemporaryDirectory() as tmp:
            probe = SqliteDatabase(Path(tmp) / "probe.sqlite")
            probe.connect()
            probe.migrate()
            tables = sorted(probe.table_names() - {"schema_version", "sqlite_sequence"})
            probe.close()

        working = database_path(config)
        if not working.exists():
            report.add(
                "Схема базы", True,
                f"код ждёт версию {required}, таблицы: {', '.join(tables)}; "
                f"рабочего файла {working} ещё нет — он появится первым прогоном",
            )
        else:
            live = SqliteDatabase(working)
            live.connect()
            version = live.schema_version()
            live.close()
            report.add(
                "Схема базы", version >= required,
                f"рабочий файл {working}: версия {version}, код ждёт {required}"
                + ("" if version >= required
                   else " — накати миграции: python -m listam recheck")
                + f"; таблицы: {', '.join(tables)}",
            )
    except Exception as exc:
        report.add("Схема базы", False, str(exc))
```

- [ ] **Шаг 4: тесты проходят**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_doctor.py`
Ожидается: все зелёные.

- [ ] **Шаг 5: коммит**

```bash
git add listam/doctor.py tests/test_doctor.py
git commit -m "fix(doctor): проверяется схема рабочей базы, а не временного пробника"
```

### Задача 5.4. След звонка не принимает выдуманных слов, заливка не роняет отчёт

**Файлы:**
- Изменить: `listam/adapters/db_sqlite.py:605-611` (`set_match_status`),
  `listam/requests_sync.py:110`, `listam/matching.py:278-303` (`_upload`)
- Тест: `tests/contracts/test_database_contract.py`, `tests/test_matching.py`,
  `tests/test_requests_sync.py`

- [ ] **Шаг 1: падающие тесты**

```python
# tests/contracts/test_database_contract.py — дописать
def test_a_match_status_outside_the_list_is_refused(db):
    db.upsert_request(make_request(), NOW)
    request = db.get_request("R-1")
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="L-1", score=80.0), NOW)
    stored = db.matches_for_request(request.id)[0]
    with pytest.raises(ValueError) as exc:
        db.set_match_status(stored.id, "ПОЖАЛУЙ НЕТ")
    assert "new" in str(exc.value), "отказ обязан перечислить, какие статусы бывают"
```

```python
# tests/test_matching.py — дописать
def test_a_storage_that_refuses_the_upload_is_a_note_and_not_a_crash(
        matching_config, monkeypatch):
    import listam.matching as matching_module

    class Refusing:
        def download(self, *args, **kwargs):
            raise FileNotFoundError("в хранилище копии нет")

        def upload(self, *args, **kwargs):
            raise OSError("хранилище недоступно")

        def list_files(self, *args, **kwargs):
            return []

    monkeypatch.setattr(matching_module, "build_storage", lambda config: Refusing())
    report = run_match(matching_config)
    assert report.errors >= 1
    assert "хранилище недоступно" in (report.notes or "")
```

```python
# tests/test_requests_sync.py — дописать
def test_a_broken_database_is_a_message_and_not_a_traceback(tmp_path, requests_config):
    config, csv_path = requests_config
    write_requests(csv_path, [request_row("R-1")])
    path = database_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a database at all")
    report = run_requests_sync(config)
    assert report.errors == 1
    assert "база" in (report.notes or "").lower()
```

- [ ] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/ -k "outside_the_list or refuses_the_upload or broken_database_is_a_message"`
Ожидается: три FAIL — `DID NOT RAISE`, `OSError`, `sqlite3.DatabaseError`.

- [ ] **Шаг 3: статусы матча — закрытый список**

```python
# listam/adapters/db_sqlite.py — рядом с MATCH_FIELDS
# След звонка: что человек может сказать про матч. Список закрыт — выдуманное
# слово лежало бы в базе и выходило в выгрузку как есть, а витрина переводит
# на русский только то, что знает.
MATCH_STATUSES = ("new", "sent", "called", "rejected")
```

```python
    def set_match_status(self, match_id: int, status: str,
                         reject_reason: str | None = None) -> None:
        if status not in MATCH_STATUSES:
            raise ValueError(
                f"Статус матча {status!r} не из списка. "
                f"Бывают: {', '.join(MATCH_STATUSES)}"
            )
        with self.transaction():
            self.conn.execute(
                "UPDATE matches SET status = ?, reject_reason = ? WHERE id = ?",
                (status, reject_reason, int(match_id)),
            )
```

- [ ] **Шаг 4: заливка под `try`, `requests` ловит то же, что соседи**

```python
# listam/matching.py — _upload, вторая половина
    database.close()
    try:
        rotate_backups(
            storage,
            remote_name,
            int(config.get("storage.keep_backups", DEFAULT_KEEP_BACKUPS) or 0),
            Path(local_db).parent,
        )
        storage.upload(snapshot, remote_name)
    except Exception as exc:       # OSError, ошибки Google API — сеть отказала
        # База уже записана и закрыта: заливка — это про копию в хранилище,
        # и её провал не имеет права съесть отчёт о проделанной работе.
        report.errors += 1
        notes.append(f"база не залита в хранилище: {exc}")
        snapshot.unlink(missing_ok=True)
        return True
    snapshot.unlink(missing_ok=True)
    notes.append("база с матчами залита в хранилище")
    return True
```

```python
# listam/requests_sync.py:110 — было `except OSError as exc:`
        except Exception as exc:       # OSError, sqlite3.Error — базы нет
```

- [ ] **Шаг 5: тесты проходят, батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: ~678 passed, 18 skipped.

- [ ] **Шаг 6: коммит**

```bash
git add listam/adapters/db_sqlite.py listam/matching.py listam/requests_sync.py tests/
git commit -m "fix: статус матча из списка, отказ хранилища — строка отчёта, а не трейсбек"
```

### Конец фазы 5

- [ ] Дописать раздел «Результат фазы 5»: числа батареи, что пришлось править
      в `config/dev.yaml` и `config/prod.yaml`, что разошлось с планом.
- [ ] Дописать стартовый промпт для фазы 6.
- [ ] `git status --short` — чисто; коммит сделан.

---

# Фаза 6. Масштаб

**Одна сессия.** Закрывает **H-5**, **M-4**, **M-10**. Фаза целиком про числа:
каждый шаг начинается с замера и им же заканчивается.

**Ожидается после фазы:** ~688 passed, 18 skipped, схема базы 9.

### Задача 6.1. Замер до правок

**Файлы:**
- Создать: `tests/test_matching_scale.py`

- [ ] **Шаг 1: тест-замер, который ловит лишние чтения**

```python
# tests/test_matching_scale.py
"""Масштаб подбора. Тест не про скорость, а про число обращений к базе:
секунды на разных машинах разные, а четыре чтения одной таблицы — везде четыре."""
from __future__ import annotations

from listam.adapters.db_sqlite import SqliteDatabase
from listam.matching import run_match


def test_one_matching_reads_the_listings_table_once(matching_config, monkeypatch):
    calls: list[str] = []
    original = SqliteDatabase.listings_for_matching

    def counted(self, since=None):
        calls.append("since" if since is not None else "вся")
        return original(self, since)

    monkeypatch.setattr(SqliteDatabase, "listings_for_matching", counted)
    run_match(matching_config)

    assert len(calls) <= 1, (
        f"вся таблица объявлений прочитана {len(calls)} раза: {calls}. "
        f"На боевых 20 826 строках каждое чтение — больше секунды"
    )
```

- [ ] **Шаг 2: убедиться, что тест падает**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_matching_scale.py`
Ожидается: FAIL — `вся таблица объявлений прочитана 4 раза`.

- [ ] **Шаг 3: одно чтение на прогон**

`cluster_database` получает уже прочитанную выборку, `run_match` читает базу
один раз и передаёт её всем, кому нужно.

```python
# listam/clustering_run.py
def cluster_database(database: Database, tolerance: float,
                     listings: list | None = None) -> ClusterReport:
    """Пересчёт по уже открытой базе: им пользуется и команда, и матчинг.

    `listings` — уже прочитанная выборка. Подбор читает таблицу один раз и
    отдаёт её сюда: на 20 826 строках каждое лишнее чтение стоит больше
    секунды, а за прогон их набиралось четыре.
    """
    report = ClusterReport()
    listings = database.listings_for_matching() if listings is None else listings
    report.listings = len(listings)
    ...
```

```python
# listam/matching.py — в run_match, вместо трёх отдельных чтений
        # Вся таблица читается один раз за прогон. Кластеры, медианы и выборка
        # считаются по ней, а не каждый по своему чтению.
        everything = database.listings_for_matching()
        _count_clusters(database, config, notes, everything)
        found = clusters(everything, area_tolerance(config))
        representatives = {cluster.cheapest_id: cluster for cluster in found}
        medians = median_price_per_sqm_by_district(everything)
```

```python
def _count_clusters(database: Database, config: Config, notes: list[str],
                    listings: list) -> None:
    """Кластеры перед подбором — по уже прочитанной выборке. См. фазу 2."""
    counted = cluster_database(database, area_tolerance(config), listings=listings)
    notes.append(
        f"кластеры пересчитаны: объявлений {counted.listings}, "
        f"кластеров {counted.clusters}, изменено строк {counted.changed}"
    )
```

И `_listings_scope` при `only_new=False` больше не читает заново:

```python
def _listings_scope(database: Database, only_new: bool, config: Config,
                    everything: list) -> tuple[list, str]:
    """Выборка объявлений и как она называется по-человечески."""
    if not only_new:
        return everything, "вся база"
    mark, note = since_point(
        database, None, fallback_hours=config.get("changes.fallback_hours", 24)
    )
    return (database.listings_touched_since(mark),
            f"новое и подешевевшее: {note}")
```

Вызов: `selection, scope = _listings_scope(database, only_new, config, everything)`.

- [ ] **Шаг 4: тест проходит, батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: ~679 passed, 18 skipped.

- [ ] **Шаг 5: коммит**

```bash
git add listam/matching.py listam/clustering_run.py tests/test_matching_scale.py
git commit -m "perf(match): таблица объявлений читается один раз за прогон"
```

### Задача 6.2. Матчи пишутся пачкой

**Файлы:**
- Изменить: `listam/ports/database.py`, `listam/adapters/db_sqlite.py`,
  `listam/matching.py` (`_write_matches`)
- Тест: `tests/contracts/test_database_contract.py`, `tests/test_matching_scale.py`

**Interfaces — Produces:**
```python
Database.upsert_matches(matches: list[Match], now: datetime) -> dict[str, int]
# {"new": N, "updated": M, "unchanged": K}
```

- [ ] **Шаг 1: контрактный тест и тест-замер**

```python
# tests/contracts/test_database_contract.py — дописать
def test_a_batch_of_matches_gives_the_same_counts_as_one_by_one(db):
    db.upsert_request(make_request(), NOW)
    request = db.get_request("R-1")
    for number in range(3):
        db.upsert_listing(make_listing(f"L-{number}"), NOW)
    batch = [Match(request_id=request.id, listing_id=f"L-{number}",
                   score=float(70 + number)) for number in range(3)]

    assert db.upsert_matches(batch, NOW) == {"new": 3, "updated": 0, "unchanged": 0}
    assert db.upsert_matches(batch, LATER) == {"new": 0, "updated": 0, "unchanged": 3}

    batch[0].score = 99.0
    assert db.upsert_matches(batch, LATER) == {"new": 0, "updated": 1, "unchanged": 2}


def test_a_batch_does_not_touch_the_call_trace(db):
    db.upsert_request(make_request(), NOW)
    request = db.get_request("R-1")
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_matches([Match(request_id=request.id, listing_id="L-1", score=70.0)], NOW)
    stored = db.matches_for_request(request.id)[0]
    db.set_match_status(stored.id, "called", "дорого")

    db.upsert_matches([Match(request_id=request.id, listing_id="L-1", score=90.0)], LATER)

    again = db.matches_for_request(request.id)[0]
    assert again.score == 90.0
    assert again.status == "called"
    assert again.reject_reason == "дорого"
```

```python
# tests/test_matching_scale.py — дописать
def test_matches_are_written_in_batches_and_not_one_transaction_each(
        matching_config, monkeypatch):
    begins: list[str] = []
    import sqlite3
    original = sqlite3.Connection.execute

    def watched(self, sql, *args, **kwargs):
        if isinstance(sql, str) and sql.strip().upper().startswith("BEGIN"):
            begins.append(sql)
        return original(self, sql, *args, **kwargs)

    monkeypatch.setattr(sqlite3.Connection, "execute", watched)
    report = run_match(matching_config)

    assert report.new > 1, "тест бессмыслен, если матч один"
    assert len(begins) < report.new, (
        f"{len(begins)} транзакций на {report.new} матчей: на боевых 67 000 "
        f"строках это минута записи вместо секунд"
    )
```

- [ ] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/ -k "batch_of_matches or written_in_batches"`
Ожидается: FAIL — метода нет; транзакций столько же, сколько матчей.

- [ ] **Шаг 3: порт и реализация пачки**

```python
# listam/ports/database.py
    @abstractmethod
    def upsert_matches(self, matches: list["Match"], now: datetime) -> dict[str, int]:
        """Пачка матчей одной транзакцией. Отдаёт счётчики new/updated/unchanged.

        Правила те же, что у `upsert_match`: след звонка не трогается,
        `matched_at` не двигается у неизменившихся, закрытие гасится
        подтверждением. Отличие одно — граница транзакции: на боевых числах
        одна транзакция на строку стоит минуту на прогон.
        """
```

```python
# listam/adapters/db_sqlite.py — в разделе матчей
    def upsert_matches(self, matches: list[Match], now: datetime) -> dict[str, int]:
        """См. порт. Читает существующие одним запросом, пишет одной транзакцией."""
        counts = {"new": 0, "updated": 0, "unchanged": 0}
        if not matches:
            return counts

        request_ids = {match.request_id for match in matches}
        existing: dict[tuple, sqlite3.Row] = {}
        for request_id in request_ids:
            for row in self.conn.execute(
                "SELECT * FROM matches WHERE request_id = ?", (request_id,)
            ):
                existing[(row["request_id"], row["listing_id"])] = row

        stamp = to_iso(now)
        inserts: list[dict] = []
        updates: list[dict] = []
        for match in matches:
            values = {name: _match_value(match, name) for name in MATCH_FIELDS}
            was = existing.get((match.request_id, match.listing_id))
            if was is None:
                values.update(
                    request_id=match.request_id, listing_id=match.listing_id,
                    status=match.status or "new", reject_reason=match.reject_reason,
                    first_matched_at=stamp, matched_at=stamp,
                )
                inserts.append(values)
                counts["new"] += 1
                continue
            same = all(_normalize(values[name]) == _normalize(was[name])
                       for name in MATCH_COMPARED)
            if same and was["retired_at"] is None:
                counts["unchanged"] += 1
                continue
            values.update(matched_at=stamp, retired_at=None, retired_reason=None,
                          id=was["id"])
            updates.append(values)
            counts["updated"] += 1

        with self.transaction():
            if inserts:
                columns = ", ".join(inserts[0])
                placeholders = ", ".join(f":{name}" for name in inserts[0])
                self.conn.executemany(
                    f"INSERT INTO matches ({columns}) VALUES ({placeholders})", inserts
                )
            if updates:
                assignments = ", ".join(
                    f"{name} = :{name}" for name in updates[0] if name != "id"
                )
                self.conn.executemany(
                    f"UPDATE matches SET {assignments} WHERE id = :id", updates
                )
        return counts
```

`upsert_match` остаётся как есть: он читается в тестах и в контракте, и
переписывать его через пачку значило бы объяснять одно через другое.

- [ ] **Шаг 4: подбор копит пачку**

```python
# listam/matching.py — в _write_matches, вместо вызова upsert_match в цикле
    now = datetime.now(timezone.utc)
    for request in requests:
        confirmed: set[str] = set()
        batch: list[Match] = []
        for listing in candidates:
            result = score(request, listing, median_by_district=medians,
                           weights=tuning.weights,
                           stretch_percent=tuning.stretch_percent)
            if result.rejected_by is not None:
                continue
            cluster = representatives[listing.id]
            batch.append(Match(
                request_id=request.id, listing_id=listing.id,
                score=float(result.value), run_id=run_id,
                breakdown=result.breakdown or None,
                cluster_id=cluster.cluster_id, cluster_size=cluster.size,
                cluster_spread_usd=cluster.spread_usd,
            ))
            confirmed.add(listing.id)
            if tuning.hot is not None and result.value >= tuning.hot:
                report.hot += 1
            elif tuning.digest is not None and result.value >= tuning.digest:
                report.digest += 1
        for outcome, count in database.upsert_matches(batch, now).items():
            setattr(report, outcome, getattr(report, outcome) + count)
        if full_sweep:
            report.retired += database.retire_matches(
                request.id, keep=confirmed, now=now,
                reason="проход больше не подтверждает этот вариант",
            )
```

- [ ] **Шаг 5: тесты проходят, батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: ~684 passed, 18 skipped.

- [ ] **Шаг 6: замер на синтетической базе**

Повтори замер аудита и запиши числа «до/после» в отчёт фазы. Скрипт:

```python
# положи во временную папку, в репозиторий не коммить
import random, time, tempfile
from datetime import datetime, timezone
from pathlib import Path
from listam.adapters.db_sqlite import SqliteDatabase, to_iso
from listam.domain.models import Request

random.seed(7)
NOW = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)
DISTRICTS = ["Кентрон", "Арабкир", "Давташен", "Ачапняк", "Малатия",
             "Эребуни", "Норк", "Шенгавит", "Канакер", "Аван"]
STREETS = [f"улица {i}" for i in range(400)]
db = SqliteDatabase(Path(tempfile.mkdtemp()) / "big.sqlite")
db.connect(); db.migrate()
rows = []
for i in range(20826):
    area = round(random.uniform(30, 160), 1)
    price = round(random.uniform(40000, 400000), -2)
    rows.append((f"L{i}", f"https://list.am/{i}", f"кв {i}",
                 random.choice(DISTRICTS),
                 random.choice(STREETS) if random.random() < 0.8 else None,
                 f"{price}$", "USD", price, price, round(price / area, 2), area,
                 random.choice([1, 2, 3, 4, 5]), random.randint(1, 12),
                 random.randint(3, 16), random.choice(["owner", "agency"]),
                 "active" if i % 100 else "gone", to_iso(NOW), to_iso(NOW)))
db.conn.execute("BEGIN")
db.conn.executemany(
    "INSERT INTO listings (id,url,title,district,street,price_raw,currency,"
    "price_amount,price_usd,price_per_sqm,area,rooms,floor,floors_total,"
    "seller_type,status,first_seen,last_seen) VALUES (" + ",".join("?" * 18) + ")",
    rows)
db.conn.execute("COMMIT")
for n in range(50):
    db.upsert_request(Request(
        external_id=f"R-{n}", status="active",
        budget_max=random.choice([90000, 120000, 150000, 200000, 300000]),
        districts=random.sample(DISTRICTS, k=random.randint(1, 4)),
        rooms=[2, 3], area_min=50.0, area_max=120.0, no_first_floor=True), NOW)
print("база готова:", db.count_listings(), "объявлений")
db.close()
```

Дальше собери конфиг на эту базу (по образцу фикстуры `matching_config`),
замерь `run_match(config)` целиком и запиши в отчёт: секунды, число матчей,
число транзакций. Эталон аудита — **~58 с только на записи**.

- [ ] **Шаг 7: коммит**

```bash
git add listam/ports/database.py listam/adapters/db_sqlite.py listam/matching.py tests/
git commit -m "perf(match): матчи пишутся пачкой — одна транзакция вместо 67 000"
```

### Задача 6.3. Витрина читает объявления одним запросом

**Файлы:**
- Изменить: `listam/ports/database.py`, `listam/adapters/db_sqlite.py`,
  `listam/matching.py:331-392` (`collect_matches`),
  `listam/cli.py:328-362` (`_export`), `config/dev.yaml`, `config/prod.yaml`
- Тест: `tests/contracts/test_database_contract.py`, `tests/test_matching_scale.py`

**Interfaces — Produces:**
```python
Database.matches_with_listings(request_id: int, min_score: float | None = None,
                               limit: int | None = None) -> list[tuple[Match, Listing]]
```

- [ ] **Шаг 1: контрактный тест и тест-замер**

```python
# tests/contracts/test_database_contract.py — дописать
def test_matches_come_with_their_listings_in_one_go(db):
    db.upsert_request(make_request(), NOW)
    request = db.get_request("R-1")
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="L-1", score=80.0), NOW)

    rows = db.matches_with_listings(request.id)

    assert len(rows) == 1
    match, listing = rows[0]
    assert match.listing_id == "L-1"
    assert listing.district == "Кентрон"


def test_a_gone_listing_still_comes_with_its_match(db):
    db.upsert_request(make_request(), NOW)
    request = db.get_request("R-1")
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="L-1", score=80.0), NOW)
    db.mark_gone(["L-1"], LATER)

    rows = db.matches_with_listings(request.id)

    assert [listing.status for _, listing in rows] == ["gone"], (
        "решение 8: снятое витрина помечает, а не прячет"
    )
```

```python
# tests/test_matching_scale.py — дописать
def test_the_window_does_not_read_listings_one_by_one(matching_config, monkeypatch):
    run_match(matching_config)
    from listam.matching import collect_matches

    calls: list[str] = []
    original = SqliteDatabase.get_listing

    def counted(self, listing_id):
        calls.append(listing_id)
        return original(self, listing_id)

    monkeypatch.setattr(SqliteDatabase, "get_listing", counted)
    rows = collect_matches(matching_config)

    assert len(rows) > 1, "тест бессмыслен на одной строке"
    assert calls == [], (
        f"{len(calls)} отдельных get_listing на {len(rows)} матчей: "
        f"на боевых числах это 66 910 запросов"
    )
```

- [ ] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/ -k "in_one_go or one_by_one"`
Ожидается: FAIL — метода нет; `get_listing` позван по разу на матч.

- [ ] **Шаг 3: порт и реализация**

```python
# listam/ports/database.py
    @abstractmethod
    def matches_with_listings(self, request_id: int, min_score: float | None = None,
                              limit: int | None = None
                              ) -> list[tuple["Match", "Listing"]]:
        """Живые матчи заявки вместе с объявлениями, одним запросом.

        Снятое объявление приходит с пометкой, а не выпадает (решение 8):
        «мы звонили по этой квартире» не исчезает вместе с карточкой.
        """
```

```python
# listam/adapters/db_sqlite.py — в разделе матчей
    def matches_with_listings(self, request_id: int, min_score: float | None = None,
                              limit: int | None = None
                              ) -> list[tuple[Match, Listing]]:
        """См. порт. INNER JOIN: матч без объявления показывать нечем."""
        match_columns = [row["name"] for row in
                         self.conn.execute("PRAGMA table_info(matches)")]
        listing_columns = [row["name"] for row in
                           self.conn.execute("PRAGMA table_info(listings)")]
        select = ", ".join(
            [f"m.{name} AS m_{name}" for name in match_columns]
            + [f"l.{name} AS l_{name}" for name in listing_columns]
        )
        query = (f"SELECT {select} FROM matches m "
                 f" JOIN listings l ON l.id = m.listing_id "
                 f" WHERE m.request_id = ? AND m.retired_at IS NULL")
        params: list = [request_id]
        if min_score is not None:
            query += " AND m.score >= ?"
            params.append(float(min_score))
        query += " ORDER BY m.score DESC, m.listing_id"
        if limit is not None:
            query += " LIMIT ?"
            params.append(int(limit))

        pairs: list[tuple[Match, Listing]] = []
        for row in self.conn.execute(query, tuple(params)):
            data = dict(row)
            match_row = {name: data[f"m_{name}"] for name in match_columns}
            listing_row = {name: data[f"l_{name}"] for name in listing_columns}
            pairs.append((_row_to_match(match_row), _row_to_listing(listing_row)))
        return pairs
```

`_row_to_match` и `_row_to_listing` берут значения по ключу, и обычный `dict`
им подходит так же, как `sqlite3.Row` — менять их не нужно.

- [ ] **Шаг 4: витрина и выгрузка**

```python
# listam/matching.py — в collect_matches, вместо цикла с get_listing
        rows: list[MatchRow] = []
        for request in requests:
            for match, listing in database.matches_with_listings(
                    request.id, min_score=min_score, limit=limit):
                rows.append((request, match, listing))
        return rows
```

Потолок листа «Матчи» в выгрузке — из конфига:

```python
# listam/cli.py — в _export
    from listam.config import positive
    from listam.matching import collect_matches

    matches = collect_matches(
        config, limit=positive(config, "export.matches_limit", 200)
    )
```

```yaml
# config/dev.yaml и config/prod.yaml — в секцию export
export:
  kind: xlsx_local
  path: ./out
  matches_limit: 200            # строк на заявку в листе «Матчи»; null — без потолка
```

- [ ] **Шаг 5: тесты проходят, батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: ~688 passed, 18 skipped.

- [ ] **Шаг 6: коммит**

```bash
git add listam/ports/database.py listam/adapters/db_sqlite.py listam/matching.py listam/cli.py config/ tests/
git commit -m "perf(matches): витрина читает объявления одним запросом, лист «Матчи» с потолком"
```

### Конец фазы 6

- [ ] Дописать раздел «Результат фазы 6»: таблица «до/после» по замерам
      (чтений таблицы, транзакций, секунд на `run_match`, запросов витрины),
      числа батареи, что разошлось.
- [ ] Дописать стартовый промпт для фазы 7.
- [ ] `git status --short` — чисто; коммит сделан.

---

# Фаза 7. Долг: один каркас вместо пяти копий

**Одна сессия.** Закрывает **R-1**, **R-2**, **R-3**, **L-1**, **L-2**, **L-3**.
Правок поведения в этой фазе нет — и это проверяется тем, что батарея после
каждого шага та же самая, без единого нового теста на поведение.

**Ожидается после фазы:** ~692 passed, 18 skipped, схема базы 9.

### Задача 7.1. Общий каркас оркестрации

**Файлы:**
- Создать: `listam/runner.py`
- Изменить: `listam/matching.py`, `listam/clustering_run.py`,
  `listam/requests_sync.py`, `listam/recheck.py`
- Тест: `tests/test_runner.py` (создать)

**Interfaces — Produces:**
```python
@dataclass
class Session:
    database: Database
    storage: Storage
    notes: list[str]
    local_db: Path
    remote_name: str

@contextmanager
def working_session(config: Config, *, needs_schema: bool = True) -> Iterator[Session]
    # замок → свежая копия → connect → migrate → проверка схемы; выход — close + release

def publish(session: Session, config: Config, what: str) -> bool
    # снимок → ротация → заливка; отдаёт, закрыта ли база
```

- [ ] **Шаг 1: тесты каркаса**

```python
# tests/test_runner.py
"""Каркас оркестрации: пять команд делали одно и то же пятью способами."""
from __future__ import annotations

import pytest

from listam.adapters.run_lock import LockBusy
from listam.runner import SessionRefused, working_session


def test_the_session_gives_an_open_database_and_releases_the_lock(runner_config):
    with working_session(runner_config) as session:
        assert session.database.schema_version() > 0
    with working_session(runner_config) as again:
        assert again.database.schema_version() > 0, "замок отпущен"


def test_a_busy_lock_is_a_refusal_and_not_a_crash(runner_config):
    with working_session(runner_config):
        with pytest.raises(SessionRefused) as exc:
            with working_session(runner_config):
                pass
    assert "замок" in str(exc.value).lower()


def test_a_broken_database_is_a_refusal_naming_the_file(runner_config):
    from listam.wiring import database_path

    path = database_path(runner_config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a database")
    with pytest.raises(SessionRefused) as exc:
        with working_session(runner_config):
            pass
    assert str(path) in str(exc.value) or "база" in str(exc.value).lower()


def test_an_old_schema_is_a_refusal_naming_both_versions(runner_config):
    with working_session(runner_config) as session:
        session.database.conn.execute("DELETE FROM schema_version WHERE version >= 5")
        session.database.conn.commit()
    with pytest.raises(SessionRefused) as exc:
        with working_session(runner_config):
            pass
    assert "4" in str(exc.value)
```

Фикстуру `runner_config` положи в `tests/conftest.py` по образцу
`matching_config`, но без объявлений и заявок — каркасу они не нужны.

- [ ] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_runner.py`
Ожидается: FAIL — модуля `listam.runner` нет.

- [ ] **Шаг 3: написать каркас**

```python
"""Каркас оркестрации: то общее, что делают все команды, пишущие в базу.

Замок → свежая копия из хранилища → открыть → накатить миграции → проверить,
что схема не старше кода. На выходе — закрыть и отпустить замок. Пять команд
(`scrape`, `recheck`, `cluster`, `requests`, `match`) писали это пятью
копиями, и копии уже разошлись: одна ловила `OSError` там, где другие ловили
`Exception`, и падала трейсбеком на битом файле; другая звала `storage.upload`
вне `try` и теряла отчёт о проделанной работе из-за отказа сети.

Отчёты команд каркас не трогает: что считать ошибкой и что печатать человеку,
каждая команда решает сама.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from listam.adapters.db_sqlite import latest_schema_version
from listam.adapters.run_lock import LockBusy
from listam.config import Config
from listam.crawler import rotate_backups, take_the_fresher_copy
from listam.ports.database import Database
from listam.ports.storage import Storage
from listam.wiring import build_database, build_run_lock, build_storage, database_path

DEFAULT_KEEP_BACKUPS = 5


class SessionRefused(Exception):
    """Работать нельзя: замок занят, файла нет, база бита или схема старая."""


@dataclass
class Session:
    database: Database
    storage: Storage
    local_db: Path
    remote_name: str
    notes: list[str] = field(default_factory=list)
    closed: bool = False


@contextmanager
def working_session(config: Config, *, needs_schema: bool = True) -> Iterator[Session]:
    """Открытая база под замком. Отказ — `SessionRefused` с внятной причиной."""
    lock = build_run_lock(config)
    try:
        lock.acquire()
    except LockBusy as exc:
        raise SessionRefused(str(exc)) from exc

    local_db = database_path(config)
    remote_name = config.get("storage.db_filename", "listam.sqlite")
    database = None
    try:
        try:
            storage = build_storage(config)
            remote = take_the_fresher_copy(storage, remote_name, local_db)
            database = build_database(config)
            database.connect()
            database.migrate()
        except Exception as exc:       # OSError, sqlite3.Error — базы нет или бита
            raise SessionRefused(f"файл базы недоступен: {exc}") from exc

        if needs_schema:
            required = latest_schema_version()
            version = database.schema_version()
            if version < required:
                raise SessionRefused(
                    f"схема базы {version}, а код ждёт {required}. "
                    f"Команда ничего не мигрирует — накати миграции: "
                    f"python -m listam recheck"
                )

        session = Session(database=database, storage=storage, local_db=local_db,
                          remote_name=remote_name)
        if remote.note:
            session.notes.append(remote.note)
        yield session
    finally:
        if database is not None:
            database.close()
        lock.release()


def publish(session: Session, config: Config, what: str) -> bool:
    """Снимок → ротация копий → заливка. Отдаёт, закрыта ли база.

    Заливать надо именно снимок, а не файл, в который ещё пишут. Отказ сети
    после записи — строка отчёта, а не трейсбек: база уже сохранена, и работа
    не пропала.
    """
    snapshot = session.local_db.with_name(session.local_db.name + ".snapshot")
    try:
        session.database.snapshot(snapshot)
    except Exception as exc:       # sqlite3.Error, OSError — заливать нечего
        session.notes.append(f"снимок базы не сделан: {exc}")
        return False
    session.database.close()
    session.closed = True
    try:
        rotate_backups(
            session.storage, session.remote_name,
            int(config.get("storage.keep_backups", DEFAULT_KEEP_BACKUPS) or 0),
            Path(session.local_db).parent,
        )
        session.storage.upload(snapshot, session.remote_name)
    except Exception as exc:       # OSError, ошибки Google API — сеть отказала
        session.notes.append(f"база не залита в хранилище: {exc}")
        snapshot.unlink(missing_ok=True)
        return True
    snapshot.unlink(missing_ok=True)
    session.notes.append(f"{what} залита в хранилище")
    return True
```

- [ ] **Шаг 4: перевести команды на каркас — по одной, батарея после каждой**

Порядок: `clustering_run` → `requests_sync` → `matching` → `recheck`.
`crawler` не переводится: у него свой порядок (прогон пишет в базу по ходу
обхода, а не одним куском) — и это записывается в отчёт фазы как решение.

После каждой команды:
Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: то же число passed, что и до перевода. **Ни один тест поведения
меняться не должен.** Поехал — значит перевод изменил поведение, откатывай
и разбирайся.

- [ ] **Шаг 5: коммит по команде**

```bash
git add listam/runner.py tests/test_runner.py tests/conftest.py
git commit -m "refactor: каркас оркестрации — замок, копия, миграция, заливка в одном месте"
git add listam/clustering_run.py && git commit -m "refactor(cluster): команда на общем каркасе"
git add listam/requests_sync.py && git commit -m "refactor(requests): команда на общем каркасе"
git add listam/matching.py && git commit -m "refactor(match): команда на общем каркасе"
git add listam/recheck.py && git commit -m "refactor(recheck): команда на общем каркасе"
```

### Задача 7.2. Витрина отдельно от подбора, подписи в одном месте

**Файлы:**
- Создать: `listam/matches_view.py`, `listam/domain/labels.py`
- Изменить: `listam/matching.py`, `listam/cli.py`,
  `listam/adapters/exporter_xlsx.py`
- Тест: `tests/test_matching.py` (правка импортов), `tests/test_labels.py` (создать)

- [ ] **Шаг 1: тест на единственность словаря подписей**

```python
# tests/test_labels.py
"""Подписи статусов живут в одном месте: два словаря расходятся через месяц."""
from __future__ import annotations

from listam.domain import labels


def test_every_match_status_of_the_schema_has_a_russian_word():
    from listam.adapters.db_sqlite import MATCH_STATUSES

    assert set(MATCH_STATUSES) == set(labels.MATCH_STATUSES)


def test_the_window_and_the_export_read_the_same_dictionary():
    from listam.adapters import exporter_xlsx
    from listam import matches_view

    assert exporter_xlsx.MATCH_STATUSES is labels.MATCH_STATUSES
    assert matches_view.MATCH_STATUSES is labels.MATCH_STATUSES
    assert exporter_xlsx.SELLER_TYPES is labels.SELLER_TYPES
```

- [ ] **Шаг 2: убедиться, что тест падает**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_labels.py`
Ожидается: FAIL — модуля `listam.domain.labels` нет.

- [ ] **Шаг 3: словарь подписей**

```python
"""Подписи на русском: один словарь на витрину, выгрузку и отчёты.

Два словаря с одинаковым содержимым в разных файлах — это не дублирование
строк, а обещание, что их однажды поправят порознь. Витрина уже знала
«отправлен», а выгрузка — нет, и разошлись бы они тихо.
"""
from __future__ import annotations

SELLER_TYPES = {"owner": "собственник", "agency": "агентство"}
LISTING_STATUSES = {"active": "на ленте", "gone": "снято"}
MATCH_STATUSES = {"new": "новый", "sent": "отправлен",
                  "called": "звонили", "rejected": "отказ"}
```

В `listam/adapters/exporter_xlsx.py` и в новом `listam/matches_view.py`
заменить локальные словари импортом:

```python
from listam.domain.labels import LISTING_STATUSES as STATUSES, MATCH_STATUSES, \
    SELLER_TYPES
```

- [ ] **Шаг 4: вынести витрину**

Перенести из `listam/matching.py` в новый `listam/matches_view.py` целиком,
без правок поведения: `MatchRow`, `MatchesError`, `DEFAULT_LIMIT`,
`display_limit`, `collect_matches`, `_plural`, `_score`, `_place`, `_what`,
`_note`, `render_matches`. В `matching.py` оставить подбор.

`collect_matches` читает пороги через `settings` из `matching.py` — импорт
`from listam.matching import settings` в `matches_view.py` не создаёт цикла,
потому что `matching.py` на витрину больше не ссылается. Проверь это
запуском: `.venv/Scripts/python.exe -c "import listam.matches_view"`.

Поправить импорты в `listam/cli.py` (`_matches`, `_export`) и в тестах.

- [ ] **Шаг 5: мелочи того же файла**

`listam/adapters/db_sqlite.py`, `iter_requests` — естественный порядок вместо
лексикографического (**L-1**):

```python
        # R-2 идёт перед R-10: человек читает «заявка 2» и «заявка 10», а
        # не строки. Сортировка по (длина, значение) даёт числовой порядок
        # для числовых хвостов и остаётся определённой для любых других.
        query += " ORDER BY LENGTH(external_id), external_id"
```

`listam/matching.py`, `run_match` и `listam/cli.py`, `_match` — убрать
`recount_all` (**L-2**): параметр принимался и не использовался. Проверка
«флаг обязателен» живёт в CLI и там остаётся.

`listam/matches_view.py`, `render_matches` — группировать по `external_id`
(**L-3**):

```python
    by_request: dict[str, list[MatchRow]] = {}
    requests: dict[str, Request] = {}
    for request, match, listing in rows:
        # Ключ — внешний идентификатор, а не `id`: заявка, не записанная
        # в базу, имеет `id = None`, и все такие схлопнулись бы в одну группу.
        key = request.external_id or f"#{request.id}"
        by_request.setdefault(key, []).append((request, match, listing))
        requests[key] = request
```

и дальше по тексту `request_id` → `key`.

- [ ] **Шаг 6: батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: ~692 passed, 18 skipped. Тест на порядок заявок (если он был
написан под старый порядок) перепиши под новый и запиши это в отчёт.

- [ ] **Шаг 7: коммит**

```bash
git add listam/domain/labels.py listam/matches_view.py listam/matching.py listam/cli.py listam/adapters/ tests/
git commit -m "refactor: витрина отдельно от подбора, подписи статусов в одном месте"
```

### Конец фазы 7

- [ ] Дописать раздел «Результат фазы 7»: числа батареи, какие команды
      переведены на каркас и почему `crawler` не переведён, что разошлось.
- [ ] Дописать стартовый промпт для фазы 8.
- [ ] `git status --short` — чисто; коммит сделан.

---

# Фаза 8. Боевая приёмка и разбор

**Одна сессия.** Ни одной новой возможности. Восемь находок проверяются
на копии боевой базы: то, что нельзя показать живьём, не считается
исправленным.

**Ожидается после фазы:** батарея без изменений, схема боевой базы 9.

### Задача 8.1. Копия боевой базы и миграции

- [ ] **Шаг 1: взять копию, а не оригинал**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "import shutil; shutil.copy('data/listam.sqlite', 'data/listam-before-qa.sqlite')"
```

Если `data/listam.sqlite` на машине нет — забери базу из хранилища
(`python -m listam export` скачает её по дороге) и повтори. Если хранилище
недоступно, приёмка идёт на синтетической базе из фазы 6, и это **пишется
в отчёт первой строкой**: «боевая база недоступна, приёмка синтетическая».

- [ ] **Шаг 2: накатить миграции на копию и сверить числа**

Открой копию `SqliteDatabase`, `migrate()`, проверь: `schema_version() == 9`,
число объявлений и прогонов совпало с тем, что было до миграции. Запиши
числа в отчёт.

### Задача 8.2. Восемь проверок живьём

Каждая — команда и ожидаемый ответ. Запиши в отчёт, что увидел на самом деле.

- [ ] **B-1.** `python -m listam match --all`, затем поднять цену у одного
      сматченного объявления прямо в копии базы, ещё раз `match --all` →
      в отчёте «Закрыто матчей: 1», в `matches` этого варианта нет.
- [ ] **B-2.** Добавить в копию объявление-двойник дешевле сматченного,
      `match --all` → в витрине одна строка на квартиру, а не две.
- [ ] **B-3.** Убрать строку из `data/requests.csv`, `python -m listam requests`
      → «закрытых 1» и имя заявки в отчёте.
- [ ] **H-1.** `python -m listam cluster` дважды подряд → второй раз
      «изменено строк: 0».
- [ ] **H-2.** Снизить цену у объявления в копии, `match --new`
      → объявление в выборке и в витрине.
- [ ] **H-3.** `python -m listam cluster` → записать в отчёт число кластеров
      и размер крупнейшего; сравнить с числами из «Результата фазы 6» плана M2.
- [ ] **H-5.** Замерить `match --all` на боевых числах целиком. Эталон
      аудита — 4 чтения таблицы и ~58 с на записи.
- [ ] **M-3.** `python -m listam doctor --no-network` → строка «Схема базы»
      называет версию рабочего файла.

### Задача 8.3. README и закрытие

- [ ] **Шаг 1: README**

Дописать в `README.md`: закрытие матча (`retired_at` — что это и почему не
удаление), закрытие заявки при исчезновении из источника, новый смысл `--new`,
новое правило про конфиг («бессмысленное значение отклоняется на входе, как
в командной строке»), `export.matches_limit`.

- [ ] **Шаг 2: батарея и коммит**

```bash
.venv/Scripts/python.exe -m pytest -q
git add README.md docs/superpowers/plans/2026-09-22-qa-hardening-after-m2.md
git commit -m "docs: приёмка QA-ужесточения и разбор находок"
```

### Конец фазы 8

- [ ] Дописать раздел «Результат фазы 8»: таблица «находка → как проверена →
      что увидели», числа боевой базы, числа батареи.
- [ ] Дописать раздел «Что осталось открытым» — находки, которые решили не
      чинить, с причиной.
- [ ] Дописать **стартовый промпт для написания плана M3** (ниже готовый
      текст — перенеси его в конец файла и подставь настоящие числа).
- [ ] `git status --short` — чисто; коммит сделан.

---

## Шаблон стартового промпта (каждая фаза дописывает свой)

```
Ты продолжаешь работу над инструментом мониторинга list.am
в C:\Users\Artur.A.Gevorgyan\Downloads\new_project.

Прочитай docs/superpowers/plans/2026-09-22-qa-hardening-after-m2.md:
разделы «Что нашёл аудит», «Global Constraints», «Карта файлов»,
«Результат фазы <N-1>» и свою «Фазу <N>». Чужие фазы не трогай.
Рядом лежат план и спека M2 — из них читаются «Принятые решения»:
docs/superpowers/plans/2026-09-22-m2-requests-and-matching.md,
docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md.
Решения 1–11 в фазах не пересматриваются; если фаза одно из них уточняет,
это прямо написано в её заголовке.

Исходное состояние: HEAD <хэш>, дерево чистое, батарея <N> passed,
<M> skipped, схема базы <версия>.

Твоя задача — фаза <N>: <одна фраза о смысле фазы>.
<Три-четыре строки о том, что именно делается и почему это одно целое.>

Работай по шагам задач: на каждое поведение — падающий тест ДО правки.
Тесты гоняй только .venv/Scripts/python.exe -m pytest -q, любой прогон CLI
из скрипта — только с PYTHONIOENCODING=utf-8. Пороги в конфиге не поднимай.
Новый метод порта — это новый контрактный тест в tests/contracts/.
Схему меняют только миграции 008 (фаза 1) и 009 (фаза 3); своих не заводи.
След звонка (matches.status, matches.reject_reason) не трогает ничто.

В конце сессии допиши в план раздел «Результат фазы <N>»: что сделано, числа
батареи, что разошлось с планом и почему, и стартовый промпт для фазы <N+1>.
Сделай коммит.
```

---

## Стартовый промпт для написания плана M3

Этот текст дописывается в конец файла последней фазой — с подставленными
числами. Он не для исполнителя, а для агента, который будет **писать план**.

```
Ты планируешь следующий этап инструмента мониторинга list.am
в C:\Users\Artur.A.Gevorgyan\Downloads\new_project.

Прочитай:
- docs/superpowers/plans/2026-09-22-qa-hardening-after-m2.md целиком, включая
  «Результат фазы 8» и «Что осталось открытым» — это правда о состоянии кода;
- docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md —
  принятые решения 1–11, они не пересматриваются;
- README.md — раздел про заявки, матчинг и витрину.

Состояние на старте: HEAD <хэш>, дерево чистое, батарея <N> passed,
<M> skipped, схема базы 9, боевая база <число> объявлений, прогонов <число>.

Твоя задача — спроектировать и написать план **M3: уведомления и дайджест**.
Что в него входит по роадмапу (list.am → заявки покупателей MVP-спека
и роадмап.md): порог hot даёт немедленное уведомление, порог digest — дневную
сводку; порт notify уже есть (notify.kind: stdout), telegram появляется здесь.
Телефоны продавцов — M4, дашборд — M5, в M3 их нет.

Два числа из боевой приёмки M2, которые определяют дизайн M3 и которые нельзя
игнорировать:
- порог hot = 70 на широкой заявке даёт 348 горячих матчей. Уведомление
  по одному на матч неприемлемо. Порог при этом не поднимается — это решается
  на стороне сборки уведомления: пачка, лимит, «что нового со вчера»;
- «что нового со вчера» держится на том, что пересчёт отличает unchanged
  от updated и не двигает matched_at зря, а закрытые матчи (retired_at,
  фаза 1 QA-плана) из уведомлений выпадают. Это проверено живьём и закрыто
  тестами — не сломай, добавляя свои поля в сравнение.

Сначала — брainstorm: используй скилл superpowers:brainstorming, задай вопросы
и запиши решения в спеку docs/superpowers/specs/<дата>-m3-notifications-design.md.
Только потом — план: скилл superpowers:writing-plans, файл
docs/superpowers/plans/<дата>-m3-notifications.md.

План обязан быть устроен так же, как этот:
- разбит на фазы, **одна фаза — одна сессия**;
- у каждой фазы есть раздел «Ожидается после фазы» с числами батареи;
- каждая фаза кончается двумя обязательными пунктами: дописать в файл
  «Результат фазы N» (что сделано, числа, что разошлось с планом и почему)
  и дописать стартовый промпт для следующей фазы;
- в конце файла — «Шаблон стартового промпта» и раздел «Что в этот план
  не входит»;
- шаги внутри задач — по 2–5 минут, на каждое поведение падающий тест ДО
  правки, код в шагах написан целиком, без «TODO» и «аналогично задаче N»;
- Global Constraints переносятся из этого плана и дополняются: схему меняет
  только новая версионированная миграция (следующая — 010), пороги в конфиге
  не поднимаются ради зелёного теста, новый метод порта — новый контрактный
  тест, след звонка не трогает ничто.

Последняя фаза M3 — боевая приёмка, и она начинается с копии базы, а не
с оригинала. После неё допиши стартовый промпт для написания плана M4.
```

---

## Что в этот план не входит

- Уведомления и дайджест (M3), телефоны продавцов (M4), дашборд (M5).
- Скоринг `must_have` и `nice_to_have` свободным текстом — решение M2, в силе.
- Автоматическое сужение будущих матчей по `reject_reason`: поле заполняется
  руками, механики обучения нет.
- Живая проверка `gsheet`: адаптер остаётся под `skipif`, приёмка идёт на `csv`
  (решение 11 спеки). Фаза 4 правит его разбор, а не способ проверки.
- Перевод `listam/crawler.py` на общий каркас: у прогона свой порядок записи,
  и ломать его ради единообразия — плохой размен (решение фазы 7).
- Курсы EUR и RUB, формат отметок времени в базе — как и в планах M1 и M2,
  трогать незачем.
- Удаление старых матчей и заявок из базы. Закрытые строки остаются: на них
  след звонков, и место они занимают несопоставимо меньшее, чем ответ на
  вопрос «кому мы звонили полгода назад».
