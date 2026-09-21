# M1: дельта, история цен и снятые объявления — план

> **Для исполнителя:** шаги отмечаются чекбоксами (`- [ ]` → `- [x]`) по мере выполнения.
> Каждая фаза выполняется **в отдельной сессии**. Фаза заканчивается разделом
> «Результат фазы N» в этом файле и стартовым промптом для следующей сессии —
> следующая сессия читает отчёт предыдущей отсюда, а не из чата.
> Порядок фаз менять нельзя: каждая опирается на схему и интерфейсы предыдущей.

**Цель:** закрыть этап M1. Критерий из роадмапа: *повторный запуск добавляет только новое
и фиксирует изменения цен*. На руках должно быть: команда `scrape --fresh`, которая за
2–4 страницы вместо 215 приносит всё новое; пометка `status=gone` у объявлений, ушедших
с ленты; журнал прогона, в котором видно, сколько пришло, сколько сменило цену и сколько
снято; команда `changes`, которая это показывает человеку.

**Подход:** сначала схема и журнал (фаза 1) — без них ни дельту, ни снятые записать некуда.
Потом инкрементальный обход (фаза 2) и пометка снятых (фаза 3) — обе ходят по сохранённым
страницам (`scrape.kind: files`), сайт не трогаем. Потом витрина дельты (фаза 4). И только
в фазе 5 готовым кодом трогаем боевую базу и живой сайт. На каждое поведение — падающий
тест **до** правки.

**Стек:** Python 3.12, BeautifulSoup + lxml, SQLite, openpyxl, pytest.

**Спека:** `list.am → заявки покупателей MVP-спека и роадмап.md` (раздел «Роадмап», строка M1;
раздел «Оба направления» — про обход до первого известного ID).
**План M0:** `docs/superpowers/plans/2026-09-21-m0-parser-crawl.md`
**Разбор QA-аудита M0:** `docs/superpowers/plans/2026-09-21-m0-qa-fixes.md`

## Исходное состояние (снято 21.09.2026 перед началом работ)

| Что | Значение |
| --- | --- |
| Батарея тестов | 312 passed, 12 skipped |
| HEAD | `a2d47be` «docs: числа M0 пересчитаны после QA-аудита» |
| `git status` | чисто |
| Боевая база | `data/listam.sqlite`, `schema_version = 3`, `listings 20 569` (все `active`), `price_history 20 570`, `runs 4` |
| Последний прогон | `runs.id = 4`, 215 страниц, 20 576 карточек, 18 649 новых, ошибок 0 |
| Колонок в `.xlsx` | 21, автофильтр `A1:U` |

Прогоны 1–3 в журнале — короткие (2, 2 и 20 страниц): они делались с `--max-pages`.
Это важно для фазы 1: сейчас любой из них может стать меркой полноты для следующего
полного обхода, и проверка недобора страниц из M0 от этого замолкает.

## Общие ограничения (из спеки, нарушать нельзя)

- Пагинация только путевая: `/ru/category/60/2`. Query-параметры фильтров (`n=`, `srt=`, `price1=`)
  запрещены в `robots.txt` сайта — в том числе «сортировка по дате» query-параметром.
- Пауза между запросами — из конфига (`scrape.delay_seconds`, по умолчанию 1.5 с).
  Параллельных пачек нет.
- Все отметки времени в UTC, пути относительные от корня проекта.
- Ни один путь, ключ, идентификатор и имя адаптера не зашит в код: внешнее — за портом,
  выбор — в конфиге, секреты — только в `.env`, подключение — в `listam/wiring.py`.
- Парсер не падает из-за отсутствия поля: нет поля — `None`.
- Схема базы меняется **только** новой версионированной миграцией в `listam/migrations/`.
- Новый метод порта — это новый контрактный тест в `tests/contracts/`.
- Правки парсера отлаживаются на `tests/fixtures/` и `data/pages/`, а не походом на сайт.
- Телефоны (`contacts`) — это M4, заявки и матчинг (`requests`, `matches`) — M2. В M1 не трогаем.

**Запуск тестов — интерпретатором окружения проекта:**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Системный python не годится: в нём нет openpyxl.

Числа «ожидается N passed» в шагах — арифметика от 312 плюс тесты этого плана. Разошлось
на один-два — не повод подгонять: сверь, что именно добавилось, и поправь число в плане.

## Принятые решения (в фазах не пересматриваются)

1. **Инкрементальный обход останавливается не на первом известном ID, а на странице без новых.**
   Спека говорит «до первого уже известного ID», но лента list.am переставляет объявление
   наверх при поднятии: первое известное встречается на первой же странице почти всегда,
   и обход вставал бы, не дойдя до настоящего нового. Поэтому условие остановки —
   `scrape.fresh_stop_after_known_pages` (по умолчанию 2) страниц **подряд**, на которых
   не встретилось ни одного нового ID. Две страницы — это около 190 карточек запаса.
2. **У инкрементального обхода есть потолок** `scrape.fresh_max_pages` (по умолчанию 20).
   Упёрлись в него — это ошибка прогона, а не успех: до известных объявлений обход не дошёл,
   значит часть ленты он не видел и нужен полный обход. Молча укоротить ленту нельзя.
3. **`runs.mode` — четыре значения:** `full` (обход без ограничений), `partial` (`--max-pages`),
   `resume` (`--resume`), `fresh` (`--fresh`). Меркой полноты для следующего обхода
   (`last_successful_run`) считается **только** `full`; строки с `mode IS NULL` — это прогоны
   M0, их считаем полными. Без этого инкрементальный прогон на двух страницах становится
   нормой, и проверка недобора страниц из M0 перестаёт срабатывать.
4. **`--fresh` и `--resume` вместе не работают:** одно идёт с головы ленты, другое продолжает
   прерванный полный обход. Командная строка отвечает отказом и кодом возврата 2.
5. **`status=gone` ставится только после полного удачного обхода:** `mode == "full"`
   и `errors == 0`. Инкрементальный, укороченный и продолженный обходы видели не всю ленту,
   и «не встретилось» у них не значит «снято».
6. **У пометки снятых есть порог** `scrape.max_gone_percent` (по умолчанию 10). Пропало больше
   этой доли активных — не помечаем ничего, считаем ошибкой прогона и пишем об этом в журнал.
   Так выглядит оборванный обход или уехавшая вёрстка, а не рынок.
7. **Дата снятия живёт в отдельной колонке `listings.gone_at`, а не выводится из `last_seen`.**
   Вернувшееся на ленту объявление обязано забыть дату снятия, а `last_seen` двигает каждый
   прогон. Вернулось — `status='active'`, `gone_at = NULL`, `first_seen` не трогаем: это то же
   объявление, а не новое.
8. **Снятое объявление остаётся в базе и в выгрузке.** Удалять нечего: цена снятой квартиры —
   это история рынка, и на M2 по ней считаются медианы кластеров. В `.xlsx` добавляется
   колонка «Снято» (`gone_at`), колонок становится 22, автофильтр `A1:V`.
9. **Изменение цены считается отдельным счётчиком.** `runs.price_changed` — сколько карточек
   сменили **сырую** цену (`price_raw` или `currency`); эти же карточки входят
   в `updated_listings` («изменилось хоть что-то»). Правило M0 не меняется: движение курса
   изменением цены не считается и точку в `price_history` не ставит.
10. **`changes` ничего не мигрирует и не ходит на сайт** — как `export`. Схема младше той,
    которую ждёт код, — внятная ошибка и код возврата 1.

## Распределение работ по фазам

| Фаза | Что делаем | Готово, когда |
| --- | --- | --- |
| 1 | Схема 004, модель, порт, режим и счётчики прогона | Миграция накатывается на схему 3 с данными; `mode`, `price_changed`, `gone_marked`, `stop_reason`, `gone_at` пишутся; меркой полноты стал только полный обход |
| 2 | `scrape --fresh` — инкрементальный обход | Второй прогон по той же ленте проходит одну-две страницы вместо всей и объясняет, почему встал |
| 3 | `status=gone` и колонка «Снято» в выгрузке | Пропавшее с ленты объявление помечено, вернувшееся — разпомечено, оборванный обход не помечает ничего |
| 4 | Команда `changes` — витрина дельты | `python -m listam changes` печатает новое, сменившее цену и снятое с прошлого прогона |
| 5 | Боевая база, живой сайт, документация | Схема 4 на боевой базе, полный обход, инкрементальный обход через час, `.xlsx`, README, контрольный список |

---

## Фаза 1. Схема, журнал и режим прогона

**Зачем:** дельту некуда записывать. В `runs` нет ни режима, ни счётчика сменивших цену,
ни счётчика снятых; у объявления нет даты снятия. И пока режима нет, короткий прогон
с `--max-pages` становится меркой полноты для следующего полного — проверка недобора
страниц из M0 при этом молчит.

### Задача 1.1. Миграция 004 и модель

**Файлы:**
- Создать: `listam/migrations/004_delta_and_gone.sql`
- Изменить: `listam/domain/models.py` (`Listing.gone_at`, поля `Run`)
- Изменить: `tests/test_migrations.py`

**Интерфейсы:**
- Отдаёт дальше: `Listing.gone_at: datetime | None`; `Run.mode: str | None`,
  `Run.price_changed: int`, `Run.gone_marked: int`, `Run.stop_reason: str | None`.

- [x] **Шаг 1. Падающий тест: миграция накатывается на схему 3 с данными.**

```python
# tests/test_migrations.py
def test_migration_004_adds_delta_columns_to_a_filled_database(tmp_path):
    """Схема 3 с данными доезжает до 4, ничего не потеряв: базу на 20 000 строк
    никто заново собирать не будет."""
    database = SqliteDatabase(tmp_path / "listam.sqlite", migrations_dir=upto(tmp_path, 3))
    database.connect()
    database.migrate()
    database.upsert_listing(
        Listing(id="1", url="https://www.list.am/ru/item/1", price_raw="100,000", currency="USD"),
        seen_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
    )
    database.close()

    database = SqliteDatabase(tmp_path / "listam.sqlite")   # все миграции с диска
    database.connect()
    database.migrate()
    assert database.schema_version() == 4
    columns = {row["name"] for row in database.conn.execute("PRAGMA table_info(listings)")}
    assert "gone_at" in columns
    run_columns = {row["name"] for row in database.conn.execute("PRAGMA table_info(runs)")}
    assert {"mode", "price_changed", "gone_marked", "stop_reason"} <= run_columns
    assert database.get_listing("1").price_raw == "100,000"   # данные на месте
    database.close()
```

Вспомогательная `upto` — рядом с существующими помощниками этого файла:

```python
def upto(tmp_path: Path, version: int) -> Path:
    """Папка с миграциями по N-ю включительно: имитация базы, отставшей на версию."""
    partial = tmp_path / f"migrations-{version}"
    partial.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if int(path.name.split("_", 1)[0]) <= version:
            shutil.copyfile(path, partial / path.name)
    return partial
```

- [x] **Шаг 2. Прогнать — тест падает.**

```bash
.venv/Scripts/python.exe -m pytest tests/test_migrations.py -q -k migration_004
```

Ожидается `AssertionError: assert 3 == 4`.

- [x] **Шаг 3. Написать миграцию.**

```sql
-- listam/migrations/004_delta_and_gone.sql
-- Версия 4: дельта прогона и снятые объявления.
--
-- listings.gone_at — когда объявление впервые не встретилось на полном обходе.
--   Отдельная колонка, а не вывод из last_seen: вернувшееся объявление обязано
--   забыть дату снятия, а last_seen двигает каждый прогон.
-- runs.mode — full | partial | resume | fresh. Меркой полноты для следующего
--   обхода считается только full: инкрементальный прогон на двух страницах
--   не имеет права стать нормой, иначе проверка недобора страниц замолчит.
--   NULL — прогоны M0, они были полными.
-- runs.price_changed — сколько карточек сменили сырую цену. Раньше они терялись
--   внутри updated_listings, и «фиксирует изменения цен» нечем было предъявить.
-- runs.gone_marked — сколько объявлений помечено снятыми этим прогоном.
-- runs.stop_reason — почему обход кончился: конец ленты, лимит, страницы без
--   новых, потолок, ошибка.

ALTER TABLE listings ADD COLUMN gone_at TEXT;

ALTER TABLE runs ADD COLUMN mode TEXT;
ALTER TABLE runs ADD COLUMN price_changed INTEGER DEFAULT 0;
ALTER TABLE runs ADD COLUMN gone_marked INTEGER DEFAULT 0;
ALTER TABLE runs ADD COLUMN stop_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_listings_gone_at ON listings(gone_at);
CREATE INDEX IF NOT EXISTS idx_listings_last_seen ON listings(last_seen);
CREATE INDEX IF NOT EXISTS idx_price_history_seen_at ON price_history(seen_at);
```

- [x] **Шаг 4. Дописать поля в модель.**

```python
# listam/domain/models.py, Listing — после status
    gone_at: datetime | None = None       # когда объявление ушло с ленты; вернулось — снова None

# listam/domain/models.py, Run — после notes
    mode: str | None = None               # full | partial | resume | fresh
    price_changed: int = 0                # карточек, у которых сменилась сырая цена
    gone_marked: int = 0                  # объявлений, помеченных снятыми
    stop_reason: str | None = None        # чем кончился обход
```

- [x] **Шаг 5. Прогнать — тест зелёный, батарея целая.**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Ожидается 313 passed, 12 skipped (было 312 плюс один новый).

- [x] **Шаг 6. Коммит.**

```bash
git add listam/migrations/004_delta_and_gone.sql listam/domain/models.py tests/test_migrations.py
git commit -m "feat(db): схема 4 — дата снятия, режим прогона и счётчики дельты"
```

### Задача 1.2. Порт и адаптер: снятые, режим, мерка полноты

**Файлы:**
- Изменить: `listam/ports/database.py`
- Изменить: `listam/adapters/db_sqlite.py`
- Изменить: `tests/contracts/test_database_contract.py`

**Интерфейсы (на них опираются фазы 2–4):**

```python
# listam/ports/database.py
def start_run(self, started_at: datetime, rate_amd_per_usd: float | None,
              mode: str = "full") -> int: ...
def active_ids(self) -> set[str]: ...                            # только status='active'
def mark_gone(self, listing_ids, gone_at: datetime) -> int: ...  # сколько помечено
def last_run(self, mode: str | None = None) -> Run | None: ...   # None — любой режим
def last_successful_run(self) -> Run | None: ...                 # только полный обход
```

- [x] **Шаг 1. Падающие контрактные тесты.**

Константы рядом с существующим `NOW`: `LATER = NOW + timedelta(hours=1)`,
`EVEN_LATER = NOW + timedelta(hours=2)`.

```python
# tests/contracts/test_database_contract.py
def test_mark_gone_marks_only_what_was_active(database):
    database.upsert_listing(listing("1"), seen_at=NOW)
    database.upsert_listing(listing("2"), seen_at=NOW)

    marked = database.mark_gone({"1"}, gone_at=LATER)

    assert marked == 1
    assert database.get_listing("1").status == "gone"
    assert database.get_listing("1").gone_at == LATER
    assert database.get_listing("2").status == "active"
    assert database.get_listing("2").gone_at is None


def test_mark_gone_does_not_move_the_date_of_an_already_gone_listing(database):
    """Объявление снято один раз. Второй обход не имеет права молодить дату."""
    database.upsert_listing(listing("1"), seen_at=NOW)
    database.mark_gone({"1"}, gone_at=LATER)

    assert database.mark_gone({"1"}, gone_at=EVEN_LATER) == 0
    assert database.get_listing("1").gone_at == LATER


def test_mark_gone_leaves_last_seen_alone(database):
    """Снятие — не встреча: объявление никто не видел, и дата встречи стоит на месте."""
    database.upsert_listing(listing("1"), seen_at=NOW)
    database.mark_gone({"1"}, gone_at=LATER)

    assert database.get_listing("1").last_seen == NOW


def test_a_listing_back_on_the_feed_forgets_that_it_was_gone(database):
    database.upsert_listing(listing("1"), seen_at=NOW)
    database.mark_gone({"1"}, gone_at=LATER)

    database.upsert_listing(listing("1"), seen_at=EVEN_LATER)

    back = database.get_listing("1")
    assert back.status == "active"
    assert back.gone_at is None
    assert back.first_seen == NOW          # то же объявление, а не новое


def test_active_ids_skips_the_gone_ones(database):
    database.upsert_listing(listing("1"), seen_at=NOW)
    database.upsert_listing(listing("2"), seen_at=NOW)
    database.mark_gone({"2"}, gone_at=LATER)

    assert database.active_ids() == {"1"}
    assert database.known_ids() == {"1", "2"}   # известны по-прежнему обе


def test_run_remembers_its_mode_and_delta_counters(database):
    run_id = database.start_run(NOW, rate_amd_per_usd=400.0, mode="fresh")
    database.finish_run(run_id, LATER, price_changed=3, gone_marked=7,
                        stop_reason="2 страниц подряд без новых объявлений")

    stored = database.last_run()
    assert stored.mode == "fresh"
    assert stored.price_changed == 3
    assert stored.gone_marked == 7
    assert stored.stop_reason == "2 страниц подряд без новых объявлений"


def test_the_yardstick_is_the_last_full_run_not_the_last_short_one(database):
    """Мерка полноты — только полный обход: инкрементальный прогон на двух
    страницах не имеет права стать нормой для следующего полного."""
    full = database.start_run(NOW, 400.0, mode="full")
    database.finish_run(full, NOW, pages_fetched=215, errors=0)
    fresh = database.start_run(LATER, 400.0, mode="fresh")
    database.finish_run(fresh, LATER, pages_fetched=2, errors=0)
    short = database.start_run(LATER, 400.0, mode="partial")
    database.finish_run(short, LATER, pages_fetched=5, errors=0)

    assert database.last_successful_run().pages_fetched == 215


def test_last_run_can_be_asked_about_one_mode(database):
    """`--resume` продолжает прерванный полный обход, а не инкрементальный,
    который прошёл между ними."""
    full = database.start_run(NOW, 400.0, mode="full")
    database.mark_page(full, 12)
    fresh = database.start_run(LATER, 400.0, mode="fresh")
    database.finish_run(fresh, LATER, pages_fetched=2, errors=0)

    assert database.last_run().mode == "fresh"
    assert database.last_run(mode="full").last_page == 12


def test_legacy_runs_without_a_mode_count_as_full(database):
    """Прогоны M0 писались без режима. Они были полными — так их и читаем."""
    run_id = database.start_run(NOW, 400.0)
    database.conn.execute("UPDATE runs SET mode = NULL WHERE id = ?", (run_id,))
    database.finish_run(run_id, LATER, pages_fetched=215, errors=0)

    assert database.last_successful_run().pages_fetched == 215
```

- [x] **Шаг 2. Прогнать — тесты падают.**

```bash
.venv/Scripts/python.exe -m pytest tests/contracts/test_database_contract.py -q
```

Ожидается `AttributeError: 'SqliteDatabase' object has no attribute 'mark_gone'` и
`TypeError: start_run() got an unexpected keyword argument 'mode'`.

- [x] **Шаг 3. Дописать порт** (`listam/ports/database.py`) — пять сигнатур из блока
  «Интерфейсы» выше, каждая с абзацем-объяснением в том же стиле, что у соседних.

- [x] **Шаг 4. Реализовать в адаптере.**

```python
# listam/adapters/db_sqlite.py

    def start_run(self, started_at: datetime, rate_amd_per_usd: float | None,
                  mode: str = "full") -> int:
        cursor = self.conn.execute(
            "INSERT INTO runs (started_at, rate_amd_per_usd, mode) VALUES (?, ?, ?)",
            (to_iso(started_at), rate_amd_per_usd, mode),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def active_ids(self) -> set[str]:
        rows = self.conn.execute("SELECT id FROM listings WHERE status = 'active'")
        return {row["id"] for row in rows}

    def mark_gone(self, listing_ids, gone_at: datetime) -> int:
        """Переводит объявления в 'gone' и ставит дату снятия — один раз.

        `last_seen` не двигается: снятие — это не встреча, объявление никто
        не видел. COALESCE бережёт дату первого снятия: объявление, которое
        уже неделю как ушло, не должно молодеть с каждым обходом.
        """
        marked = 0
        stamp = to_iso(gone_at)
        with self.transaction():
            for listing_id in listing_ids:
                cursor = self.conn.execute(
                    "UPDATE listings SET status = 'gone', gone_at = COALESCE(gone_at, ?) "
                    "WHERE id = ? AND status <> 'gone'",
                    (stamp, listing_id),
                )
                marked += cursor.rowcount
        return marked

    def last_run(self, mode: str | None = None) -> Run | None:
        if mode is None:
            row = self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM runs WHERE IFNULL(mode, 'full') = ? ORDER BY id DESC LIMIT 1",
                (mode,),
            ).fetchone()
        return _row_to_run(row) if row else None

    def last_successful_run(self) -> Run | None:
        """Последний полный обход, который дошёл до конца и не насчитал ошибок.

        Именно полный: укороченный `--max-pages`, продолженный `--resume` и
        инкрементальный `--fresh` видели не всю ленту, и мерить их числом
        страниц полноту следующего обхода — значит выключить проверку.
        """
        row = self.conn.execute(
            "SELECT * FROM runs WHERE finished_at IS NOT NULL AND IFNULL(errors, 0) = 0 "
            "AND IFNULL(mode, 'full') = 'full' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return _row_to_run(row) if row else None
```

Там же, точечные правки:

```python
# finish_run — расширить разрешённые счётчики
        allowed = {
            "pages_fetched", "listings_seen", "new_listings", "updated_listings",
            "price_changed", "gone_marked", "errors", "notes", "last_page", "stop_reason",
        }

# _row_to_run — дочитать новые колонки
        mode=row["mode"],
        price_changed=row["price_changed"] or 0,
        gone_marked=row["gone_marked"] or 0,
        stop_reason=row["stop_reason"],

# _row_to_listing — дату снятия читаем так же, как остальные даты
    data["gone_at"] = from_iso(data.get("gone_at"))

# upsert_listing, ветка вставки — дату кладём строкой, а не объектом datetime
            values["gone_at"] = to_iso(listing.gone_at)

# upsert_listing, ветка обновления — встретили на ленте, значит вернулось
        updates["gone_at"] = None
```

- [x] **Шаг 5. Прогнать всю батарею.**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Ожидается 322 passed, 12 skipped.

- [x] **Шаг 6. Коммит.**

```bash
git add listam/ports/database.py listam/adapters/db_sqlite.py tests/contracts/test_database_contract.py
git commit -m "feat(db): снятые объявления, режим прогона и мерка полноты по полным обходам"
```

### Задача 1.3. Прогон пишет режим и считает сменившие цену

**Файлы:**
- Изменить: `listam/crawler.py` (`_Counters`, `run_scrape`, новая `run_mode`)
- Изменить: `listam/cli.py` (строка итога)
- Изменить: `tests/test_crawler.py`

**Интерфейсы:**
- Берёт из задачи 1.2: `start_run(..., mode=...)`, `finish_run(..., price_changed=..., gone_marked=..., stop_reason=...)`.
- Отдаёт дальше: `def run_mode(*, fresh: bool, resume: bool, limit: int | None) -> str`.

- [x] **Шаг 1. Падающие тесты.**

```python
# tests/test_crawler.py
def test_a_full_crawl_is_written_down_as_full(project):
    run_scrape(project)

    database = opened(project)
    stored = database.last_run()
    database.close()
    assert stored.mode == "full"


def test_a_crawl_cut_by_max_pages_is_written_down_as_partial(project):
    run_scrape(project, max_pages=1)

    database = opened(project)
    stored = database.last_run()
    database.close()
    assert stored.mode == "partial"


def test_a_short_run_does_not_become_the_yardstick_for_the_next_full_one(project, tmp_path):
    """Прогон на одну страницу не имеет права стать нормой: следующий полный
    обход, вставший на первой странице, обязан быть пойман по прошлому полному."""
    run_scrape(project)                      # полный: 2 страницы
    run_scrape(project, max_pages=1)         # укороченный: 1 страница, ошибок нет

    (tmp_path / "pages" / "category-60-2.html").unlink()   # пагинатор ведёт в никуда
    run = run_scrape(project)

    assert run.errors >= 1
    assert "прошлый удачный прогон прошёл 2" in run.notes


def test_a_changed_price_is_counted_apart_from_other_updates(project, tmp_path):
    """Смена цены — это и обновление карточки тоже: updated_listings остаётся
    счётчиком «изменилось хоть что-то», price_changed отвечает на «что с ценой»."""
    run_scrape(project)
    page = tmp_path / "pages" / "category-60.html"
    page.write_text(page.read_text(encoding="utf-8").replace("290,000", "275,000"),
                    encoding="utf-8")

    run = run_scrape(project)

    assert run.price_changed == 1
    assert run.updated_listings == 1


def test_a_finished_crawl_says_why_it_stopped(project):
    run = run_scrape(project)

    assert "конец ленты" in run.stop_reason
```

- [x] **Шаг 2. Прогнать — тесты падают.**

```bash
.venv/Scripts/python.exe -m pytest tests/test_crawler.py -q -k "yardstick or written_down or counted_apart or why_it_stopped"
```

Ожидается `AssertionError: assert None == 'full'`, `AttributeError: 'Run' object has no attribute 'price_changed'`
и `assert 0 >= 1` в тесте про мерку.

- [x] **Шаг 3. Реализовать.**

```python
# listam/crawler.py — рядом с resume_start_page

def run_mode(*, fresh: bool, resume: bool, limit: int | None) -> str:
    """Каким был этот обход. От режима зависит, кому он норма и кого он снимает.

    Полным считается только обход без ограничений: укороченный, продолженный
    и инкрементальный видели не всю ленту.
    """
    if fresh:
        return "fresh"
    if resume:
        return "resume"
    if limit is not None:
        return "partial"
    return "full"
```

```python
# _Counters — новые счётчики
    price_changed: int = 0
    gone_marked: int = 0

# run_scrape, до цикла
    mode = run_mode(fresh=False, resume=resume, limit=limit)   # fresh появится в фазе 2
    stop_reason = None
    run_id = None if dry_run else database.start_run(started_at, rate_value, mode=mode)

# разбор итога апсерта
                        if outcome == "new":
                            counters.new_listings += 1
                        elif outcome == "price_changed":
                            counters.price_changed += 1
                            counters.updated_listings += 1
                        elif outcome == "updated":
                            counters.updated_listings += 1
```

Причина остановки проставляется в каждом месте выхода из цикла:

```python
                    if limit is not None and counters.pages_fetched >= limit:
                        stop_reason = f"лимит страниц: scrape.max_pages = {limit}"
                        break
                    # между ними — проверка scrape.hard_page_limit, она уже написана:
                    # там тоже ставится stop_reason = "ошибка обхода"
                    following = parse_next_page(html)
                    if following is None or following <= page:
                        stop_reason = "конец ленты: пагинатор не дал следующей страницы"
                        break
```

а в ветках ошибок (страница не получена, контейнер не найден, карточек мало или много,
потолок `hard_page_limit`) — `stop_reason = "ошибка обхода"` рядом с `counters.errors += 1`.

```python
# journal() — новые счётчики уходят в базу
                        price_changed=counters.price_changed,
                        gone_marked=counters.gone_marked,
                        stop_reason=stop_reason,

# возвращаемый Run — те же поля
            price_changed=counters.price_changed,
            gone_marked=counters.gone_marked,
            mode=mode,
            stop_reason=stop_reason,
```

Возврат при раннем выходе (курс не получен, замок занят, файл базы недоступен) тоже
получает `mode=mode` — иначе прогон, который не начался, выглядит в журнале безрежимным.
`mode` для этого считается до похода за курсом.

```python
# listam/cli.py, _scrape — строка итога
    print(
        f"Прогон ({run.mode}): страниц: {run.pages_fetched}, карточек: {run.listings_seen}, "
        f"новых: {run.new_listings}, обновлённых: {run.updated_listings}, "
        f"сменили цену: {run.price_changed}, снято: {run.gone_marked}, "
        f"ошибок: {run.errors}"
    )
    if run.stop_reason:
        print(f"Обход кончился: {run.stop_reason}")
```

- [x] **Шаг 4. Прогнать всю батарею.**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Ожидается 327 passed, 12 skipped. Тест в `tests/test_cli.py`, проверяющий строку итога
прогона, поправить под новый формат — это ожидаемая правка, а не поломка.

- [x] **Шаг 5. Коммит.**

```bash
git add listam/crawler.py listam/cli.py tests/test_crawler.py tests/test_cli.py
git commit -m "feat(crawler): режим прогона, счётчик сменивших цену и причина остановки"
```

**Результат фазы 1**

Сделано: схема доведена до версии 4 (`004_delta_and_gone.sql`: `listings.gone_at`,
`runs.mode`, `runs.price_changed`, `runs.gone_marked`, `runs.stop_reason` и три индекса);
модель, порт и адаптер знают про снятые, режим прогона и счётчики дельты; прогон пишет
режим, считает сменивших цену отдельно от прочих обновлений и объясняет, чем кончился обход.

Батарея: было 312 passed, 12 skipped → стало **327 passed, 12 skipped**
(313 после задачи 1.1, 322 после 1.2, 327 после 1.3 — как и ждал план).

Коммиты: `577980a`, `4bf761c`, `bcfbb6f`.

Новые интерфейсы:
- `Database.active_ids() -> set[str]` — только `status='active'`.
- `Database.mark_gone(listing_ids, gone_at) -> int` — сколько помечено; дата ставится один
  раз (COALESCE), `last_seen` не двигается.
- `Database.start_run(started_at, rate_amd_per_usd, mode="full") -> int`.
- `Database.last_run(mode=None) -> Run | None` — `mode` сужает выборку до режима;
  сравнение идёт через `IFNULL(mode,'full')`, поэтому прогоны M0 читаются как полные.
- `Database.last_successful_run()` — теперь **только** полный обход без ошибок.
- `finish_run` принимает `price_changed`, `gone_marked`, `stop_reason`.
- `crawler.run_mode(*, fresh, resume, limit) -> str` — full | partial | resume | fresh.
- `Run.mode`, `Run.price_changed`, `Run.gone_marked`, `Run.stop_reason`; `Listing.gone_at`.

Отклонения от плана (и почему):
1. **Вставка объявления пишет только те колонки, которые есть в таблице**
   (`SqliteDatabase._columns("listings")`). Тест миграции 004 по заданию наполняет базу,
   стоящую на схеме 3, кодом, который уже знает про `gone_at`, — без фильтра вставка падала
   на «table listings has no column named gone_at». Модель законно бежит впереди базы, пока
   миграции не накатаны.
2. **Проверки версии схемы в тестах перестали быть числом.** `tests/test_recheck.py` и
   `tests/test_cli.py` держали «3» константой и падали на схеме 4; теперь спрашивают
   `latest_schema_version()`. Тест миграции 003 проверяет `>= 3` и наличие колонки.
   План этих правок не предусматривал, но без них «312 плюс новые» не сходилось.
3. **В тесте на смену цены правится `162,000`, а не `290,000`.** `290,000` в фикстуре стоит
   в блоке «Топ объявления», который парсер в разбор не берёт: замена ничего не меняла и
   `price_changed` оставался нулём. `162,000` — карточка 23973917 из самой ленты.
4. `stop_reason` в ветках ошибок — строка «ошибка обхода», как и сказано в плане; текст
   самой ошибки по-прежнему уходит в `notes`.

Чего в фазе 1 нет (и не должно быть): `--fresh`, пометки снятых, колонки «Снято» в выгрузке,
команды `changes`. Боевая база не тронута — она на схеме 3 до фазы 5.

## Стартовый промпт для новой сессии (фаза 2)

Открыть новую сессию **в этой же папке** (`C:\Users\Admin\Downloads\list`) и скопировать целиком:

```markdown
Проект: listam — мониторинг list.am под заявки покупателей. Папка C:\Users\Admin\Downloads\list,
ветка master.

Прочитай перед началом, в этом порядке:
1. docs/superpowers/plans/2026-09-21-m1-delta-and-history.md — план этапа M1, он же твоё задание;
   раздел «Результат фазы 1» — отчёт предыдущей сессии.
2. README.md — как устроен проект и чем он запускается.
3. «list.am → заявки покупателей MVP-спека и роадмап.md» — раздел «Роадмап» (строка M1)
   и «Оба направления».

Делаешь ТОЛЬКО фазу 2 (инкрементальный обход `scrape --fresh`). Фазы 3–5 — другие сессии.
Раздел «Принятые решения» в плане не пересматривается.

Исходное состояние: батарея 327 passed, 12 skipped; HEAD — коммит фазы 1 `bcfbb6f`;
git status чистый; боевая база по-прежнему на схеме 3 — её не трогать.

Что нужно знать про фазу 1 (сессия её не видела):
- Схема 4: listings.gone_at; runs.mode | price_changed | gone_marked | stop_reason.
- `start_run(started_at, rate, mode="full")`; `last_run(mode=None)`; `last_successful_run()`
  отдаёт только полный обход (`IFNULL(mode,'full')='full'`, errors = 0, finished_at не пуст).
- `finish_run` принимает price_changed, gone_marked, stop_reason; лишний счётчик — ValueError.
- `active_ids()` и `mark_gone(ids, gone_at)` уже есть — ими пользуется фаза 3, не фаза 2.
- В `run_scrape` уже есть `mode = run_mode(fresh=False, resume=resume, limit=limit)` и
  переменная `stop_reason`; фазе 2 остаётся передать туда настоящий `fresh` и поставить свои
  причины остановки. `run_mode` лежит в listam/crawler.py рядом с `upload_refusal`.
- Строка итога в CLI уже печатает режим, «сменили цену», «снято» и отдельной строкой
  «Обход кончился: <stop_reason>».
- Вставка объявления фильтрует колонки по PRAGMA table_info — модель может опережать базу.
- Проверки версии схемы в тестах идут через `latest_schema_version()`, не числом.

Правила, которые нельзя нарушать:
- На каждое новое поведение — падающий тест ДО правки.
- Новый метод порта — новый контрактный тест в tests/contracts/.
- Ни один путь, ключ и порог не зашит в код: пороги `scrape.fresh_stop_after_known_pages`
  и `scrape.fresh_max_pages` — в оба конфига (config/dev.yaml и config/prod.yaml).
- Все отметки времени в UTC, пути относительные от корня проекта.
- Боевую базу data/listam.sqlite не трогать. На сайт не ходить: только tests/fixtures/.

Запуск тестов — интерпретатором окружения проекта:
.venv/Scripts/python.exe -m pytest -q
Системный python не годится: в нём нет openpyxl.

Когда закончишь: покажи полный вывод pytest, git status и git log --oneline; заполни
«Результат фазы 2» в файле плана и допиши туда стартовый промпт для фазы 3.
```

---

## Фаза 2. Инкрементальный обход `scrape --fresh`

**Зачем:** это и есть M1. Полный обход — 215 страниц и около десяти минут; раз в час так
ходить нельзя и незачем. Свежие объявления лежат в голове ленты, и чтобы их забрать,
хватает двух-трёх страниц.

**Что известно про ленту (из M0):** одна страница — 96 карточек в `#contentr` (блок `#tp`
исключён), пагинация путевая, одно и то же объявление иногда попадает на две соседние
страницы.

### Задача 2.1. Условие остановки

**Файлы:**
- Изменить: `listam/crawler.py` (новая `incremental_stop`)
- Создать: `tests/test_crawler_fresh.py`

**Интерфейсы:**

```python
def incremental_stop(*, pages_without_new: int, threshold: int,
                     pages_fetched: int, ceiling: int) -> tuple[str | None, bool]:
    """(причина остановки, это ли ошибка). (None, False) — идём дальше."""
```

- [x] **Шаг 1. Падающие тесты на чистую функцию.**

```python
# tests/test_crawler_fresh.py
from listam.crawler import incremental_stop


def test_incremental_walks_on_while_new_listings_keep_coming():
    assert incremental_stop(pages_without_new=1, threshold=2,
                            pages_fetched=1, ceiling=20) == (None, False)


def test_incremental_stops_after_the_configured_number_of_known_pages():
    reason, is_error = incremental_stop(pages_without_new=2, threshold=2,
                                        pages_fetched=2, ceiling=20)
    assert "2 страниц подряд без новых" in reason
    assert is_error is False


def test_hitting_the_ceiling_is_a_failure_not_a_finish():
    """Потолок значит, что до известных объявлений обход не дошёл: часть ленты
    он не видел, и молча считать такой прогон удачным нельзя."""
    reason, is_error = incremental_stop(pages_without_new=0, threshold=2,
                                        pages_fetched=20, ceiling=20)
    assert "fresh_max_pages" in reason
    assert is_error is True
```

- [x] **Шаг 2. Прогнать — падает на импорте.**

```bash
.venv/Scripts/python.exe -m pytest tests/test_crawler_fresh.py -q
```

Ожидается `ImportError: cannot import name 'incremental_stop'`.

- [x] **Шаг 3. Реализовать функцию.**

```python
# listam/crawler.py
DEFAULT_FRESH_STOP_PAGES = 2     # столько страниц подряд без новых — и хватит
DEFAULT_FRESH_MAX_PAGES = 20     # потолок инкрементального обхода


def incremental_stop(
    *, pages_without_new: int, threshold: int, pages_fetched: int, ceiling: int
) -> tuple[str | None, bool]:
    """Пора ли кончать инкрементальный обход и честный ли это конец.

    Спека говорит «до первого известного ID», но лента переставляет объявление
    наверх при поднятии: первое известное встречается на первой же странице
    почти всегда. Поэтому считаем страницы подряд, на которых не было ни одного
    нового ID: две страницы — это около 190 карточек запаса.

    Потолок — не конец, а сбой: обход не дошёл до известного, значит часть ленты
    он не видел, и следующим должен идти полный обход.
    """
    if threshold and pages_without_new >= threshold:
        return (
            f"{pages_without_new} страниц подряд без новых объявлений "
            f"(scrape.fresh_stop_after_known_pages = {threshold})",
            False,
        )
    if ceiling and pages_fetched >= ceiling:
        return (
            f"инкрементальный обход упёрся в потолок scrape.fresh_max_pages = {ceiling}, "
            f"до известных объявлений он не дошёл — нужен полный обход",
            True,
        )
    return None, False
```

- [x] **Шаг 4. Прогнать — зелено.**

```bash
.venv/Scripts/python.exe -m pytest tests/test_crawler_fresh.py -q
```

- [x] **Шаг 5. Коммит.**

```bash
git add listam/crawler.py tests/test_crawler_fresh.py
git commit -m "feat(crawler): условие остановки инкрементального обхода"
```

### Задача 2.2. Флаг `--fresh` в прогоне и в командной строке

**Файлы:**
- Изменить: `listam/crawler.py` (`run_scrape`), `listam/cli.py`
- Изменить: `config/dev.yaml`, `config/prod.yaml`
- Изменить: `tests/test_crawler_fresh.py`, `tests/test_cli.py`

**Интерфейсы:**

```python
def run_scrape(config, *, max_pages=None, dry_run=False, allow_shrink=False,
               resume=False, allow_upload_with_errors=False, fresh=False) -> Run
```

Новые ключи в секции `scrape` обоих конфигов:

```yaml
  fresh_stop_after_known_pages: 2   # столько страниц подряд без новых — и хватит
  fresh_max_pages: 20               # потолок инкрементального обхода
```

- [x] **Шаг 1. Падающие тесты на прогоне.**

Фикстура `project` копируется в `tests/test_crawler_fresh.py` из `tests/test_crawler.py`
(импортировать её оттуда нельзя — это сцепит файлы; копия в 25 строк честнее), с добавкой
в секцию `scrape`:

```python
                "fresh_stop_after_known_pages": 1,   # фикстура — две страницы, порог свой
                "fresh_max_pages": 10,
```

```python
def test_fresh_run_after_a_full_one_stops_on_the_first_known_page(project):
    run_scrape(project)                      # полный обход: 2 страницы, 8 объявлений

    run = run_scrape(project, fresh=True)

    assert run.pages_fetched == 1
    assert run.new_listings == 0
    assert run.errors == 0
    assert run.mode == "fresh"
    assert "1 страниц подряд без новых объявлений" in run.stop_reason


def test_fresh_run_picks_up_a_genuinely_new_listing(project, tmp_path):
    run_scrape(project)
    page = tmp_path / "pages" / "category-60.html"
    page.write_text(page.read_text(encoding="utf-8").replace("24100001", "99100001"),
                    encoding="utf-8")

    run = run_scrape(project, fresh=True)

    assert run.new_listings == 1
    database = opened(project)
    assert database.get_listing("99100001") is not None
    database.close()


def test_fresh_run_that_never_reached_known_listings_is_a_failure(project):
    """База пуста: новое на каждой странице. Такой обход обязан упереться
    в потолок и сказать, что полной картины он не собрал."""
    project.data["scrape"]["fresh_max_pages"] = 1

    run = run_scrape(project, fresh=True)

    assert run.errors == 1
    assert "fresh_max_pages" in run.notes


def test_fresh_run_is_not_accused_of_walking_too_few_pages(project):
    """Проверка недобора страниц из M0 к инкрементальному обходу не применяется:
    он укорочен нарочно."""
    run_scrape(project)
    project.data["scrape"]["expected_pages_min"] = 2

    run = run_scrape(project, fresh=True)

    assert run.errors == 0


def test_fresh_run_does_not_become_the_yardstick(project, tmp_path):
    run_scrape(project)                      # полный: 2 страницы
    run_scrape(project, fresh=True)          # инкрементальный: 1 страница

    (tmp_path / "pages" / "category-60-2.html").unlink()
    run = run_scrape(project)

    assert "прошлый удачный прогон прошёл 2" in run.notes


def test_resume_continues_the_interrupted_full_crawl_not_the_fresh_one(project, tmp_path):
    """Между прерванным полным обходом и `--resume` мог пройти инкрементальный.
    Продолжать надо полный: иначе `--resume` пойдёт со второй страницы вместо
    сто седьмой и отчитается успехом."""
    run_scrape(project, max_pages=1)         # обход, который дальше оборвали
    database = opened(project)
    database.conn.execute(
        "UPDATE runs SET mode = 'full', finished_at = NULL, errors = 1, last_page = 7"
    )
    database.conn.commit()
    database.close()
    run_scrape(project, fresh=True)          # между ними — инкрементальный прогон

    run = run_scrape(project, resume=True)

    assert "обход продолжен со страницы 8" in run.notes


def test_fresh_run_records_a_price_change(project, tmp_path):
    run_scrape(project)
    page = tmp_path / "pages" / "category-60.html"
    page.write_text(page.read_text(encoding="utf-8").replace("290,000", "275,000"),
                    encoding="utf-8")

    run = run_scrape(project, fresh=True)

    assert run.price_changed == 1
```

```python
# tests/test_cli.py
def test_fresh_and_resume_together_are_refused(tmp_path, capsys):
    """Одно идёт с головы ленты, другое продолжает прерванный полный обход."""
    code = main(["--config-dir", str(config_dir(tmp_path)), "scrape", "--fresh", "--resume"])

    assert code == 2
    assert "вместе не работают" in capsys.readouterr().err
```

`config_dir` — тот же помощник, которым пользуются соседние тесты этого файла.

- [x] **Шаг 2. Прогнать — падает.**

```bash
.venv/Scripts/python.exe -m pytest tests/test_crawler_fresh.py tests/test_cli.py -q
```

Ожидается `TypeError: run_scrape() got an unexpected keyword argument 'fresh'`.

- [x] **Шаг 3. Реализовать в `run_scrape`.**

```python
# сигнатура
def run_scrape(config, *, max_pages=None, dry_run=False, allow_shrink=False,
               resume=False, allow_upload_with_errors=False, fresh=False) -> Run:

# до цикла (mode уже считается в задаче 1.3 — теперь с настоящим fresh)
    mode = run_mode(fresh=fresh, resume=resume, limit=limit)
    fresh_threshold = int(config.get("scrape.fresh_stop_after_known_pages",
                                     DEFAULT_FRESH_STOP_PAGES) or 0)
    fresh_ceiling = int(config.get("scrape.fresh_max_pages", DEFAULT_FRESH_MAX_PAGES) or 0)
    if fresh and limit is not None:
        fresh_ceiling = min(fresh_ceiling or limit, limit)   # --max-pages опускает потолок
    known = database.known_ids() if (fresh and database is not None) else set()
    pages_without_new = 0

# внутри цикла, после разбора карточек страницы и до их записи
                    fresh_on_page = sum(1 for card in cards if card.id not in known)

# после записи карточек страницы, рядом с mark_page
                    if fresh:
                        known.update(card.id for card in cards)
                        pages_without_new = 0 if fresh_on_page else pages_without_new + 1
                        reason, is_error = incremental_stop(
                            pages_without_new=pages_without_new,
                            threshold=fresh_threshold,
                            pages_fetched=counters.pages_fetched,
                            ceiling=fresh_ceiling,
                        )
                        if reason:
                            stop_reason = reason
                            if is_error:
                                counters.errors += 1
                                note = f"{note}; {reason}"
                            break

# --resume продолжает прерванный ПОЛНЫЙ обход: инкрементальный прогон,
# прошедший между ними, продолжать нечего
        if resume and database is not None:
            start_page, resume_note = resume_start_page(database.last_run(mode="full"))

# проверка недобора страниц — инкрементальному обходу не предъявляется
                if limit is None and not resume and not fresh:

# проверка «обход кончился на первой странице» — для fresh это норма
                alone = (
                    counters.pages_fetched == 1
                    and not fresh
                    and not (resume and start_page > 1)
                )
```

`known` в пробном прогоне (`dry_run`) остаётся пустым множеством: базы он не касается
и притворяться, что знает её содержимое, не имеет права — такой прогон дойдёт до потолка
и честно об этом скажет.

```python
# listam/cli.py — флаг
    scrape.add_argument("--fresh", action="store_true",
                        help="инкрементальный обход: только свежая часть ленты "
                             "до уже известных объявлений")

# listam/cli.py, main — перед вызовом _scrape
    if args.command == "scrape":
        if args.fresh and args.resume:
            print(
                "--fresh и --resume вместе не работают: первый идёт с головы ленты, "
                "второй продолжает прерванный полный обход. Выбери одно.",
                file=sys.stderr,
            )
            return 2
```

и передача `fresh=args.fresh` в `_scrape`, а оттуда в `run_scrape`.

- [x] **Шаг 4. Дописать оба ключа в `config/dev.yaml` и `config/prod.yaml`** — с теми же
  комментариями, что в блоке «Интерфейсы» выше.

- [x] **Шаг 5. Прогнать всю батарею.**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Ожидается 338 passed, 12 skipped.

- [x] **Шаг 6. Коммит.**

```bash
git add listam/crawler.py listam/cli.py config/dev.yaml config/prod.yaml tests/test_crawler_fresh.py tests/test_cli.py
git commit -m "feat(scrape): инкрементальный обход --fresh до известных объявлений"
```

**Результат фазы 2**

Сделано: `incremental_stop` решает, когда инкрементальный обход кончился и честный ли это
конец; `run_scrape(..., fresh=True)` идёт по ленте, пока на странице попадается хоть одно
новое объявление, и встаёт после `scrape.fresh_stop_after_known_pages` страниц подряд без
новых; потолок `scrape.fresh_max_pages` — ошибка прогона, а не успех. `--resume` теперь
продолжает последний **полный** обход (`last_run(mode="full")`), а не затесавшийся между
ними инкрементальный. Проверки «недобор страниц» и «обход кончился на первой» к `fresh`
не применяются: он укорочен нарочно. В командной строке появился флаг `--fresh`;
`--fresh` вместе с `--resume` — отказ и код возврата 2. Оба порога прописаны
в `config/dev.yaml` и `config/prod.yaml`.

Батарея: было 327 passed, 12 skipped → стало **339 passed, 12 skipped**
(330 после задачи 2.1, 339 после 2.2).

Коммиты: `0a6ab6f`, `1c235db`.

Новые интерфейсы:
- `crawler.incremental_stop(*, pages_without_new, threshold, pages_fetched, ceiling)
  -> tuple[str | None, bool]` — (причина остановки, это ли ошибка).
- `crawler.DEFAULT_FRESH_STOP_PAGES = 2`, `crawler.DEFAULT_FRESH_MAX_PAGES = 20`.
- `run_scrape(config, *, ..., fresh=False)`; `cli._scrape(..., fresh=False)`;
  флаг `python -m listam scrape --fresh`.
- Ключи конфига `scrape.fresh_stop_after_known_pages`, `scrape.fresh_max_pages`.

Новых методов порта фаза 2 не добавила: известное берётся уже существующим
`Database.known_ids()`, и контрактный тест на него есть с M0. Поэтому новых файлов
в `tests/contracts/` нет.

Отклонения от плана (и почему):
1. **Тесты «новое объявление» и «смена цены» правят другие данные, чем написано в плане.**
   `24100001` лежит на *второй* странице фикстуры, и замена его на `99100001` не давала
   нового на первой — обход вставал, не дойдя до подмены. Меняется `23987063` (карточка
   первой страницы). Цена `290,000` в фикстуре стоит в блоке «Топ объявления», который
   парсер в разбор не берёт (это уже выяснила фаза 1) — вместо неё правится `162,000`,
   карточка 23973917 из самой ленты.
2. **Тест отказа `--fresh --resume` пользуется помощником `run(project, ...)`**, а не
   `config_dir(tmp_path)`: помощника с таким именем в `tests/test_cli.py` нет, соседние
   тесты ходят через фикстуру `project` и `run`.
3. **Добавлен тест `test_fresh_flag_reaches_the_run`** — тот же приём, что у соседнего
   `test_resume_flag_reaches_the_run`: без него никто не проверял, что флаг командной
   строки доезжает до `run_scrape`. Отсюда 339 вместо обещанных планом 338.
4. **`known` считается после подготовки базы, рядом с `resume_start_page`**, а не «до цикла»
   вместе с порогами: до этого места `database` ещё `None`. Пороги остались там, где сказано.

Чего в фазе 2 нет (и не должно быть): пометки снятых, колонки «Снято» в выгрузке, команды
`changes`, строки про `--fresh` в README (README — фаза 5). Боевая база не тронута: она
на схеме 3 до фазы 5.

## Стартовый промпт для новой сессии (фаза 3)

Открыть новую сессию **в этой же папке** (`C:\Users\Admin\Downloads\list`) и скопировать целиком:

```markdown
Проект: listam — мониторинг list.am под заявки покупателей. Папка C:\Users\Admin\Downloads\list,
ветка master.

Прочитай перед началом, в этом порядке:
1. docs/superpowers/plans/2026-09-21-m1-delta-and-history.md — план этапа M1, он же твоё задание;
   разделы «Результат фазы 1» и «Результат фазы 2» — отчёты предыдущих сессий.
2. README.md — как устроен проект и чем он запускается.
3. «list.am → заявки покупателей MVP-спека и роадмап.md» — раздел «Роадмап» (строка M1).

Делаешь ТОЛЬКО фазу 3 (`status=gone` и колонка «Снято» в выгрузке). Фазы 4–5 — другие сессии.
Раздел «Принятые решения» в плане не пересматривается.

Исходное состояние: батарея 339 passed, 12 skipped; HEAD — коммит фазы 2 `1c235db`;
git status чистый; боевая база по-прежнему на схеме 3 — её не трогать.

Что нужно знать про фазы 1–2 (сессия их не видела):
- Схема 4: listings.gone_at; runs.mode | price_changed | gone_marked | stop_reason.
- `Database.active_ids() -> set[str]` (только status='active') и
  `Database.mark_gone(listing_ids, gone_at) -> int` уже есть — фаза 3 ими и пользуется.
  Дата снятия ставится один раз (COALESCE), last_seen не двигается.
- `last_successful_run()` отдаёт только полный обход без ошибок; `last_run(mode=None)`
  сужает выборку по режиму через IFNULL(mode,'full').
- `run_mode(*, fresh, resume, limit)` даёт full | partial | resume | fresh; в `run_scrape`
  режим считается до похода за курсом, переменная `stop_reason` уже есть и ставится
  во всех ветках выхода из цикла.
- `run_scrape(config, *, max_pages, dry_run, allow_shrink, resume,
  allow_upload_with_errors, fresh)`; `counters.gone_marked` уже уходит в журнал и в Run.
- Пометка снятых ставится ТОЛЬКО при mode == "full" и errors == 0 (решение 5) и имеет
  порог `scrape.max_gone_percent` (решение 6) — ключ нужен в оба конфига.
- Вернувшееся объявление обязано забыть gone_at и снова стать active (решение 7).
- Вставка объявления фильтрует колонки по PRAGMA table_info — модель может опережать базу.
- Проверки версии схемы в тестах идут через `latest_schema_version()`, не числом.
- Фикстура ленты: страница 1 — 6 карточек (23973917 с ценой `$ 162,000`, 24228087, 23598471,
  23311644, 23987063, 99999999 без цены), страница 2 — 24100001, 24100002 и повтор 23973917.
  Цена `290,000` в фикстуре лежит в блоке «Топ объявления» и в разбор НЕ попадает.
- Инкрементальный обход `--fresh` уже есть: тесты на него — в tests/test_crawler_fresh.py,
  там же своя копия фикстуры `project`.

Правила, которые нельзя нарушать:
- На каждое новое поведение — падающий тест ДО правки.
- Новый метод порта — новый контрактный тест в tests/contracts/.
- Ни один путь, ключ и порог не зашит в код: порог `scrape.max_gone_percent` — в оба
  конфига (config/dev.yaml и config/prod.yaml).
- Все отметки времени в UTC, пути относительные от корня проекта.
- Боевую базу data/listam.sqlite не трогать. На сайт не ходить: только tests/fixtures/.

Запуск тестов — интерпретатором окружения проекта:
.venv/Scripts/python.exe -m pytest -q
Системный python не годится: в нём нет openpyxl.

Когда закончишь: покажи полный вывод pytest, git status и git log --oneline; заполни
«Результат фазы 3» в файле плана и допиши туда стартовый промпт для фазы 4.
```

---

## Фаза 3. `status=gone` и колонка «Снято»

**Зачем:** база, в которой всё вечно `active`, через месяц наполовину состоит из проданных
квартир. Матчинг на M2 будет носить брокеру варианты, которых уже нет.

### Задача 3.1. Пометка снятых после полного обхода

**Файлы:**
- Изменить: `listam/crawler.py` (новая `gone_refusal`, блок пометки в `run_scrape`)
- Изменить: `config/dev.yaml`, `config/prod.yaml`
- Создать: `tests/test_gone.py`

**Интерфейсы:**

```python
def gone_refusal(missing: int, active_total: int, max_percent: float) -> str | None
```

Новый ключ в секции `scrape` обоих конфигов:

```yaml
  max_gone_percent: 10          # больше этой доли пропало с ленты — не помечаем, это сбой
```

- [x] **Шаг 1. Падающие тесты на порог.**

```python
# tests/test_gone.py
from listam.crawler import gone_refusal


def test_a_handful_of_missing_listings_is_just_the_market():
    assert gone_refusal(missing=5, active_total=1000, max_percent=10) is None


def test_half_the_feed_missing_is_a_broken_crawl_not_a_market():
    refusal = gone_refusal(missing=500, active_total=1000, max_percent=10)
    assert "max_gone_percent" in refusal
    assert "500" in refusal


def test_an_empty_base_marks_nothing_and_says_nothing():
    assert gone_refusal(missing=0, active_total=0, max_percent=10) is None
```

- [x] **Шаг 2. Прогнать — падает на импорте.**

```bash
.venv/Scripts/python.exe -m pytest tests/test_gone.py -q
```

- [x] **Шаг 3. Реализовать порог.**

```python
# listam/crawler.py
DEFAULT_MAX_GONE = 10     # доля активных, которая может пропасть с ленты за один обход


def gone_refusal(missing: int, active_total: int, max_percent: float) -> str | None:
    """Причина, по которой снятыми не помечается ничего. None — помечаем.

    С ленты за час уходит десяток объявлений. Если пропала пятая часть базы,
    объяснение не в рынке: обход не дошёл до конца, уехала вёрстка или в разбор
    попала не та страница. Пометить их снятыми — значит выбросить из работы
    тысячи живых квартир, а вернёт их только следующий удачный обход.
    """
    if not missing or not active_total or max_percent <= 0:
        return None
    share = missing / active_total * 100
    if share <= max_percent:
        return None
    return (
        f"снятыми не помечено ничего: с ленты пропало {missing} объявлений "
        f"из {active_total} активных ({share:.1f}%), это больше порога "
        f"scrape.max_gone_percent = {max_percent:.0f}%. Так выглядит оборванный "
        f"обход, а не рынок"
    )
```

- [x] **Шаг 4. Падающие тесты на прогоне.**

Фикстура `project` — копия из `tests/test_crawler.py` с добавкой `"max_gone_percent": 50`
в секцию `scrape`: в фикстуре всего 8 объявлений, и одно — это 12.5%, при пороге 10
не сработала бы ни одна пометка.

```python
def test_a_listing_that_left_the_feed_is_marked_gone(project, tmp_path):
    run_scrape(project)
    page = tmp_path / "pages" / "category-60.html"
    page.write_text(page.read_text(encoding="utf-8").replace("24100001", "99100001"),
                    encoding="utf-8")

    run = run_scrape(project)

    assert run.gone_marked == 1
    database = opened(project)
    left = database.get_listing("24100001")
    database.close()
    assert left.status == "gone"
    assert left.gone_at is not None


def test_the_date_a_listing_was_last_seen_does_not_move_when_it_goes(project, tmp_path):
    run_scrape(project)
    database = opened(project)
    seen_before = database.get_listing("24100001").last_seen
    database.close()

    page = tmp_path / "pages" / "category-60.html"
    page.write_text(page.read_text(encoding="utf-8").replace("24100001", "99100001"),
                    encoding="utf-8")
    run_scrape(project)

    database = opened(project)
    assert database.get_listing("24100001").last_seen == seen_before
    database.close()


def test_a_listing_back_on_the_feed_is_active_again(project, tmp_path):
    page = tmp_path / "pages" / "category-60.html"
    original = page.read_text(encoding="utf-8")
    run_scrape(project)
    page.write_text(original.replace("24100001", "99100001"), encoding="utf-8")
    run_scrape(project)

    page.write_text(original, encoding="utf-8")     # объявление вернулось на ленту
    run_scrape(project)

    database = opened(project)
    back = database.get_listing("24100001")
    database.close()
    assert back.status == "active"
    assert back.gone_at is None


def test_an_incremental_run_marks_nothing_gone(project):
    """Инкрементальный обход видел одну страницу. «Не встретилось» у него
    не значит «снято»."""
    run_scrape(project)

    run = run_scrape(project, fresh=True)

    assert run.gone_marked == 0
    database = opened(project)
    assert all(item.status == "active" for item in database.iter_listings())
    database.close()


def test_a_crawl_cut_by_max_pages_marks_nothing_gone(project):
    run_scrape(project)

    run = run_scrape(project, max_pages=1)

    assert run.gone_marked == 0


def test_a_run_with_errors_marks_nothing_gone(project, tmp_path):
    run_scrape(project)
    (tmp_path / "pages" / "category-60-2.html").unlink()

    run = run_scrape(project)

    assert run.errors >= 1
    assert run.gone_marked == 0


def test_too_many_missing_listings_stop_the_marking(project, tmp_path):
    project.data["scrape"]["max_gone_percent"] = 10
    run_scrape(project)
    page = tmp_path / "pages" / "category-60.html"
    page.write_text(page.read_text(encoding="utf-8").replace("24100001", "99100001"),
                    encoding="utf-8")

    run = run_scrape(project)

    assert run.gone_marked == 0
    assert run.errors >= 1
    assert "max_gone_percent" in run.notes
    database = opened(project)
    assert database.get_listing("24100001").status == "active"
    database.close()
```

- [x] **Шаг 5. Прогнать — падает.**

```bash
.venv/Scripts/python.exe -m pytest tests/test_gone.py -q
```

- [x] **Шаг 6. Вставить пометку в прогон.**

Место — внутри `try`, после всех проверок обхода (недобор страниц, вёрстка, «одна страница»)
и **до** `finally` с журналом и снимком: копия, уезжающая в хранилище, обязана содержать
пометки.

```python
# listam/crawler.py, в run_scrape, после блока проверок
                # Снятыми помечает только полный удачный обход: он один видел
                # всю ленту, и только у него «не встретилось» значит «ушло».
                if mode == "full" and counters.errors == 0 and database is not None:
                    active = database.active_ids()
                    missing = active - seen_ids
                    refusal = gone_refusal(
                        len(missing), len(active),
                        float(config.get("scrape.max_gone_percent", DEFAULT_MAX_GONE) or 0),
                    )
                    if refusal:
                        counters.errors += 1
                        note = f"{note}; {refusal}"
                    elif missing:
                        counters.gone_marked = database.mark_gone(
                            missing, gone_at=datetime.now(timezone.utc)
                        )
                        note = f"{note}; снято с публикации: {counters.gone_marked}"
```

- [x] **Шаг 7. Дописать `max_gone_percent` в оба конфига.**

- [x] **Шаг 8. Прогнать всю батарею.**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Ожидается 348 passed, 12 skipped.

- [x] **Шаг 9. Коммит.**

```bash
git add listam/crawler.py config/dev.yaml config/prod.yaml tests/test_gone.py
git commit -m "feat(crawler): объявление, ушедшее с ленты, помечается снятым"
```

### Задача 3.2. Колонка «Снято» в выгрузке

**Файлы:**
- Изменить: `listam/adapters/exporter_xlsx.py` (`COLUMNS`)
- Изменить: `tests/contracts/test_exporter_contract.py` (строки 160–161)

- [x] **Шаг 1. Падающий тест.**

```python
# tests/contracts/test_exporter_contract.py
def test_a_gone_listing_shows_its_status_and_the_day_it_left(exporter):
    gone = Listing(id="1", url="https://www.list.am/ru/item/1", status="gone",
                   first_seen=NOW, last_seen=NOW,
                   gone_at=datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc))

    sheet = load_workbook(exporter.export([gone])).active
    headers = [cell.value for cell in sheet[1]]

    assert headers[-1] == "Снято"
    assert sheet.cell(row=2, column=len(headers)).value == "2026-09-22 08:00"
    assert sheet.cell(row=2, column=headers.index("Статус") + 1).value == "gone"
```

и правка существующей проверки на месте: `assert len(headers) == 22`,
`assert sheet.auto_filter.ref.startswith("A1:V")`.

- [x] **Шаг 2. Прогнать — падает.**

```bash
.venv/Scripts/python.exe -m pytest tests/contracts/test_exporter_contract.py -q
```

- [x] **Шаг 3. Дописать колонку.**

```python
# listam/adapters/exporter_xlsx.py, в конец COLUMNS
    ("Снято", "gone_at", 18, None),
```

`_present` уже умеет `datetime`: у активных колонка пустая, у снятых — дата.

- [x] **Шаг 4. Прогнать батарею.** Ожидается 349 passed, 12 skipped.

- [x] **Шаг 5. Коммит.**

```bash
git add listam/adapters/exporter_xlsx.py tests/contracts/test_exporter_contract.py
git commit -m "feat(export): колонка «Снято» — 22 колонки, автофильтр A1:V"
```

**Результат фазы 3**

Сделано: `gone_refusal` решает, помечать ли снятых вообще; полный удачный обход сверяет
активных с тем, что встретил на ленте, и помечает пропавшее `status='gone'` с датой
в `listings.gone_at`. Инкрементальный, укороченный `--max-pages`, продолженный `--resume`
и любой обход с ошибками не помечают ничего (решение 5). Порог `scrape.max_gone_percent`
(решение 6) прописан в `config/dev.yaml` и `config/prod.yaml`: пропало больше доли —
пометки нет, прогон получает ошибку и строку в `notes`. Вернувшееся на ленту объявление
снова `active` с пустым `gone_at` (решение 7) — это уже умел апсерт из фазы 1, теперь
на это есть тест прогона. В `.xlsx` добавилась колонка «Снято»: колонок 22, автофильтр `A1:V`.

Батарея: было 339 passed, 12 skipped → стало **350 passed, 12 skipped**
(349 после задачи 3.1, 350 после 3.2).

Коммиты: `adf7ded`, `820b2e1`.

Новые интерфейсы:
- `crawler.gone_refusal(missing, active_total, max_percent) -> str | None`.
- `crawler.DEFAULT_MAX_GONE = 10`.
- Ключ конфига `scrape.max_gone_percent`.
- Колонка выгрузки `("Снято", "gone_at", 18, None)` — последняя в `COLUMNS`.

Новых методов порта фаза 3 не добавила: `active_ids()` и `mark_gone()` пришли с фазой 1,
контрактные тесты на них лежат в `tests/contracts/test_database_contract.py`. Поэтому новых
файлов в `tests/contracts/` нет.

Отклонения от плана (и почему):
1. **Тесты про ушедшее объявление правят вторую страницу фикстуры, а не первую.** `24100001`
   лежит в `category-60-page2.html`; замена его в `category-60.html` не меняла ничего, и
   объявление с ленты не пропадало. В тестах заведена константа
   `FEED_PAGE = "category-60-2.html"`. Та же причина, по которой фаза 2 правила `23987063`
   вместо `24100001`, — только с обратным знаком: там нужна была первая страница, здесь вторая.
2. **350 вместо обещанных планом 349.** Арифметика в плане потеряла один тест: задача 3.1
   добавляет 10 тестов (3 на порог + 7 на прогон), а не 9. Ничего лишнего не добавлено.
3. `README.md` по-прежнему говорит «колонок 21, автофильтр `A1:U`» — правка README стоит
   в фазе 5 (шаг 9), и трогать его раньше времени фаза 3 не стала.

Чего в фазе 3 нет (и не должно быть): команды `changes`, строк про снятые в README.
Боевая база не тронута: она на схеме 3 до фазы 5.

## Стартовый промпт для новой сессии (фаза 4)

Открыть новую сессию **в этой же папке** (`C:\Users\Admin\Downloads\list`) и скопировать целиком:

```markdown
Проект: listam — мониторинг list.am под заявки покупателей. Папка C:\Users\Admin\Downloads\list,
ветка master.

Прочитай перед началом, в этом порядке:
1. docs/superpowers/plans/2026-09-21-m1-delta-and-history.md — план этапа M1, он же твоё задание;
   разделы «Результат фазы 1», «Результат фазы 2» и «Результат фазы 3» — отчёты предыдущих сессий.
2. README.md — как устроен проект и чем он запускается.
3. «list.am → заявки покупателей MVP-спека и роадмап.md» — раздел «Роадмап» (строка M1).

Делаешь ТОЛЬКО фазу 4 (команда `changes` — витрина дельты). Фаза 5 — другая сессия.
Раздел «Принятые решения» в плане не пересматривается.

Исходное состояние: батарея 350 passed, 12 skipped; HEAD — коммит фазы 3 `820b2e1`;
git status чистый; боевая база по-прежнему на схеме 3 — её не трогать.

Что нужно знать про фазы 1–3 (сессия их не видела):
- Схема 4: listings.gone_at; runs.mode | price_changed | gone_marked | stop_reason.
- `last_successful_run()` отдаёт только полный обход без ошибок; `last_run(mode=None)`
  сужает выборку по режиму через IFNULL(mode,'full') — прогоны M0 читаются как полные.
- `Database.active_ids()`, `Database.mark_gone(ids, gone_at)`, `known_ids()`, `iter_listings()`,
  `get_listing(id)` — уже есть; выборки дельты фазы 4 добавляются рядом с ними,
  и на каждый новый метод порта — новый контрактный тест в tests/contracts/.
- `run_scrape(config, *, max_pages, dry_run, allow_shrink, resume, allow_upload_with_errors,
  fresh)`; счётчики `new_listings`, `updated_listings`, `price_changed`, `gone_marked`
  уходят и в журнал, и в возвращённый Run.
- `crawler.run_mode`, `crawler.incremental_stop`, `crawler.gone_refusal` — пороги к ним
  лежат в обоих конфигах: fresh_stop_after_known_pages, fresh_max_pages, max_gone_percent.
- Пометка снятых ставится только при mode == "full" и errors == 0; порог
  `scrape.max_gone_percent` — доля активных, которая может пропасть за один обход.
- `changes` ничего не мигрирует и на сайт не ходит (решение 10): схема младше ожидаемой —
  внятная ошибка и код возврата 1, как у `export`.
- Вставка объявления фильтрует колонки по PRAGMA table_info — модель может опережать базу.
- Проверки версии схемы в тестах идут через `latest_schema_version()`, не числом.
- Выгрузка: 22 колонки, последняя — «Снято» (`gone_at`), автофильтр `A1:V`.
- Фикстура ленты: страница 1 — 6 карточек (23973917 с ценой `$ 162,000`, 24228087, 23598471,
  23311644, 23987063, 99999999 без цены), страница 2 — 24100001, 24100002 и повтор 23973917.
  Цена `290,000` в фикстуре лежит в блоке «Топ объявления» и в разбор НЕ попадает.
- Свои копии фикстуры `project` есть в tests/test_crawler.py, tests/test_crawler_fresh.py
  и tests/test_gone.py — у последней поднят `max_gone_percent: 50` под размер фикстуры
  (8 объявлений, одно пропавшее — это 12.5%).

Правила, которые нельзя нарушать:
- На каждое новое поведение — падающий тест ДО правки.
- Новый метод порта — новый контрактный тест в tests/contracts/.
- Ни один путь, ключ и порог не зашит в код: всё новое — в оба конфига
  (config/dev.yaml и config/prod.yaml).
- Все отметки времени в UTC, пути относительные от корня проекта.
- Боевую базу data/listam.sqlite не трогать. На сайт не ходить: только tests/fixtures/.

Запуск тестов — интерпретатором окружения проекта:
.venv/Scripts/python.exe -m pytest -q
Системный python не годится: в нём нет openpyxl.

Когда закончишь: покажи полный вывод pytest, git status и git log --oneline; заполни
«Результат фазы 4» в файле плана и допиши туда стартовый промпт для фазы 5.
```

---

## Фаза 4. Команда `changes` — витрина дельты

**Зачем:** критерий M1 — «повторный запуск добавляет только новое и фиксирует изменения цен».
Предъявить это сейчас нечем: числа лежат в `runs`, а что именно пришло и что подешевело —
только в SQL. Брокеру нужен список.

### Задача 4.1. Выборки дельты в порту и адаптере

**Файлы:**
- Изменить: `listam/ports/database.py`, `listam/adapters/db_sqlite.py`
- Изменить: `tests/contracts/test_database_contract.py`

**Интерфейсы:**

```python
def listings_first_seen_since(self, since: datetime) -> list[Listing]
def listings_gone_since(self, since: datetime) -> list[Listing]
def price_changes_since(self, since: datetime) -> list[tuple[Listing, float | None, float | None]]
    """(объявление, прежняя цена в $, новая цена в $) — по точкам price_history.

    Первая точка объявления изменением не считается: у неё нет прежней цены,
    это появление, а не смена цены.
    """
```

- [x] **Шаг 1. Падающие контрактные тесты.**

```python
def test_new_listings_are_the_ones_first_seen_after_the_mark(database):
    database.upsert_listing(listing("1"), seen_at=NOW)
    database.upsert_listing(listing("2"), seen_at=EVEN_LATER)

    fresh = database.listings_first_seen_since(LATER)

    assert [item.id for item in fresh] == ["2"]


def test_a_price_change_carries_the_price_it_had_before(database):
    database.upsert_listing(listing("1", price_raw="100,000", price_usd=100_000.0), seen_at=NOW)
    database.upsert_listing(listing("1", price_raw="90,000", price_usd=90_000.0), seen_at=LATER)

    changes = database.price_changes_since(LATER)

    assert len(changes) == 1
    item, was, now = changes[0]
    assert (item.id, was, now) == ("1", 100_000.0, 90_000.0)


def test_the_first_point_of_a_listing_is_not_a_price_change(database):
    database.upsert_listing(listing("1", price_usd=100_000.0), seen_at=LATER)

    assert database.price_changes_since(NOW) == []


def test_gone_listings_are_the_ones_marked_after_the_mark(database):
    database.upsert_listing(listing("1"), seen_at=NOW)
    database.upsert_listing(listing("2"), seen_at=NOW)
    database.mark_gone({"1"}, gone_at=NOW)
    database.mark_gone({"2"}, gone_at=EVEN_LATER)

    assert [item.id for item in database.listings_gone_since(LATER)] == ["2"]
```

`listing(...)` — тот же помощник файла, что у тестов задачи 1.2; если он не принимает
`price_raw`/`price_usd`, дописать их туда параметрами со значением `None` по умолчанию.

- [x] **Шаг 2. Прогнать — падает.**

```bash
.venv/Scripts/python.exe -m pytest tests/contracts/test_database_contract.py -q
```

- [x] **Шаг 3. Реализовать.**

```python
# listam/adapters/db_sqlite.py

    def listings_first_seen_since(self, since: datetime) -> list[Listing]:
        rows = self.conn.execute(
            "SELECT * FROM listings WHERE first_seen >= ? ORDER BY first_seen DESC, id DESC",
            (to_iso(since),),
        )
        return [_row_to_listing(row) for row in rows]

    def listings_gone_since(self, since: datetime) -> list[Listing]:
        rows = self.conn.execute(
            "SELECT * FROM listings WHERE gone_at >= ? ORDER BY gone_at DESC, id DESC",
            (to_iso(since),),
        )
        return [_row_to_listing(row) for row in rows]

    def price_changes_since(self, since: datetime):
        """Точки истории после отметки вместе с ценой, которая была до них.

        Прежняя цена берётся подзапросом по той же карточке: у первой точки
        её нет, и такая строка отбрасывается — это появление объявления,
        а не смена цены.
        """
        rows = self.conn.execute(
            "SELECT h.listing_id AS listing_id, h.price_usd AS new_price, "
            "       (SELECT p.price_usd FROM price_history p "
            "         WHERE p.listing_id = h.listing_id AND p.id < h.id "
            "         ORDER BY p.id DESC LIMIT 1) AS old_price, "
            "       EXISTS (SELECT 1 FROM price_history p "
            "                WHERE p.listing_id = h.listing_id AND p.id < h.id) AS has_previous "
            "  FROM price_history h WHERE h.seen_at >= ? ORDER BY h.id DESC",
            (to_iso(since),),
        ).fetchall()
        changes = []
        for row in rows:
            if not row["has_previous"]:
                continue          # первая точка — это появление, а не смена цены
            item = self.get_listing(row["listing_id"])
            if item is not None:
                changes.append((item, row["old_price"], row["new_price"]))
        return changes
```

плюс три абстрактных метода в `listam/ports/database.py` с теми же объяснениями.

- [x] **Шаг 4. Прогнать батарею.** Ожидается 354 passed, 12 skipped (350 плюс четыре теста шага 1;
  в плане стояло 353 — арифметика разошлась на один).

- [x] **Шаг 5. Коммит.**

```bash
git add listam/ports/database.py listam/adapters/db_sqlite.py tests/contracts/test_database_contract.py
git commit -m "feat(db): выборки дельты — новое, сменившее цену, снятое"
```

### Задача 4.2. Команда `changes`

**Файлы:**
- Создать: `listam/changes.py`
- Изменить: `listam/cli.py` (подкоманда), `README.md` (таблица команд)
- Создать: `tests/test_changes.py`

**Интерфейсы:**

```python
# listam/changes.py
@dataclass
class PriceMove:
    listing: Listing
    was: float | None
    now: float | None
    @property
    def percent(self) -> float | None: ...

@dataclass
class Changes:
    since: datetime | None
    since_note: str
    new: list[Listing]
    moved: list[PriceMove]
    gone: list[Listing]
    errors: int = 0
    notes: str | None = None

def since_point(database, hours: float | None) -> tuple[datetime, str]
def run_changes(config, *, hours: float | None = None) -> Changes
def render(changes: Changes, limit: int) -> str
```

Командная строка:

```
python -m listam changes             # с начала последнего завершённого прогона
python -m listam changes --hours 24  # за сутки
python -m listam changes --limit 20  # не больше 20 строк в каждом разделе
```

- [x] **Шаг 1. Падающие тесты.**

Фикстура `project` — копия из `tests/test_crawler.py` с `"max_gone_percent": 50`.

```python
# tests/test_changes.py
def test_changes_since_the_last_run_show_what_it_brought(project, tmp_path):
    run_scrape(project)
    page = tmp_path / "pages" / "category-60.html"
    page.write_text(page.read_text(encoding="utf-8")
                    .replace("24100001", "99100001")
                    .replace("290,000", "275,000"), encoding="utf-8")
    run_scrape(project)

    report = run_changes(project)

    assert [item.id for item in report.new] == ["99100001"]
    assert [move.listing.id for move in report.moved] == ["24221707"]
    assert (report.moved[0].was, report.moved[0].now) == (290_000.0, 275_000.0)
    assert [item.id for item in report.gone] == ["24100001"]


def test_changes_over_a_quiet_period_are_empty_and_say_so(project):
    run_scrape(project)

    report = run_changes(project, hours=0.0001)

    assert (report.new, report.moved, report.gone) == ([], [], [])
    assert "изменений нет" in render(report, limit=50)


def test_render_shows_the_direction_of_the_price_move(project, tmp_path):
    run_scrape(project)
    page = tmp_path / "pages" / "category-60.html"
    page.write_text(page.read_text(encoding="utf-8").replace("290,000", "275,000"),
                    encoding="utf-8")
    run_scrape(project)

    text = render(run_changes(project), limit=50)

    assert "было $290,000 → стало $275,000" in text
    assert "−5.2%" in text


def test_changes_do_not_migrate_the_database(project, tmp_path):
    """`changes` читает базу, а не чинит её: молчаливая правка общей базы
    по дороге к списку — это то же, за что в M0 отучили `export`."""
    run_scrape(project)
    _roll_schema_back_to(database_path(project), 3)

    report = run_changes(project)

    assert report.errors == 1
    assert "схема базы 3" in report.notes
```

Помощник — в этом же файле:

```python
def _roll_schema_back_to(path, version: int) -> None:
    """Делает вид, что база отстала на версию: сносит отметки старше нужной.

    Колонки при этом остаются — проверяем именно отказ по версии, а не падение
    запроса. Так же ведёт себя настоящая база, которую не домигрировали.
    """
    connection = sqlite3.connect(path)
    try:
        connection.execute("DELETE FROM schema_version WHERE version > ?", (version,))
        connection.commit()
    finally:
        connection.close()
```

- [x] **Шаг 2. Прогнать — падает на импорте.**

```bash
.venv/Scripts/python.exe -m pytest tests/test_changes.py -q
```

- [x] **Шаг 3. Реализовать `listam/changes.py`.**

```python
"""Что принёс последний прогон: новое, сменившее цену, снятое.

Команда ничего не узнаёт о рынке и никуда не ходит: она читает базу и
складывает из неё список для человека. Базу она не чинит — схема младше той,
которую ждёт код, это ошибка, а не повод молча накатить миграции.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from listam.adapters.db_sqlite import latest_schema_version
from listam.domain.models import Listing
from listam.wiring import build_database, build_storage, database_path


@dataclass
class PriceMove:
    listing: Listing
    was: float | None
    now: float | None

    @property
    def percent(self) -> float | None:
        """На сколько процентов сдвинулась цена. Прежней цены нет — и процента нет."""
        if not self.was or self.now is None:
            return None
        return (self.now - self.was) / self.was * 100


@dataclass
class Changes:
    since: datetime | None = None
    since_note: str = ""
    new: list[Listing] = field(default_factory=list)
    moved: list[PriceMove] = field(default_factory=list)
    gone: list[Listing] = field(default_factory=list)
    errors: int = 0
    notes: str | None = None


def since_point(database, hours: float | None) -> tuple[datetime, str]:
    """С какого момента считаем изменения и как это объяснить человеку.

    По умолчанию — начало последнего завершённого прогона: «что принёс
    последний обход» и есть тот вопрос, ради которого команду зовут.
    """
    if hours is not None:
        mark = datetime.now(timezone.utc) - timedelta(hours=float(hours))
        return mark, f"за последние {hours:g} ч (с {mark:%Y-%m-%d %H:%M} UTC)"
    last = database.last_run()
    if last is None or last.started_at is None:
        mark = datetime.now(timezone.utc) - timedelta(days=1)
        return mark, "прогонов в журнале нет — показываю за сутки"
    return last.started_at, (
        f"с начала прогона {last.id} ({last.started_at:%Y-%m-%d %H:%M} UTC, "
        f"режим {last.mode or 'full'})"
    )
```

Дальше — сама команда. Подготовка повторяет `_export` в `cli.py`: база берётся из хранилища,
если локальной нет, и схема сверяется до чтения. Замок не берётся: команда только читает.

```python
def run_changes(config, *, hours: float | None = None) -> Changes:
    storage = build_storage(config)
    local_db = database_path(config)
    remote_name = config.get("storage.db_filename", "listam.sqlite")
    if not local_db.exists():
        storage.download(remote_name, local_db)

    database = build_database(config)
    database.connect()
    try:
        required = latest_schema_version()
        version = database.schema_version()
        if version < required:
            return Changes(
                errors=1,
                notes=(
                    f"Изменения не показаны: схема базы {version}, а код ждёт {required}. "
                    f"Команда ничего не мигрирует — накати миграции: python -m listam recheck"
                ),
            )
        since, since_note = since_point(database, hours)
        return Changes(
            since=since,
            since_note=since_note,
            new=database.listings_first_seen_since(since),
            moved=[
                PriceMove(listing=item, was=was, now=now)
                for item, was, now in database.price_changes_since(since)
            ],
            gone=database.listings_gone_since(since),
        )
    finally:
        database.close()
```

`render` печатает шапку и три раздела; знаки `+` (новое), `$` (цена), `−` (снято);
строк в разделе — не больше `limit`, обрезано — строка «…и ещё N»:

```
Изменения с начала прогона 5 (2026-09-21 16:40 UTC, режим fresh)
Новых: 37   Сменили цену: 4   Снято: 12

Новые
+ 24221707  Кентрон   3 ком., 92 м²   $215,000   2337 $/м²   https://www.list.am/ru/item/24221707
Цены
$ 24100001  Арабкир   было $100,000 → стало $90,000  (−10.0%)  https://www.list.am/ru/item/24100001
Снято
− 24076812  Аван      висело 13 дней, последняя цена $140,000   https://www.list.am/ru/item/24076812
```

Пусто во всех трёх разделах — одна строка «изменений нет».

Как форматируются числа (от этого зависят тесты шага 1): деньги — `f"${value:,.0f}"`
(`$290,000`), процент — `f"{value:+.1f}%".replace("-", "−")`, то есть
со знаком и с настоящим минусом U+2212 (`−5.2%`); дней на ленте у снятого —
`(gone_at - first_seen).days`. Цены нет (`None`) — на её месте прочерк `—`, а не `None`.

- [x] **Шаг 4. Подключить команду в `listam/cli.py`.**

```python
    changes = commands.add_parser("changes", help="что принёс последний прогон")
    changes.add_argument("--hours", type=float,
                         help="за сколько часов считать (по умолчанию — с начала прошлого прогона)")
    changes.add_argument("--limit", type=int, default=50,
                         help="сколько строк показывать в каждом разделе")
```

```python
    if args.command == "changes":
        from listam.changes import render, run_changes

        report = run_changes(config, hours=args.hours)
        if report.errors:
            print(report.notes, file=sys.stderr)
            return 1
        print(render(report, limit=args.limit))
        return 0
```

- [x] **Шаг 5. Дописать строку в таблицу команд README.**

- [x] **Шаг 6. Прогнать всю батарею.** Ожидается 363 passed, 12 skipped
  (в плане стояло 357: к шести тестам `tests/test_changes.py` добавились три на команду
  в `tests/test_cli.py`, а батарея после задачи 4.1 была 354, а не 353).

- [x] **Шаг 7. Коммит.**

```bash
git add listam/changes.py listam/cli.py README.md tests/test_changes.py
git commit -m "feat(cli): changes — новое, сменившее цену и снятое с прошлого прогона"
```

**Результат фазы 4**

Сделано 21.09.2026. Батарея — **363 passed, 12 skipped**, дерево чистое.
Коммиты фазы: `4f284a9` (выборки дельты), `cefac0f` (команда `changes`) и этот отчёт
поверх них.

Что появилось:

- **Три выборки в порту и адаптере.** `listings_first_seen_since(since)` — новое (мерка
  `first_seen`, а не `last_seen`: встреченное заново новым не стало),
  `listings_gone_since(since)` — снятое (мерка `gone_at`, она ставится один раз),
  `price_changes_since(since)` — точки `price_history` после отметки вместе с ценой,
  которая была до них. Прежняя цена берётся подзапросом по той же карточке; у первой
  точки её нет, и такая строка отбрасывается: появление объявления сменой цены не считается.
  На каждый метод — контрактный тест в `tests/contracts/test_database_contract.py`.
- **`listam/changes.py`.** `run_changes(config, *, hours=None)` собирает `Changes`
  (`since`, `since_note`, `new`, `moved`, `gone`, `errors`, `notes`); `PriceMove.percent`
  считает сдвиг в процентах. `since_point(database, hours, fallback_hours)` отвечает,
  с какого момента считаем: по умолчанию — начало последнего прогона из журнала, с
  `--hours` — окно в часах, при пустом журнале — `changes.fallback_hours` и строка
  «прогонов в журнале нет» (пустой список не должен выглядеть как тишина на рынке).
  `render(changes, limit)` печатает шапку, счётчики и три раздела: `+` новое,
  `$` цены, `−` снятое. Деньги — `$162,000`, процент — со знаком и настоящим минусом
  (`−5.6%`), отсутствующее значение — прочерк `—`, а не `None`. Пусто во всех
  трёх — одна строка «изменений нет». Раздел режется до `limit` строк, обрезано —
  строка «…и ещё N».
- **Подкоманда `changes` в `listam/cli.py`** с `--hours` и `--limit`; строка в таблице
  команд README и абзац про то, по каким меркам считается дельта.
- **Решение 10 соблюдено:** замок не берётся, на сайт команда не ходит, миграций не
  накатывает. Схема младше `latest_schema_version()` — внятная ошибка в stderr и код
  возврата 1, схема при этом не тронута (тесты `tests/test_changes.py` и `tests/test_cli.py`).

Конфиги: в `config/dev.yaml` и `config/prod.yaml` появилась секция `changes`
с `limit: 50` и `fallback_hours: 24`. Числа в командной строке нет: `--limit` без
значения берёт порог из конфига.

Что разошлось с планом и почему:

- **Тест «тихого периода» переписан.** План звал `run_changes(project, hours=0.0001)`
  сразу после прогона и ждал пустоты, но 0.0001 ч — это 0.36 секунды, а прогон по
  фикстуре укладывается в доли секунды: окно накрывало его целиком, и тест падал.
  Вместо него — `test_a_repeat_run_over_an_unchanged_feed_shows_no_changes`: два прогона
  по неизменной ленте, дельта пуста, `render` печатает «изменений нет». Это и есть
  критерий M1 с другой стороны. Окно в часах проверяется отдельным тестом
  `test_the_window_can_be_asked_for_in_hours`.
- **Идентификаторы и цены в тестах взяты из настоящей фикстуры.** План ссылался на
  объявление `24221707` и цену `290,000` на первой странице — оба лежат в блоке
  «Топ объявления» и в разбор не попадают (`24221707` в базе нет вовсе). Смена цены
  изображается правкой `162,000` → `153,000` у объявления `23973917` на первой странице,
  «пришло новое / пропало старое» — подменой `24100001` на второй, как в `tests/test_gone.py`.
  Отсюда и `−5.6%` в тесте на `render` вместо `−5.2%` из плана.
- **Версия схемы в тестах — через `latest_schema_version()`**, а не число 3: откат идёт
  на «последняя минус один», и сообщение сверяется с тем же числом.
- **Добавлены три теста командной строки** (`tests/test_cli.py`): подкоманда печатает то,
  что принёс прогон; лимит раздела берётся из конфига; отставшая схема даёт код 1 и
  схему не трогает. Перед правкой `cli.py` проверено, что все три падают.
- **Числа «ожидается N passed» поправлены:** 354 после задачи 4.1 (350 + 4) и 363 после
  задачи 4.2 (354 + 6 + 3). В плане стояли 353 и 357.

Чего фаза не делала: боевую базу `data/listam.sqlite` не трогала (она по-прежнему на
схеме 3), на сайт не ходила, `.xlsx` не переделывала — это фаза 5.

---

## Стартовый промпт для новой сессии (фаза 5)

Открыть новую сессию **в этой же папке** (`C:\Users\Admin\Downloads\list`) и скопировать целиком:

```markdown
Проект: listam — мониторинг list.am под заявки покупателей. Папка C:\Users\Admin\Downloads\list,
ветка master.

Прочитай перед началом, в этом порядке:
1. docs/superpowers/plans/2026-09-21-m1-delta-and-history.md — план этапа M1, он же твоё задание;
   разделы «Результат фазы 1»…«Результат фазы 4» — отчёты предыдущих сессий.
2. README.md — как устроен проект и чем он запускается.
3. «list.am → заявки покупателей MVP-спека и роадмап.md» — раздел «Роадмап» (строка M1).

Делаешь фазу 5 — последнюю: боевая база, живой сайт, документация. Ею M1 закрывается.
Раздел «Принятые решения» в плане не пересматривается.

Исходное состояние: батарея 363 passed, 12 skipped; git status чистый; HEAD — коммит
«docs: план M1 — фаза 4 закрыта, отчёт и промпт для фазы 5», последний в ветке
(под ним `cefac0f` — команда `changes`). Боевая база data/listam.sqlite на схеме 3,
20 569 объявлений, последний прогон — M0 от 21.09 09:25 UTC.

Это единственная фаза, которая трогает боевую базу и ходит на живой сайт. Копия базы
снимается ПЕРВЫМ шагом, до всего остального. Обход идёт через браузер
(scrape.kind: playwright, headless: false) — окно Chromium должно открыться;
полный обход по 215 страницам с паузой 1.5 с идёт порядка 10–15 минут, это нормально.

Что нужно знать про фазы 1–4 (сессия их не видела):
- Схема 4: listings.gone_at; runs.mode | price_changed | gone_marked | stop_reason.
  Миграции накатывает `recheck` — ни `scrape`, ни `export`, ни `changes` этого не делают.
- `scrape --fresh` — инкрементальный обход: идёт с головы ленты и встаёт после
  fresh_stop_after_known_pages (2) страниц подряд без новых ID; потолок fresh_max_pages (20),
  упёрлись в него — это ошибка прогона, а не успех. `--fresh` и `--resume` вместе дают
  код возврата 2. Меркой полноты (`last_successful_run`) считается только mode == "full".
- Пометка снятых ставится только при mode == "full" и errors == 0, с порогом
  scrape.max_gone_percent (боевое значение 10). Отказал порог — НЕ поднимать его,
  а разбираться: так выглядит оборванный обход или уехавшая вёрстка.
- Вернувшееся на ленту объявление снова active, gone_at = NULL, first_seen не трогается.
- Выгрузка: 22 колонки, последняя — «Снято» (`gone_at`), автофильтр A1:V.
- `python -m listam changes` печатает новое, сменившее цену и снятое с начала последнего
  прогона; есть `--hours N` и `--limit N` (по умолчанию — `changes.limit` из конфига).
  Команда не берёт замок, не ходит на сайт и ничего не мигрирует: схема младше
  ожидаемой — внятная ошибка и код возврата 1.
- Пороги фаз 1–4 лежат в обоих конфигах: fresh_stop_after_known_pages, fresh_max_pages,
  max_gone_percent, changes.limit, changes.fallback_hours.

Правила, которые нельзя нарушать:
- На каждое новое поведение — падающий тест ДО правки. (В этой фазе нового кода почти нет:
  если он всё же понадобился — сначала тест.)
- Ни один путь, ключ и порог не зашит в код: всё новое — в оба конфига
  (config/dev.yaml и config/prod.yaml).
- Все отметки времени в UTC, пути относительные от корня проекта.
- Пороги под результат не подгоняются. Не сошлось — это находка для отчёта, а не повод
  поправить конфиг.
- Числа в README должны совпадать с тем, что реально в базе после прогонов.

Запуск тестов — интерпретатором окружения проекта:
.venv/Scripts/python.exe -m pytest -q
Системный python не годится: в нём нет openpyxl.

Шаг 6 плана ждёт час между полным и инкрементальным обходом — если столько ждать нельзя,
скажи об этом и предложи, чем заменить, а не пропускай шаг молча.

Когда закончишь: покажи полный вывод pytest, git status и git log --oneline; заполни
«Результат фазы 5» и контрольный список приёмки M1 в файле плана, обнови README
и допиши стартовый промпт для сессии M2.
```

---

## Фаза 5. Боевая база, живой сайт и документация

**Зачем:** до этой фазы всё проверено на фикстурах. M1 закрыт тогда, когда инкрементальный
прогон отработал на живой ленте, снятые помечены в настоящей базе, а README не врёт.

**Осторожно:** это единственная фаза, которая трогает `data/listam.sqlite` и ходит на сайт.
Копия базы снимается до всего остального.

- [ ] **Шаг 1. Снять копию боевой базы.**

```bash
cp data/listam.sqlite "data/listam-before-m1-$(date -u +%Y%m%d-%H%M%S).sqlite"
```

- [ ] **Шаг 2. Довести схему до 4.** Миграции накатывает `recheck` — он же пересчитает
  пометки, если правила менялись.

```bash
.venv/Scripts/python.exe -m listam recheck
```

Ожидается: строк 20 569, ошибок 0. Проверить глазами: `schema_version = 4`, колонка
`gone_at` на месте и пустая у всех.

- [ ] **Шаг 3. Полный обход.** Первый после M0: помечает снятым всё, что ушло с ленты
  с 21.09 09:25 UTC.

```bash
.venv/Scripts/python.exe -m listam scrape
```

Записать в отчёт: страниц, карточек, новых, сменивших цену, снятых, ошибок, `stop_reason`.
Если пометка снятых отказала по порогу — **не поднимать порог**, а разобраться: это либо
оборванный обход, либо лента отдала не то.

- [ ] **Шаг 4. Показать дельту.**

```bash
.venv/Scripts/python.exe -m listam changes
```

- [ ] **Шаг 5. Проверить, что голова ленты сортируется по свежести.** Условие остановки
  инкрементального обхода держится на этом. Взять 5 объявлений с первой страницы и 5
  со сто седьмой, сверить `first_seen` в базе: у первой страницы они должны быть
  сегодняшними. Расхождение — повод пересмотреть решение 1 этого плана и написать об этом
  в отчёте, а не молча поднять порог.

- [ ] **Шаг 6. Через час — инкрементальный обход.**

```bash
.venv/Scripts/python.exe -m listam scrape --fresh
```

Ожидается: 2–4 страницы, десяток-другой новых, ошибок 0, `stop_reason` — «2 страниц подряд
без новых объявлений». Это и есть критерий готовности M1: повторный запуск добавил только новое.

- [ ] **Шаг 7. Ещё раз дельта и выгрузка.**

```bash
.venv/Scripts/python.exe -m listam changes
.venv/Scripts/python.exe -m listam export
```

Проверить в `.xlsx`: 22 колонки, автофильтр `A1:V`, «Статус» = `gone` и «Снято» непусто
ровно у помеченных строк.

- [ ] **Шаг 8. Прогнать контрольный список приёмки** (таблица ниже) целиком, вывод показать
  в сессии.

- [ ] **Шаг 9. Обновить README.**
  - Таблица команд: `scrape --fresh`, `changes`, `changes --hours N`, `changes --limit N`.
  - Раздел про устройство прогона: пункт про инкрементальный обход (решения 1, 2, 3)
    и пункт про снятые объявления (решения 5, 6, 7, 8).
  - Числа: колонок 22 и автофильтр `A1:V`, состояние базы после полного и инкрементального
    прогонов, размер батареи тестов.
  - Короткий раздел «Как запускать по расписанию»: инкрементальный прогон раз в час
    (`schtasks` на Windows, `cron` на сервере под `xvfb-run`), полный обход — раз в сутки.
    Замок прогона уже не даёт им наложиться.
  - Шапку: M1 закрыт, дальше M2 — заявки и матчинг.

- [ ] **Шаг 10. Заполнить в этом файле «Результат фазы 5» и контрольный список,**
  дописать стартовый промпт для сессии M2.

- [ ] **Шаг 11. Коммиты и чистое дерево.**

```bash
git status --short
git add -A
git commit -m "docs: M1 закрыт — инкрементальный прогон, снятые объявления, дельта"
```

**Результат фазы 5**

_(заполняет сессия фазы 5)_

---

## Критерий готовности M1

Из роадмапа: *повторный запуск добавляет только новое и фиксирует изменения цен*.
Предъявляется так: полный обход на живой ленте; через час `scrape --fresh`, который прошёл
2–4 страницы вместо 215 и принёс только новые объявления; `changes`, который показывает это
списком; `.xlsx` с непустой колонкой «Снято»; чистое рабочее дерево.

## Контрольный список приёмки M1

Прогоняется целиком в фазе 5, вывод показывается в сессии.

| № | Проверка | Как проверяем | Ожидаем |
| --- | --- | --- | --- |
| 1 | Миграция на живых данных | `recheck` на боевой базе (копия снята) | схема 4, 20 569 строк на месте, `gone_at` пуст |
| 2 | Инкрементальный обход короткий | `scrape --fresh` после полного | 2–4 страницы, ошибок 0, `stop_reason` про страницы без новых |
| 3 | Инкрементальный обход приносит новое | `changes` после `--fresh` | список новых непуст, все — сегодняшние |
| 4 | Потолок — это сбой | `scrape --fresh` с `fresh_max_pages: 1` на временной базе | ошибок 1, в журнале про потолок |
| 5 | `--fresh` не мерка полноты | после `--fresh` оборвать полный обход | в ноте «прошлый удачный прогон прошёл 215» |
| 6 | `--fresh` и `--resume` | `scrape --fresh --resume` | код 2, внятный отказ |
| 7 | Снятые помечаются | полный обход на живой ленте | `gone_marked > 0`, у всех `gone_at`, `last_seen` не сдвинулся |
| 8 | Снятое возвращается | пометить снятой карточку с первой страницы, `scrape --fresh` | `status=active`, `gone_at` пуст, `first_seen` прежний |
| 9 | Оборванный обход не снимает | обход с `expected_pages_min` выше факта | `gone_marked = 0`, ошибка в журнале |
| 10 | Порог снятия | временная база, пропало больше 10% | не помечено ничего, в ноте `max_gone_percent` |
| 11 | Цены фиксируются | `changes` после прогона со сменой цены | строка «было → стало» и процент |
| 12 | `changes` не мигрирует | база на схеме 3 | код 1, «схема базы 3, а код ждёт 4», схема осталась 3 |
| 13 | Выгрузка | `export` после пометок | 22 колонки, `A1:V`, «Снято» непусто ровно у снятых |
| 14 | Журнал | `select mode, pages_fetched, new_listings, price_changed, gone_marked, stop_reason from runs` | у каждого прогона режим и причина остановки |
| 15 | Числа в документах | README против кода и базы | колонки, счётчики и батарея сходятся |

## Что в M1 не входит

- Матчинг, заявки, кластеры, `requests`/`matches` — это M2.
- Уведомления о новых объявлениях — M3. `changes` печатает в терминал, и на M1 этого достаточно.
- Телефоны и `contacts` — M4.
- Курсы EUR и RUB по-прежнему не добираются: 57 строк живут с суммой в валюте оригинала.
- Запуск по расписанию — только раздел в README, без кода: планировщик живёт в системе,
  а не в инструменте.

---

## Стартовый промпт для новой сессии (фаза 1)

Открыть новую сессию **в этой же папке** (`C:\Users\Admin\Downloads\list`) и скопировать целиком:

```markdown
Проект: listam — мониторинг list.am под заявки покупателей. Папка C:\Users\Admin\Downloads\list,
ветка master.

Прочитай перед началом, в этом порядке:
1. docs/superpowers/plans/2026-09-21-m1-delta-and-history.md — план этапа M1 по фазам, он же твоё задание.
2. README.md — как устроен проект и чем он запускается.
3. «list.am → заявки покупателей MVP-спека и роадмап.md» — спека, раздел «Роадмап» (строка M1)
   и «Оба направления». Раздел про переносимость обязателен к исполнению.
4. docs/superpowers/plans/2026-09-21-m0-qa-fixes.md — чем кончился M0 и какие решения уже приняты.

Делаешь ТОЛЬКО фазу 1 (схема 004, модель, порт, режим и счётчики прогона). Фазы 2–5 — другие
сессии, в них не лезь. Раздел «Принятые решения» в плане — это уже принятые решения,
их не пересматривать.

Исходное состояние: батарея 312 passed, 12 skipped; HEAD a2d47be; git status чистый;
боевая база на схеме 3, 20 569 объявлений, все active.

Правила, которые нельзя нарушать:
- Схема базы меняется только новой версионированной миграцией в listam/migrations/.
- Новый метод порта — это новый контрактный тест в tests/contracts/.
- На каждое новое поведение — падающий тест ДО правки.
- Ни один путь, ключ, идентификатор и имя адаптера не зашит в код: внешнее — за портом,
  выбор — в конфиге, секреты — только в .env, подключение — в listam/wiring.py.
- Все отметки времени в UTC. Пути относительные от корня проекта.
- Боевую базу data/listam.sqlite в этой фазе не трогать вообще: она мигрируется в фазе 5.
- На сайт в этой фазе не ходить.

Запуск тестов — интерпретатором окружения проекта:
.venv/Scripts/python.exe -m pytest -q
Системный python не годится: в нём нет openpyxl.

Когда закончишь: покажи полный вывод pytest, git status и git log --oneline; заполни в файле
плана раздел «Результат фазы 1» тем же форматом, что у результатов фаз в плане
2026-09-21-m0-qa-fixes.md, и допиши туда же стартовый промпт для фазы 2 — со сводкой того,
что сделано, и того, что следующей сессии нужно знать про изменившиеся интерфейсы.
```
