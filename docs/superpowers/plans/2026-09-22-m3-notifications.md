# M3. Уведомления и дайджест: план реализации

> **Для агента-исполнителя:** одна фаза — одна сессия. В начале сессии читаешь
> «Global Constraints», «Карта файлов», «Результат фазы N−1» и свою фазу; чужие
> фазы не трогаешь. Рядом лежит спека —
> `docs/superpowers/specs/2026-09-22-m3-notifications-design.md`, решения 1–12
> в фазах не пересматриваются. В конце сессии дописываешь в этот файл раздел
> «Результат фазы N» и стартовый промпт для следующей фазы, затем делаешь коммит.
> Шаги помечены `- [ ]` — отмечай по ходу. Исполнять план помогает скилл
> `superpowers:executing-plans` или `superpowers:subagent-driven-development`.

**Цель:** брокер получает в Telegram «что нового со вчера» по своим заявкам —
новые варианты, подешевевшие и вернувшиеся, — а повторный запуск не шлёт то же
самое второй раз.

**Архитектура:** ничего не переписывается заново. У матча появляется отметка
воскресения (`revived_at`), у базы — журнал отправок (`notifications`), у порта —
выборка «что тронулось с отметки», у домена — классификация события. Команда
`notify` встаёт на тот же каркас `runner.working_session`, что и четыре
существующие, пятой копии блока не будет. Витрина получает потолок, доведённый
до выборки, и счётчик отдельно от строк.

**Стек:** Python 3.12, SQLite, pytest, `requests` (уже в `requirements.txt`),
openpyxl, PyYAML. В боевой путь новых зависимостей план не вводит; `playwright` появляется только как **dev-зависимость** живой проверки
фазы 5 (задача 5.4) и живёт в отдельном `requirements-dev.txt`.

**Исходное состояние (снято 22.09.2026):**

| Что | Значение |
| --- | --- |
| HEAD | `dbd4716` (спека M3), дерево чистое |
| Батарея | **687 passed, 18 skipped** |
| Схема базы | 9 (миграции 001–009) |
| Боевые числа (срез ленты 21.09.2026) | 20 826 объявлений, 20 619 активных, 14 355 кластеров, 51 заявка, 51 117 матчей, из них 35 572 горячих |
| Порт `Notifier` | `send(text)`; `none` и `stdout` есть, `telegram` нет |
| `.env` | на 22.09 заведены пустыми; заполнены и проверены позже — раздел «Ключи Telegram» |
| Боевой файл базы | недоступен: `GDRIVE_FOLDER` и `GDRIVE_CREDENTIALS_FILE` пусты |

---

## Global Constraints (нарушать нельзя)

Переносятся из плана QA-ужесточения и дополняются тремя правилами, добытыми
его приёмкой.

- Схема базы меняется **только** новой версионированной миграцией в
  `listam/migrations/`. В этом плане миграция одна — **010** (фаза 2). Больше
  никаких: понадобилась вторая — это находка в отчёт фазы, а не файл `011`.
- Новый метод порта — это новый контрактный тест в `tests/contracts/`.
- Ни один путь, ключ, идентификатор и имя адаптера не зашит в код: внешнее — за
  портом, выбор — в конфиге, секреты — только в `.env`, подключение —
  в `listam/wiring.py`.
- Все отметки времени в UTC (`to_iso`/`from_iso` из
  `listam/adapters/db_sqlite.py`), пути относительные от корня проекта.
- **Пороги в конфиге не поднимаются, чтобы тест позеленел.** Порог `hot` равен
  70 и остаётся равным 70: переполнение решается лимитами уведомления, а не
  порогом (решение 5 спеки). Порог, который мешает, — это находка или решение;
  и то и другое записывается в отчёт фазы.
- Ноль в пороге значит «ноль», а не «выключено»; выключается `null`. Читать
  пороги — только через `listam.config.threshold` / `positive` / `score_threshold`.
- Бессмысленное значение — и в командной строке, и в конфиге — отклоняется
  на входе кодом возврата 2, а не истолковывается.
- След звонка (`matches.status`, `matches.reject_reason`) пишет только человек.
  Ни одна правка этого плана его не трогает, включая новую колонку `revived_at`
  и закрытие матчей с причиной.
- **`MATCH_COMPARED` не расширяется.** Сравнение `unchanged`/`updated` — то,
  на чём держится вся механика «нового»: оно уже закрыто тестами фаз 1 и 3
  QA-плана. Новые поля (`revived_at`) пишутся, но в сравнение не входят.
- Тесты — только `.venv/Scripts/python.exe -m pytest -q`. Системный python
  не годится: в нём нет `openpyxl`.
- Любой прогон CLI из скрипта — только с `PYTHONIOENCODING=utf-8`.
- Телефоны продавцов — M4, дашборд — M5. Не трогаем.
- `listam/crawler.py` в этом плане не правится вовсе.
- **Ни одного числа в отчёте фазы без команды, которая его напечатала.**
  Приёмка фазы 8 QA отменила заранее записанное «348 горячих матчей»: живьём
  их 35 572. Числа, взятые из головы, доживают до плана следующего этапа
  и портят его дизайн.
- **Уведомление — действие наружу.** У фазы, которая первой шлёт в Telegram,
  обязан быть режим «покажи, что послал бы» и явный шаг приёмки на живом канале.
  Отправленное нельзя отозвать, и брокер не должен узнать об ошибке из чата
  клиента.
- **Первая фаза — не код, а выборка.** Сначала «что считается событием» с
  числами на боевой базе, и только потом канал: 35 572 горячих матча делают
  любой дизайн, начатый с канала, неверным.
- Числа «ожидается N passed» — арифметика от 687 плюс тесты фазы. Разошлось
  на один-два — не повод подгонять: сверь, что именно добавилось, и поправь
  число в плане.

## Карта файлов

| Файл | Ответственность | Фаза |
| --- | --- | --- |
| `tmp/` (вне репозитория) | скрипты замера и восстановления базы приёмки | 1, 7 |
| `listam/migrations/010_notifications.sql` | схема 9 → 10: журнал отправок и `matches.revived_at` | 2 |
| `listam/ports/database.py` | `match_events_since`, `record_notification`, `last_notification`, `count_matches_alive`; новая форма `retire_matches` | 2, 3, 6 |
| `listam/adapters/db_sqlite.py` | реализация выборки событий, журнала, счётчика; `revived_at` в апсерте | 2, 3, 6 |
| `listam/domain/models.py` | `Match.revived_at`, `Notification` | 2 |
| `listam/domain/events.py` | что считается событием и как режется по потолку | 2 |
| `listam/matches_view.py` | потолок доходит до выборки, счётчик отдельно, срез `--new` | 3 |
| `listam/notifications.py` | окно, сборка сообщения, отправка, запись в журнал | 4 |
| `listam/ports/notifier.py` | `send(text, to=None)`, `describe()`, `NotifyError` | 4 |
| `listam/adapters/notify_telegram.py` | Telegram Bot API поверх `requests` | 5 |
| `tests/live/test_telegram_live.py` | живая отправка, прочитанная в Telegram Web через Playwright | 5, 7 |
| `requirements-dev.txt` | `playwright` — только для живой проверки | 5 |
| `listam/matching.py` | причина закрытия из жёстких критериев | 6 |
| `listam/config.py` | `score_threshold`: балл вне 0…100 — отказ | 6 |
| `listam/doctor.py` | канал уведомлений, пороги вне шкалы, самомиграция | 5, 6 |
| `listam/cli.py` | команда `notify`, флаг `matches --new` | 3, 4 |
| `config/dev.yaml`, `config/prod.yaml` | секция `notify` | 4 |
| `README.md` | уведомления, `matches --new`, самомиграция команд | 4, 6 |

---

## Ключи Telegram: заведены и проверены (22.09.2026, до фазы 5)

Фазе 5 не нужно заводить бота: он есть, ключи лежат в `.env`, канал проверен
живой отправкой. Числа ниже — из вывода команд, не из головы.

| Что | Значение |
| --- | --- |
| Бот | `@ListamTotifybot`, id `8833522087` |
| Чат уведомлений | **личка брокера**, `TELEGRAM_CHAT_ID = 1930501720` (`type: private`, `artuc2020`) |
| `getMe` | `ok: true`, username `ListamTotifybot` |
| `sendMessage` | HTTP 200, `ok: true`, `message_id 7` — сообщение «listam: канал проверен…» лежит в чате |
| `TELEGRAM_WEB_CHAT` | `https://web.telegram.org/k/#@ListamTotifybot` — для браузерной проверки задачи 5.4 |
| `TELEGRAM_LIVE` | **пусто** — живые тесты выключены, включает только явный `TELEGRAM_LIVE=1` |
| `TELEGRAM_SEND_DELAY` | пусто → умолчание 4 с |

Что из этого следует для фазы 5:

- **Токен в репозиторий не попадает и в этом плане не пишется.** Он живёт
  только в `.env`, а `.env` в `.gitignore` (строка 5). В yaml — ссылка
  `${TELEGRAM_BOT_TOKEN}`. Если в выводе команды мелькнул токен — вывод
  в отчёт не идёт.
- **Чат — личка, а не группа.** Плановая рекомендация «отдельная группа для
  проверок» не выполнена сознательно: личка с тестовым ботом клиентских
  сообщений не содержит, а переезд на группу — это одна строка `chat_id`
  и ноль строк кода. Но помни: **каждая живая отправка приходит брокеру
  в личку**, в том числе четыре сообщения контрактного теста.
- **`.env` есть только на этой машине** (`C:\Users\Admin\Downloads\list`).
  Другая машина — `.env` заводится заново из `.env.example`, ключи берутся
  у брокера.
- **Батарея от этого не меняется.** `pytest` сам `.env` не читает: `load_dotenv`
  зовётся внутри `load_config()`, а `skipif` контрактного теста и живых тестов
  считается на сборе, когда `os.environ` ещё пуст. Поэтому контракт `telegram`
  остаётся skip, и четыре сообщения в личку никто не шлёт, пока переменные
  не выставлены в самой команде запуска.

---

# Фаза 1. Сколько событий на самом деле

**Одна сессия. Кода в `listam/` — ноль.** Фаза отвечает числами на вопрос, от
которого зависят все лимиты конфига: сколько событий в сутки даёт боевая база
на 51 заявке и как они распределены по заявкам. Порог `hot: 70` даёт 35 572
горячих матча — но это **остаток**, а не поток. Поток никто не мерил.

Вторая половина фазы — база, на которой этот замер вообще что-то значит.
Приёмка фазы 8 шла на базе, восстановленной из одной выгрузки, и `price_history`
в ней пуст: вид события «подешевел» на такой базе неотличим от «ничего не
произошло». В `out/` лежат **восемь** боевых выгрузок за 20–22 сентября — если
залить их по возрастанию времени, история цен соберётся сама, настоящая.

**Ожидается после фазы:** батарея **687 passed, 18 skipped** — фаза кода не
трогает. Схема базы приёмки 9 (миграций фаза не добавляет).

### Задача 1.1. База с настоящей историей цен

**Файлы:**
- Создать: `tmp/restore_from_exports.py` (вне репозитория, в `.gitignore` уже
  попадает всё, кроме перечисленного; если `tmp/` не игнорируется — клади
  скрипт в папку из переменной `TMPDIR`, в репозиторий он не коммитится)

- [x] **Шаг 1: посмотреть, что есть в `out/`**

```bash
ls -la out/*.xlsx
```

Ожидается восемь файлов вида `listam-20260920-2314.xlsx` … `listam-20260922-0544.xlsx`.
Если файлов меньше — запиши в отчёт, сколько их на самом деле: дальше от их числа
зависит, сколько точек истории цен соберётся.

- [x] **Шаг 2: написать восстановление**

```python
# tmp/restore_from_exports.py
"""Боевая база из выгрузок .xlsx: по одной на срез ленты, в порядке времени.

Каждая выгрузка — снимок ленты. Заливая их по возрастанию времени через
`upsert_listing`, мы получаем не только объявления, но и `price_history`:
точка истории ставится при каждой записи, а смена цены между срезами —
это ровно то событие, ради которого пишется дайджест.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import load_workbook

from listam.adapters.db_sqlite import SqliteDatabase
from listam.domain.models import Listing

# Заголовок листа «Объявления» → поле Listing. Порядок не важен: колонки
# ищутся по именам шапки, как и в источнике заявок.
COLUMNS = {
    "ID": "id", "Ссылка": "url", "Заголовок": "title", "Район": "district",
    "Улица": "street", "Цена, $": "price_usd", "Цена, ֏": "price_amd",
    "Цена как на сайте": "price_raw", "Цена в валюте": "price_amount",
    "Площадь, м²": "area", "$/м²": "price_per_sqm", "Комнат": "rooms",
    "Этаж": "floor", "Этажей": "floors_total", "Продавец": "seller_type",
    "Новостройка": "new_build", "Проверено": "verified",
    "Сомнительно": "anomaly", "Статус": "status",
}
SELLERS = {"собственник": "owner", "агентство": "agency"}
STATUSES = {"на ленте": "active", "снято": "gone"}
STAMP = re.compile(r"listam-(\d{8})-(\d{4})\.xlsx$")


def when(path: Path) -> datetime:
    """Отметка среза берётся из имени файла: оно и есть время выгрузки."""
    match = STAMP.search(path.name)
    if match is None:
        raise SystemExit(f"{path.name}: имя не похоже на выгрузку listam-ГГГГММДД-ЧЧММ.xlsx")
    return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M").replace(
        tzinfo=timezone.utc
    )


def listings(path: Path) -> list[Listing]:
    sheet = load_workbook(path, read_only=True, data_only=True)["Объявления"]
    rows = sheet.iter_rows(values_only=True)
    header = [COLUMNS.get(str(title).strip()) if title else None for title in next(rows)]
    result = []
    for row in rows:
        fields = {name: value for name, value in zip(header, row) if name}
        if not fields.get("id"):
            continue
        fields["id"] = str(fields["id"])
        fields["seller_type"] = SELLERS.get(fields.get("seller_type"))
        fields["status"] = STATUSES.get(fields.get("status"), "active")
        fields["currency"] = "USD"
        result.append(Listing(**fields))
    return result


def main(target: str) -> None:
    database = SqliteDatabase(Path(target))
    database.connect()
    database.migrate()
    for path in sorted(Path("out").glob("listam-*.xlsx"), key=when):
        seen_at = when(path)
        rows = listings(path)
        counts: dict[str, int] = {}
        for item in rows:
            outcome = database.upsert_listing(item, seen_at=seen_at)
            counts[outcome] = counts.get(outcome, 0) + 1
        print(f"{path.name} ({seen_at:%d.%m %H:%M}): {len(rows)} строк, {counts}")
    points = database.conn.execute(
        "SELECT COUNT(*) AS n FROM price_history"
    ).fetchone()["n"]
    print(f"Итого: объявлений {database.count_listings()}, точек истории цен {points}")
    database.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/listam-m3.sqlite")
```

- [x] **Шаг 3: собрать базу и записать числа**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe tmp/restore_from_exports.py data/listam-m3.sqlite
```

Ожидается: по строке на выгрузку и итог. В отчёт фазы идут **напечатанные**
числа: сколько объявлений, сколько точек истории цен, сколько объявлений сменили
цену между срезами (это видно по счётчику `price_changed` в выводе каждой
выгрузки).

- [x] **Шаг 4: проверить, что история не пуста**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "
from listam.adapters.db_sqlite import SqliteDatabase
db = SqliteDatabase('data/listam-m3.sqlite'); db.connect()
rows = db.conn.execute('''
  SELECT listing_id, COUNT(*) AS points, MIN(price_usd) AS low, MAX(price_usd) AS high
    FROM price_history GROUP BY listing_id HAVING COUNT(*) > 1 AND low < high
   ORDER BY (high - low) DESC LIMIT 5''').fetchall()
print('объявлений со сменой цены:', db.conn.execute('''
  SELECT COUNT(*) AS n FROM (SELECT listing_id FROM price_history
    GROUP BY listing_id HAVING COUNT(DISTINCT price_usd) > 1)''').fetchone()['n'])
for r in rows: print(dict(r))
db.close()"
```

Если «объявлений со сменой цены: 0» — дальше замерять нечего, и это записывается
в отчёт первой строкой: значит, выгрузки сняты с одного и того же среза ленты,
и историю придётся добирать иначе (например, двумя прогонами `scrape` с
интервалом). Не выдумывай числа под ожидание — запиши, что получилось.

### Задача 1.2. Заявки и матчи на этой базе

- [x] **Шаг 1: положить заявки**

Если `data/requests.csv` содержит одну строку-образец, сгенерируй 50 заявок
с реальными районами и бюджетами 90 000–400 000 $ плюс одну нарочно широкую —
тем же способом, что и приёмка фазы 8 (её «Результат» описывает состав).
Число заявок **назови в отчёте**: от него прямо зависят все остальные числа.

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam requests
```

- [x] **Шаг 2: полный подбор**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam match --all
```

В отчёт: строка «Матчи: новых N, обновлённых M, без изменений K», «Из них
горячих», время команды.

- [x] **Шаг 3: второй подбор — мерка «сколько событий даёт сутки»**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam match --all
```

Второй прогон по той же базе обязан дать «новых 0»: всё, что меняется на
повторе, — это и есть шум, который дайджест не должен показывать. Запиши,
что получилось на самом деле.

### Задача 1.3. Замер событий в окне

**Файлы:**
- Создать: `tmp/measure_events.py`

- [x] **Шаг 1: написать замерщик**

Считает то, что в фазе 2 станет выборкой порта, — но пока прямым SQL, чтобы
числа были раньше кода:

```python
# tmp/measure_events.py
"""Сколько событий в окне на боевых числах. Числа до кода, а не после.

Окно задаётся часами от «сейчас». Событие считается по тем же четырём
отметкам, которые в фазе 2 станут выборкой порта: first_matched_at,
matched_at, retired_at и (пока её нет) revived_at.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from listam.adapters.db_sqlite import SqliteDatabase, to_iso

HOT = 70.0
DIGEST = 40.0


def main(path: str, hours: float) -> None:
    database = SqliteDatabase(path)
    database.connect()
    since = to_iso(datetime.now(timezone.utc) - timedelta(hours=hours))

    totals = database.conn.execute(
        "SELECT COUNT(*) AS all_matches, "
        "       SUM(retired_at IS NULL) AS alive, "
        "       SUM(retired_at IS NULL AND score >= ?) AS hot "
        "  FROM matches", (HOT,)
    ).fetchone()
    print(f"матчей всего {totals['all_matches']}, живых {totals['alive']}, "
          f"из них горячих {totals['hot']}")

    events = database.conn.execute(
        "SELECT SUM(first_matched_at >= :since) AS fresh, "
        "       SUM(matched_at >= :since AND first_matched_at < :since) AS moved, "
        "       SUM(retired_at >= :since) AS retired "
        "  FROM matches", {"since": since}
    ).fetchone()
    print(f"за {hours:g} ч: новых матчей {events['fresh'] or 0}, "
          f"обновлённых {events['moved'] or 0}, закрытых {events['retired'] or 0}")

    cheaper = database.conn.execute(
        "SELECT COUNT(*) AS n FROM matches m "
        " WHERE m.retired_at IS NULL AND m.matched_at >= :since "
        "   AND EXISTS (SELECT 1 FROM price_history p "
        "                WHERE p.listing_id = m.listing_id AND p.seen_at >= :since) ",
        {"since": since}
    ).fetchone()["n"]
    print(f"из обновлённых с точкой истории цен в окне: {cheaper}")

    spread = database.conn.execute(
        "SELECT r.external_id AS id, COUNT(*) AS events "
        "  FROM matches m JOIN requests r ON r.id = m.request_id "
        " WHERE m.first_matched_at >= :since OR m.matched_at >= :since "
        " GROUP BY r.external_id ORDER BY events DESC", {"since": since}
    ).fetchall()
    if spread:
        counts = [row["events"] for row in spread]
        middle = sorted(counts)[len(counts) // 2]
        print(f"заявок с событиями {len(spread)}; "
              f"максимум {counts[0]} ({spread[0]['id']}), медиана {middle}, "
              f"минимум {counts[-1]}")
        print("первые пять:", [(row["id"], row["events"]) for row in spread[:5]])
    database.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/listam-m3.sqlite",
         float(sys.argv[2]) if len(sys.argv) > 2 else 24.0)
```

- [x] **Шаг 2: замерить сутки и час**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe tmp/measure_events.py data/listam-m3.sqlite 24
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe tmp/measure_events.py data/listam-m3.sqlite 1
```

- [x] **Шаг 3: замерить, что приносит лента вне заявок**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam changes --hours 24
```

В отчёт: сколько новых объявлений, сколько сменили цену, сколько снято —
это нижняя граница для `notify.feed.limit`.

- [x] **Шаг 4: записать предлагаемые лимиты**

Из замеров выводятся четыре числа, которые фаза 4 положит в конфиг. Каждое —
с обоснованием из напечатанного:

| Ключ | Как выводится |
| --- | --- |
| `notify.hot.per_request` | столько строк на заявку, чтобы часовое сообщение читалось за полминуты: медиана событий за час, но не больше 5 |
| `notify.digest.per_request` | медиана событий за сутки на заявку, округлённая вверх до 5 |
| `notify.digest.wide_request` | верхний квартиль: столько событий за сутки бывает у заявки, которую пора сужать |
| `notify.feed.limit` | сколько новых объявлений в час приносит лента по `changes` |

### Конец фазы 1

- [x] Батарея: `.venv/Scripts/python.exe -m pytest -q` → **687 passed, 18 skipped**
      (фаза кода не трогала; разошлось — это находка).
- [x] Дописать раздел «Результат фазы 1»: как собрана база, сколько точек истории
      цен, сколько событий в сутки и в час, распределение по заявкам, предлагаемые
      четыре лимита. **Каждое число — с командой, которая его напечатала.**
- [x] Дописать стартовый промпт для фазы 2.
- [x] `git status --short` — чисто (скрипты лежат вне репозитория); коммит —
      только этот файл плана.

---

# Фаза 2. База помнит событие и отправку

**Одна сессия.** Миграция 010, отметка воскресения у матча, выборка «что
тронулось с отметки» и журнал отправок. Домен учится отличать «новый» от
«подешевел», «вернулся» и «закрылся». Наружу ещё ничего не шлётся.

**Ожидается после фазы:** **711 passed, 18 skipped**, схема базы **10**
(687 плюс 24 теста фазы: 2 миграции, 11 контрактных, 11 доменных — число
пересчитано по факту, см. «Результат фазы 2»).

### Задача 2.1. Миграция 010

**Файлы:**
- Создать: `listam/migrations/010_notifications.sql`
- Изменить: `listam/domain/models.py`
- Тест: `tests/test_migrations.py`

- [x] **Шаг 1: падающий тест на миграцию**

```python
# tests/test_migrations.py — дописать в конец
def test_migration_010_adds_the_journal_and_the_revival_mark(tmp_path):
    """Схема 9 → 10: журнал отправок и отметка воскресения матча."""
    database = opened(tmp_path, upto(tmp_path, 9))
    database.migrate()
    assert database.schema_version() == 9
    database.close()

    database = opened(tmp_path, MIGRATIONS_DIR)
    database.migrate()

    assert "notifications" in database.table_names()
    columns = {row["name"] for row in database.conn.execute("PRAGMA table_info(matches)")}
    assert "revived_at" in columns
    assert database.schema_version() == 10
    database.close()


def test_migration_010_keeps_what_was_in_the_base(tmp_path):
    """Миграция ничего не закрывает и ничего не считает отправленным."""
    database = opened(tmp_path, upto(tmp_path, 9))
    database.migrate()
    database.conn.execute(
        "INSERT INTO listings (id, url, status) VALUES ('1', 'u', 'active')"
    )
    database.conn.execute("INSERT INTO requests (external_id) VALUES ('R-1')")
    database.conn.execute(
        "INSERT INTO matches (request_id, listing_id, score) VALUES (1, '1', 80)"
    )
    database.conn.commit()
    database.close()

    database = opened(tmp_path, MIGRATIONS_DIR)
    database.migrate()

    row = database.conn.execute("SELECT * FROM matches").fetchone()
    assert row["score"] == 80
    assert row["revived_at"] is None
    assert database.conn.execute(
        "SELECT COUNT(*) AS n FROM notifications").fetchone()["n"] == 0
    database.close()
```

- [x] **Шаг 2: убедиться, что тесты падают**

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_migrations.py -k 010
```

Ожидается FAIL: `assert 9 == 10` — миграции 010 нет.

- [x] **Шаг 3: написать миграцию**

```sql
-- listam/migrations/010_notifications.sql
-- Версия 10: уведомления получают память.
--
-- notifications — журнал отправок. Отметка «это уже отправлено» обязана
--   жить в базе: без неё повторный запуск шлёт всё заново, а отправленное
--   отозвать нельзя. Окно следующего запуска — window_to последней успешной
--   строки этого вида. Сбой сети строки не пишет: окно не сдвинулось,
--   событие не потеряно.
-- notifications.text — что именно ушло в чат. Восстановить сообщение
--   пересчётом нельзя: база с тех пор изменилась.
-- matches.revived_at — когда закрытый матч снова подтвердился. upsert гасит
--   retired_at и retired_reason, и без этой колонки воскресший матч
--   неотличим от обычного пересчёта — а он обязан быть событием ровно
--   один раз.

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,          -- hot | digest | feed
    sent_at     TEXT NOT NULL,
    window_from TEXT,                   -- NULL — первая отправка этого вида
    window_to   TEXT NOT NULL,
    events      INTEGER NOT NULL DEFAULT 0,
    requests    INTEGER NOT NULL DEFAULT 0,
    text        TEXT,
    notes       TEXT
);

CREATE INDEX IF NOT EXISTS idx_notifications_kind ON notifications(kind, sent_at);

ALTER TABLE matches ADD COLUMN revived_at TEXT;
```

- [x] **Шаг 4: поле в модели**

```python
# listam/domain/models.py — в dataclass Match, после retired_reason
    revived_at: datetime | None = None      # когда закрытый матч снова подтвердился
```

И новая модель рядом с `Run` (после dataclass `Run`):

```python
@dataclass
class Notification:
    """Строка журнала отправок: одна успешная отправка одного вида."""

    id: int | None = None
    kind: str = ""                          # hot | digest | feed
    sent_at: datetime | None = None
    window_from: datetime | None = None     # None — первая отправка этого вида
    window_to: datetime | None = None       # отсюда считается следующее окно
    events: int = 0
    requests: int = 0
    text: str | None = None
    notes: str | None = None
```

- [x] **Шаг 5: тесты проходят**

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_migrations.py
```

Ожидается PASS.

- [x] **Шаг 6: коммит**

```bash
git add listam/migrations/010_notifications.sql listam/domain/models.py tests/test_migrations.py
git commit -m "feat(db): миграция 010 — журнал уведомлений и отметка воскресения матча"
```

### Задача 2.2. Воскресший матч помнит, когда он вернулся

**Файлы:**
- Изменить: `listam/adapters/db_sqlite.py:592-707`
- Тест: `tests/contracts/test_database_contract.py`

- [x] **Шаг 1: падающие тесты контракта**

```python
# tests/contracts/test_database_contract.py — дописать
def test_a_revived_match_remembers_when_it_came_back(db):
    """Подтвердился снова — в строке остаётся след возврата, а не только
    погашенное закрытие: иначе воскресение неотличимо от пересчёта."""
    db.upsert_listing(make_listing(), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_match(Match(request_id=request.id, listing_id="24254997", score=80.0), NOW)
    db.retire_matches(request.id, keep=set(), now=LATER, reason="бюджет")

    db.upsert_match(
        Match(request_id=request.id, listing_id="24254997", score=80.0), EVEN_LATER
    )

    match = db.matches_for_request(request.id)[0]
    assert match.retired_at is None
    assert match.retired_reason is None
    assert match.revived_at == EVEN_LATER


def test_a_match_that_was_never_retired_has_no_revival_mark(db):
    """Обычный пересчёт отметку возврата не ставит: вернуться неоткуда."""
    db.upsert_listing(make_listing(), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_match(Match(request_id=request.id, listing_id="24254997", score=80.0), NOW)

    db.upsert_matches(
        [Match(request_id=request.id, listing_id="24254997", score=91.0)], LATER
    )

    assert db.matches_for_request(request.id)[0].revived_at is None


def test_a_batch_revival_is_marked_too(db):
    """Пачка и одиночная запись ведут себя одинаково: у подбора путь один —
    пачка, и правило, проверенное только на `upsert_match`, в бою не работает."""
    db.upsert_listing(make_listing(), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_matches(
        [Match(request_id=request.id, listing_id="24254997", score=80.0)], NOW
    )
    db.retire_matches(request.id, keep=set(), now=LATER, reason="бюджет")

    counts = db.upsert_matches(
        [Match(request_id=request.id, listing_id="24254997", score=80.0)], EVEN_LATER
    )

    assert counts == {"new": 0, "updated": 1, "unchanged": 0}
    assert db.matches_for_request(request.id)[0].revived_at == EVEN_LATER
```

- [x] **Шаг 2: убедиться, что тесты падают**

```bash
.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py -k reviv
```

Ожидается FAIL: `AttributeError: 'Match' object has no attribute 'revived_at'`
либо `assert None == datetime(...)`.

- [x] **Шаг 3: отметка в одиночном апсерте**

```python
# listam/adapters/db_sqlite.py — в upsert_match, вместо трёх строк
#   updates["retired_at"] = None
#   updates["retired_reason"] = None
#   updates["id"] = existing["id"]
        updates = dict(values)
        updates["matched_at"] = to_iso(now)
        # Подтвердился снова — значит, живой. Гасим закрытие вместе с причиной:
        # причина без даты читалась бы как «закрыт неизвестно когда». А вот дату
        # возврата, наоборот, ставим: без неё воскресение неотличимо от обычного
        # пересчёта, и уведомление либо промолчит про вернувшийся вариант, либо
        # расскажет про него дважды.
        updates["revived_at"] = (to_iso(now) if existing["retired_at"] is not None
                                 else existing["revived_at"])
        updates["retired_at"] = None
        updates["retired_reason"] = None
        updates["id"] = existing["id"]
```

- [x] **Шаг 4: отметка в пачке**

```python
# listam/adapters/db_sqlite.py — в upsert_matches, вместо строки
#   values.update(matched_at=stamp, retired_at=None, retired_reason=None, id=was["id"])
            # Ключи всех словарей пачки обязаны совпадать: `executemany` строит
            # один SQL на всю пачку по первому из них. Поэтому `revived_at`
            # перечисляется всегда — у не воскресавшего матча в него ложится
            # то, что там и лежало.
            values.update(
                matched_at=stamp, retired_at=None, retired_reason=None,
                revived_at=(stamp if was["retired_at"] is not None
                            else was["revived_at"]),
                id=was["id"],
            )
```

- [x] **Шаг 5: `revived_at` читается из строки**

```python
# listam/adapters/db_sqlite.py — в _row_to_match, рядом с retired_at
        revived_at=from_iso(row["revived_at"]),
```

Найди функцию `_row_to_match` (конец файла) и добавь строку в конструктор
`Match(...)`; `MATCH_FIELDS` и `MATCH_COMPARED` **не трогаются** — `revived_at`
не вычисляется пересчётом и в сравнение «то же самое?» не входит.

- [x] **Шаг 6: тесты проходят**

```bash
.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py tests/test_matching.py
```

Ожидается PASS целиком: правки не должны сдвинуть ни один тест фаз 1 и 3
QA-плана (`unchanged` остаётся `unchanged`).

- [x] **Шаг 7: коммит**

```bash
git add listam/adapters/db_sqlite.py tests/contracts/test_database_contract.py
git commit -m "feat(db): воскресший матч помнит дату возврата"
```

### Задача 2.3. Выборка «что тронулось с отметки»

**Файлы:**
- Изменить: `listam/ports/database.py`, `listam/adapters/db_sqlite.py`
- Тест: `tests/contracts/test_database_contract.py`

- [x] **Шаг 1: падающие тесты контракта**

```python
# tests/contracts/test_database_contract.py — дописать
def test_match_events_bring_the_match_the_listing_and_the_old_price(db):
    """Событие — это матч, карточка и цена до окна: без цены «подешевело»
    не показать, а без карточки не позвонить."""
    db.upsert_listing(make_listing(price_usd=200000.0), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_match(Match(request_id=request.id, listing_id="24254997", score=80.0), LATER)

    rows = db.match_events_since(NOW, EVEN_LATER)

    assert len(rows) == 1
    match, listing, price_before = rows[0]
    assert match.listing_id == "24254997"
    assert listing.district == "Центр"
    assert price_before == 200000.0      # точка истории от upsert_listing


def test_match_events_skip_what_did_not_move(db):
    """Матч, которого окно не коснулось, событием не считается."""
    db.upsert_listing(make_listing(), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_match(Match(request_id=request.id, listing_id="24254997", score=80.0), NOW)

    assert db.match_events_since(LATER, EVEN_LATER) == []


def test_match_events_include_the_retired_ones(db):
    """Закрытый матч из выборки не выпадает: дайджест обязан сказать, почему
    вчерашняя карточка пропала. Показывать ли его — решает домен."""
    db.upsert_listing(make_listing(), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_match(Match(request_id=request.id, listing_id="24254997", score=80.0), NOW)
    db.retire_matches(request.id, keep=set(), now=LATER, reason="бюджет")

    rows = db.match_events_since(NOW, EVEN_LATER)

    assert len(rows) == 1
    assert rows[0][0].retired_at == LATER
    assert rows[0][0].retired_reason == "бюджет"


def test_match_events_can_be_narrowed_to_one_request(db):
    """Витрина одной заявки не читает события всех пятидесяти."""
    db.upsert_listing(make_listing(), seen_at=NOW)
    for external_id in ("R-1", "R-2"):
        db.upsert_request(Request(external_id=external_id), now=NOW)
        request = db.get_request(external_id)
        db.upsert_match(
            Match(request_id=request.id, listing_id="24254997", score=80.0), LATER
        )

    only = db.match_events_since(NOW, EVEN_LATER, request_id=db.get_request("R-2").id)

    assert len(only) == 1
    assert only[0][0].request_id == db.get_request("R-2").id


def test_match_events_do_not_reach_past_the_window(db):
    """Верхняя граница окна — не украшение: событие, случившееся после неё,
    уйдёт в следующую отправку, а не в эту."""
    db.upsert_listing(make_listing(), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_match(
        Match(request_id=request.id, listing_id="24254997", score=80.0), EVEN_LATER
    )

    assert db.match_events_since(NOW, LATER) == []
```

- [x] **Шаг 2: убедиться, что тесты падают**

```bash
.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py -k match_events
```

Ожидается FAIL: `AttributeError: 'SqliteDatabase' object has no attribute 'match_events_since'`.

- [x] **Шаг 3: метод порта**

```python
# listam/ports/database.py — после matches_with_listings
    @abstractmethod
    def match_events_since(self, since: datetime, until: datetime,
                           request_id: int | None = None
                           ) -> list[tuple[Match, Listing, float | None]]:
        """Матчи, которых коснулось окно `(since, until]`, с карточкой и старой ценой.

        Коснулось — это любая из четырёх отметок: матч появился
        (`first_matched_at`), пересчитался (`matched_at`), закрылся
        (`retired_at`) или вернулся (`revived_at`). Что из этого считать
        событием и как назвать, решает домен (`listam/domain/events.py`):
        база отдаёт сырьё, а не приговор.

        Третье значение строки — последняя цена объявления **до** окна.
        Без неё «подешевело с 225 000 до 150 000» не написать, а балл
        на этот вопрос не отвечает: его двигают и пересчёт кластера,
        и смена медианы района.

        Закрытые матчи из выборки не выпадают: дайджест обязан сказать,
        почему вчерашняя карточка пропала.
        """
```

- [x] **Шаг 4: реализация**

```python
# listam/adapters/db_sqlite.py — после matches_with_listings
    def match_events_since(self, since: datetime, until: datetime,
                           request_id: int | None = None
                           ) -> list[tuple[Match, Listing, float | None]]:
        """См. порт. Один запрос: карточки по одной — это 66 910 запросов на
        50 заявках, мы это уже проходили в фазе 6 QA-плана.

        Границы окна строковые, как и везде: все отметки пишет `to_iso`
        в одном формате, и сравнение текстов даёт тот же порядок, что
        сравнение времён.
        """
        match_columns = [row["name"] for row in
                         self.conn.execute("PRAGMA table_info(matches)")]
        listing_columns = [row["name"] for row in
                           self.conn.execute("PRAGMA table_info(listings)")]
        select = ", ".join(
            [f"m.{name} AS m_{name}" for name in match_columns]
            + [f"l.{name} AS l_{name}" for name in listing_columns]
        )
        query = (
            f"SELECT {select}, "
            f"       (SELECT p.price_usd FROM price_history p "
            f"         WHERE p.listing_id = m.listing_id AND p.seen_at <= :since "
            f"         ORDER BY p.id DESC LIMIT 1) AS price_before "
            f"  FROM matches m JOIN listings l ON l.id = m.listing_id "
            f" WHERE ((m.first_matched_at > :since AND m.first_matched_at <= :until) "
            f"     OR (m.matched_at      > :since AND m.matched_at      <= :until) "
            f"     OR (m.retired_at      > :since AND m.retired_at      <= :until) "
            f"     OR (m.revived_at      > :since AND m.revived_at      <= :until))"
        )
        params = {"since": to_iso(since), "until": to_iso(until)}
        if request_id is not None:
            query += " AND m.request_id = :request_id"
            params["request_id"] = request_id
        query += " ORDER BY m.score DESC, m.listing_id"

        events: list[tuple[Match, Listing, float | None]] = []
        for row in self.conn.execute(query, params):
            data = dict(row)
            match_row = {name: data[f"m_{name}"] for name in match_columns}
            listing_row = {name: data[f"l_{name}"] for name in listing_columns}
            events.append((_row_to_match(match_row), _row_to_listing(listing_row),
                           data["price_before"]))
        return events
```

- [x] **Шаг 5: тесты проходят**

```bash
.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py
```

Ожидается PASS.

- [x] **Шаг 6: коммит**

```bash
git add listam/ports/database.py listam/adapters/db_sqlite.py tests/contracts/test_database_contract.py
git commit -m "feat(db): выборка матчей, которых коснулось окно"
```

### Задача 2.4. Журнал отправок

**Файлы:**
- Изменить: `listam/ports/database.py`, `listam/adapters/db_sqlite.py`
- Тест: `tests/contracts/test_database_contract.py`

- [x] **Шаг 1: падающие тесты контракта**

```python
# tests/contracts/test_database_contract.py — дописать
def test_the_journal_remembers_the_last_successful_send(db):
    """Окно следующего запуска — window_to последней успешной строки."""
    from listam.domain.models import Notification

    db.record_notification(Notification(
        kind="digest", sent_at=LATER, window_from=NOW, window_to=LATER,
        events=7, requests=3, text="Заявка R-1 — 7 новых",
    ))

    last = db.last_notification("digest")

    assert last.window_to == LATER
    assert last.events == 7
    assert last.text.startswith("Заявка R-1")


def test_kinds_of_notification_do_not_mix(db):
    """Часовое «горячее» не двигает окно дневного дайджеста и наоборот."""
    from listam.domain.models import Notification

    db.record_notification(Notification(kind="hot", sent_at=LATER, window_to=LATER))

    assert db.last_notification("hot").window_to == LATER
    assert db.last_notification("digest") is None


def test_the_journal_answers_none_before_the_first_send(db):
    assert db.last_notification("digest") is None
```

- [x] **Шаг 2: убедиться, что тесты падают**

```bash
.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py -k notification
```

Ожидается FAIL: `AttributeError: … 'record_notification'`.

- [x] **Шаг 3: методы порта**

```python
# listam/ports/database.py — после count_matches
    @abstractmethod
    def record_notification(self, notification: Notification) -> int:
        """Записывает успешную отправку и отдаёт её идентификатор.

        Строка пишется **только после того, как сообщение ушло**: окно
        следующего запуска считается от неё, и запись до отправки означала бы
        потерянное событие при первом же отказе сети.
        """

    @abstractmethod
    def last_notification(self, kind: str) -> Notification | None:
        """Последняя отправка этого вида; не было ни одной — None.

        Виды не смешиваются: часовое «горячее» не двигает окно дневного
        дайджеста, иначе вечерняя сводка показывала бы последний час.
        """
```

Там же, наверху файла, в импорт моделей добавляется `Notification`:

```python
from listam.domain.models import Listing, Match, Notification, PricePoint, Request, Run
```

- [x] **Шаг 4: реализация**

```python
# listam/adapters/db_sqlite.py — после count_matches
    # --- журнал уведомлений ----------------------------------------------
    def record_notification(self, notification: Notification) -> int:
        with self.transaction():
            cursor = self.conn.execute(
                "INSERT INTO notifications "
                "(kind, sent_at, window_from, window_to, events, requests, text, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (notification.kind, to_iso(notification.sent_at),
                 to_iso(notification.window_from), to_iso(notification.window_to),
                 int(notification.events), int(notification.requests),
                 notification.text, notification.notes),
            )
        return int(cursor.lastrowid)

    def last_notification(self, kind: str) -> Notification | None:
        row = self.conn.execute(
            "SELECT * FROM notifications WHERE kind = ? ORDER BY sent_at DESC, id DESC "
            "LIMIT 1", (kind,),
        ).fetchone()
        if row is None:
            return None
        return Notification(
            id=row["id"], kind=row["kind"], sent_at=from_iso(row["sent_at"]),
            window_from=from_iso(row["window_from"]),
            window_to=from_iso(row["window_to"]),
            events=row["events"], requests=row["requests"],
            text=row["text"], notes=row["notes"],
        )
```

Импорт модели — в шапке файла:

```python
from listam.domain.models import Listing, Match, Notification, PricePoint, Request, Run
```

- [x] **Шаг 5: тесты проходят**

```bash
.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py
```

Ожидается PASS.

- [x] **Шаг 6: коммит**

```bash
git add listam/ports/database.py listam/adapters/db_sqlite.py tests/contracts/test_database_contract.py
git commit -m "feat(db): журнал отправленных уведомлений"
```

### Задача 2.5. Домен: что считается событием

**Файлы:**
- Создать: `listam/domain/events.py`
- Тест: `tests/test_events.py`

- [x] **Шаг 1: падающие тесты домена**

```python
# tests/test_events.py
"""Что считается событием: чистые функции, ни базы, ни сети."""
from __future__ import annotations

from datetime import datetime, timezone

from listam.domain.events import CHEAPER, NEW, RETIRED, REVIVED, classify, \
    events_for, limited
from listam.domain.models import Listing, Match

SINCE = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
INSIDE = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
UNTIL = datetime(2026, 9, 22, 0, 0, tzinfo=timezone.utc)
BEFORE = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)


def listing(price=150000.0) -> Listing:
    return Listing(id="1", url="https://www.list.am/ru/item/1", district="Кентрон",
                   price_usd=price, area=68.0, rooms=2)


def match(**over) -> Match:
    fields = dict(request_id=1, listing_id="1", score=85.0,
                  first_matched_at=INSIDE, matched_at=INSIDE)
    fields.update(over)
    return Match(**fields)


def test_a_match_born_inside_the_window_is_new():
    event = classify(match(), listing(), None, SINCE, UNTIL)
    assert event.kind == NEW


def test_a_match_that_only_got_recounted_is_not_an_event():
    """Пересчёт двигает cluster_size у тысяч матчей: это не событие рынка."""
    event = classify(match(first_matched_at=BEFORE), listing(), None, SINCE, UNTIL)
    assert event is None


def test_a_match_whose_listing_got_cheaper_is_an_event():
    event = classify(match(first_matched_at=BEFORE), listing(150000.0), 225000.0,
                     SINCE, UNTIL)
    assert event.kind == CHEAPER
    assert event.price_before == 225000.0


def test_a_match_whose_listing_got_dearer_is_not_an_event():
    """Подорожание не повод звонить: если вариант вышел за бюджет, полный
    проход его закроет, и это уже другое событие."""
    assert classify(match(first_matched_at=BEFORE), listing(260000.0), 225000.0,
                    SINCE, UNTIL) is None


def test_a_retired_match_is_an_event_of_its_own_kind():
    event = classify(match(retired_at=INSIDE, retired_reason="бюджет"),
                     listing(), None, SINCE, UNTIL)
    assert event.kind == RETIRED


def test_a_revived_match_is_an_event_once():
    """Воскресший — событие ровно один раз: в следующем окне отметка возврата
    уже позади, и матч молчит, пока с ним снова что-нибудь не случится."""
    revived = match(first_matched_at=BEFORE, revived_at=INSIDE)
    assert classify(revived, listing(), None, SINCE, UNTIL).kind == REVIVED
    assert classify(revived, listing(), None, UNTIL,
                    datetime(2026, 9, 23, tzinfo=timezone.utc)) is None


def test_a_retired_match_never_pretends_to_be_new():
    """Закрытый матч в уведомление не уходит ни под каким видом — даже если
    родился в этом же окне."""
    event = classify(match(retired_at=INSIDE), listing(), None, SINCE, UNTIL)
    assert event.kind == RETIRED


def test_events_are_filtered_by_score_and_sorted():
    rows = [
        (match(score=91.0, listing_id="a"), listing(), None),
        (match(score=45.0, listing_id="b"), listing(), None),
    ]
    events = events_for(rows, SINCE, UNTIL, min_score=70.0)
    assert [event.match.listing_id for event in events] == ["a"]


def test_retired_events_ignore_the_score_floor():
    """Закрытие объясняет пропавшую карточку, а балл у закрытого — вчерашний."""
    rows = [(match(score=41.0, retired_at=INSIDE), listing(), None)]
    assert [event.kind for event in events_for(rows, SINCE, UNTIL, min_score=70.0)] \
        == [RETIRED]


def test_limited_keeps_the_best_and_tells_the_truth_about_the_rest():
    events = events_for(
        [(match(score=float(90 - index), listing_id=str(index)), listing(), None)
         for index in range(7)],
        SINCE, UNTIL, min_score=None,
    )
    shown, total = limited(events, 3)
    assert total == 7
    assert [event.match.listing_id for event in shown] == ["0", "1", "2"]


def test_limited_without_a_ceiling_shows_everything():
    events = events_for([(match(), listing(), None)], SINCE, UNTIL, min_score=None)
    shown, total = limited(events, None)
    assert len(shown) == total == 1
```

- [x] **Шаг 2: убедиться, что тесты падают**

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_events.py
```

Ожидается FAIL: `ModuleNotFoundError: No module named 'listam.domain.events'`.

- [x] **Шаг 3: написать домен**

```python
# listam/domain/events.py
"""Что считается событием: новый вариант, подешевевший, вернувшийся, закрытый.

Чистые функции. Базы здесь нет: выборку отдаёт порт
(`Database.match_events_since`), а эти функции решают, как назвать
случившееся и что из этого показывать человеку.

Два правила, из-за которых модуль и появился:

* **Пересчёт — не событие.** Ночной `match --all` двигает `matched_at` у
  тысяч матчей: меняется размер кластера, медиана района, разброс цен.
  Показать это человеку — значит утопить настоящее событие в шуме.
* **Подешевело — это про цену, а не про балл.** Балл двигают и кластер,
  и медиана; единственный честный признак — цена до окна против цены сейчас.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from listam.domain.models import Listing, Match

NEW = "new"
CHEAPER = "cheaper"
REVIVED = "revived"
RETIRED = "retired"

# Подписи для человека: один словарь на уведомление и на витрину, как
# `labels.MATCH_STATUSES` — два словаря с одним смыслом однажды разойдутся.
EVENT_LABELS = {
    NEW: "новый",
    CHEAPER: "подешевел",
    REVIVED: "вернулся",
    RETIRED: "отпал",
}


@dataclass
class MatchEvent:
    """Что случилось с матчем в окне: вид, матч, карточка и цена до окна."""

    kind: str
    match: Match
    listing: Listing
    price_before: float | None = None


def _inside(when: datetime | None, since: datetime, until: datetime) -> bool:
    """Отметка попала в окно `(since, until]`.

    Нижняя граница строгая, верхняя — нет: `since` это `window_to` прошлой
    отправки, и событие, ушедшее в неё, не имеет права уйти второй раз.
    """
    return when is not None and since < when <= until


def classify(match: Match, listing: Listing, price_before: float | None,
             since: datetime, until: datetime) -> MatchEvent | None:
    """Вид события или `None`, если в окне с матчем ничего не случилось.

    Порядок правил — это и есть решение. Закрытие идёт первым: закрытый матч
    не имеет права выглядеть новым, даже если он в этом же окне родился.
    Воскресение — вторым: оно крупнее пересчёта. Рождение — третьим.
    Подешевение — последним, потому что это единственное правило, которое
    смотрит не на матч, а на цену карточки.
    """
    if _inside(match.retired_at, since, until):
        return MatchEvent(kind=RETIRED, match=match, listing=listing)
    if match.retired_at is not None:
        # Закрыт раньше окна и не воскрес — звонить по нему некуда.
        return None
    if _inside(match.revived_at, since, until):
        return MatchEvent(kind=REVIVED, match=match, listing=listing,
                          price_before=price_before)
    if _inside(match.first_matched_at, since, until):
        return MatchEvent(kind=NEW, match=match, listing=listing)
    if _inside(match.matched_at, since, until) \
            and price_before is not None and listing.price_usd is not None \
            and listing.price_usd < price_before:
        return MatchEvent(kind=CHEAPER, match=match, listing=listing,
                          price_before=price_before)
    return None


def events_for(rows, since: datetime, until: datetime,
               min_score: float | None) -> list[MatchEvent]:
    """События окна, от лучшего к худшему.

    `min_score` не трогает закрытия: закрытие объясняет пропавшую карточку,
    а балл у закрытого матча — вчерашний, и порог о нём ничего не знает.
    """
    events: list[MatchEvent] = []
    for match, listing, price_before in rows:
        event = classify(match, listing, price_before, since, until)
        if event is None:
            continue
        if event.kind != RETIRED and min_score is not None \
                and (event.match.score or 0) < min_score:
            continue
        events.append(event)
    events.sort(key=lambda event: (-(event.match.score or 0), event.match.listing_id))
    return events


def limited(events: list[MatchEvent], per_request: int | None
            ) -> tuple[list[MatchEvent], int]:
    """Показанные события и сколько их всего.

    Два числа, а не одно: «показаны 10 из 412» — то же обещание, что «…и ещё N»
    в витрине, и оно обязано быть правдой. Одна широкая заявка даёт
    7 515 горячих матчей — потолок здесь не удобство, а условие разговора.
    """
    if per_request is None:
        return events, len(events)
    return events[:per_request], len(events)
```

- [x] **Шаг 4: тесты проходят**

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_events.py
```

Ожидается PASS (12 тестов).

- [x] **Шаг 5: коммит**

```bash
git add listam/domain/events.py tests/test_events.py
git commit -m "feat(domain): классификация событий матча"
```

### Конец фазы 2

- [x] Батарея: `.venv/Scripts/python.exe -m pytest -q` → ожидается
      **712 passed, 18 skipped**.
- [x] Проверить схему на базе замера фазы 1:
      `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam doctor --no-network`
      → «Схема базы … версия 10». Записать время миграции на боевых числах
      (эталон: 008 и 009 шли 0,06 с — объём базы ничего не решает).
- [x] Дописать «Результат фазы 2»: что сделано, числа батареи, что разошлось
      с планом и почему.
- [x] Дописать стартовый промпт для фазы 3.
- [x] Коммит.

---

# Фаза 3. Витрина: потолок доходит до выборки, срез «со вчера» — в терминал

**Одна сессия.** Закрывает долг фазы 8 QA: `matches` без `--request` читает
**50 633 строки за 4,0 с** и печатает 2 849 строк по 48 разделам. Потолок
доходит до SQL, счётчик считается отдельно, и появляется срез `matches --new` —
та самая выборка, на которой в фазе 4 соберётся текст уведомления.

**Ожидается после фазы:** **723 passed, 18 skipped** (711 плюс 12: три
контрактных, восемь витринных, один на командную строку; пересчитано по факту,
см. «Результат фазы 3»).

### Задача 3.1. Счётчик отдельно от выборки

**Файлы:**
- Изменить: `listam/ports/database.py`, `listam/adapters/db_sqlite.py`
- Тест: `tests/contracts/test_database_contract.py`

- [x] **Шаг 1: падающие тесты контракта**

```python
# tests/contracts/test_database_contract.py — дописать
def test_alive_matches_are_counted_without_reading_them(db):
    """Счётчик считает строки, а не читает их: на боевых числах это разница
    между 4,0 с и 0,22 с."""
    db.upsert_listing(make_listing("1"), seen_at=NOW)
    db.upsert_listing(make_listing("2"), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_matches([
        Match(request_id=request.id, listing_id="1", score=80.0),
        Match(request_id=request.id, listing_id="2", score=50.0),
    ], NOW)

    assert db.count_matches_alive(request.id) == 2
    assert db.count_matches_alive(request.id, min_score=70.0) == 1


def test_a_retired_match_is_not_counted(db):
    """Счётчик и выборка считают одно и то же: закрытых не видит ни один."""
    db.upsert_listing(make_listing("1"), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_match(Match(request_id=request.id, listing_id="1", score=80.0), NOW)
    db.retire_matches(request.id, keep=set(), now=LATER, reason="бюджет")

    assert db.count_matches_alive(request.id) == 0


def test_a_match_without_a_listing_is_not_counted(db):
    """Счётчик обязан совпадать с выборкой: она идёт JOIN'ом и матч без
    карточки не отдаёт — иначе «…и ещё 1» показывало бы то, чего нет."""
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_match(Match(request_id=request.id, listing_id="сгинувшее", score=80.0), NOW)

    assert db.count_matches_alive(request.id) == 0
    assert db.matches_with_listings(request.id) == []
```

- [x] **Шаг 2: убедиться, что тесты падают**

```bash
.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py -k alive
```

Ожидается FAIL: `AttributeError: … 'count_matches_alive'`.

- [x] **Шаг 3: метод порта**

```python
# listam/ports/database.py — после count_matches
    @abstractmethod
    def count_matches_alive(self, request_id: int, min_score: float | None = None) -> int:
        """Сколько живых матчей заявки выше порога — счётом, без чтения строк.

        Считает ровно то же, что отдала бы `matches_with_listings` без потолка:
        живые, с карточкой в базе, от порога и выше. Иначе «…и ещё 704»
        обещало бы человеку строки, которых он не получит.
        """
```

- [x] **Шаг 4: реализация**

```python
# listam/adapters/db_sqlite.py — после count_matches
    def count_matches_alive(self, request_id: int, min_score: float | None = None) -> int:
        """См. порт. Тот же JOIN, что у витрины, но COUNT вместо колонок."""
        query = ("SELECT COUNT(*) AS n FROM matches m "
                 " JOIN listings l ON l.id = m.listing_id "
                 " WHERE m.request_id = ? AND m.retired_at IS NULL")
        params: list = [request_id]
        if min_score is not None:
            query += " AND m.score >= ?"
            params.append(float(min_score))
        return int(self.conn.execute(query, tuple(params)).fetchone()["n"])
```

- [x] **Шаг 5: тесты проходят**

```bash
.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py
```

Ожидается PASS.

- [x] **Шаг 6: коммит**

```bash
git add listam/ports/database.py listam/adapters/db_sqlite.py tests/contracts/test_database_contract.py
git commit -m "feat(db): счётчик живых матчей отдельно от выборки"
```

### Задача 3.2. Витрина отдаёт страницу, а не список

**Файлы:**
- Изменить: `listam/matches_view.py`, `listam/cli.py:312-328`, `listam/cli.py:344-383`
- Тест: `tests/test_matches_view.py` (новый), `tests/test_matching_scale.py`

- [x] **Шаг 1: падающие тесты витрины**

```python
# tests/test_matches_view.py
"""Витрина: потолок доходит до выборки, а «…и ещё N» остаётся правдой."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from listam.domain.models import Listing, Match, Request
from listam.matches_view import MatchesPage, collect_matches, render_matches
from listam.wiring import build_database, database_path

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    """Конфиг с базой во временной папке и тремя матчами одной заявки."""
    from tests.conftest import make_config       # уже используется другими тестами

    config = make_config(tmp_path)
    database = build_database(config)
    database.connect()
    database.migrate()
    for index in range(3):
        database.upsert_listing(
            Listing(id=str(index), url=f"https://www.list.am/ru/item/{index}",
                    district="Кентрон", price_usd=100000.0 + index, area=60.0,
                    rooms=2, status="active"),
            seen_at=NOW,
        )
    database.upsert_request(Request(external_id="R-1", client_name="Ани"), now=NOW)
    request = database.get_request("R-1")
    database.upsert_matches([
        Match(request_id=request.id, listing_id=str(index), score=90.0 - index)
        for index in range(3)
    ], NOW)
    database.close()
    return config


def test_the_page_knows_how_many_there_are_in_total(prepared):
    page = collect_matches(prepared, limit=1)

    assert isinstance(page, MatchesPage)
    assert len(page.rows) == 1
    assert page.totals["R-1"] == 3


def test_the_tail_counts_rows_that_were_never_read(prepared):
    """«…и ещё 2» берётся из счётчика, а не из длины прочитанного: иначе
    потолок в выборке превратил бы обещание в ложь."""
    page = collect_matches(prepared, limit=1)

    printed = render_matches(page, limit=1)

    assert "…и ещё 2" in printed


def test_a_request_without_matches_is_not_a_section(prepared):
    page = collect_matches(prepared, external_id="R-1", min_score=99.0)

    assert page.rows == []
    assert render_matches(page, limit=50) == "Подобранных вариантов нет."
```

- [x] **Шаг 2: убедиться, что тесты падают**

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_matches_view.py
```

Ожидается FAIL: `ImportError: cannot import name 'MatchesPage'`.

Если `tests/conftest.py` не отдаёт `make_config`, посмотри, как конфиг собирают
`tests/test_matching.py` и `tests/test_clustering_run.py`, и повтори их приём —
общий помощник важнее, чем новый: два способа собрать конфиг в тестах разойдутся
так же, как разошлись пять оркестраторов.

- [x] **Шаг 3: страница вместо списка**

```python
# listam/matches_view.py — заменить MatchRow и collect_matches
MatchRow = tuple[Request, Match, Listing]


@dataclass
class MatchesPage:
    """Что витрина прочитала и сколько всего есть.

    Две величины, а не одна. Потолок теперь доходит до выборки: на боевых
    числах `matches` без `--request` читала 50 633 строки за 4,0 с, а с
    потолком в SQL — 2 247 строк за 0,22 с. Но «…и ещё 704» обязано остаться
    правдой, а прочитанные строки про непрочитанные ничего не знают — их
    считает отдельный запрос.
    """

    rows: list[MatchRow] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)   # ключ группы → всего


def group_key(request: Request) -> str:
    """Ключ раздела витрины: внешний идентификатор, а не `id`.

    Заявка, не записанная в базу, имеет `id = None`, и все такие схлопнулись
    бы в один раздел.
    """
    return request.external_id or f"#{request.id}"


def collect_matches(config: Config, *, external_id: str | None = None,
                    min_score: float | None = None,
                    limit: int | None = None) -> MatchesPage:
    """Матчи для витрины: заявка, матч и объявление одной строкой.

    Объявление приходит вместе с матчем, одним запросом на заявку, и снятое
    из выдачи не выпадает (решение 8): витрина его помечает, а не прячет.
    Матч, у которого объявления в базе нет вовсе, не показывается — `JOIN`
    его не отдаёт; это не «снято», снятое лежит на месте с пометкой.

    `min_score` не задан — берётся порог дайджеста из конфига. `limit` —
    сколько строк **читать**: он доходит до SQL, а сколько их всего, отвечает
    счётчик. Замка здесь нет и записи тоже: витрина читает базу, а не чинит её.
    """
    if min_score is None:
        min_score = settings(config).digest

    storage = build_storage(config)
    local_db = database_path(config)
    if not local_db.exists():
        storage.download(config.get("storage.db_filename", "listam.sqlite"), local_db)

    database = build_database(config)
    database.connect()
    try:
        required = latest_schema_version()
        version = database.schema_version()
        if version < required:
            raise MatchesError(
                f"Матчи не показаны: схема базы {version}, а код ждёт {required}. "
                f"Витрина ничего не мигрирует — накати миграции: "
                f"python -m listam recheck"
            )

        if external_id is None:
            requests = list(database.iter_requests())
        else:
            one = database.get_request(external_id)
            if one is None:
                raise MatchesError(
                    f"заявки {external_id} в базе нет — сначала прочитай источник: "
                    f"python -m listam requests"
                )
            # Статус здесь не проверяется, в отличие от подбора: «кому мы уже
            # звонили по этой квартире» переживает и паузу заявки.
            requests = [one]

        page = MatchesPage()
        for request in requests:
            pairs = database.matches_with_listings(
                request.id, min_score=min_score, limit=limit)
            if not pairs:
                continue
            page.totals[group_key(request)] = database.count_matches_alive(
                request.id, min_score=min_score)
            for match, listing in pairs:
                page.rows.append((request, match, listing))
        return page
    finally:
        database.close()
```

Импорты в шапке файла дополняются:

```python
from dataclasses import dataclass, field
```

- [x] **Шаг 4: печать берёт хвост из счётчика**

```python
# listam/matches_view.py — заменить render_matches целиком
def render_matches(page: MatchesPage, limit: int,
                   min_score: float | None = None) -> str:
    """Витрина: по разделу на заявку, по строке на кластер.

    `min_score` называется в шапке, чтобы пустой раздел читался как «выше
    порога ничего нет», а не как «матчинг не работает». Мерка приходит
    аргументом, а не вычитывается из конфига: печать конфига не читает.

    Хвост «…и ещё N» считается от `page.totals`, а не от длины прочитанного:
    потолок доходит до выборки, и прочитанные строки про остальные ничего
    не знают.
    """
    if not page.rows:
        return "Подобранных вариантов нет."

    by_request: dict[str, list[MatchRow]] = {}
    requests: dict[str, Request] = {}
    for request, match, listing in page.rows:
        key = group_key(request)
        by_request.setdefault(key, []).append((request, match, listing))
        requests[key] = request

    lines: list[str] = []
    for key, group in by_request.items():
        request = requests[key]
        total = page.totals.get(key, len(group))
        who = f" ({request.client_name})" if request.client_name else ""
        head = f"Заявка {request.external_id or key}{who}"
        if min_score is not None:
            head += f", порог дайджеста {min_score:g}"
        head += f" — подобрано {total}"
        if lines:
            lines.append("")
        lines.append(head)
        for _, match, listing in group[:limit]:
            # Снятое видно с первого взгляда, а не из второй строки: минус
            # в начале строки — та же пометка, что в разделе «Снято» у `changes`.
            mark = MINUS if listing.status == "gone" else "•"
            lines.append(
                f"  {mark} {_score(match.score):>10}  {money(listing.price_usd):>10}  "
                f"{per_sqm(listing):>12}  {_place(listing):<26}  {_what(listing):<40}  "
                f"{listing.url}"
            )
            note = _note(match, listing)
            if note:
                lines.append(f"      {note}")
        left = total - len(group[:limit])
        if left > 0:
            lines.append(f"  …и ещё {left}")
    return "\n".join(lines)
```

- [x] **Шаг 5: CLI отдаёт потолок в выборку**

```python
# listam/cli.py — заменить тело _matches
def _matches(config, external_id: str | None, limit: int | None,
             min_score: float | None) -> int:
    from listam.matches_view import (MatchesError, collect_matches, display_limit,
                                     render_matches)
    from listam.matching import settings

    if min_score is None:
        min_score = settings(config).digest
    if limit is None:
        limit = display_limit(config)
    try:
        # Потолок уходит в выборку, а не только в печать: без него витрина
        # читала 50 633 строки, чтобы показать 2 247.
        page = collect_matches(config, external_id=external_id,
                               min_score=min_score, limit=limit)
    except MatchesError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(render_matches(page, limit=limit, min_score=min_score))
    return 0
```

- [x] **Шаг 6: выгрузка берёт строки страницы**

```python
# listam/cli.py — в _export, вместо `matches = collect_matches(...)`
    # Потолок на заявку, а не на весь лист: без него боевые числа дают
    # десятки тысяч строк в .xlsx, и лист «Матчи» открывается минутами.
    # Ноль здесь бессмыслен — это счётчик строк, а не порог (`positive`).
    matches = collect_matches(
        config, limit=positive(config, "export.matches_limit", DEFAULT_MATCHES_LIMIT)
    ).rows
```

- [x] **Шаг 7: тесты проходят**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Ожидается PASS. Тесты, которые звали `collect_matches` и ждали список
(`tests/test_matching.py`, `tests/test_cli.py`, `tests/test_matching_scale.py`),
правятся здесь же: `.rows` там, где нужен список. Если тест сверял «…и ещё N»
по длине списка — теперь он сверяет `page.totals`.

- [x] **Шаг 8: коммит**

```bash
git add listam/matches_view.py listam/cli.py tests/
git commit -m "perf(matches): потолок доходит до выборки, счётчик считается отдельно"
```

### Задача 3.3. `matches --new`: срез «что нового со вчера»

**Файлы:**
- Изменить: `listam/matches_view.py`, `listam/cli.py:79-89`, `listam/cli.py:204-223`
- Тест: `tests/test_matches_view.py`

- [x] **Шаг 1: падающие тесты**

```python
# tests/test_matches_view.py — дописать
from datetime import timedelta

from listam.domain.events import NEW
from listam.matches_view import collect_events, render_events


def test_the_slice_shows_only_what_moved_inside_the_window(prepared):
    """Главное, ради чего фаза: подешевевшая квартира стояла на 150-м месте
    из 627 и человеку не показывалась никогда."""
    database = build_database(prepared)
    database.connect()
    request = database.get_request("R-1")
    database.upsert_matches(
        [Match(request_id=request.id, listing_id="0", score=95.0)],
        NOW + timedelta(hours=5),
    )
    database.close()

    page = collect_events(prepared, since=NOW + timedelta(hours=1),
                          until=NOW + timedelta(hours=6))

    assert [event.match.listing_id for event in page.events] == ["0"]


def test_the_slice_says_when_there_is_nothing(prepared):
    page = collect_events(prepared, since=NOW + timedelta(hours=10),
                          until=NOW + timedelta(hours=11))

    assert page.events == []
    assert "событий нет" in render_events(page, per_request=5)


def test_the_slice_names_the_kind_of_each_event(prepared):
    page = collect_events(prepared, since=NOW - timedelta(hours=1),
                          until=NOW + timedelta(hours=1))

    printed = render_events(page, per_request=5)

    assert "новый" in printed
    assert all(event.kind == NEW for event in page.events)
```

- [x] **Шаг 2: убедиться, что тесты падают**

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_matches_view.py -k slice
```

Ожидается FAIL: `ImportError: cannot import name 'collect_events'`.

- [x] **Шаг 3: выборка событий и её печать**

```python
# listam/matches_view.py — после render_matches
@dataclass
class EventsPage:
    """События окна и как это окно объяснить человеку."""

    events: list = field(default_factory=list)          # list[MatchEvent]
    totals: dict[str, int] = field(default_factory=dict)  # ключ группы → всего событий
    requests: dict[str, Request] = field(default_factory=dict)
    note: str = ""                                       # «с 21.09 19:00 UTC»


def collect_events(config: Config, *, since, until,
                   external_id: str | None = None,
                   min_score: float | None = None,
                   note: str = "") -> EventsPage:
    """События окна по активным заявкам. Та же выборка, что у уведомления.

    Одна выборка и две подачи (решение 10 спеки): текст сообщения нельзя
    проверить иначе, чем отправкой, а отправленное не отзывается. Значит,
    человек обязан уметь посмотреть то же самое в терминале — до отправки.
    """
    if min_score is None:
        min_score = settings(config).digest

    database = build_database(config)
    database.connect()
    try:
        required = latest_schema_version()
        version = database.schema_version()
        if version < required:
            raise MatchesError(
                f"События не показаны: схема базы {version}, а код ждёт {required}. "
                f"Витрина ничего не мигрирует — накати миграции: "
                f"python -m listam recheck"
            )

        if external_id is None:
            requests = list(database.iter_requests())
        else:
            one = database.get_request(external_id)
            if one is None:
                raise MatchesError(
                    f"заявки {external_id} в базе нет — сначала прочитай источник: "
                    f"python -m listam requests"
                )
            requests = [one]

        page = EventsPage(note=note)
        for request in requests:
            rows = database.match_events_since(since, until, request_id=request.id)
            found = events_for(rows, since, until, min_score=min_score)
            if not found:
                continue
            key = group_key(request)
            page.events.extend(found)
            page.totals[key] = len(found)
            page.requests[key] = request
        return page
    finally:
        database.close()


def render_events(page: EventsPage, per_request: int | None,
                  head: str = "Что нового") -> str:
    """Срез событий: по разделу на заявку, лучшие сверху, честный хвост.

    Закрытые собираются в одну строку внизу: «отпало 4 (бюджет 3, …)».
    Закрытие не повод звонить — это объяснение, куда делась вчерашняя
    карточка, и место ему в конце, а не среди вариантов.
    """
    if not page.events:
        return f"{head}: событий нет" + (f" ({page.note})" if page.note else "")

    by_request: dict[str, list] = {}
    for event in page.events:
        by_request.setdefault(_owner_key(page, event), []).append(event)

    lines = [f"{head}{': ' + page.note if page.note else ''}"]
    for key, events in by_request.items():
        request = page.requests[key]
        alive = [event for event in events if event.kind != RETIRED]
        gone = [event for event in events if event.kind == RETIRED]
        shown, total = limited(alive, per_request)

        who = f" ({request.client_name})" if request.client_name else ""
        counts = ", ".join(
            f"{EVENT_LABELS[kind]}: {sum(1 for e in alive if e.kind == kind)}"
            for kind in (NEW, CHEAPER, REVIVED)
            if any(e.kind == kind for e in alive)
        ) or "событий нет"
        lines.append("")
        lines.append(f"Заявка {request.external_id or key}{who} — {counts}")
        for event in shown:
            listing = event.listing
            mark = MINUS if listing.status == "gone" else "•"
            lines.append(
                f"  {mark} {_score(event.match.score):>10}  "
                f"{money(listing.price_usd):>10}  {per_sqm(listing):>12}  "
                f"{_place(listing):<26}  {_what(listing):<40}  {listing.url}"
            )
            note = _event_note(event)
            if note:
                lines.append(f"      {note}")
        left = total - len(shown)
        if left > 0:
            lines.append(
                f"  …и ещё {left} из {total} — "
                f"python -m listam matches --request {request.external_id} --new"
            )
        if gone:
            reasons: dict[str, int] = {}
            for event in gone:
                reason = event.match.retired_reason or "причина не записана"
                reasons[reason] = reasons.get(reason, 0) + 1
            listed = ", ".join(f"{reason} {count}" for reason, count in reasons.items())
            lines.append(f"  отпало {len(gone)} ({listed})")
    return "\n".join(lines)


def _owner_key(page: EventsPage, event) -> str:
    """Ключ раздела, к которому относится событие.

    Заявку событие знает только идентификатором, а раздел витрины называется
    внешним идентификатором — сопоставление лежит в самой странице.
    """
    for key, request in page.requests.items():
        if request.id == event.match.request_id:
            return key
    return f"#{event.match.request_id}"


def _event_note(event) -> str:
    """Вторая строка события: чем оно отличается от вчерашнего.

    «Подешевело с 225 000 $» — ровно тот факт, ради которого этот срез
    и появился: без него квартира стояла на 150-м месте из 627.
    """
    parts = [EVENT_LABELS[event.kind]]
    if event.kind == CHEAPER and event.price_before is not None:
        parts[0] = f"подешевело с {money(event.price_before)}"
    if event.listing.status == "gone":
        parts.append("снято с ленты")
    if event.match.cluster_size and event.match.cluster_size > 1:
        spread = (f", разброс {money(event.match.cluster_spread_usd)}"
                  if event.match.cluster_spread_usd is not None else "")
        parts.append(
            f"{event.match.cluster_size} "
            + _plural(event.match.cluster_size, "объявление", "объявления",
                      "объявлений")
            + spread
        )
    return " · ".join(parts)
```

Импорты в шапке `matches_view.py`:

```python
from listam.domain.events import CHEAPER, EVENT_LABELS, NEW, RETIRED, REVIVED, \
    events_for, limited
```

- [x] **Шаг 4: флаг в командной строке**

```python
# listam/cli.py — в описание подкоманды matches, после --min-score
    matches.add_argument("--new", action="store_true",
                         help="только то, что появилось, подешевело или вернулось "
                              "с прошлой отправки дайджеста")
    matches.add_argument("--hours", type=float,
                         help="окно среза --new в часах назад от «сейчас» "
                              "(по умолчанию — с прошлой отправки дайджеста)")
```

```python
# listam/cli.py — в _dispatch, ветка matches, перед `return _matches(...)`
        if args.hours is not None and not args.new:
            print(
                "--hours работает только со срезом --new: у витрины по баллу "
                "окна нет, она показывает всё живое.",
                file=sys.stderr,
            )
            return 2
        if args.hours is not None and args.hours <= 0:
            print(
                f"--hours {args.hours:g} не годится: окно считается назад от «сейчас», "
                "и отрицательное или нулевое окно всегда пусто. Нужно число больше нуля.",
                file=sys.stderr,
            )
            return 2
        if args.new:
            return _matches_new(config, external_id=args.request,
                                limit=args.limit, min_score=args.min_score,
                                hours=args.hours)
```

```python
# listam/cli.py — рядом с _matches
def _matches_new(config, external_id: str | None, limit: int | None,
                 min_score: float | None, hours: float | None) -> int:
    from listam.matches_view import MatchesError, collect_events, display_limit, \
        render_events
    from listam.matching import settings
    from listam.notifications import window_for

    if min_score is None:
        min_score = settings(config).digest
    if limit is None:
        limit = display_limit(config)
    since, until, note = window_for(config, kind="digest", hours=hours)
    try:
        page = collect_events(config, since=since, until=until,
                              external_id=external_id, min_score=min_score, note=note)
    except MatchesError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(render_events(page, per_request=limit))
    return 0
```

`window_for` пишется в фазе 4 вместе с командой `notify`. Чтобы фаза 3
не зависела от ненаписанного, в ней заводится **только окно по часам**:

```python
# listam/notifications.py — новый файл, в фазе 3 только это
"""Окно уведомлений: с какого момента считаем события и как это назвать.

Сборка сообщения и отправка появятся в фазе 4; окно живёт здесь с фазы 3,
потому что срез витрины `matches --new` мерится ровно тем же.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from listam.config import Config, threshold

DEFAULT_FALLBACK_HOURS = {"hot": 2.0, "digest": 24.0, "feed": 24.0}


def window_for(config: Config, kind: str, hours: float | None = None,
               database=None) -> tuple[datetime, datetime, str]:
    """Окно `(since, until]` и человеческое объяснение, откуда оно взялось.

    `hours` — окно назад от «сейчас», как у `changes --hours`. Иначе мерка —
    `window_to` последней успешной отправки этого вида: повторный запуск
    не шлёт то же самое второй раз, а сбой сети не теряет событие. Отправок
    ещё не было — берём запасное окно из конфига и **говорим об этом вслух**,
    чтобы пустой список не читался как «на рынке тишина».
    """
    until = datetime.now(timezone.utc)
    if hours is not None:
        since = until - timedelta(hours=float(hours))
        return since, until, f"за последние {hours:g} ч (с {since:%d.%m %H:%M} UTC)"

    last = database.last_notification(kind) if database is not None else None
    if last is not None and last.window_to is not None:
        return last.window_to, until, (
            f"с прошлой отправки ({last.window_to:%d.%m %H:%M} UTC)"
        )

    fallback = threshold(config, f"notify.{kind}.fallback_hours",
                         DEFAULT_FALLBACK_HOURS[kind])
    fallback = DEFAULT_FALLBACK_HOURS[kind] if fallback is None else float(fallback)
    since = until - timedelta(hours=fallback)
    return since, until, (
        f"отправок ещё не было — беру последние {fallback:g} ч "
        f"(с {since:%d.%m %H:%M} UTC)"
    )
```

В фазе 3 `_matches_new` зовёт `window_for(config, kind="digest", hours=hours)`
без базы: пока журнал не читается, окно берётся запасное. Фаза 4 передаёт туда
открытую базу — и подпись меняется сама.

- [x] **Шаг 5: тест командной строки**

```python
# tests/test_cli.py — дописать
def test_hours_without_new_is_refused(tmp_path, capsys):
    """Бессмысленный ввод отклоняется на входе: у витрины по баллу окна нет."""
    from listam.cli import main

    assert main(["--config-dir", str(config_dir(tmp_path)), "matches", "--hours", "5"]) == 2
    assert "--hours работает только со срезом --new" in capsys.readouterr().err
```

`config_dir` — помощник, которым пользуются соседние тесты этого файла;
загляни в начало `tests/test_cli.py` и возьми тот же.

- [x] **Шаг 6: тесты проходят**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Ожидается PASS.

- [x] **Шаг 7: коммит**

```bash
git add listam/matches_view.py listam/notifications.py listam/cli.py tests/
git commit -m "feat(matches): срез --new — что появилось, подешевело и вернулось"
```

### Конец фазы 3

- [x] Батарея: ожидается **723 passed, 18 skipped**.
- [x] Замерить на базе фазы 1 и записать в отчёт **обе** команды:

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam matches
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam matches --new
```

Эталон фазы 8: 50 633 строки за 4,0 с. Ожидание — около 2 247 строк за 0,22 с
в процессе; в отчёт идёт то, что напечатала команда, а не ожидание.
- [x] Дописать «Результат фазы 3» и стартовый промпт для фазы 4.
- [x] Коммит.

---

# Фаза 4. Сообщение и команда `notify` без сети

**Одна сессия.** Появляется текст уведомления, журнал отправок начинает
работать и команда `notify` на общем каркасе. Канал — `stdout`: наружу
в этой фазе не уходит ничего, и это нарочно. Telegram — фаза 5.

**Ожидается после фазы:** **747 passed, 18 skipped** (723 плюс 24: восемь
контрактных — четыре теста на двух реализациях, один конфигурационный,
двенадцать на уведомление, три на командную строку). Число пересчитано
от **723**: в фазе 3 добавился двенадцатый тест (см. «Результат фазы 3»),
а на уведомление тестов вышло двенадцать, а не восемь.

### Задача 4.1. Порт уведомления принимает адресата

**Файлы:**
- Изменить: `listam/ports/notifier.py`, `listam/wiring.py:136-142`
- Тест: `tests/contracts/test_notifier_contract.py` (новый)

- [x] **Шаг 1: падающий контрактный тест**

```python
# tests/contracts/test_notifier_contract.py
"""Контрактный тест порта Notifier: одинаков для любой реализации.

Канал один — брокерский (решение 4 спеки), но адресата порт принимает с
первого дня: маршруты появятся позже, а менять подпись у трёх реализаций
разом — это тот самый разъезд, который план ловит контрактом.
"""
from __future__ import annotations

import pytest

from listam.ports.notifier import NullNotifier, Notifier, StdoutNotifier


@pytest.fixture(params=["none", "stdout"])
def notifier(request) -> Notifier:
    return NullNotifier() if request.param == "none" else StdoutNotifier()


def test_is_a_notifier(notifier):
    assert isinstance(notifier, Notifier)


def test_sends_without_raising(notifier):
    notifier.send("Заявка R-1 — 3 новых")


def test_accepts_an_addressee(notifier):
    """`to` не обязателен, но принимается всеми: иначе первая же попытка
    послать не туда, куда обычно, потребует править три класса."""
    notifier.send("Заявка R-1 — 3 новых", to="-1001234567890")


def test_describes_itself(notifier):
    """`doctor` показывает канал словами: «уведомления никуда не идут» —
    это ответ, а пустая строка — нет."""
    assert notifier.describe()
```

- [x] **Шаг 2: убедиться, что тесты падают**

```bash
.venv/Scripts/python.exe -m pytest -q tests/contracts/test_notifier_contract.py
```

Ожидается FAIL: `TypeError: send() got an unexpected keyword argument 'to'`.

- [x] **Шаг 3: порт**

```python
# listam/ports/notifier.py — заменить файл целиком
"""Порт Notifier: доставка уведомлений брокеру.

Канал один и он брокерский (решение 4 спеки M3): клиентам брокер пересылает
сам. Адресата порт всё равно принимает — маршруты появятся позже, и менять
подпись у трёх реализаций разом дороже, чем принять `to=None` сегодня.

Отказ канала — `NotifyError`, а не голое исключение библиотеки: команда,
которая его ловит, не обязана знать, чем именно ходит адаптер в сеть.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class NotifyError(Exception):
    """Сообщение не ушло: сеть, токен, чат или лимит канала."""


class Notifier(ABC):
    @abstractmethod
    def send(self, text: str, to: str | None = None) -> None:
        """Отправляет сообщение; `to` не задан — адресат по умолчанию из конфига."""

    @abstractmethod
    def describe(self) -> str:
        """Чем является канал — для `doctor` и для отчёта команды."""


class NullNotifier(Notifier):
    """`notify.kind: none` — канал не настроен, и это не ошибка."""

    def send(self, text: str, to: str | None = None) -> None:
        return None

    def describe(self) -> str:
        return "уведомления никуда не идут (notify.kind: none)"


class StdoutNotifier(Notifier):
    """`notify.kind: stdout` — сообщение печатается, а не отправляется.

    Это не заглушка, а рабочий режим: на нём проверяют текст до того, как
    он уйдёт человеку. Отправленное не отзывается.
    """

    def send(self, text: str, to: str | None = None) -> None:
        print(f"[уведомление{' → ' + to if to else ''}]\n{text}")

    def describe(self) -> str:
        return "уведомления печатаются в консоль (notify.kind: stdout)"
```

- [x] **Шаг 4: тесты проходят**

```bash
.venv/Scripts/python.exe -m pytest -q tests/contracts/test_notifier_contract.py tests/test_wiring.py
```

Ожидается PASS.

- [x] **Шаг 5: коммит**

```bash
git add listam/ports/notifier.py tests/contracts/test_notifier_contract.py
git commit -m "feat(notify): порт принимает адресата и описывает себя"
```

### Задача 4.2. Секция `notify` в конфиге

**Файлы:**
- Изменить: `config/dev.yaml`, `config/prod.yaml`
- Тест: `tests/test_config.py`

- [x] **Шаг 1: падающий тест**

```python
# tests/test_config.py — дописать
def test_both_configs_declare_the_notification_knobs():
    """Ручка, которой нет в конфиге, не существует для человека: он не знает,
    что её можно покрутить, и правит код."""
    import yaml
    from pathlib import Path

    for name in ("dev", "prod"):
        data = yaml.safe_load(Path(f"config/{name}.yaml").read_text(encoding="utf-8"))
        notify = data["notify"]
        assert notify["kind"] in ("none", "stdout", "telegram")
        for kind in ("hot", "digest", "feed"):
            assert "enabled" in notify[kind], f"{name}: notify.{kind}.enabled"
            assert "fallback_hours" in notify[kind], f"{name}: notify.{kind}.fallback_hours"
        assert "per_request" in notify["hot"]
        assert "per_request" in notify["digest"]
        assert "wide_request" in notify["digest"]
        assert "limit" in notify["feed"]
```

- [x] **Шаг 2: убедиться, что тест падает**

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_config.py -k notification
```

Ожидается FAIL: `KeyError: 'hot'`.

- [x] **Шаг 3: секция в обоих конфигах**

Числа `per_request`, `wide_request` и `limit` берутся **из отчёта фазы 1**;
ниже — форма и комментарии, значения подставь замеренные.

```yaml
# config/dev.yaml и config/prod.yaml — заменить секцию notify
notify:
  kind: stdout                  # none | stdout | telegram
  # token: ${TELEGRAM_BOT_TOKEN}      # для kind: telegram
  # chat_id: ${TELEGRAM_CHAT_ID}      # чат брокера: клиентам он пересылает сам
  hot:                          # «звони сейчас»: раз в час, после match --new
    enabled: true
    per_request: 5              # строк на заявку; порог балла — match.thresholds.hot
    fallback_hours: 2           # окно, когда отправок ещё не было
  digest:                       # «посмотри вечером»: раз в сутки
    enabled: true
    per_request: 10             # строк на заявку; порог — match.thresholds.digest
    wide_request: 50            # событий больше — заявка помечается как слишком широкая
    include_retired: true       # тихий раздел «отпало»: куда делась вчерашняя карточка
    fallback_hours: 24
  feed:                         # что пришло на ленту вне заявок
    enabled: false              # тумблер: от заявок не зависит вовсе
    limit: 10                   # строк в сообщении, лучшие по выгодности
    fallback_hours: 24
```

В `config/prod.yaml` `kind` остаётся `stdout` до конца фазы 5: канал наружу
включается только после приёмки на живом чате.

- [x] **Шаг 4: тест проходит**

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_config.py
```

Ожидается PASS.

- [x] **Шаг 5: коммит**

```bash
git add config/dev.yaml config/prod.yaml tests/test_config.py
git commit -m "feat(config): секция notify — тумблеры, лимиты и запасные окна"
```

### Задача 4.3. Сборка сообщения и отправка

**Файлы:**
- Изменить: `listam/notifications.py`
- Тест: `tests/test_notifications.py` (новый)

- [x] **Шаг 1: падающие тесты**

```python
# tests/test_notifications.py
"""Уведомление: окно, текст, журнал, тумблеры. Сети здесь нет."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from listam.domain.models import Listing, Match, Request
from listam.notifications import run_notify
from listam.wiring import build_database

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def prepared(tmp_path):
    """Конфиг, база и одна заявка с двумя свежими матчами."""
    from tests.conftest import make_config

    config = make_config(tmp_path)
    database = build_database(config)
    database.connect()
    database.migrate()
    for index in range(2):
        database.upsert_listing(
            Listing(id=str(index), url=f"https://www.list.am/ru/item/{index}",
                    district="Кентрон", price_usd=100000.0, area=60.0, rooms=2),
            seen_at=NOW,
        )
    database.upsert_request(Request(external_id="R-1", client_name="Ани"), now=NOW)
    request = database.get_request("R-1")
    database.upsert_matches([
        Match(request_id=request.id, listing_id="0", score=91.0),
        Match(request_id=request.id, listing_id="1", score=85.0),
    ], datetime.now(timezone.utc))
    database.close()
    return config


def test_a_send_writes_one_line_in_the_journal(prepared, capsys):
    report = run_notify(prepared, kind="hot")

    assert report.errors == 0
    assert report.events == 2
    assert report.sent is True

    database = build_database(prepared)
    database.connect()
    last = database.last_notification("hot")
    database.close()
    assert last is not None
    assert last.events == 2
    assert "Заявка R-1" in last.text


def test_the_second_run_sends_nothing_new(prepared):
    """Ради этого и заведён журнал: повторный запуск не шлёт то же дважды."""
    run_notify(prepared, kind="hot")

    again = run_notify(prepared, kind="hot")

    assert again.events == 0
    assert "событий нет" in again.text


def test_dry_run_prints_but_does_not_remember(prepared):
    """«Покажи, что послал бы» обязано быть безопасным: окно не двигается,
    и то же самое потом уйдёт в чат."""
    report = run_notify(prepared, kind="hot", dry_run=True)

    assert report.dry_run is True
    assert report.sent is False
    database = build_database(prepared)
    database.connect()
    assert database.last_notification("hot") is None
    database.close()


def test_a_disabled_kind_does_nothing(prepared):
    prepared.data["notify"]["hot"]["enabled"] = False

    report = run_notify(prepared, kind="hot")

    assert report.errors == 0
    assert report.sent is False
    assert "выключены в конфиге" in report.text


def test_a_broken_channel_keeps_the_window_where_it_was(prepared, monkeypatch):
    """Сообщение не ушло — строки в журнале нет: следующий запуск пошлёт то,
    что не дошло. Иначе событие теряется навсегда."""
    from listam.ports.notifier import NotifyError, StdoutNotifier

    def refuse(self, text, to=None):
        raise NotifyError("сеть отказала")

    monkeypatch.setattr(StdoutNotifier, "send", refuse)

    report = run_notify(prepared, kind="hot")

    assert report.errors == 1
    database = build_database(prepared)
    database.connect()
    assert database.last_notification("hot") is None
    database.close()


def test_a_wide_request_is_marked(prepared):
    """Сотни событий в сутки — это незаполненная заявка, а не рынок."""
    prepared.data["notify"]["digest"]["wide_request"] = 1
    prepared.data["notify"]["digest"]["per_request"] = 1

    report = run_notify(prepared, kind="digest", dry_run=True)

    assert "слишком широкая" in report.text
    assert "…и ещё 1 из 2" in report.text


def test_an_empty_window_still_moves_it(prepared):
    """Пустая отправка тоже пишется в журнал: иначе завтра придёт сегодняшняя
    пустота плюс завтрашние события — с окном в двое суток."""
    run_notify(prepared, kind="digest")
    report = run_notify(prepared, kind="digest")

    assert report.events == 0
    database = build_database(prepared)
    database.connect()
    assert database.last_notification("digest").events == 0
    database.close()
```

- [x] **Шаг 2: убедиться, что тесты падают**

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_notifications.py
```

Ожидается FAIL: `ImportError: cannot import name 'run_notify'`.

- [x] **Шаг 3: отчёт и настройки**

```python
# listam/notifications.py — дописать после window_for
from dataclasses import dataclass

from listam.config import ConfigError, positive
from listam.domain.models import Notification
from listam.matches_view import collect_events, render_events
from listam.matching import settings
from listam.ports.notifier import NotifyError
from listam.runner import SessionRefused, publish, working_session
from listam.wiring import build_notifier

KINDS = ("hot", "digest", "feed")


@dataclass
class NotifyReport:
    """Что сделало уведомление. Печатает это CLI, а не сам отправитель."""

    kind: str = ""
    scope: str = ""             # человеческое объяснение окна
    events: int = 0
    requests: int = 0
    sent: bool = False
    dry_run: bool = False
    text: str = ""
    errors: int = 0
    notes: str | None = None

    def render(self) -> str:
        lines = [f"Уведомление ({self.kind}): {self.scope}"]
        if self.text:
            lines.append(self.text)
        lines.append(
            f"Событий: {self.events}, заявок: {self.requests}, "
            + ("отправлено" if self.sent else
               "не отправлено (пробный прогон)" if self.dry_run else "не отправлено")
        )
        if self.notes:
            lines.append(self.notes)
        return "\n".join(lines)


def enabled(config: Config, kind: str) -> bool:
    """Тумблер вида уведомления. Выключено — это решение человека, не ошибка."""
    return bool(threshold(config, f"notify.{kind}.enabled", True))


def per_request(config: Config, kind: str) -> int | None:
    """Сколько строк на заявку. Ноль бессмыслен — это счётчик строк."""
    key = f"notify.{kind}.limit" if kind == "feed" else f"notify.{kind}.per_request"
    value = positive(config, key, 10)
    return None if value is None else int(value)
```

- [x] **Шаг 4: сам прогон**

```python
# listam/notifications.py — дописать в конец
def run_notify(config: Config, *, kind: str, dry_run: bool = False) -> NotifyReport:
    """Одна отправка одного вида. Сводку печатает вызывающий.

    Порядок здесь и есть решение: сообщение уходит **до** записи в журнал,
    и запись делается только после успеха. Наоборот было бы «отправлено»
    в базе при неотправленном сообщении — и потерянное событие навсегда.

    Замок, свежая копия, миграции и заливка — общий каркас
    (`listam/runner.py`): тот же порядок, что у прогона, пересчёта, кластеров,
    заявок и подбора. Пятой копии этого блока не будет.
    """
    if kind not in KINDS:
        raise ConfigError(f"вид уведомления {kind!r} не из списка: {', '.join(KINDS)}")

    report = NotifyReport(kind=kind, dry_run=dry_run)
    if not enabled(config, kind):
        report.text = f"уведомления вида {kind} выключены в конфиге (notify.{kind}.enabled)"
        report.scope = "тумблер выключен"
        return report

    tuning = settings(config)
    min_score = tuning.hot if kind == "hot" else tuning.digest
    notes: list[str] = []
    try:
        with working_session(config) as session:
            notes.extend(session.notes)
            session.notes = notes
            database = session.database

            since, until, scope = window_for(config, kind, database=database)
            report.scope = scope

            if kind == "feed":
                report.text, report.events = _feed_text(database, config, since, until)
            else:
                page = collect_events(config, since=since, until=until,
                                      min_score=min_score, note=scope)
                report.events = len(page.events)
                report.requests = len(page.totals)
                report.text = _match_text(page, config, kind)

            if dry_run:
                notes.append("пробный прогон: не отправлено, журнал не тронут")
                return report

            notifier = build_notifier(config)
            try:
                notifier.send(report.text)
            except NotifyError as exc:
                report.errors = 1
                notes.append(f"канал отказал: {exc}. Окно не сдвинуто — "
                             f"следующий запуск пошлёт то же самое")
                return report
            report.sent = True

            database.record_notification(Notification(
                kind=kind, sent_at=datetime.now(timezone.utc),
                window_from=since, window_to=until,
                events=report.events, requests=report.requests, text=report.text,
            ))
            publish(session, config, "журнал уведомлений")
            report.errors += session.failures
    except SessionRefused as exc:
        report.errors = 1
        notes.append(str(exc))
    finally:
        report.notes = "; ".join(note for note in notes if note) or None
    return report


def _match_text(page, config: Config, kind: str) -> str:
    """Текст уведомления по заявкам плюс пометка слишком широких заявок.

    Широкая заявка — это разговор с брокером, а не с рынком: 10 250 матчей
    и 7 515 горячих на одной заявке боевая приёмка уже видела.
    """
    head = "Звони сейчас" if kind == "hot" else "Что нового со вчера"
    text = render_events(page, per_request=per_request(config, kind), head=head)
    if kind != "digest":
        return text

    wide = positive(config, "notify.digest.wide_request", 50)
    if wide is None:
        return text
    marked = []
    for line in text.splitlines():
        marked.append(line)
        for key, total in page.totals.items():
            if line.startswith(f"Заявка {key}") and total > int(wide):
                marked.append(
                    f"  ⚠ заявка слишком широкая: {total} событий за окно. "
                    f"Сузь районы или бюджет, иначе разговор не состоится"
                )
    return "\n".join(marked)


def _feed_text(database, config: Config, since, until) -> tuple[str, int]:
    """Что пришло на ленту вне заявок: счётчики и лучшие по выгодности.

    Тумблер отдельный нарочно: брокеру нужно видеть ленту, даже когда
    ни одна заявка этого не взяла.
    """
    from listam.changes import money, per_sqm
    from listam.domain.stats import median_price_per_sqm_by_district

    fresh = [item for item in database.listings_first_seen_since(since)
             if item.first_seen is not None and item.first_seen <= until]
    cheaper = [row for row in database.price_changes_since(since)
               if row[1] is not None and row[2] is not None and row[2] < row[1]]
    gone = database.listings_gone_since(since)

    medians = median_price_per_sqm_by_district(database.listings_for_matching())

    def cheapness(item) -> float:
        """Насколько объявление дешевле медианы своего района. Нет медианы —
        считаем ноль: не наказываем и не награждаем, как и скоринг."""
        median = medians.get(item.district or "")
        if not median or item.price_per_sqm is None:
            return 0.0
        return (median - item.price_per_sqm) / median

    best = sorted(fresh, key=cheapness, reverse=True)[:per_request(config, "feed") or 0]
    lines = [
        f"На ленте: новых {len(fresh)}, подешевели {len(cheaper)}, снято {len(gone)}",
    ]
    for item in best:
        lines.append(
            f"  • {money(item.price_usd):>10}  {per_sqm(item):>12}  "
            f"{item.district or '—'}, {item.street or '—'}  {item.url}"
        )
    left = len(fresh) - len(best)
    if left > 0:
        lines.append(f"  …и ещё {left} — python -m listam changes")
    return "\n".join(lines), len(fresh)
```

Шапка файла дополняется импортом `Config` и `threshold` (они уже есть),
а также `datetime`/`timezone` — они тоже уже импортированы в `window_for`.

- [x] **Шаг 5: тесты проходят**

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_notifications.py
```

Ожидается PASS (7 тестов).

- [x] **Шаг 6: коммит**

```bash
git add listam/notifications.py tests/test_notifications.py
git commit -m "feat(notify): окно, текст, журнал и тумблеры уведомлений"
```

### Задача 4.4. Команда `notify`

**Файлы:**
- Изменить: `listam/cli.py`, `README.md`
- Тест: `tests/test_cli.py`

- [x] **Шаг 1: падающие тесты командной строки**

```python
# tests/test_cli.py — дописать
def test_notify_without_a_kind_is_refused(tmp_path, capsys):
    """Ни одного флага — это не «пошли всё»: три вида шлют разное разным людям."""
    from listam.cli import main

    assert main(["--config-dir", str(config_dir(tmp_path)), "notify"]) == 2
    assert "укажи --hot" in capsys.readouterr().err


def test_notify_with_two_kinds_is_refused(tmp_path, capsys):
    from listam.cli import main

    assert main(["--config-dir", str(config_dir(tmp_path)),
                 "notify", "--hot", "--digest"]) == 2
    assert "вместе не работают" in capsys.readouterr().err


def test_notify_dry_run_prints_the_message(tmp_path, capsys):
    from listam.cli import main

    assert main(["--config-dir", str(config_dir(tmp_path)),
                 "notify", "--digest", "--dry-run"]) == 0
    printed = capsys.readouterr().out
    assert "Уведомление (digest)" in printed
```

- [x] **Шаг 2: убедиться, что тесты падают**

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_cli.py -k notify
```

Ожидается FAIL: `argparse` не знает команды `notify` (SystemExit: 2 с другим текстом).

- [x] **Шаг 3: подкоманда**

```python
# listam/cli.py — после подкоманды matches
    notify = commands.add_parser(
        "notify", help="послать уведомление: горячее, дайджест или сводку по ленте")
    notify.add_argument("--hot", action="store_true",
                        help="немедленное: что появилось выше порога match.thresholds.hot")
    notify.add_argument("--digest", action="store_true",
                        help="дневная сводка: всё, что тронулось с прошлой отправки")
    notify.add_argument("--feed", action="store_true",
                        help="что пришло на ленту вне заявок")
    notify.add_argument("--dry-run", action="store_true",
                        help="показать, что было бы послано, и не посылать")
```

- [x] **Шаг 4: разбор и вызов**

```python
# listam/cli.py — в _dispatch, после ветки matches
    if args.command == "notify":
        # Три вида — три разных разговора, и «оба сразу» не значит ничего.
        # Бессмысленный ввод отклоняется на входе: угадав за человека, мы
        # послали бы не то, что он просил, — а отправленное не отзывается.
        chosen = [name for name, on in (("--hot", args.hot), ("--digest", args.digest),
                                        ("--feed", args.feed)) if on]
        if len(chosen) > 1:
            print(
                f"{' и '.join(chosen)} вместе не работают: это три разных "
                "уведомления. Выбери одно.",
                file=sys.stderr,
            )
            return 2
        if not chosen:
            print(
                "Нечего посылать: укажи --hot для немедленного, --digest "
                "для дневной сводки или --feed для сводки по ленте.",
                file=sys.stderr,
            )
            return 2
        return _notify(config, kind=chosen[0].lstrip("-"), dry_run=args.dry_run)
```

```python
# listam/cli.py — рядом с _match
def _notify(config, kind: str, dry_run: bool) -> int:
    from listam.notifications import run_notify

    report = run_notify(config, kind=kind, dry_run=dry_run)
    print(report.render())
    return 1 if report.errors else 0
```

- [x] **Шаг 5: срез витрины мерится тем же журналом**

В фазе 3 `_matches_new` звал `window_for` без базы — журнала тогда ещё не было,
и окно всегда бралось запасное. Теперь журнал есть, и «две подачи одной
выборки» (решение 10) обязаны мериться одинаково: иначе `matches --new`
покажет одно, а `notify --digest` пошлёт другое.

```python
# listam/cli.py — в _matches_new, вместо строки window_for(...)
    # Окно то же, что у `notify --digest`: срез витрины и текст сообщения
    # обязаны показывать одно и то же, иначе сличить их глазами нельзя.
    # Базу открываем на чтение и без замка: витрина её не чинит.
    database = build_database(config)
    database.connect()
    try:
        since, until, note = window_for(config, kind="digest", hours=hours,
                                        database=database)
    finally:
        database.close()
```

Тест на это — здесь же:

```python
# tests/test_notifications.py — дописать
def test_the_view_and_the_message_measure_the_same_window(prepared, capsys):
    """Витрина `matches --new` и `notify --digest` берут окно из одного
    журнала: иначе сличить отправляемое глазами невозможно."""
    from listam.cli import main
    from listam.notifications import run_notify

    run_notify(prepared, kind="digest")          # окно сдвинулось

    assert main(["--config-dir", str(prepared.path.parent), "matches", "--new"]) == 0
    assert "с прошлой отправки" in capsys.readouterr().out
```

- [x] **Шаг 6: README (иначе падает `test_docs.py`)**

`tests/test_docs.py::test_readme_names_every_command_the_cli_has` требует, чтобы
в README была строка `python -m listam notify`. В раздел «Матчинг и витрина»
дописывается подраздел:

```markdown
## Уведомления и дайджест

```bash
.venv/Scripts/python -m listam matches --new       # что нового со вчера, в терминал
.venv/Scripts/python -m listam notify --hot        # «звони сейчас», раз в час
.venv/Scripts/python -m listam notify --digest     # дневная сводка, вечером
.venv/Scripts/python -m listam notify --feed       # что пришло на ленту вне заявок
.venv/Scripts/python -m listam notify --digest --dry-run   # показать и не посылать
```

**Событие — это не «матч есть», а «сегодня с ним что-то случилось».** На боевых
числах порог `hot: 70` даёт 35 572 горячих матча, а одна широкая заявка — 7 515:
посылать по одному сообщению на матч нельзя ни в каком виде. Уведомление
показывает четыре вида событий: вариант **появился**, **подешевел**, **вернулся**
(закрытый матч снова подтвердился) и **отпал**. Последнее живёт отдельной тихой
строкой внизу дайджеста: закрытие не повод звонить, это объяснение, куда делась
вчерашняя карточка.

**Окно считается от прошлой отправки, а не «за сутки».** Каждая успешная
отправка пишет строку в журнал `notifications`; следующая начинается там, где
кончилась прошлая. Поэтому повторный запуск не шлёт то же самое второй раз,
а отказ канала ничего не теряет: строки нет — окно не сдвинулось. Отправок ещё
не было — берётся `notify.<вид>.fallback_hours`, и команда говорит об этом
вслух.

**Заявка, у которой событий сотни, помечается.** На заявку печатается
`notify.<вид>.per_request` лучших по баллу и честный хвост «…и ещё N из M».
Перевалившая за `notify.digest.wide_request` получает отдельную строку: столько
событий в сутки — это незаполненная заявка, а не рынок.

**`--dry-run` показывает то, что ушло бы в чат, и не пишет в журнал.**
Отправленное не отзывается, и брокер не должен узнавать об ошибке из чата
клиента. Тот же текст можно посмотреть витриной: `matches --new`.
```

- [x] **Шаг 7: тесты проходят**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Ожидается PASS, включая `tests/test_docs.py`.

- [x] **Шаг 8: коммит**

```bash
git add listam/cli.py README.md tests/test_cli.py tests/test_notifications.py
git commit -m "feat(cli): команда notify и раздел README про уведомления"
```

### Конец фазы 4

- [x] Батарея: **747 passed, 18 skipped** (86,23 с).
- [x] Прогнать на базе фазы 1 **и вложить вывод в отчёт**:

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam notify --digest --dry-run
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam notify --hot --dry-run
```

В отчёт: сколько событий, сколько заявок, сколько строк в сообщении, как
выглядит пометка широкой заявки. Числа — из вывода команды.
- [x] Дописать «Результат фазы 4» и стартовый промпт для фазы 5.
- [x] Коммит.

---

# Фаза 5. Telegram

**Одна сессия.** Канал наружу. Всё, что можно проверить без сети, проверяется
без сети; живой чат — один явный шаг в конце и приёмка фазы 7.

**Ожидается после фазы:** **749 passed, 25 skipped** (742 плюс пять тестов
адаптера и два `doctor`; четыре новых skip — контракт `telegram` без токена
в окружении, по одному на каждый тест контракта, и ещё три — живые тесты
задачи 5.4, которые без `TELEGRAM_LIVE=1` не запускаются никогда).

**Ключи Telegram нужны именно здесь** — раньше пятой фазы им применения нет
(фазы 1–4 наружу не ходят вовсе). Где они лежат — задача 5.3, шаг 0.

### Задача 5.1. Адаптер

**Файлы:**
- Создать: `listam/adapters/notify_telegram.py`
- Изменить: `listam/wiring.py:136-142`
- Тест: `tests/test_notify_telegram.py` (новый), `tests/contracts/test_notifier_contract.py`

- [ ] **Шаг 1: падающие тесты адаптера**

```python
# tests/test_notify_telegram.py
"""Telegram: длинное сообщение, отказ сети, адресат по умолчанию.

Сети здесь нет: `requests.post` подменяется. Живой чат — приёмка фазы 7,
и она делается руками, потому что отправленное не отзывается.
"""
from __future__ import annotations

import pytest

from listam.adapters.notify_telegram import LIMIT, TelegramNotifier, split_message
from listam.ports.notifier import NotifyError


class Answer:
    def __init__(self, ok=True, status=200, text='{"ok":true}'):
        self.ok, self.status_code, self.text = ok, status, text

    def json(self):
        return {"ok": self.ok, "description": "нет"}


def test_a_message_goes_to_the_chat_from_the_config(monkeypatch):
    sent = []

    def post(url, json, timeout):
        sent.append((url, json))
        return Answer()

    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post", post)
    TelegramNotifier(token="123:abc", chat_id="-100500").send("Заявка R-1 — 3 новых")

    url, payload = sent[0]
    assert url.endswith("/bot123:abc/sendMessage")
    assert payload["chat_id"] == "-100500"
    assert payload["text"] == "Заявка R-1 — 3 новых"


def test_an_explicit_addressee_wins(monkeypatch):
    sent = []
    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post",
                        lambda url, json, timeout: sent.append(json) or Answer())

    TelegramNotifier(token="t", chat_id="-100500").send("текст", to="-100777")

    assert sent[0]["chat_id"] == "-100777"


def test_a_refusal_of_the_channel_is_a_notify_error(monkeypatch):
    """Чужое исключение наружу не выпускается: команда ловит `NotifyError`
    и не обязана знать, чем адаптер ходит в сеть."""
    monkeypatch.setattr(
        "listam.adapters.notify_telegram.requests.post",
        lambda url, json, timeout: Answer(ok=False, status=403,
                                          text='{"ok":false,"description":"forbidden"}'),
    )

    with pytest.raises(NotifyError):
        TelegramNotifier(token="t", chat_id="-1").send("текст")


def test_a_long_message_is_split_by_sections():
    """Резать посреди строки нельзя: обрезанная ссылка — это несостоявшийся
    звонок. Режем по разделам, в крайнем случае — по строкам."""
    section = "Заявка R-1\n" + "\n".join(f"  • строка {i}" for i in range(200))
    text = "\n\n".join([section] * 4)

    parts = split_message(text)

    assert len(parts) > 1
    assert all(len(part) <= LIMIT for part in parts)
    assert "".join(parts).count("https") == text.count("https")


def test_a_short_message_stays_one_piece():
    assert split_message("Заявка R-1 — 3 новых") == ["Заявка R-1 — 3 новых"]
```

- [ ] **Шаг 2: убедиться, что тесты падают**

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_notify_telegram.py
```

Ожидается FAIL: `ModuleNotFoundError: listam.adapters.notify_telegram`.

- [ ] **Шаг 3: адаптер**

```python
# listam/adapters/notify_telegram.py
"""Реализация Notifier поверх Telegram Bot API.

Зависимость — `requests`, она уже в проекте (ею ходит `fetcher_http`).
Секреты сюда приходят аргументами: токен и чат живут в `.env`, выбор
адаптера — в конфиге, знание имени — в `listam/wiring.py`.

Telegram режет сообщение на 4096 символах. Резать посреди строки нельзя:
обрезанная ссылка — это несостоявшийся звонок. Режем по разделам (пустая
строка), внутри раздела — по строкам.
"""
from __future__ import annotations

import requests

from listam.ports.notifier import NotifyError, Notifier

DEFAULT_API = "https://api.telegram.org"
LIMIT = 4096
DEFAULT_TIMEOUT = 20.0


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    """Сообщение, разрезанное так, чтобы ни одна строка не разорвалась."""
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        # Строка длиннее лимита целиком — такое бывает только у нечеловеческого
        # ввода; режем как есть, потому что альтернатива — не отправить вовсе.
        while len(line) > limit:
            parts.append(line[:limit])
            line = line[limit:]
        current = line
    if current:
        parts.append(current)
    return parts


class TelegramNotifier(Notifier):
    def __init__(self, token: str, chat_id: str, timeout: float = DEFAULT_TIMEOUT,
                 api_url: str = DEFAULT_API):
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout
        self.api_url = api_url.rstrip("/")

    def send(self, text: str, to: str | None = None) -> None:
        chat = to or self.chat_id
        for part in split_message(text):
            try:
                answer = requests.post(
                    f"{self.api_url}/bot{self.token}/sendMessage",
                    json={"chat_id": chat, "text": part,
                          "disable_web_page_preview": True},
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                raise NotifyError(f"Telegram не ответил: {exc}") from exc
            if not getattr(answer, "ok", False):
                raise NotifyError(
                    f"Telegram отказал (код {answer.status_code}): {answer.text}"
                )

    def describe(self) -> str:
        tail = self.chat_id[-4:] if self.chat_id else "?"
        return f"Telegram, чат …{tail} (notify.kind: telegram)"
```

- [ ] **Шаг 4: подключение в `wiring`**

```python
# listam/wiring.py — заменить build_notifier
def build_notifier(config: Config) -> Notifier:
    kind = _kind(config, "notify", "none")
    if kind in ("none", "null"):
        return NullNotifier()
    if kind == "stdout":
        return StdoutNotifier()
    if kind == "telegram":
        from listam.adapters.notify_telegram import TelegramNotifier

        token = config.get("notify.token")
        chat_id = config.get("notify.chat_id")
        if not token or not chat_id:
            raise ConfigError(
                "notify.kind = telegram, но notify.token или notify.chat_id пуст: "
                "секреты живут в .env (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID), "
                "а конфиг на них ссылается. Отправлять в никуда мы не будем."
            )
        return TelegramNotifier(
            token=token, chat_id=str(chat_id),
            timeout=config.get("notify.timeout_seconds", 20.0),
        )
    raise _unknown("notify", kind, ["none", "stdout", "telegram"])
```

- [ ] **Шаг 5: контракт принимает третью реализацию**

```python
# tests/contracts/test_notifier_contract.py — заменить фикстуру
import os

@pytest.fixture(params=["none", "stdout", pytest.param("telegram", marks=pytest.mark.skipif(
    not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"),
    reason="нет TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID: живой канал не проверить"))])
def notifier(request) -> Notifier:
    if request.param == "none":
        return NullNotifier()
    if request.param == "stdout":
        return StdoutNotifier()
    from listam.adapters.notify_telegram import TelegramNotifier

    return TelegramNotifier(token=os.environ["TELEGRAM_BOT_TOKEN"],
                            chat_id=os.environ["TELEGRAM_CHAT_ID"])
```

Контракт с живым токеном **отправит сообщение в чат** — это осознанно: канал,
который нельзя проверить, не канал. Без токена тесты пропускаются, как уже
сделано для `gdrive` и `gsheet`.

- [ ] **Шаг 6: тесты проходят**

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_notify_telegram.py tests/contracts/test_notifier_contract.py tests/test_wiring.py
```

Ожидается PASS, контракт `telegram` — SKIP.

- [ ] **Шаг 7: коммит**

```bash
git add listam/adapters/notify_telegram.py listam/wiring.py tests/
git commit -m "feat(notify): адаптер Telegram и разрезание длинных сообщений"
```

### Задача 5.2. `doctor` показывает канал

**Файлы:**
- Изменить: `listam/doctor.py`
- Тест: `tests/test_doctor.py`

- [ ] **Шаг 1: падающие тесты**

```python
# tests/test_doctor.py — дописать
def test_doctor_names_the_notification_channel(tmp_path):
    """Канал, про который `doctor` молчит, включают вслепую."""
    from listam.doctor import notify_check

    config = make_config(tmp_path)          # помощник этого файла
    config.data["notify"] = {"kind": "stdout", "hot": {"enabled": True},
                             "digest": {"enabled": True}, "feed": {"enabled": False}}

    check = notify_check(config)

    assert check.ok
    assert "stdout" in check.details
    assert "feed" in check.details          # выключенный вид назван, а не спрятан


def test_doctor_refuses_telegram_without_a_token(tmp_path):
    """Пустой секрет — это сбой, а не предупреждение: команда всё равно не пошлёт."""
    from listam.doctor import notify_check

    config = make_config(tmp_path)
    config.data["notify"] = {"kind": "telegram", "token": "", "chat_id": ""}

    check = notify_check(config)

    assert not check.ok
    assert "TELEGRAM_BOT_TOKEN" in check.details
```

- [ ] **Шаг 2: убедиться, что тесты падают**

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_doctor.py -k notification
```

Ожидается FAIL: `ImportError: cannot import name 'notify_check'`.

- [ ] **Шаг 3: проверка**

```python
# listam/doctor.py — после match_check
def notify_check(config: Config) -> Check:
    """Куда пойдут уведомления и какие виды включены.

    Конфиг `doctor` не правит — он его показывает. Но пустой секрет при
    `kind: telegram` — это сбой: команда всё равно откажется слать, и узнать
    об этом лучше здесь, чем вечером, когда дайджест не пришёл.
    """
    kind = str(config.get("notify.kind", "none"))
    switches = ", ".join(
        f"{name}: {'вкл' if config.get(f'notify.{name}.enabled', True) else 'выкл'}"
        for name in ("hot", "digest", "feed")
    )
    details = f"канал: {kind}; {switches}"

    harm: list[str] = []
    warn: list[str] = []
    if kind == "telegram":
        if not config.get("notify.token") or not config.get("notify.chat_id"):
            harm.append(
                "notify.kind = telegram, но токен или чат пуст: заполни "
                "TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в .env"
            )
    elif kind == "none":
        warn.append("канал выключен: уведомления никуда не идут")

    if all(not config.get(f"notify.{name}.enabled", True)
           for name in ("hot", "digest", "feed")):
        warn.append("все три вида выключены — команда notify не пошлёт ничего")

    details = "; ".join([details] + harm + warn)
    return Check(name="Уведомления", ok=not harm, details=details, warn=bool(warn))
```

- [ ] **Шаг 4: проверка попадает в отчёт**

```python
# listam/doctor.py — в run_doctor, рядом с report.checks.append(match_check(config))
    report.checks.append(notify_check(config))
```

Найди в `run_doctor` место, где добавляются `match_check` и `requests_check`,
и допиши строку следом — порядок проверок в отчёте тот же, что порядок работы:
заявки → матчинг → уведомления.

- [ ] **Шаг 5: тесты проходят**

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_doctor.py
```

Ожидается PASS.

- [ ] **Шаг 6: коммит**

```bash
git add listam/doctor.py tests/test_doctor.py
git commit -m "feat(doctor): канал уведомлений и его тумблеры в отчёте"
```

### Задача 5.3. Живой канал — один раз, руками

- [x] **Шаг 0: куда кладутся ключи** — сделано до фазы, см. «Ключи Telegram»

Два значения: `TELEGRAM_BOT_TOKEN` (от `@BotFather`) и `TELEGRAM_CHAT_ID`
(числовой id чата, у группы — со знаком минус). Оба живут в `.env` в корне
проекта: файл в `.gitignore`, в репозиторий не уезжает. В `config/dev.yaml`
стоит ссылка `${TELEGRAM_BOT_TOKEN}`, не значение.

```
TELEGRAM_BOT_TOKEN=8123456789:AAH...
TELEGRAM_CHAT_ID=-1002345678901
```

**Чат для проверок — отдельный.** Живые тесты задачи 5.4 шлют настоящие
сообщения при каждом прогоне; клиентский чат брокера для этого не годится.
Завести группу вида «listam — проверки», добавить туда бота и держать её id
в `.env` всё время, пока идут фазы 5 и 7.

- [x] **Шаг 1: завести бота и чат** — сделано: `@ListamTotifybot`, чат
`1930501720`, `getMe` и `sendMessage` прошли. Скрипт ниже остаётся на случай,
если ключи придётся добывать заново на другой машине.

Если `TELEGRAM_BOT_TOKEN` пуст: бот заводится у `@BotFather`, `TELEGRAM_CHAT_ID`
узнаётся так (бот должен быть добавлен в чат и получить там хотя бы одно
сообщение):

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "
import os, requests
from dotenv import load_dotenv
load_dotenv('.env')
answer = requests.get(f\"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/getUpdates\", timeout=20).json()
for update in answer.get('result', []):
    chat = (update.get('message') or {}).get('chat') or {}
    print(chat.get('id'), chat.get('title') or chat.get('username'))"
```

Если ключей нет и завести их сейчас нельзя — **это записывается в отчёт фазы
первой строкой**, `notify.kind` остаётся `stdout`, а живая проверка переносится
в фазу 7. Выдумывать успешную отправку нельзя.

- [ ] **Шаг 2: сухой прогон перед живым**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam notify --digest --dry-run
```

Прочитать глазами весь текст: адресат, ссылки, числа в шапках. Отправленное
не отзывается.

- [ ] **Шаг 3: одна живая отправка**

Включить `notify.kind: telegram` в `config/dev.yaml`, раскомментировать `token`
и `chat_id`, затем:

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam notify --digest
```

Ожидается: сообщение в чате, «отправлено» в отчёте, одна строка в журнале.

- [ ] **Шаг 4: повтор ничего не шлёт**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam notify --digest
```

Ожидается «Событий: 0» и второе короткое сообщение «событий нет» — либо, если
это раздражает, находка в отчёт: «пустую отправку стоит не слать, а только
двигать окно». Решение принимается числами приёмки, а не заранее.

- [ ] **Шаг 5: записать в отчёт**

Что именно пришло в чат (текст первых строк), сколько сообщений, сколько
символов, резалось ли длинное.

### Задача 5.4. Живая отправка, прочитанная в браузере (Playwright)

**Зачем.** Шаг 5.3 проверяет, что Bot API ответил `ok: true`. Это не то же
самое, что «брокер увидел сообщение». Бот отвечает успехом и тогда, когда текст
приехал в другой чат, разъехался по разметке или потерял хвост на разрезе.
Единственная честная проверка — прочитать чат глазами; браузер делает это
повторяемо, а мок вокруг `requests.post` — нет.

**Файлы:**
- Создать: `tests/live/__init__.py`, `tests/live/conftest.py`, `tests/live/test_telegram_live.py`
- Создать: `requirements-dev.txt` — `playwright>=1.47` (в `requirements.txt` не идёт: боевому запуску браузер не нужен)
- Изменить: `.gitignore` (`tmp/telegram-profile/`), `pytest.ini` — маркер `live`
- Изменить: `.env.example` — `TELEGRAM_LIVE`, `TELEGRAM_WEB_CHAT`, `TELEGRAM_SEND_DELAY`

Тесты **всегда пропускаются**, кроме явного прогона: нет `TELEGRAM_LIVE=1` —
нет отправки. Поэтому обычная батарея растёт ровно на три skip.

**Чат сейчас — личка брокера** (раздел «Ключи Telegram»), и живой прогон
кладёт туда три сообщения, одно из них — длинное в несколько частей. Это
ожидаемо; если мешает, заводится группа и меняется `TELEGRAM_CHAT_ID`.

- [ ] **Шаг 1: вход в Telegram Web — один раз, руками**

Профиль браузера с готовой сессией; лежит вне репозитория, живёт между
прогонами. Команда интерактивная — её выполняет человек, не агент:

```bash
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m playwright install chromium
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context('tmp/telegram-profile', headless=False)
    (ctx.pages[0] if ctx.pages else ctx.new_page()).goto('https://web.telegram.org/k/')
    input('Войди по номеру телефона, открой чат проверок и нажми Enter...')
    ctx.close()"
```

Персистентный профиль, а не `storage_state`: Telegram Web держит часть сессии
в IndexedDB, и слепок `storage_state` её не увозит. Ссылку на открытый чат
(`https://web.telegram.org/k/#-1002345678901`) положить в `.env` как
`TELEGRAM_WEB_CHAT`.

- [ ] **Шаг 2: падающие тесты**

```python
# tests/live/test_telegram_live.py
"""Живой канал: сообщение доходит до чата и читается в Telegram Web.

Прогон только явный:
    TELEGRAM_LIVE=1 .venv/Scripts/python.exe -m pytest -q tests/live
Нужны: .env с ключами и профиль tmp/telegram-profile со входом (шаг 1).
Каждый прогон шлёт НАСТОЯЩИЕ сообщения в чат из TELEGRAM_CHAT_ID.
"""
import os
import subprocess
import sys
import time
import uuid

import pytest

# Ключи лежат в .env, а pytest его сам не читает: load_dotenv зовётся внутри
# load_config(). Тянем его здесь — но только под флагом, чтобы в обычном
# прогоне токен не появился в os.environ и не расскипал контракт telegram.
if os.environ.get("TELEGRAM_LIVE") == "1":
    from dotenv import load_dotenv

    load_dotenv(".env", override=False)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("TELEGRAM_LIVE") != "1"
        or not os.environ.get("TELEGRAM_BOT_TOKEN")
        or not os.environ.get("TELEGRAM_CHAT_ID"),
        reason="живой канал выключен: нет TELEGRAM_LIVE=1 или ключей в .env",
    ),
]

# Пауза между отправками. Bot API пускает в группу около 20 сообщений в минуту,
# а на пачке частей длинного сообщения 429 ловится и на меньшем темпе.
SEND_DELAY = float(os.environ.get("TELEGRAM_SEND_DELAY", "4"))
RENDER_WAIT = 20_000  # сколько ждём, пока сообщение доедет до вкладки


def notifier():
    from listam.adapters.notify_telegram import TelegramNotifier

    return TelegramNotifier(token=os.environ["TELEGRAM_BOT_TOKEN"],
                            chat_id=os.environ["TELEGRAM_CHAT_ID"])


def test_short_message_is_visible_in_the_chat(chat):
    mark = f"listam-live {uuid.uuid4().hex[:8]}"
    notifier().send(f"{mark}\nЗаявка R-1 — 3 новых")
    chat.get_by_text(mark).last.wait_for(timeout=RENDER_WAIT)
    time.sleep(SEND_DELAY)


def test_long_message_arrives_whole_and_never_cuts_a_line(chat):
    from listam.adapters.notify_telegram import LIMIT

    mark = f"listam-live {uuid.uuid4().hex[:8]}"
    lines = [f"{mark} строка {n:04d} " + "объявление" * 6 for n in range(200)]
    body = "\n".join(lines)
    assert len(body) > LIMIT, "тест бессмыслен, если текст влезает в одно сообщение"

    notifier().send(body)
    # последняя строка на месте — значит доехали все части
    chat.get_by_text(f"{mark} строка 0199").last.wait_for(timeout=RENDER_WAIT)
    chat.get_by_text(f"{mark} строка 0000").last.wait_for(timeout=RENDER_WAIT)
    seen = "\n".join(chat.get_by_text(mark).all_inner_texts())
    assert all(line in seen for line in lines[:5] + lines[-5:])
    time.sleep(SEND_DELAY)


def test_digest_command_reaches_the_chat(chat, live_config_dir):
    """Сквозняк: команда CLI, а не только адаптер."""
    run = subprocess.run(
        [sys.executable, "-m", "listam", "notify", "--digest",
         "--config-dir", str(live_config_dir)],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert run.returncode == 0, run.stdout + run.stderr
    if "Событий: 0" in run.stdout:
        pytest.skip("событий в окне нет: сдвинь окно журнала и повтори")
    head = next(line for line in run.stdout.splitlines() if line.startswith("Заявка"))
    chat.get_by_text(head[:40]).last.wait_for(timeout=RENDER_WAIT)
    time.sleep(SEND_DELAY)
```

```python
# tests/live/conftest.py
"""Браузер с готовой сессией и конфиг с kind: telegram."""
import os
import shutil

import pytest
import yaml


@pytest.fixture(scope="session")
def chat():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as play:
        context = play.chromium.launch_persistent_context(
            "tmp/telegram-profile", headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(os.environ.get("TELEGRAM_WEB_CHAT",
                                 "https://web.telegram.org/k/"))
        if page.get_by_text("Log in to Telegram").count():
            pytest.skip("в профиле нет входа: выполни шаг 1 задачи 5.4 руками")
        yield page
        context.close()


@pytest.fixture(scope="session")
def live_config_dir(tmp_path_factory):
    """Копия config/ с notify.kind: telegram — репозиторный конфиг не трогаем."""
    target = tmp_path_factory.mktemp("config-live")
    shutil.copytree("config", target, dirs_exist_ok=True)
    path = target / "dev.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["notify"].update({"kind": "telegram",
                           "token": "${TELEGRAM_BOT_TOKEN}",
                           "chat_id": "${TELEGRAM_CHAT_ID}"})
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return target
```

```ini
# pytest.ini — маркер, чтобы `-m "not live"` работал без предупреждений
[pytest]
markers =
    live: ходит в настоящий Telegram; включается TELEGRAM_LIVE=1
```

- [ ] **Шаг 3: обычная батарея — живые тесты обязаны пропуститься**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Ожидается три новых skip и ни одного сообщения в чате. Если сообщение пришло —
`skipif` написан неправильно, и это чинится **до** шага 4.

- [ ] **Шаг 4: живой прогон**

```powershell
$env:TELEGRAM_LIVE="1"; $env:PYTHONIOENCODING="utf-8"
.venv/Scripts/python.exe -m pytest -q tests/live -s
$env:TELEGRAM_LIVE=""        # обязательно: иначе следующая батарея пошлёт живьём
```

Git Bash — то же самое одной строкой:

```bash
TELEGRAM_LIVE=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest -q tests/live -s
```

Ожидается: три passed, окно браузера показывает чат, в нём — короткое
сообщение, длинное несколькими частями и дайджест. Между отправками пауза
`TELEGRAM_SEND_DELAY` секунд; если Bot API отвечает 429 — поднять паузу, а не
глушить ошибку.

- [ ] **Шаг 5: в отчёт**

Сколько сообщений ушло, на сколько частей разрезалось длинное, сколько секунд
паузы хватило, что именно увидел браузер (первые строки). Числа — из вывода.

```bash
git add tests/live requirements-dev.txt pytest.ini .gitignore .env.example
git commit -m "test(notify): живая отправка в Telegram, прочитанная браузером"
```

### Конец фазы 5

- [ ] Батарея: ожидается **749 passed, 25 skipped**. Если токен в окружении
      есть, контракт `telegram` не пропускается, а **шлёт четыре сообщения
      в чат** — это ожидаемо, но скажи об этом в отчёте. Живые тесты задачи 5.4
      в обычном прогоне остаются skip: их включает только `TELEGRAM_LIVE=1`.
- [ ] Живой прогон задачи 5.4 **и вывод в отчёт**: сколько сообщений ушло,
      какие увидел браузер, на сколько частей разрезалось длинное.
- [ ] Дописать «Результат фазы 5»: живая отправка или причина, почему её не было.
- [ ] Дописать стартовый промпт для фазы 6.
- [ ] Коммит. **`config/prod.yaml` с `kind: telegram` коммитится только если
      живая отправка прошла.**

---

# Фаза 6. Причина закрытия, пороги вне шкалы, честный README

**Одна сессия.** Три долга фазы 8 QA-плана, которые уведомление сделало
заметными: «вариант отпал» без причины бесполезен, порог 170 молча выключает
уведомления, а README не говорит, что команды мигрируют базу сами.

**Ожидается после фазы:** **758 passed, 25 skipped** (749 плюс девять:
два контрактных, два на подбор, три на конфиг, один на `settings`, один
на README).

### Задача 6.1. `retired_reason` называет причину

**Файлы:**
- Изменить: `listam/ports/database.py`, `listam/adapters/db_sqlite.py:709-727`,
  `listam/matching.py:295-345`
- Тест: `tests/contracts/test_database_contract.py`, `tests/test_matching.py`

- [ ] **Шаг 1: падающие тесты**

```python
# tests/contracts/test_database_contract.py — дописать
def test_each_retired_match_gets_its_own_reason(db):
    """«Бюджет» и «не представитель кластера» — разные ответы на вопрос
    «почему пропала вчерашняя карточка». Одна фраза на всех не отвечает."""
    for listing_id in ("1", "2"):
        db.upsert_listing(make_listing(listing_id), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_matches([
        Match(request_id=request.id, listing_id="1", score=80.0),
        Match(request_id=request.id, listing_id="2", score=70.0),
    ], NOW)

    closed = db.retire_matches(
        request.id, keep=set(), now=LATER,
        reasons={"1": "бюджет", "2": "не представитель кластера"},
        default="проход больше не подтверждает этот вариант",
    )

    assert closed == 2
    reasons = {match.listing_id: match.retired_reason
               for match in db.matches_for_request(request.id, include_retired=True)}
    assert reasons == {"1": "бюджет", "2": "не представитель кластера"}


def test_a_match_without_a_known_reason_gets_the_general_one(db):
    """Причины нет — значит объявление выпало из выборки, не получив отказа.
    Врать про «бюджет» в этом случае хуже, чем сказать общее."""
    db.upsert_listing(make_listing("1"), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_match(Match(request_id=request.id, listing_id="1", score=80.0), NOW)

    db.retire_matches(request.id, keep=set(), now=LATER, reasons={},
                      default="проход больше не подтверждает этот вариант")

    match = db.matches_for_request(request.id, include_retired=True)[0]
    assert match.retired_reason == "проход больше не подтверждает этот вариант"
```

```python
# tests/test_matching.py — дописать
def test_a_match_closed_by_budget_says_budget(tmp_path):
    """Живьём: квартира подорожала — в базе должно лежать «бюджет», а не
    общая фраза. Приёмка фазы 8 нашла именно это: причина была одна на всех."""
    config, database = prepared_base(tmp_path)   # помощник этого файла
    # объявление в бюджете → матч
    run_match(config)
    database.connect()
    listing_id = database.matches_for_request(
        database.get_request("R-1").id)[0].listing_id
    database.conn.execute(
        "UPDATE listings SET price_usd = 9000000 WHERE id = ?", (listing_id,))
    database.conn.commit()
    database.close()

    run_match(config)

    database.connect()
    match = database.matches_for_request(
        database.get_request("R-1").id, include_retired=True)[0]
    database.close()
    assert match.retired_reason == "бюджет"


def test_a_match_closed_by_a_cheaper_twin_says_so(tmp_path):
    """Вторая живая причина: в кластере появился вариант дешевле, и матч
    на прежнего представителя закрывается — но не «по бюджету»."""
    config, database = prepared_base(tmp_path)
    run_match(config)
    database.connect()
    old = database.matches_for_request(database.get_request("R-1").id)[0].listing_id
    twin = database.get_listing(old)
    twin.id = "99000001"
    twin.url = "https://www.list.am/ru/item/99000001"
    twin.price_usd = (twin.price_usd or 100000.0) - 5000
    database.upsert_listing(twin, seen_at=datetime.now(timezone.utc))
    database.close()

    run_match(config)

    database.connect()
    closed = [match for match in database.matches_for_request(
        database.get_request("R-1").id, include_retired=True)
        if match.listing_id == old]
    database.close()
    assert closed[0].retired_reason == "не представитель кластера"
```

Помощник `prepared_base` уже есть в `tests/test_matching.py` (или называется
иначе — посмотри, как соседние тесты готовят базу с заявкой и объявлением,
и возьми тот же способ; новый заводить не надо).

- [ ] **Шаг 2: убедиться, что тесты падают**

```bash
.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py tests/test_matching.py -k reason
```

Ожидается FAIL: `TypeError: retire_matches() got an unexpected keyword argument 'reasons'`.

- [ ] **Шаг 3: порт**

```python
# listam/ports/database.py — заменить сигнатуру и docstring retire_matches
    @abstractmethod
    def retire_matches(self, request_id: int, keep: set[str], now: datetime,
                       reasons: dict[str, str], default: str) -> int:
        """Закрывает матчи заявки, которых нет в `keep`. Отдаёт, сколько закрыл.

        `reasons` — причина на объявление: «бюджет», «район», «площадь»,
        «комнаты», «не представитель кластера». Причины нет — берётся
        `default`: объявление выпало из выборки, не получив отказа, и врать
        про бюджет в этом случае хуже, чем сказать общее.

        Закрытие — не удаление: в строке лежит след звонка, и он переживает
        подорожавшее объявление. Уже закрытые повторно не трогаются, иначе
        `retired_at` двигался бы каждым прогоном и переставал отвечать на
        вопрос «когда вариант отпал».
        """
```

- [ ] **Шаг 4: реализация**

```python
# listam/adapters/db_sqlite.py — заменить retire_matches
    def retire_matches(self, request_id: int, keep: set[str], now: datetime,
                       reasons: dict[str, str], default: str) -> int:
        """Закрывает всё, что этот проход не подтвердил. См. порт."""
        closing = [
            (row["id"], reasons.get(row["listing_id"], default))
            for row in self.conn.execute(
                "SELECT id, listing_id FROM matches "
                "WHERE request_id = ? AND retired_at IS NULL",
                (request_id,),
            ) if row["listing_id"] not in keep
        ]
        if not closing:
            return 0
        stamp = to_iso(now)
        with self.transaction():
            self.conn.executemany(
                "UPDATE matches SET retired_at = ?, retired_reason = ? WHERE id = ?",
                [(stamp, reason, match_id) for match_id, reason in closing],
            )
        return len(closing)
```

- [ ] **Шаг 5: подбор собирает причины**

```python
# listam/matching.py — в _write_matches, заменить тело цикла по заявкам
    now = datetime.now(timezone.utc)
    for request in requests:
        confirmed: set[str] = set()
        # Почему вариант не подтвердился — знает только этот цикл: жёсткий
        # критерий назвал причину словом, а представительство в кластере
        # видно по `representatives`. Дальше это слово читает человек в
        # уведомлении «отпало: бюджет 3, район 1», и общая фраза ему
        # не отвечает ни на что.
        reasons: dict[str, str] = {}
        # Матчи заявки копятся и пишутся одной транзакцией: по одной на строку
        # боевые 67 000 матчей стоили минуту фиксаций на диск.
        batch: list[Match] = []
        for listing in candidates:
            result = score(request, listing, median_by_district=medians,
                           weights=tuning.weights,
                           stretch_percent=tuning.stretch_percent)
            if result.rejected_by is not None:
                # Отказ в базу не пишется: их миллионы, и звонить по ним некуда.
                # Но причина запоминается: если на это объявление есть вчерашний
                # матч, закрыть его надо со словом, а не с общей фразой.
                reasons[listing.id] = result.rejected_by
                continue
            cluster = representatives[listing.id]
            batch.append(Match(
                request_id=request.id,
                listing_id=listing.id,
                score=float(result.value),
                run_id=run_id,
                breakdown=result.breakdown or None,
                cluster_id=cluster.cluster_id,
                cluster_size=cluster.size,
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
            # Объявление живо, но представителем кластера быть перестало:
            # появился двойник дешевле. Это не «бюджет» и не «район» — клиенту
            # ту же квартиру покажут по другой карточке.
            for item in everything_by_id:
                if item not in representatives and item not in reasons:
                    reasons[item] = "не представитель кластера"
            report.retired += database.retire_matches(
                request.id, keep=confirmed | off_the_feed, now=now,
                reasons=reasons,
                default="проход больше не подтверждает этот вариант",
            )
```

`everything_by_id` — множество идентификаторов всех активных объявлений;
оно уже считается в `run_match` (`{item.id for item in everything}` внутри
вычисления `off_the_feed`). Вынеси его в переменную и передай в `_write_matches`
рядом с `off_the_feed`:

```python
# listam/matching.py — в run_match, вместо одной строки off_the_feed
            alive_ids = {item.id for item in everything}
            # Чего проход не видел: снятое с ленты и отложенное аномалией.
            # Закрывать по такому нельзя — см. `_write_matches`.
            off_the_feed = database.known_ids() - alive_ids
```

и в обоих вызовах `_write_matches(...)` добавь `everything_by_id=alive_ids`;
в сигнатуре функции — параметр `everything_by_id: set[str]`.

- [ ] **Шаг 6: тесты проходят**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Ожидается PASS. Существующие тесты, звавшие `retire_matches(..., reason=...)`,
правятся здесь же на `reasons={}, default=...`.

- [ ] **Шаг 7: коммит**

```bash
git add listam/ports/database.py listam/adapters/db_sqlite.py listam/matching.py tests/
git commit -m "fix(match): закрытый матч называет причину, а не общую фразу"
```

### Задача 6.2. Порог вне шкалы 0…100 отклоняется

**Файлы:**
- Изменить: `listam/config.py`, `listam/matching.py:84-119`, `listam/doctor.py`
- Тест: `tests/test_config.py`, `tests/test_matching.py`

- [ ] **Шаг 1: падающие тесты**

```python
# tests/test_config.py — дописать
def test_a_score_threshold_above_the_scale_is_refused(tmp_path):
    """`hot: 170` принимался молча и давал «горячих 0» — то есть выключал
    уведомления, не сказав ни слова."""
    from listam.config import ConfigError, score_threshold

    config = make_config(tmp_path)
    config.data.setdefault("match", {}).setdefault("thresholds", {})["hot"] = 170

    with pytest.raises(ConfigError) as trouble:
        score_threshold(config, "match.thresholds.hot", 70)

    assert "от 0 до 100" in str(trouble.value)


def test_a_negative_score_threshold_is_refused(tmp_path):
    from listam.config import ConfigError, score_threshold

    config = make_config(tmp_path)
    config.data.setdefault("match", {}).setdefault("thresholds", {})["digest"] = -1

    with pytest.raises(ConfigError):
        score_threshold(config, "match.thresholds.digest", 40)


def test_zero_and_null_are_still_allowed(tmp_path):
    """Ноль значит ноль (показывать всё), `null` — «порога нет»."""
    from listam.config import score_threshold

    config = make_config(tmp_path)
    config.data.setdefault("match", {}).setdefault("thresholds", {})["digest"] = 0
    assert score_threshold(config, "match.thresholds.digest", 40) == 0.0

    config.data["match"]["thresholds"]["digest"] = None
    assert score_threshold(config, "match.thresholds.digest", 40) is None
```

```python
# tests/test_matching.py — дописать
def test_match_refuses_a_threshold_outside_the_scale(tmp_path):
    """Отказ приходит до работы: порог, прочитанный посреди прохода, прилетал
    бы человеку поверх пересчитанных кластеров."""
    from listam.config import ConfigError
    from listam.matching import settings

    config = make_config(tmp_path)
    config.data.setdefault("match", {}).setdefault("thresholds", {})["hot"] = 170

    with pytest.raises(ConfigError):
        settings(config)
```

- [ ] **Шаг 2: убедиться, что тесты падают**

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_config.py tests/test_matching.py -k threshold
```

Ожидается FAIL: `ImportError: cannot import name 'score_threshold'`.

- [ ] **Шаг 3: функция в конфиге**

```python
# listam/config.py — после positive
def score_threshold(config: Config, key: str, default: Any) -> float | None:
    """Порог балла. `null` — «порога нет», число — число, но только 0…100.

    Балл по построению лежит в 0…100. Порог 170 не сработает никогда: он
    молча выключает уведомления и отвечает «горячих 0» — то есть выглядит
    как спокойный рынок. Бессмысленное значение в конфиге отклоняется так же,
    как бессмысленный флаг: на входе и кодом 2.
    """
    value = threshold(config, key, default)
    if value is None:
        return None
    number = float(value)
    if not 0 <= number <= 100:
        raise ConfigError(
            f"{key} = {value} не годится: балл — это шкала от 0 до 100, "
            f"и порог за её краем не сработает никогда. Выше ста нет ничего, "
            f"ниже нуля — тоже; чтобы снять порог, ставят null."
        )
    return number
```

- [ ] **Шаг 4: пороги читаются через неё**

```python
# listam/matching.py — в settings, заменить две строки
    hot = score_threshold(config, "match.thresholds.hot", DEFAULT_HOT)
    digest = score_threshold(config, "match.thresholds.digest", DEFAULT_DIGEST)
```

и импорт в шапке файла:

```python
from listam.config import Config, ConfigError, score_threshold, threshold
```

- [ ] **Шаг 5: `doctor` говорит то же самое**

```python
# listam/doctor.py — в match_check, заменить чтение порогов
    try:
        hot = score_threshold(config, "match.thresholds.hot", None)
        digest = score_threshold(config, "match.thresholds.digest", None)
    except ConfigError as exc:
        # `doctor` обязан отвечать то же, что ответит команда: она на таком
        # конфиге не стартует вовсе.
        return Check(name="Матчинг", ok=False, details=str(exc))
```

Импорт `score_threshold` и `ConfigError` — в шапке `doctor.py`.

- [ ] **Шаг 6: тесты проходят**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Ожидается PASS.

- [ ] **Шаг 7: коммит**

```bash
git add listam/config.py listam/matching.py listam/doctor.py tests/
git commit -m "fix(config): порог балла вне шкалы 0…100 отклоняется на входе"
```

### Задача 6.3. README не врёт про миграции

**Файлы:**
- Изменить: `README.md`
- Тест: `tests/test_docs.py`

- [ ] **Шаг 1: падающий тест**

```python
# tests/test_docs.py — дописать
def test_readme_says_which_commands_migrate_the_base():
    """Приёмка фазы 8: `match --all` на схеме 7 не отказал, а накатил
    миграции и поехал. `doctor` при этом говорил «запускать рано».
    Ни план, ни README этого не говорили."""
    assert "мигрируют базу сами" in README
    assert "python -m listam recheck" in README
```

- [ ] **Шаг 2: убедиться, что тест падает**

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_docs.py -k migrate
```

Ожидается FAIL.

- [ ] **Шаг 3: правка README**

В раздел «Устройство», к описанию `runner.py`, дописывается абзац:

```markdown
**Команды, пишущие в базу, мигрируют её сами.** `cluster`, `requests`, `match`
и `notify` стоят на общем каркасе (`listam/runner.py`): он берёт замок, забирает
свежую копию из хранилища и **накатывает миграции** — отказа «схема старая»
у них нет. Отказывают те, кто базу только читает: `export`, `changes`, витрина
`matches` и проверка `doctor`. Поэтому `doctor` на старой базе говорит
«запускать рано» строже, чем ведут себя команды, а совет «накати миграции:
`python -m listam recheck`» верен, но не единственный путь — мигрирует любая
из четырёх команд каркаса.
```

- [ ] **Шаг 4: тест проходит**

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_docs.py
```

Ожидается PASS.

- [ ] **Шаг 5: коммит**

```bash
git add README.md tests/test_docs.py
git commit -m "docs: README называет команды, которые мигрируют базу сами"
```

### Конец фазы 6

- [ ] Батарея: ожидается **758 passed, 22 skipped**.
- [ ] Проверить живьём на базе фазы 1 и записать вывод:

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam match --all
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam notify --digest --dry-run
```

В отчёт: какие причины закрытия встретились и сколько раз (строка «отпало N
(бюджет 3, …)»).
- [ ] Дописать «Результат фазы 6» и стартовый промпт для фазы 7.
- [ ] Коммит.

---

# Фаза 7. Боевая приёмка

**Одна сессия. Ни одной новой возможности.** Всё, что M3 обещает, проверяется
на копии боевой базы и на живом канале: то, что нельзя показать живьём, не
считается сделанным.

**Ожидается после фазы:** батарея без изменений (**758 passed, 25 skipped**),
схема боевой базы **10**.

### Задача 7.1. Копия базы, а не оригинал

- [ ] **Шаг 1: взять копию**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "import shutil; shutil.copy('data/listam.sqlite', 'data/listam-before-m3.sqlite')"
```

Если `data/listam.sqlite` пуст или хранилище недоступно (`GDRIVE_FOLDER` и
`GDRIVE_CREDENTIALS_FILE` в `.env` пусты) — база восстанавливается из боевых
выгрузок скриптом фазы 1 (`tmp/restore_from_exports.py`), и **это пишется
в отчёт первой строкой**: «боевого файла базы нет, приёмка идёт на базе,
восстановленной из выгрузок `out/*.xlsx`». Выдумывать приёмку на оригинале
нельзя.

- [ ] **Шаг 2: миграция 010 на боевых числах**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "
import time
from listam.adapters.db_sqlite import SqliteDatabase
db = SqliteDatabase('data/listam-m3.sqlite'); db.connect()
before = (db.count_listings(), db.count_matches())
start = time.perf_counter(); db.migrate(); spent = time.perf_counter() - start
print('схема', db.schema_version(), 'за %.2f с' % spent)
print('объявлений/матчей до', before, 'после',
      (db.count_listings(), db.count_matches()))
print('закрытых сразу после миграции',
      db.conn.execute('SELECT COUNT(*) AS n FROM matches WHERE retired_at IS NOT NULL').fetchone()['n'])
print('строк журнала уведомлений',
      db.conn.execute('SELECT COUNT(*) AS n FROM notifications').fetchone()['n'])
db.close()"
```

Эталон: 008 и 009 на тех же числах шли **0,06 с**. Числа до и после обязаны
совпасть; строк журнала — 0: миграция ничего отправленным не считает.

### Задача 7.2. Шесть проверок живьём

Каждая — команда и то, что увидели на самом деле. Записывается вывод, а не
ожидание.

- [ ] **1. Срез «что нового со вчера».**
      `python -m listam matches --new --hours 24` → строки событий и хвост
      «…и ещё N из M». Сверить с `python -m listam matches --request <id>`:
      подешевевшая квартира в срезе стоит сверху, а в витрине по баллу —
      там же, где стояла (150-е место из 627 в приёмке фазы 8).
- [ ] **2. Сухой прогон дайджеста.** `python -m listam notify --digest --dry-run`
      → текст целиком, числа шапок сходятся с пунктом 1, широкая заявка
      помечена, тихий раздел называет причины закрытия.
- [ ] **3. Живая отправка.** `python -m listam notify --digest` → сообщение
      в чате, одна строка в `notifications`, «отправлено» в отчёте.
- [ ] **4. Повтор не шлёт то же самое.** Та же команда второй раз → «Событий: 0».
- [ ] **5. Сбой канала не теряет событие.** Испортить токен в `.env`
      (`TELEGRAM_BOT_TOKEN=сломано`), `python -m listam notify --hot` → код 1,
      строки в журнале нет; вернуть токен, повторить → уходит то, что не дошло.
- [ ] **6. Живой канал глазами браузера.** Прогон живых тестов фазы 5:
      `TELEGRAM_LIVE=1 python -m pytest -q tests/live -s` → три passed,
      в чате видно короткое сообщение, длинное несколькими частями и дайджест.
      В отчёт — что увидел браузер, а не что ожидалось.
- [ ] **7. Витрина на боевых числах.** `python -m listam matches` → время
      и число строк. Эталон фазы 8: **50 633 строки за 4,0 с**; ожидание —
      около 2 247 строк за 0,22 с. В отчёт идёт замеренное.

- [ ] **Шаг 7: воскресший матч — событие ровно один раз**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "
from listam.adapters.db_sqlite import SqliteDatabase
db = SqliteDatabase('data/listam-m3.sqlite'); db.connect()
row = db.conn.execute('SELECT * FROM matches WHERE retired_at IS NOT NULL LIMIT 1').fetchone()
print('закрытый матч:', row['id'], row['listing_id'], row['retired_reason'])
db.close()"
```

Затем вернуть объявлению прежнюю цену, прогнать `match --all`, затем
`notify --digest --dry-run` дважды подряд: вернувшийся вариант обязан
появиться в тексте **один раз** и не появиться во второй.

### Задача 7.3. Закрытие

- [ ] **Шаг 1: батарея**

```bash
.venv/Scripts/python.exe -m pytest -q
```

- [ ] **Шаг 2: `doctor`**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam doctor --no-network
```

Ожидается зелёный отчёт со строкой «Уведомления: канал …».

- [ ] **Шаг 3: расписание в README**

Проверить, что раздел «Как запускать по расписанию» называет `notify --hot`
в часовом сценарии и `notify --digest` в суточном, и что примеры `schtasks`
и `cron` их содержат. Если нет — дописать.

- [ ] **Шаг 4: коммит**

```bash
git add README.md docs/superpowers/plans/2026-09-22-m3-notifications.md
git commit -m "docs: приёмка M3 и разбор находок"
```

### Конец фазы 7

- [ ] Дописать «Результат фазы 7»: таблица «что проверено → как → что увидели»,
      числа боевой базы, числа батареи, текст первого живого сообщения.
- [ ] Дописать «Что осталось открытым» — находки, которые решили не чинить,
      с причиной и адресом (M4 или «не чинится»).
- [ ] Дописать **стартовый промпт для написания плана M4** (телефоны продавцов).
- [ ] `git status --short` — чисто; коммит сделан.

---

## Результат фазы 1

**Главное одной строкой: поток событий в сутки — 710 новых матчей на 51 заявку
(медиана 9 на заявку), а шум пересчёта — 10 866 «обновлённых» матчей за один
прогон, из них у 10 219 сменился только текст `breakdown` при том же балле.**
Выборка «что тронулось с `matched_at`» без отсева даст брокеру полтора десятка
тысяч строк, из которых событий — единицы. Это главный вход в дизайн фазы 2.

Кода в `listam/` фаза не трогала: `git status --short` пуст, батарея
`.venv/Scripts/python.exe -m pytest -q` → **687 passed, 18 skipped** (41,95 с)
до и после.

### Задача 1.1. База с настоящей историей цен

Выгрузок в `out/` — **восемь**, как и ждал план, но первая (`listam-20260920-2314.xlsx`,
5,5 КБ) содержит **только шапку, ноль строк**. Рабочих срезов **семь**, с 21.09 09:37
по 22.09 05:44 UTC.

Прежний `data/listam-m3.sqlite` (база приёмки фазы 8: 20 798 объявлений и
20 866 точек истории — по одной на объявление) отложен в
`data/listam-m3.backup-phase8.sqlite`; папка `data/` не коммитится. Скрипты
фазы лежат вне репозитория, в `TMPDIR/tmp/` — `tmp/` в `.gitignore` нет,
сработала оговорка плана. Запускать их надо с `PYTHONPATH=.`: из папки вне
репозитория `listam` не импортируется.

`restore_from_exports.py` отработал по плану, без правок кода:

```
PYTHONPATH=. PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe tmp/restore_from_exports.py data/listam-m3.sqlite
listam-20260920-2314.xlsx (20.09 23:14): 0 строк, {}
listam-20260921-0937.xlsx (21.09 09:37): 20569 строк, {'new': 20569}
listam-20260921-1205.xlsx (21.09 12:05): 20569 строк, {'updated': 20569}
listam-20260921-1620.xlsx (21.09 16:20): 20791 строк, {'new': 222, 'updated': 20501, 'price_changed': 68}
listam-20260921-1720.xlsx (21.09 17:20): 20798 строк, {'new': 7, 'updated': 20791}
listam-20260921-2020.xlsx (21.09 20:20): 20819 строк, {'new': 21, 'updated': 20773, 'price_changed': 25}
listam-20260921-2119.xlsx (21.09 21:19): 20826 строк, {'new': 7, 'updated': 20818, 'price_changed': 1}
listam-20260922-0544.xlsx (22.09 05:44): 20842 строк, {'new': 16, 'updated': 20819, 'price_changed': 7}
Итого: объявлений 20842, точек истории цен 20943
```

История цен **не пуста**: 20 943 точки на 20 842 объявления, лишняя сотня —
это `price_changed` 68 + 25 + 1 + 7 = **101 запись о смене цены**. Проверка
шага 4 печатает **97 объявлений** с разной ценой в истории (четыре точки
записались на ту же цену — сменился только `price_raw` либо цена вернулась),
крупнейшая разница 480 000 → 550 000 $ (`22468826`). Схема базы — **9**,
активных объявлений 20 842.

Вид события «подешевел» на этой базе мерить **можно** — в отличие от базы
приёмки фазы 8.

### Задача 1.2. Заявки и матчи

`data/requests.csv` — по-прежнему одна строка-образец, поэтому сгенерирована
`data/requests-m3.csv`: **51 заявка** (50 обычных на 12 боевых районах, бюджеты
90 000–400 000 $ шагом 5 000, плюс `R-51` — нарочно широкая: все районы,
1–5 комнат, 30–300 м², 400 000 $). Генератор с фиксированным зерном
(`Random(20260922)`), замер воспроизводится.

Репозиторный `config/dev.yaml` не правился: команды запускались с
`--config-dir` на копию конфига в `TMPDIR/config`, где подменены
`storage.db_filename` (`listam-m3.sqlite`), `storage.directory`
(`./data/remote-m3`) и `requests.path`.

```
python -m listam --config-dir <TMPDIR>/config requests
Заявки: новых 51, обновлённых 0, без изменений 0, закрытых 0

python -m listam --config-dir <TMPDIR>/config match --all        # 3,67 с
Заявок: 51, объявлений в выборке: 20818, из них представителей кластеров: 14491
Матчи: новых 51548, обновлённых 0, без изменений 0
Из них горячих: 36658, в дайджест: 14736

python -m listam --config-dir <TMPDIR>/config match --all        # 3,14 с, повтор
Матчи: новых 0, обновлённых 0, без изменений 51548
```

Повтор дал ровно то, чего требовал шаг 3: **новых 0, обновлённых 0**. Шума на
неподвижной базе нет — он появляется, когда лента сдвинулась (см. ниже).

Числа сопоставимы с приёмкой фазы 8 (51 117 матчей, 14 355 кластеров): здесь
20 842 объявления против 20 826 и другой набор заявок, отсюда 51 548 и 14 491.

### Задача 1.3. Замер событий в окне

**Замерщик из плана на этой базе вырожден, и это находка, а не сбой.**
`measure_events.py` считает окно от «сейчас», а все 51 548 матчей созданы одним
первым подбором минуту назад:

```
PYTHONPATH=. ... tmp/measure_events.py data/listam-m3.sqlite 24
матчей всего 51548, живых 51548, из них горячих 36658
за 24 ч: новых матчей 51548, обновлённых 0, закрытых 0
из обновлённых с точкой истории цен в окне: 953
заявок с событиями 51; максимум 11093 (R-51), медиана 581, минимум 2

PYTHONPATH=. ... tmp/measure_events.py data/listam-m3.sqlite 1
за 1 ч: новых матчей 51548, обновлённых 0, закрытых 0
из обновлённых с точкой истории цен в окне: 0
```

51 548 «новых за час» — это **остаток**, а не поток: первый подбор на готовой
базе всегда возвращает всю таблицу. Лимиты из этих чисел не выводятся.

**Поэтому поток замерен иначе: лента залита по одному срезу, между срезами —
подбор** (`tmp/measure_flow.py`, отдельная база `data/listam-flow.sqlite`,
отдельный конфиг). База растёт так, как росла на самом деле, и каждый шаг —
настоящее окно между двумя обходами ленты.

| Срез (спустя) | Лента | Новых матчей | из них горячих | «Обновлённых» | Закрыто |
| --- | --- | --- | --- | --- | --- |
| 21.09 09:37 (—) | 20 569 new | 51 021 | 36 299 | 0 | 0 |
| 21.09 12:05 (2,5 ч) | 20 569 updated | **0** | 0 | **10 866** | 0 |
| 21.09 16:20 (4,2 ч) | 222 new, 68 price | 572 | 418 | 24 212 | 136 |
| 21.09 17:20 (1,0 ч) | 7 new | 6 | 3 | 10 055 | 0 |
| 21.09 20:20 (3,0 ч) | 21 new, 25 price | 60 | 43 | 9 210 | 29 |
| 21.09 21:19 (1,0 ч) | 7 new, 1 price | 13 | 8 | 4 168 | 1 |
| 22.09 05:44 (8,4 ч) | 16 new, 7 price | 59 | 36 | 13 155 | 2 |

Первая строка — тот самый backlog. Настоящие сутки — остальные шесть:
**710 новых матчей за 20,1 ч**, из них **508 горячих**, закрыто 168.

Распределение по заявкам за эти 20 ч (все 51, включая ни разу не затронутые):

```
всего новых матчей после первого прогона: 710 горячих 508
заявок затронуто: 45 из 51
событий на заявку за ~20 ч (все 51): медиана 9, p75 15, p90 24, максимум 149
горячих на заявку за ~20 ч (все 51): медиана 8, p75 12, p90 15, максимум 102
топ-5: [('R-51', 149, 102), ('R-25', 44, 32), ('R-20', 37, 23), ('R-9', 30, 14), ('R-40', 27, 15)]
```

Часовые шаги (срезы 17:20 и 21:19, интервал ровно 1,0 ч): новых матчей 6 и 13,
на заявку **медиана 0, максимум 4 и 5**. Час — тихое окно даже у широкой заявки.

### Находка фазы: «обновлённый матч» — это почти всегда не событие

Шаг 12:05 сдвинул **10 866 матчей**, не добавив ленте ни одного объявления и
ни одной смены цены. Причину замерил `tmp/why_updated.py` (подбор до и после
шага, сравнение `MATCH_COMPARED` колонка за колонкой):

```
шаг 12:05 — что сменилось у матчей:
  сменилось score: 647
  сменилось breakdown: 10866
  сменилось cluster_id: 0
  сменилось cluster_size: 0
  сменилось cluster_spread_usd: 0

только breakdown (балл тот же): 10219
балл сдвинулся на 1: 647
балл сдвинулся больше чем на 1: 0
перешли порог 70 вверх: 5
упали ниже 70: 13
```

Из 10 866 «обновлённых» брокеру интересны **18**: пять вариантов стали горячими,
тринадцать перестали. Остальные 10 848 — дрожь пересчёта: медиана `$/м²` по
району чуть сдвинулась (в срезе 12:05 появились колонки «Сомнительно» и «Цена
в валюте», из-за чего 24 объявления получили `anomaly`), и текст `breakdown`
переписался у каждого пятого матча, а балл — у 647, ровно на единицу.

Это не артефакт выгрузок: шаг 17:20, где обе выгрузки одного формата и лента
принесла всего 7 объявлений, дал **10 055 обновлённых** при 6 новых матчах.
Медиана района двигается от любого пополнения ленты.

**Что из этого следует для фазы 2.** Выборка «что тронулось с отметки» не может
опираться на `matched_at` как на признак события: она обязана либо сравнивать
балл с его прошлым значением (переход через порог), либо считать событием
только `first_matched_at`, `revived_at`, `retired_at` и точку `price_history` —
но не факт записи в `matches`. Иначе первое же уведомление уйдёт на десять тысяч
строк. Решение — за фазой 2; фаза 1 приносит числа.

### Задача 1.3, шаг 3. Что приносит лента вне заявок

```
python -m listam --config-dir <TMPDIR>/config changes --hours 24
Изменения за последние 24 ч (с 2026-09-21 14:43 UTC)
Новых: 273   Сменили цену: 99   Снято: 0   Вернулось: 0
```

273 новых объявления за 20,1 ч ленты — **13,6 в час**. Снятых и вернувшихся
ноль: в выгрузках нет строк со статусом «снято», пометить снятое `scrape` не мог.

### Задача 1.3, шаг 4. Предлагаемые лимиты

| Ключ | Значение | Откуда |
| --- | --- | --- |
| `notify.hot.per_request` | **5** | часовые шаги дают на заявку медиану 0 и максимум 4–5 новых матчей; пять строк покрывают наблюдённый максимум и читаются за полминуты |
| `notify.digest.per_request` | **10** | медиана 9 событий на заявку за сутки, округлённая вверх до 5 |
| `notify.digest.wide_request` | **15** | верхний квартиль (p75) суточных событий на заявку; у `R-51` их 149 — такую заявку пора сужать |
| `notify.feed.limit` | **15** | лента даёт 13,6 новых объявления в час по `changes`; пятнадцать — ближайшее круглое сверху |

Числа сняты на 51 заявке. На боевых пяти сотнях заявок потолки на заявку не
изменятся — меняется число сообщений, а не их длина.

### Что разошлось с планом

| Что | Как в плане | Как вышло |
| --- | --- | --- |
| Выгрузок | восемь рабочих | восемь файлов, **семь непустых**; первая — только шапка |
| `measure_events.py` | даёт поток событий | даёт остаток: все матчи созданы одним прогоном. Поток замерен отдельным `measure_flow.py` по срезам |
| `tmp/` вне репозитория | «если не игнорируется — клади в `TMPDIR`» | `tmp/` в `.gitignore` нет, скрипты лежат в `TMPDIR/tmp/`; запуск требует `PYTHONPATH=.` |
| База замера | `data/listam-m3.sqlite` | она же; прежняя отложена в `data/listam-m3.backup-phase8.sqlite` |
| Конфиг | не оговорён | репозиторный не тронут: `--config-dir` на копию в `TMPDIR` |

### Открытые вопросы фазе 2

1. **Чем считать событие.** `matched_at` не годится (10 866 против 18 настоящих).
   Спека называет четыре отметки — фаза 2 обязана добавить к ним сравнение балла
   с порогом, иначе выборка бессмысленна.
2. **«Подешевел» — это точка `price_history` или разница цен?** На этой базе
   101 точка и 97 объявлений с настоящей разницей: четыре точки записались на ту
   же цену. `changes` мерит разницу, `match --new` — факт записи (находка H-2
   фазы 8 QA). Фазе 2 нужно выбрать одно и держаться этого.
3. **Закрытые матчи в окне есть** (136 + 29 + 1 + 2 = 168 за сутки) — вид события
   «ушёл» не пустой, и его придётся резать лимитом наравне с прочими.

---

## Стартовый промпт фазы 2

```
Ты продолжаешь работу над инструментом мониторинга list.am
в C:\Users\Artur.A.Gevorgyan\Downloads\new_project.

Прочитай docs/superpowers/plans/2026-09-22-m3-notifications.md:
разделы «Global Constraints», «Карта файлов», «Результат фазы 1»
и свою «Фазу 2». Чужие фазы не трогай. Рядом лежит спека:
docs/superpowers/specs/2026-09-22-m3-notifications-design.md — решения 1–12
в фазах не пересматриваются. Решения 1–11 спеки M2
(docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md)
тоже в силе.

Исходное состояние: HEAD `58925b6`, дерево чистое, батарея
687 passed, 18 skipped, схема базы 9. База для замеров —
data/listam-m3.sqlite (20 842 объявления, 20 943 точки истории цен,
51 заявка, 51 548 матчей, из них 36 658 горячих); заявки —
data/requests-m3.csv; конфиг замера — копия config/dev.yaml вне репозитория,
запуск через --config-dir. Репозиторный config/dev.yaml фаза 1 не трогала.

Твоя задача — фаза 2: база учится помнить событие и отправку.
Миграция 010 (журнал `notifications` и `matches.revived_at`), отметка
воскресения в апсерте матча, выборка «что тронулось с отметки», журнал
отправок и доменная классификация события. Наружу ещё ничего не шлётся.

Главное из фазы 1, что меняет дизайн выборки: «обновлённый матч» — почти
всегда не событие. За один шаг ленты matched_at сдвинулся у 10 866 матчей,
при этом у 10 219 сменился только текст breakdown при том же балле, у 647
балл сдвинулся ровно на 1, и лишь 18 перешли порог 70 (5 вверх, 13 вниз).
Выборка, опирающаяся на факт записи в matches, утопит уведомление.
Событием считай first_matched_at, revived_at, retired_at, точку price_history
и переход балла через порог — и закрой это тестом.

Работай по шагам задач: на каждое поведение — падающий тест ДО правки.
Тесты гоняй только .venv/Scripts/python.exe -m pytest -q, любой прогон CLI
из скрипта — только с PYTHONIOENCODING=utf-8. Пороги в конфиге не поднимай.
Новый метод порта — это новый контрактный тест в tests/contracts/.
Схему меняет только миграция 010; своих не заводи.
След звонка (matches.status, matches.reject_reason) не трогает ничто,
MATCH_COMPARED не расширяется.
Ни одного числа в отчёте без команды, которая его напечатала.

Ожидается после фазы: 712 passed, 18 skipped, схема базы 10.

В конце сессии допиши в план раздел «Результат фазы 2»: что сделано, числа
батареи, что разошлось с планом и почему, и стартовый промпт для фазы 3.
Сделай коммит.
```

---

## Результат фазы 2

**Главное одной строкой: доменный фильтр превращает 13 216 «тронутых» строк
в 38 событий.** На последнем шаге ленты базы потока (`data/listam-flow.sqlite`)
`match_events_since` вернула 13 216 строк — ровно тот шум, о котором
предупреждала фаза 1, — а `events_for(..., min_score=70)` оставила 38: 36 новых
матчей и 2 закрытия. Находка фазы 1 закрыта тестом
`test_a_match_that_only_got_recounted_is_not_an_event`: пересчёт событием
не считается.

Батарея `.venv/Scripts/python.exe -m pytest -q` → **711 passed, 18 skipped**
(40,41 с). Схема базы — **10**. Пять коммитов, `git diff --stat c4b5ab6..HEAD`:
593 строки в 8 файлах.

### Что сделано

| Задача | Что появилось |
| --- | --- |
| 2.1 | `listam/migrations/010_notifications.sql`: таблица `notifications`, индекс `idx_notifications_kind`, колонка `matches.revived_at`. В моделях — `Match.revived_at` и dataclass `Notification` |
| 2.2 | `upsert_match` и `upsert_matches` ставят `revived_at`, когда гасят `retired_at`; `_row_to_match` её читает. `MATCH_FIELDS` и `MATCH_COMPARED` не тронуты |
| 2.3 | `Database.match_events_since(since, until, request_id=None)` → `(Match, Listing, price_before)`, один запрос с подзапросом за ценой до окна |
| 2.4 | `Database.record_notification` / `last_notification(kind)`: журнал отправок, виды не смешиваются |
| 2.5 | `listam/domain/events.py`: `classify`, `events_for`, `limited`, `MatchEvent`, `EVENT_LABELS`. Чистые функции |

Тестов добавлено 24: 2 на миграцию, 11 контрактных, 11 доменных.

### Схема на боевой базе

```
.venv/Scripts/python.exe -c "... d.migrate() ..."   # data/listam-m3.sqlite
миграция 009 -> 10: 0.05 с
объявлений: 20842
матчей: 51548
строк в notifications: 0
матчей с revived_at: 0

PYTHONIOENCODING=utf-8 python -m listam --config-dir <SCRATCH>/config doctor --no-network
OK    Схема базы       рабочий файл data\listam-m3.sqlite: версия 10, код ждёт 10;
                       таблицы: contacts, listings, matches, notifications,
                       price_history, requests, runs
Всё на месте — можно запускать прогон.
```

0,05 с на 51 548 матчах — эталон 008/009 (0,06 с) подтверждён: `ALTER TABLE …
ADD COLUMN` не зависит от объёма базы.

### Выборка против шума: шесть шагов ленты

База потока фазы 1 домигрирована до 10 и прогнана по тем же шести окнам
(`match_events_since` + `classify`, порог `hot` = 70, в конфиге не поднимался):

```
шаг 1: строк     18  события {}                            hot 0
шаг 2: строк   7741  события {'new': 571, 'retired': 136}  hot 554
шаг 3: строк     17  события {'new': 6}                    hot 3
шаг 4: строк    179  события {'new': 60, 'retired': 29}    hot 72
шаг 5: строк   4174  события {'retired': 1, 'new': 13}     hot 9
шаг 6: строк  13216  события {'new': 59, 'retired': 2}     hot 38
```

Столбец «строк» — сырьё порта, «события» — приговор домена. Сходится с таблицей
фазы 1 построчно: шаг 6 — это 13 155 «обновлённых» + 59 новых + 2 закрытия.
Шум 13 155 отсечён полностью, ни одно настоящее событие не потеряно.

Сумма за шесть шагов: 709 новых и 168 закрытий против 710 и 168 у фазы 1.
Один новый матч не попал в окно шага 2: его `first_matched_at` совпал с границей
окна, а нижняя граница строгая (`since < when <= until`) — так и задумано,
он ушёл в предыдущее окно.

### Находка фазы: «подешевел» на базах фазы 1 не меряется

Вид `cheaper` **ноль на всех шести шагах**, хотя лента принесла 101 смену цены.
Причина не в коде:

```
price_history seen_at: ('2026-09-21T09:37:00+00:00', '2026-09-22T05:44:00+00:00', 20943)
matches matched_at   : ('2026-09-22T14:39:03.062525+00:00', '2026-09-22T14:41:43.653485+00:00')
объявлений с >1 точкой: 99
```

История цен живёт на оси выгрузок, матчи — на оси прогона замера. Любое окно
по `matched_at` целиком позже всей истории, поэтому `price_before` — последняя
цена и есть, и падения не видно никогда. То же у `data/listam-m3.sqlite`.

Спека предупреждала, что база приёмки обязана иметь наполненный `price_history`.
Этого мало: **отметки матчей и точки истории должны лежать на одной оси
времени**, то есть база приёмки строится циклами `scrape && match`, а не заливкой
выгрузок с последующим подбором. Для фазы 7 это условие, а не пожелание.
Сейчас `cheaper` закрыт только модульным тестом
(`test_a_match_whose_listing_got_cheaper_is_an_event`).

`revived` на этих базах тоже ноль, но по другой причине и без последствий:
колонки `revived_at` во время замера фазы 1 ещё не было, а закрытые матчи
за шесть шагов не возвращались.

### Что разошлось с планом

| Что | Как в плане | Как вышло |
| --- | --- | --- |
| Батарея | 712 passed | **711 passed, 18 skipped**. В блоке `tests/test_events.py` самого плана 11 тестов, а не 12 (арифметика фазы: 687 + 2 + 11 + 11 = 711). Ни один тест не потерян — пересчитано число, как и велит общее ограничение |
| `tests/test_migrations.py` | не упоминается | `test_migration_009_remembers_when_a_request_was_matched` держал `assert schema_version() == 9` и упал на `10 == 9`. Ослаблен до `>= 9` — ровно так, как соседний тест 008 уже ослаблен до `>= 8`. Это не подгонка: тест проверяет колонку `requests.matched_at`, а не номер последней миграции |
| INSERT в тесте 010 | `INSERT INTO listings (id, url, status)` | падал на `NOT NULL constraint failed: listings.first_seen`. Добавлены `first_seen` и `last_seen` |
| Конфиг замера | копия `config/dev.yaml` в `TMPDIR` | она же, но в scratchpad сессии: `TMPDIR` фазы 1 не пережил смену сессии. Репозиторный `config/dev.yaml` не тронут |
| База потока | в плане не участвует | домигрирована до 10, чтобы прогнать выборку по настоящим окнам. Без неё числа отчёта были бы взяты из головы |

### Открытые вопросы фазе 3

1. **Порог не режет закрытия — и это видно в числах.** На шаге 4 событий
   с порогом 72, а новых всего 60: 29 закрытий проходят мимо порога по правилу
   `events_for`. Витрине `matches --new` надо решить, показывать ли закрытия
   там же или отдельным разделом, как это делает дайджест.
2. **`match_events_since` без `request_id` читает окно по всем заявкам.**
   На шаге 6 это 13 216 строк с полным JOIN карточек. Потолок из задачи 3.1
   обязан дойти до выборки, иначе повторится долг фазы 8.
3. **`price_before` считается подзапросом на каждую строку.** Отдельно это
   не замерялось: весь прогон шести шагов уложился в секунды. Фазе 3 стоит
   замерить выборку на полном окне и записать время.

---

## Стартовый промпт фазы 3

```
Ты продолжаешь работу над инструментом мониторинга list.am
в C:\Users\Artur.A.Gevorgyan\Downloads\new_project.

Прочитай docs/superpowers/plans/2026-09-22-m3-notifications.md:
разделы «Global Constraints», «Карта файлов», «Результат фазы 1»,
«Результат фазы 2» и свою «Фазу 3». Чужие фазы не трогай. Рядом лежит спека:
docs/superpowers/specs/2026-09-22-m3-notifications-design.md — решения 1–12
в фазах не пересматриваются. Решения 1–11 спеки M2
(docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md)
тоже в силе.

Исходное состояние: HEAD `46ae46b`, дерево чистое,
батарея 711 passed, 18 skipped, схема базы 10. База для замеров —
data/listam-m3.sqlite (20 842 объявления, 51 548 матчей, схема уже 10);
база потока по шагам ленты — data/listam-flow.sqlite (тоже схема 10);
заявки — data/requests-m3.csv; конфиг замера — копия config/dev.yaml вне
репозитория с подменёнными storage.db_filename, storage.directory и
requests.path, запуск через --config-dir. Репозиторный config/dev.yaml
фазы 1 и 2 не трогали.

Твоя задача — фаза 3: витрина. Счётчик `count_matches_alive` отдельно от
выборки, потолок доходит до SQL, `matches` отдаёт страницу вместо списка,
флаг `matches --new` печатает срез «что нового со вчера» в терминал.
Наружу по-прежнему ничего не шлётся: `listam/notifications.py` в этой фазе
получает только `window_for`.

Чем фаза 2 меняет твою работу:

* Выборка событий уже есть — `Database.match_events_since(since, until,
  request_id=None)` отдаёт `(Match, Listing, price_before)`. Классификация
  тоже: `listam/domain/events.py` — `classify`, `events_for`, `limited`,
  `EVENT_LABELS`. Своих правил «что такое новое» не заводи, `--new` питается
  этими функциями (решение 10 спеки: одна выборка — две подачи).
* Порог не режет закрытия. `events_for` пропускает `retired` мимо `min_score`,
  поэтому событий на шаге ленты бывает больше, чем прошедших порог новых.
  Реши явно, как витрина показывает закрытия, и закрой это тестом.
* `match_events_since` без `request_id` читает окно по всем заявкам: на
  замеренном шаге это 13 216 строк с полным JOIN карточек. Долг фазы 8
  (50 633 строки за 4,0 с) повторится, если потолок снова не дойдёт
  до выборки. Замерь время на полном окне и запиши число.
* Вид события `cheaper` на обеих базах замера не воспроизводится: история цен
  и отметки матчей лежат на разных осях времени. Если витрина должна его
  показать — проверяй модульным тестом, а не боевым прогоном; боевая проверка
  ждёт фазы 7.

Работай по шагам задач: на каждое поведение — падающий тест ДО правки.
Тесты гоняй только .venv/Scripts/python.exe -m pytest -q, любой прогон CLI
из скрипта — только с PYTHONIOENCODING=utf-8. Пороги в конфиге не поднимай.
Новый метод порта — это новый контрактный тест в tests/contracts/.
Схему не трогай вовсе: миграция в этом плане одна, и она уже накачена.
След звонка (matches.status, matches.reject_reason) не трогает ничто,
MATCH_COMPARED не расширяется.
Ни одного числа в отчёте без команды, которая его напечатала.

Ожидается после фазы: число из раздела «Ожидается после фазы» твоей фазы;
пересчитай его от 711 и поправь в плане, если разойдётся.

В конце сессии допиши в план раздел «Результат фазы 3»: что сделано, числа
батареи, что разошлось с планом и почему, и стартовый промпт для фазы 4.
Сделай коммит.
```

---

## Результат фазы 3

**Главное одной строкой: долг фазы 8 закрыт у витрины и переехал к срезу.**
`matches` на боевой базе шла **4,0 с** — теперь **0,415 с**: потолок дошёл
до SQL, витрина читает 2 368 строк вместо 51 394, а «…и ещё N» считает
отдельный `COUNT(*)`. Срез `matches --new` тем же потолком не лечится: он
идёт **4,32 с**, и ни одной секунды из них не тратит SQL.

Батарея `.venv/Scripts/python.exe -m pytest -q` → **723 passed, 18 skipped**
(40,49 с). Схема базы — **10**, миграций фаза не добавляла. Четыре коммита,
`git diff --stat 6a800b3..HEAD` — см. ниже.

### Что сделано

| Задача | Что появилось |
| --- | --- |
| 3.1 | `Database.count_matches_alive(request_id, min_score=None)` — тот же JOIN, что у витрины, но `COUNT(*)` вместо колонок. Порт плюс реализация плюс три контрактных теста |
| 3.2 | `MatchesPage` (`rows` + `totals`) и `group_key` в `listam/matches_view.py`; `collect_matches` отдаёт страницу и берёт потолок в SQL, `render_matches` считает хвост от `totals`. `_matches` в CLI передаёт потолок в выборку, `_export` берёт `.rows` |
| 3.3 | `EventsPage`, `collect_events`, `render_events`, `_owner_key`, `_event_note`; флаги `matches --new` и `matches --hours` с отказом кодом 2 на бессмысленный ввод; `listam/notifications.py` с одним `window_for` |

Тестов добавлено 12: 3 контрактных, 8 витринных (`tests/test_matches_view.py`,
новый файл), 1 на командную строку. Правлены под `MatchesPage` семь тестов
в `tests/test_matching.py` и один в `tests/test_matching_scale.py`.

### Замер на боевой базе (`data/listam-m3.sqlite`, 20 842 объявления, 51 548 матчей)

```
time (PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam --env dev \
      --config-dir <SCRATCH>/config matches | wc -l)
3045
real    0m0.415s

time (PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam --env dev \
      --config-dir <SCRATCH>/config matches --new | wc -l)
4885
real    0m4.323s
```

Что за этими числами, в том же процессе:

```
.venv/Scripts/python.exe -c "... collect_matches(config, limit=display_limit(config)) ..."
с потолком: прочитано 2368 строк, всего по счётчику 51394, за 0.24 с
без потолка: прочитано 51394 строк за 3.96 с
```

Эталон фазы 8 — 50 633 строки за 4,0 с — воспроизведён прямо здесь строкой
«без потолка» и закрыт строкой «с потолком»: **в 16,5 раза меньше времени и
в 21,7 раза меньше прочитанных строк**. Печать при этом не обеднела: разделов
по-прежнему 51, а «подобрано 51 394» в шапках — из счётчика, а не из длины
прочитанного.

### Находка фазы: у среза `--new` потолка нет и SQL здесь ни при чём

Замер выборки на полном окне 24 ч, как просила фаза 2:

```
.venv/Scripts/python.exe -c "... db.match_events_since(since, until) ..."
полное окно без request_id: строк 51548 за 3.91 с
домен: событий 51394 за 0.04 с
по заявкам (51 шт.): строк 51548, событий 51394 за 4.09 с
```

Первый вывод: **51 394 события из 51 548 строк** — доменный фильтр фазы 2
здесь не срезает ничего. На базе потока он превращал 13 216 строк в 38,
а тут нет: база собрана подбором в один присест, у всех матчей
`first_matched_at` лежит внутри суточного окна, и все они честно новые.
Это свойство базы, а не кода.

Второй вывод важнее. Тот же запрос голым SQL:

```
.venv/Scripts/python.exe -c "... sqlite3 ... data/listam-m3.sqlite ..."
COUNT строк окна: 0.02 с
без подзапроса цены: 51548 строк за 0.17 с
с подзапросом цены: 51548 строк за 0.22 с
```

SQL отдаёт всё окно за **0,22 с**, а `match_events_since` возвращает те же
строки за **3,91 с**. Разница — 3,7 с — уходит на сборку 51 548 пар
`Match`/`Listing` в Python. Открытый вопрос 3 фазы 2 («`price_before`
считается подзапросом на каждую строку») закрыт: подзапрос стоит **0,05 с**
на всё окно, он ни при чём.

Отсюда следует, что потолок фазы 3 до этой выборки дойти и не может:
LIMIT в SQL режет строки **до** классификации, а «сколько событий всего»
обязано остаться правдой — ровно то обещание, ради которого в задаче 3.1
и заведён отдельный счётчик. Пушить в SQL `min_score` тоже бессмысленно:
порог дайджеста 40 проходят 51 394 строки из 51 548. Чинится это не потолком,
а тем, чтобы не строить объекты для строк, которые домен всё равно выбросит,
— **находка фазе 4**, у которой из этой же выборки собирается текст.

### Закрытия: одной строкой внизу раздела

Решено по плану: закрытие не повод звонить, а объяснение, куда делась
вчерашняя карточка, — место ему в конце раздела, а не среди вариантов.
Закрыто тестом `test_closings_are_counted_at_the_bottom_and_not_among_the_options`
и проверено живьём на базе потока (`data/listam-flow.sqlite`, 168 закрытых
матчей, все с причиной «проход больше не подтверждает этот вариант»):

```
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam --env dev \
    --config-dir <SCRATCH>/config-flow matches --new --hours 1
Что нового: за последние 1 ч (с 22.09 14:26 UTC)
Заявка R-1 (Клиент 1) — новый: 1888
  …и ещё 1838 из 1888 — python -m listam matches --request R-1 --new
...
закрытий в печати всего: 168
разделов: 51
строк «…и ещё»: 46
строк «отпало»: 38
```

Все 168 закрытий на месте, ни одно не потеряно порогом (`events_for`
пропускает `retired` мимо `min_score`) и ни одно не стоит среди вариантов:
38 строк «отпало N (причина N)» на 51 раздел.

### Что разошлось с планом

| Что | Как в плане | Как вышло |
| --- | --- | --- |
| Батарея | 722 passed | **723 passed, 18 skipped**. Добавлен двенадцатый тест — `test_a_cheaper_one_says_what_it_used_to_cost`: вид `cheaper` на обеих базах замера не воспроизводится (находка фазы 2), и витрина обязана показать его хотя бы модульно. Число в «Ожидается после фазы» поправлено; фазе 4 считать от **723** |
| `tests/conftest.py` → `make_config` | тест зовёт `make_config` | такого помощника в `conftest.py` нет. Взят `cfg` из `tests/test_matching.py` — тот самый «общий помощник важнее, чем новый», о котором предупреждал шаг 2 задачи 3.2 |
| `tests/test_cli.py` → `config_dir` | тест зовёт `config_dir(tmp_path)` | в файле помощник называется иначе: фикстура `project` плюс `run(project, *args)`. Тест написан на них |
| `test_a_match_without_a_listing_is_not_counted` | матч на несуществующую карточку записывается, счётчик его не видит | записать его нельзя вовсе: `matches.listing_id` — внешний ключ на `listings`, и `upsert_match` падает с `FOREIGN KEY constraint failed`. Тест переименован в `..._cannot_exist_at_all` и проверяет то, что есть на самом деле: сироты не бывает, расходиться счётчику с выборкой не на чем |
| `test_the_slice_shows_only_what_moved_inside_the_window` | матч, уже существующий с `NOW`, перезаписывается внутри окна и ожидается событием | это ровно «пересчёт — не событие», правило фазы 2: `upsert_matches` при неизменном `MATCH_COMPARED` даже не двигает `matched_at`. Тест заводит **новый** матч внутри окна и ждёт его одного из четырёх — смысл («окно показывает только то, что в нём шевельнулось») сохранён, правило не нарушено |
| «событий нет» в шапке раздела | `counts` всегда падает в «событий нет» | у раздела, где в окне одни закрытия, это читалось бы как «ничего не было» рядом со строкой «отпало 4». Такой раздел подписан «только закрытия» |
| Ожидание «около 2 247 строк за 0,22 с» | — | живьём **2 368 строк за 0,24 с**. Разница в строках — от заявок, у которых живых матчей меньше потолка 50 |

Две мелочи, на которые наткнулись по дороге и которые стоит знать фазе 4:

* **`price_usd` — пересчитанное поле.** Сменить в тесте только его мало:
  база считает карточку неизменившейся (`TRACKED_FIELDS` про `price_raw`
  и `currency`), точку в `price_history` не пишет и `price_usd` бережёт
  через `COALESCE`. Цену в тесте двигают вместе с `price_raw`.
* **`cheaper` требует, чтобы поехал и балл.** `upsert_matches` при неизменном
  `MATCH_COMPARED` не двигает `matched_at`, а без него окно матча не касается.
  На живом прогоне так и есть — подешевевшая квартира набирает больше по
  бюджету и цене за метр, — но в тесте это надо написать руками.

### Открытые вопросы фазе 4

1. **Сборка объектов, а не SQL.** 3,7 с из 3,91 с — это `_row_to_match` и
   `_row_to_listing` на 51 548 строк. Текст уведомления собирается из этой же
   выборки; если фаза 4 позовёт её как есть, `notify` будет стоить те же
   четыре секунды на каждый запуск по расписанию.
2. **Окно без базы врёт вслух, и это видно.** `matches --new` печатает
   «отправок ещё не было — беру последние 24 ч»: в фазе 3 `window_for` зовётся
   без базы нарочно. Фаза 4 передаёт туда открытую базу, и подпись меняется
   сама — проверить, что меняется.
3. **Потолок среза считается от `match.limit`.** `_matches_new` берёт
   `display_limit(config)` — тот же потолок, что у витрины по баллу (50).
   У уведомления лимиты свои (решение 5 спеки); когда они появятся в конфиге,
   решить, чем меряется терминальный срез.

---

## Стартовый промпт фазы 4

```
Ты продолжаешь работу над инструментом мониторинга list.am
в C:\Users\Artur.A.Gevorgyan\Downloads\new_project.

Прочитай docs/superpowers/plans/2026-09-22-m3-notifications.md:
разделы «Global Constraints», «Карта файлов», «Результат фазы 2»,
«Результат фазы 3» и свою «Фазу 4». Чужие фазы не трогай. Рядом лежит спека:
docs/superpowers/specs/2026-09-22-m3-notifications-design.md — решения 1–12
в фазах не пересматриваются. Решения 1–11 спеки M2
(docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md)
тоже в силе.

Исходное состояние: HEAD `a089e13`, дерево чистое,
батарея 723 passed, 18 skipped, схема базы 10. База для замеров —
data/listam-m3.sqlite (20 842 объявления, 51 548 матчей, схема 10);
база потока по шагам ленты — data/listam-flow.sqlite (тоже схема 10,
168 закрытых матчей); заявки — data/requests-m3.csv; конфиг замера — копия
config/dev.yaml вне репозитория с подменёнными storage.db_filename,
storage.directory и requests.path, запуск через --config-dir. Репозиторный
config/dev.yaml фазы 1–3 не трогали.

Твоя задача — фаза 4: текст уведомления, журнал отправок в работе и команда
`notify` на общем каркасе. Канал — `stdout`: наружу в этой фазе не уходит
ничего, и это нарочно. Telegram — фаза 5.

Число «Ожидается после фазы» в твоей фазе записано от 722 — считай от 723
и поправь его в плане (в фазе 3 добавился двенадцатый тест, см. «Результат
фазы 3»).

Чем фаза 3 меняет твою работу:

* `listam/notifications.py` уже есть, и в нём только `window_for(config,
  kind, hours=None, database=None)`. Фаза 3 зовёт его без базы, и срез честно
  пишет «отправок ещё не было — беру последние 24 ч». Твоё дело — передать
  туда открытую базу и проверить тестом, что подпись меняется на «с прошлой
  отправки».
* Выборка и подача уже общие: `collect_events` / `render_events`
  в `listam/matches_view.py` (решение 10 спеки). Сообщение собирается из тех
  же `MatchEvent`, что печатает `matches --new`; второй выборки не заводи.
* **Четыре секунды на выборку.** На боевой базе `match_events_since` отдаёт
  51 548 строк за 3,91 с, из которых SQL — 0,22 с, а 3,7 с — сборка пар
  Match/Listing в Python (подзапрос за ценой стоит 0,05 с и ни при чём).
  `notify` ходит по расписанию: замерь `notify --dry-run` на боевой базе
  и запиши число. Потолок эту выборку не лечит — LIMIT режет строки до
  классификации, а «сколько событий всего» обязано остаться правдой.
* Доменный фильтр на этой базе не срезает ничего: 51 394 события из 51 548
  строк, потому что база собрана подбором в один присест. Лимиты
  уведомления (решение 5 спеки) — единственное, что стоит между текстом
  и сообщением на 51 394 события. Проверь их на этих числах.
* Закрытия витрина показывает одной строкой внизу раздела («отпало N
  (причина N)») и не пускает среди вариантов; порог их не режет. Дайджест
  делает то же самое — не расходись с витриной.
* `_matches_new` меряет срез потолком `match.limit` (50). Когда в конфиге
  появятся лимиты уведомления, реши, чем меряется терминальный срез,
  и запиши решение.

Работай по шагам задач: на каждое поведение — падающий тест ДО правки.
Тесты гоняй только .venv/Scripts/python.exe -m pytest -q, любой прогон CLI
из скрипта — только с PYTHONIOENCODING=utf-8. Пороги в конфиге не поднимай.
Новый метод порта — это новый контрактный тест в tests/contracts/.
Схему не трогай вовсе: миграция в этом плане одна, и она уже накачена.
След звонка (matches.status, matches.reject_reason) не трогает ничто,
MATCH_COMPARED не расширяется.
Ни одного числа в отчёте без команды, которая его напечатала.

Уведомление — действие наружу. Канал в этой фазе `stdout`, но режим
«покажи, что послал бы» обязан появиться здесь, а не в фазе 5.

В конце сессии допиши в план раздел «Результат фазы 4»: что сделано, числа
батареи, что разошлось с планом и почему, и стартовый промпт для фазы 5.
Сделай коммит.
```

---

## Результат фазы 4

**Главное одной строкой: сообщение собирается, журнал работает и окно ездит
от отправки к отправке — но лимиты на заявку сообщение не спасают.** Дайджест
на боевой базе — **51 916 событий по 50 заявкам**, которые `notify.digest.per_request: 10`
ужимает до **1 174 строк и 91 756 символов**. Предел одного сообщения
Telegram — 4 096. Лимиты сократили текст в 30 раз и всё равно промахнулись
в 22 раза: фазе 5 нужен не лишний потолок, а решение про число заявок
в сообщении.

Батарея `.venv/Scripts/python.exe -m pytest -q` → **747 passed, 18 skipped**
(86,23 с). Схема базы — **10**, миграций фаза не добавляла. Шесть коммитов,
`git diff --stat 21525e8..HEAD` — 10 файлов, +708 −13.

### Что сделано

| Задача | Что появилось |
| --- | --- |
| 4.1 | `listam/ports/notifier.py` целиком: `NotifyError`, `send(text, to=None)`, `describe()` у всех трёх реализаций. Новый контрактный тест `tests/contracts/test_notifier_contract.py` — четыре теста на двух реализациях |
| 4.2 | Секция `notify` в `config/dev.yaml` и `config/prod.yaml`: тумблеры, `per_request`, `wide_request`, `limit`, `fallback_hours`. Числа — из «Задачи 1.3, шаг 4» фазы 1 (5 / 10 / 15 / 15), в обоих конфигах `kind: stdout` |
| 4.3 | `NotifyReport`, `enabled`, `per_request`, `run_notify`, `_match_text`, `_feed_text` в `listam/notifications.py`. Отправка — до записи в журнал; запись — только после успеха |
| 4.4 | Подкоманда `notify` с `--hot`/`--digest`/`--feed`/`--dry-run`, отказ кодом 2 на «ни одного» и «два сразу»; `_matches_new` мерит окно тем же журналом; раздел README |

Тестов добавлено **24**: 8 контрактных, 1 конфигурационный, 12 на уведомление
(`tests/test_notifications.py`, новый файл), 3 на командную строку. Чужие тесты
не правились ни одного.

### Замер на боевой базе (`data/listam-m3.sqlite`, 20 842 объявления, 52 098 матчей)

```
time (PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam --env dev \
      --config-dir <SCRATCH>/config notify --digest --dry-run)
Уведомление (digest): отправок ещё не было — беру последние 24 ч (с 21.09 16:46 UTC)
...
Событий: 51916, заявок: 50, не отправлено (пробный прогон)
локальная копия не старее удалённой; пробный прогон: не отправлено, журнал не тронут
real    0m10.901s
строк: 1174   разделов: 50   пометок «слишком широкая»: 46   строк «…и ещё»: 46

time (... notify --hot --dry-run)
Уведомление (hot): отправок ещё не было — беру последние 2 ч (с 22.09 14:47 UTC)
Событий: 37813, заявок: 50, не отправлено (пробный прогон)
real    0m10.397s
строк: 642   разделов: 50   строк «…и ещё»: 48
```

Раздел на заявку выглядит так (первые строки `--hot`):

```
Заявка R-1 (Клиент 1) — новый: 3
  •  88 баллов     $85,000    1,104 $/м²  Арабкир, Комитаса    3 ком., 77 м², эт. 1/2, агентство   https://www.list.am/ru/item/24056507
      новый
```

А пометка широкой — так:

```
Заявка R-2 (Клиент 2) — новый: 1024
  ⚠ заявка слишком широкая: 1024 событий за окно. Сузь районы или бюджет, иначе разговор не состоится
```

Лента — отдельный разговор и отдельный тумблер; в репозиторном конфиге он
выключен, и команда так и говорит:

```
... notify --feed --dry-run                      # notify.feed.enabled: false
Уведомление (feed): тумблер выключен
уведомления вида feed выключены в конфиге (notify.feed.enabled)

... --config-dir <SCRATCH>/config-feed notify --feed --dry-run   # тумблер включён
Уведомление (feed): отправок ещё не было — беру последние 24 ч (с 21.09 16:48 UTC)
На ленте: новых 51, подешевели 15, снято 0
  •   $104,611    1,113 $/м²  Эребуни, —  https://www.list.am/ru/item/23540286
```

### Находка фазы: лимит на заявку не делает из витрины сообщение

Тот же текст, померенный в процессе:

```
.venv/Scripts/python.exe -c "... collect_events(...) ... _match_text(...) ..."
выборка: событий 51916, заявок 50, за 9.76 с
текст: строк 1171, символов 91469, за 0.19 с

.venv/Scripts/python.exe -c "... разделы посчитать ..."
разделов 50
символов в разделе: медиана 1869 максимум 1989
весь текст сообщения, символов: 91756
предел одного сообщения Telegram: 4096
```

`per_request: 10` работает — без него в тексте лежало бы 51 916 строк вместо
1 174. Но **потолок режет строки внутри заявки, а заявок пятьдесят**, и каждая
стоит около 1 870 символов: два раздела уже не влезают в одно сообщение. Ни
одна ручка секции `notify` этого не чинит, потому что чинить надо не длину
раздела, а их число. Это **находка фазе 5**, и она же — первый вопрос
её дизайна: одно сообщение на заявку, разбиение длинного текста на части
или потолок на число заявок в сообщении.

### Четыре секунды выборки: подтверждены, но на этой машине их десять

Открытый вопрос 1 фазы 3 проверен прямо:

```
.venv/Scripts/python.exe -c "... db.match_events_since(since, until) ..."
полное окно без request_id: строк 52098 за 9.38 с
домен: событий 51916 за 0.11 с
COUNT матчей: 52098 за 0.00 с
```

Пропорция фазы 3 воспроизведена один в один: SQL и домен стоят доли секунды,
всё время уходит на сборку пар `Match`/`Listing` в Python. Абсолютные числа
больше: фаза 3 мерила 51 548 строк за 3,91 с, здесь 52 098 строк за 9,38 с —
**замер переехал на другую машину** (см. «Что разошлось с планом»), и она
примерно в 2,4 раза медленнее на этой работе. Вывод фазы 3 от этого только
крепче: `notify` по расписанию стоит десять секунд, из которых девять с
половиной — объекты, которые домен почти целиком оставляет себе.

### Журнал: окно действительно едет

Две отправки подряд, живьём:

```
... notify --hot
Событий: 37813, заявок: 50, отправлено
локальная копия не старее удалённой; база с журналом уведомлений залита в хранилище

... notify --hot            # сразу следом
Уведомление (hot): с прошлой отправки (22.09 16:48 UTC)
Звони сейчас: событий нет (с прошлой отправки (22.09 16:48 UTC))
Событий: 0, заявок: 0, отправлено

sqlite> select id, kind, window_from, window_to, events, requests, length(text) from notifications
(1, 'hot', '2026-09-22T14:48:27.201610+00:00', '2026-09-22T16:48:27.201610+00:00', 37813, 50, 46136)
(2, 'hot', '2026-09-22T16:48:27.201610+00:00', '2026-09-22T16:48:39.290220+00:00', 0, 0, 64)
```

`window_from` второй строки равен `window_to` первой — окна смыкаются без
зазора и без нахлёста. Пустая отправка тоже записана: иначе завтра пришла бы
сегодняшняя пустота плюс завтрашние события.

Витрина после отправки дайджеста мерит то же окно:

```
... notify --digest
Событий: 51916, заявок: 50, отправлено

... matches --new
Что нового: событий нет (с прошлой отправки (22.09 16:48 UTC))
```

### Решение: чем меряется терминальный срез

Открытый вопрос 3 фазы 3 закрыт так: **окно у витрины и у сообщения одно,
потолок — разный.** `matches --new` и `notify --digest` берут отметку из
одного журнала — иначе сличить отправляемое глазами нельзя, а ради этого
решение 10 спеки и заводило две подачи одной выборки. Строк же на заявку
витрина показывает `match.limit` (50), а сообщение — `notify.<вид>.per_request`
(5 и 10). Причина простая: у терминала есть полоса прокрутки, а у сообщения
в чате её нет. Записано в README.

### Ошибка, которую нашёл боевой прогон

Пометка «слишком широкая» садилась на строку по `startswith("Заявка R-1")`,
а `R-1` — начало `R-11`, `R-10`, `R-19`. На боевой базе это дало **77 пометок
на 50 разделов**, причём соседу доставался чужой счётчик. Найдено сравнением
числа пометок с числом заявок в выводе команды, закрыто тестом
`test_a_wide_mark_does_not_stick_to_a_neighbour` и исправлено пробелом после
ключа: теперь **46 пометок на 50 разделов**, ровно столько же, сколько строк
«…и ещё».

Батарея этого не ловила и поймать не могла: в тестах была одна заявка.

### Что разошлось с планом

| Что | Как в плане | Как вышло |
| --- | --- | --- |
| Машина и база замера | `data/listam-m3.sqlite` с прошлой сессии | **папка `data/` не коммитится**, и на этой машине её нет: ни базы замера, ни базы потока, ни `requests-m3.csv`, ни скриптов `tmp/`. База собрана заново из восьми выгрузок `out/` — 20 842 объявления и 20 943 точки цен, число в число как в фазе 1. Заявки сгенерированы заново (зерно `Random(20260922)`), поэтому матчей **52 098** против 51 548 фазы 1, кластеров 14 501 против 14 491 |
| Первое восстановление базы | — | **читало колонки по номеру и молча врало**: у восьми выгрузок 19, 21 и 22 колонки, и площадь, цена за метр и комнаты разъезжались («1780 ком., 89000 м²»). Замеры на той базе выброшены, скрипт переписан на чтение по именам заголовков. Числа в этом отчёте — со второй, правильной базы |
| База потока `data/listam-flow.sqlite` | есть | **нет и не восстанавливалась**: фазе 4 она не нужна, а закрытия проверены модульно. Фазе 5, если понадобятся 168 закрытий живьём, её придётся собрать заново (`tmp/measure_flow.py` тоже не пережил переезд) |
| `tests/conftest.py` → `make_config` | тест зовёт `make_config` | такого помощника в `conftest.py` по-прежнему нет — та же находка, что в фазе 3. Помощник написан в самом `tests/test_notifications.py` и пишет **настоящий файл конфига** в `tmp_path`: `matches --new` зовётся через `--config-dir`, и конфигу из памяти там взяться неоткуда |
| `tests/test_cli.py` → `config_dir(tmp_path)` | тест зовёт `config_dir(tmp_path)` | в файле фикстура `project` и помощник `run(project, *args)`. Тесты написаны на них; `notify --digest --dry-run` требует базы, поэтому перед ним идёт `scrape` |
| Тестов на уведомление | восемь | **двенадцать**: семь из плана, один на общее окно, один на «отправок ещё не было», два на ленту и один на пометку широкой заявки. `_feed_text` в плане не был закрыт ни одним тестом — тумблер ленты выключен по умолчанию, и ошибка в нём дожила бы до первого включения |
| Батарея | 742 passed (от 722) | **747 passed, 18 skipped**. Счёт от **723**, как велел стартовый промпт, плюс 24 теста. Число в «Ожидается после фазы» поправлено |
| `notify.digest.wide_request` | 50 в образце секции | **15** — число из отчёта фазы 1 (p75 суточных событий на заявку), как и требовал шаг 3 задачи 4.2: «значения подставь замеренные» |
| Строка отчёта о заливке | `publish(session, config, "журнал уведомлений")` | «журнал уведомлений залита в хранилище» — каркас склеивает `{what} залита`. Передаётся «база с журналом уведомлений», как у матчей и кластеров |

### Открытые вопросы фазе 5

1. **Сообщение в 91 756 символов не отправится.** Предел Telegram — 4 096 на
   сообщение, раздел заявки стоит около 1 870. Лимиты секции `notify` эту
   задачу не решают: они режут строки внутри заявки, а заявок пятьдесят.
   Фазе 5 решать, что именно уходит в чат, и решать до того, как появится
   живой канал.
2. **Отправка не дробится, и это заметно при отказе.** `run_notify` шлёт один
   текст и пишет одну строку журнала. Если фаза 5 разобьёт сообщение на части,
   отказ на третьей части из семи оставит окно несдвинутым целиком — и следующий
   запуск пошлёт всё заново, включая дошедшее. Это решение, а не деталь.
3. **Десять секунд на выборку никуда не делись.** `notify` по расписанию —
   раз в час; девять с половиной секунд из десяти уходят на сборку объектов,
   которые домен почти целиком оставляет себе. Чинится это в `db_sqlite`, а не
   в уведомлении, и фаза 5 туда не идёт — но числа для решения уже есть.
4. **`doctor` про канал ещё молчит.** `describe()` у всех трёх реализаций есть
   с фазы 4, но никто его не зовёт. Задача 5.2 плана это и закрывает.

---

## Стартовый промпт фазы 5

```
Ты продолжаешь работу над инструментом мониторинга list.am.

Прочитай docs/superpowers/plans/2026-09-22-m3-notifications.md:
разделы «Global Constraints», «Карта файлов», «Результат фазы 3»,
«Результат фазы 4» и свою «Фазу 5». Чужие фазы не трогай. Рядом лежит спека:
docs/superpowers/specs/2026-09-22-m3-notifications-design.md — решения 1–12
в фазах не пересматриваются. Решения 1–11 спеки M2
(docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md)
тоже в силе.

Исходное состояние: HEAD `45227f5`, дерево чистое,
батарея 747 passed, 18 skipped, схема базы 10.

Твоя задача — фаза 5: адаптер Telegram, канал в `doctor` и одна живая
проверка руками. Всё, что можно проверить без сети, проверяется без сети.

**Данных в репозитории нет — папка `data/` не коммитится.** Если ты на той же
машине, где шла фаза 4, база замера лежит в `data/listam-m3.sqlite`
(20 842 объявления, 52 098 матчей, 51 заявка из `data/requests-m3.csv`,
две строки в журнале `notifications`), конфиг замера — копия `config/dev.yaml`
вне репозитория с подменёнными `storage.db_filename` (`listam-m3.sqlite`),
`storage.directory` (`./data/remote-m3`) и `requests.path`, запуск через
`--config-dir`. Если базы нет — восстанови её из восьми выгрузок `out/`,
**читая колонки по именам заголовков, а не по номерам**: у выгрузок 19, 21
и 22 колонки, и позиционное чтение молча сдвигает площадь, цену за метр
и комнаты. Так фаза 4 уже один раз обожглась. Репозиторный `config/dev.yaml`
фазы 1–4 не трогали, кроме секции `notify` (задача 4.2).

Чем фаза 4 меняет твою работу:

* **Сообщение, которое собирает `notify --digest`, в Telegram не влезет.**
  На боевой базе это 91 756 символов при пределе 4 096: 50 разделов заявок
  по ~1 870 символов каждый. Лимиты секции `notify` тут бессильны — они
  режут строки внутри заявки, а чинить надо число заявок. Это первое, что
  фаза 5 обязана решить, и решить **до** живого канала. Числа — в «Находке
  фазы» результата фазы 4.
* **Порт готов.** `send(text, to=None)`, `describe()`, `NotifyError` есть
  у обеих реализаций, контрактный тест лежит в
  `tests/contracts/test_notifier_contract.py` и параметризован по реализациям —
  добавить `telegram` туда значит добавить параметр, а не файл.
* **Режим «покажи, что послал бы» уже есть** и проверен на боевой базе:
  `notify --digest --dry-run` не трогает журнал. Живой канал включается
  ровно одним шагом задачи 5.3, после того как текст посмотрели глазами.
* **Журнал работает и окна смыкаются** (`window_from` следующей отправки равен
  `window_to` предыдущей). Если сообщение начнёт дробиться на части, реши,
  что писать в журнал при отказе на середине: сейчас запись одна и на неё
  завязано «повторный запуск не шлёт то же дважды».
* **`doctor` про канал молчит.** `describe()` написан, но никем не зовётся —
  задача 5.2.
* **Выборка стоит десять секунд** на этой машине (девять с половиной — сборка
  объектов в Python, SQL и домен — доли секунды). Фаза 5 в `db_sqlite`
  не идёт, но если замеряешь `notify` живьём, это число — фон, а не сбой.

Работай по шагам задач: на каждое поведение — падающий тест ДО правки.
Тесты гоняй только `.venv/Scripts/python.exe -m pytest -q`, любой прогон CLI
из скрипта — только с `PYTHONIOENCODING=utf-8`. Пороги в конфиге не поднимай.
Новый метод порта — это новый контрактный тест в `tests/contracts/`.
Схему не трогай вовсе: миграция в этом плане одна, и она уже накачена.
След звонка (`matches.status`, `matches.reject_reason`) не трогает ничто,
`MATCH_COMPARED` не расширяется. Токен — только в `.env`, в yaml ссылка
`${TELEGRAM_BOT_TOKEN}`; контракт `telegram` без токена в окружении — `skipif`.
Ни одного числа в отчёте без команды, которая его напечатала.

**Отправленное нельзя отозвать.** Живой чат — один явный шаг в конце,
и до него наружу не уходит ничего.

**Ключи Telegram уже есть и проверены** — раздел «Ключи Telegram» в начале
плана. Бот `@ListamTotifybot`, чат — личка брокера `1930501720`, `.env`
заполнен, `sendMessage` прошёл живьём (`message_id 7`). Шаги 0 и 1 задачи 5.3
закрыты; заводить бота заново не надо. Токен в отчёт, в yaml и в коммит
не попадает: в конфиге ссылка `${TELEGRAM_BOT_TOKEN}`, `.env` в `.gitignore`.
Если ты на другой машине и `.env` там нет — задачи 5.1 и 5.2 делаются целиком
(сети они не требуют), а 5.3 и 5.4 переносятся в фазу 7 **с записью первой
строкой отчёта**. Выдумывать успешную отправку нельзя.

**Каждая живая отправка приходит брокеру в личку** — там же, где он читает
рабочую переписку. Перед `notify --digest` на боевой базе посмотри текст
через `--dry-run`: отозвать нельзя.

**Задача 5.4 — живая проверка браузером.** Отправка проверяется не только
ответом Bot API, но и глазами: Playwright открывает Telegram Web с готовым
профилем `tmp/telegram-profile` и читает чат. Тесты лежат в `tests/live/`,
включаются переменной `TELEGRAM_LIVE=1` и в обычной батарее всегда skip.
Между отправками — пауза `TELEGRAM_SEND_DELAY` (по умолчанию 4 с): Bot API
режет темп. Вход в Telegram Web делается руками один раз (шаг 1 задачи 5.4) —
это единственный шаг фазы, который агент не выполняет сам.

В конце сессии допиши в план раздел «Результат фазы 5»: что сделано, числа
батареи, что разошлось с планом и почему, и стартовый промпт для фазы 6.
Сделай коммит.
```

---

## Шаблон стартового промпта (каждая фаза дописывает свой)

```
Ты продолжаешь работу над инструментом мониторинга list.am
в C:\Users\Artur.A.Gevorgyan\Downloads\new_project.

Прочитай docs/superpowers/plans/2026-09-22-m3-notifications.md:
разделы «Global Constraints», «Карта файлов», «Результат фазы <N-1>»
и свою «Фазу <N>». Чужие фазы не трогай. Рядом лежит спека:
docs/superpowers/specs/2026-09-22-m3-notifications-design.md — решения 1–12
в фазах не пересматриваются. Решения 1–11 спеки M2
(docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md)
тоже в силе.

Исходное состояние: HEAD <хэш>, дерево чистое, батарея <N> passed,
<M> skipped, схема базы <версия>. База для замеров — <путь>.

Твоя задача — фаза <N>: <одна фраза о смысле фазы>.
<Три-четыре строки о том, что именно делается и почему это одно целое.>

Работай по шагам задач: на каждое поведение — падающий тест ДО правки.
Тесты гоняй только .venv/Scripts/python.exe -m pytest -q, любой прогон CLI
из скрипта — только с PYTHONIOENCODING=utf-8. Пороги в конфиге не поднимай.
Новый метод порта — это новый контрактный тест в tests/contracts/.
Схему меняет только миграция 010 (фаза 2); своих не заводи.
След звонка (matches.status, matches.reject_reason) не трогает ничто,
MATCH_COMPARED не расширяется.
Ни одного числа в отчёте без команды, которая его напечатала.

В конце сессии допиши в план раздел «Результат фазы <N>»: что сделано, числа
батареи, что разошлось с планом и почему, и стартовый промпт для фазы <N+1>.
Сделай коммит.
```

---

## Что в этот план не входит

- **Телефоны продавцов (M4) и дашборд (M5).** Порт `contacts` не заводится,
  `matches` телефон не показывает.
- **Маршруты «сообщение клиенту напрямую».** Канал один и он брокерский
  (решение 4 спеки). Порт принимает `to`, но заявка своего адреса не имеет,
  и колонка в источнике не появляется.
- **Своя очередь отправки и повторы по расписанию.** Отказ канала — это код 1
  и несдвинутое окно; следующий запуск по расписанию пошлёт то, что не дошло.
  Демона у `listam` нет и не будет.
- **Форматирование Telegram (HTML/Markdown, кнопки, картинки).** Сообщение —
  простой текст: разметка ломается на адресах и названиях улиц, а звонок от неё
  не становится вероятнее.
- **Пересмотр порогов `hot` и `digest`.** Остаются 70 и 40; переполнение решают
  лимиты уведомления (решение 5 спеки).
- **Скоринг `must_have` / `nice_to_have`.** Решение M2 в силе.
- **`crawler` на общем каркасе.** Решение фазы 7 QA-плана, не чинится.
- **Живая проверка `gsheet`.** Решение 11 спеки M2 в силе: адаптер под `skipif`.
- **Уведомления о снятых с ленты объявлениях вне заявок.** `changes` это уже
  показывает; дублировать витрину в чат незачем.
