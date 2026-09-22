# QA-ужесточение после M3: план исправлений

> **Для агента-исполнителя:** одна фаза — одна сессия. В начале сессии читаешь
> «Что нашёл аудит», «Global Constraints», «Карта файлов», «Результат фазы N−1»
> и свою фазу; чужие фазы не трогаешь. В конце сессии дописываешь в этот файл
> раздел «Результат фазы N» и стартовый промпт для следующей фазы, затем делаешь
> коммит. Шаги помечены `- [ ]` — отмечай по ходу. Исполнять план помогает скилл
> `superpowers:executing-plans` или `superpowers:subagent-driven-development`.

**Нумерация.** M0 — парсер и обход, M1 — дельта и история, M2 — заявки и
матчинг, затем QA-ужесточение после M2 (восемь фаз), M3 — уведомления и дайджест
(закрыт 22.09.2026, семь фаз). Этот план — не новый этап, а разбор второго
внешнего QA: по всему, что построено к этому дню, с упором на M3. Следующий
этап (телефоны продавцов) в файлах зовётся **M4**; его план пишется после
фазы 8 этого документа.

**Цель:** брокер, получивший в Telegram «Звони сейчас», звонит по квартире,
которая правда появилась или правда подешевела, — не по закрытию, не по той же
квартире под другой карточкой и не по второй копии вчерашнего сообщения. А
подешевевшую квартиру он не теряет из-за того, в каком порядке расписание
запустило команды.

**Архитектура:** ничего не переписывается заново. Закрытие перестаёт выдавать
себя за событие; смена представителя кластера читается как «подешевела», а не
«новая»; «подешевело» мерится историей цен, а не сдвигом балла; журнал отправок
участвует в выборе свежей копии базы и помнит канал; секция `notify` проверяется
на входе, как `match`. Схема меняется одной миграцией — **011**.

**Стек:** Python 3.12, SQLite, pytest, `requests`, openpyxl, PyYAML. Новых
зависимостей план не вводит; `playwright` по-прежнему только dev-зависимость
живой проверки.

**Спеки, решения которых в силе:**
`docs/superpowers/specs/2026-09-22-m3-notifications-design.md` (решения 1–12) и
`docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md`
(решения 1–11). Где фаза решение **уточняет**, это написано в её заголовке.

**Исходное состояние (снято 23.09.2026):**

| Что | Значение |
| --- | --- |
| HEAD | `3a2ff7f` («docs: приёмка M3 и разбор находок»), дерево чистое |
| Батарея | **778 passed, 25 skipped** (86,1 с) |
| Схема базы | 10 (миграции 001–010) |
| База приёмки M3 | `data/listam-m3.sqlite` — восстановлена из выгрузок, с настоящей историей цен и одним свежим обходом (фаза 1 и 7 плана M3) |
| Боевой файл базы | недоступен: `GDRIVE_FOLDER` и `GDRIVE_CREDENTIALS_FILE` пусты |
| Telegram | ключи в `.env` (только эта машина), чат — личка брокера |

---

## Что нашёл аудит

Каждая находка ниже **воспроизведена запуском** — скриптом на временной базе
(конфиг и фикстуры `tests/test_notifications.py` и `tests/test_matching.py`,
настоящие `run_match`, `run_notify`, `collect_events`, `take_the_fresher_copy`),
а не вычитана глазами. Номера используются в задачах фаз.

### Блокеры

**B-1. «Звони сейчас» приносит закрытия.**
Решение 1 спеки M3: «немедленным уведомлением закрытие не шлётся никогда».
`run_notify` для `hot` зовёт тот же `collect_events`, что и дайджест, а
`render_events` печатает тихий раздел «отпало» для любого вида. Брокер
получает в «горячем» раздел заявки, по которой звонить некому.
*Воспроизведено:* два матча отправлены `notify --hot`, затем оба закрыты
(«бюджет») → следующий `notify --hot --dry-run`:

```
Звони сейчас: с прошлой отправки (22.09 21:19 UTC)

Заявка R-1 (Ани) — только закрытия
  отпало 2 (бюджет 2)
```

и `events 2, requests 1` в отчёте.

**B-2. Та же квартира приходит «новым вариантом», когда нашлась её карточка
дешевле.** В `matches` лежит самый дешёвый член кластера. Пришла карточка той
же квартиры дешевле — подбор пишет новый матч на неё и закрывает прежний с
причиной «не представитель кластера». Классификатор видит `first_matched_at`
в окне и зовёт это «новый». Брокер звонит по квартире, о которой уже знает, а
настоящее событие — «подешевела на $2 000» — не названо вовсе.
*Воспроизведено на `run_match`:* `old` (85 м², 119 000 $), подбор, затем `twin`
(85,5 м², 117 000 $, тот же дом и этаж), подбор → события окна
`[('retired', 'old', 'e8cdd980…'), ('new', 'twin', 'e8cdd980…')]` — **один
`cluster_id`**, а в тексте «новый: 1 … 2 объявления, разброс $2,000» и
«отпало 1 (не представитель кластера 1)». На приёмке M3 свежий обход закрыл
43 матча с этой причиной — каждый из них пришёл брокеру «новым».

**B-3. Журнал отправок не участвует в выборе свежей копии базы — вторая машина
шлёт всё повторно.** `take_the_fresher_copy` сравнивает копии по
`MAX(runs.started_at)`. Журнал прогонов пишет только `scrape`; `notify`,
`match` и `requests` пишут базу, прогона не открывая. Копия, в которой дайджест
уже записан, и копия без него для этой мерки — ровесницы, и побеждает
локальная. Проект живёт минимум на двух машинах (стартовые промпты планов
называют `C:\Users\Artur.A.Gevorgyan\…` и `C:\Users\Admin\…`).
*Воспроизведено:* «машина B» — `notify --hot`, отправлено 2, база залита;
«машина A» — своя локальная копия с тем же журналом прогонов →
`notify --hot --dry-run`: «локальная копия не старее удалённой», **2 события
к отправке** — те же самые. Следующая заливка A сотрёт строку журнала B.

### Высокие

**H-1. Подешевевшее теряется навсегда, если дайджест прошёл между обходом и
подбором.** `CHEAPER` требует, чтобы в окне сдвинулся `matched_at`, а «цену до
окна» берёт на `since`. Расписание README — `scrape --fresh && match --new &&
notify --hot` раз в час и `notify --digest` в 20:00: замок отпускается между
командами цепочки, и дайджест может встать между `scrape` и `match`. Тогда в
окне дайджеста цена упала, но матч не пересчитан — не событие; в следующем
окне матч пересчитан, но «цена до окна» уже новая — снова не событие.
*Воспроизведено на `run_match`:* 118 000 → 100 000 $, окно дайджеста закрыто до
подбора → `[]`; подбор (`updated 2`) → следующее окно → `[]`.

**H-2. Глубокая скидка, не сдвинувшая балл, — не событие.** Фактор выгодности
упирается в потолок на −25 % к медиане района, бюджет — на «в пределах». У
квартиры, которая и так была самой выгодной, цена падает — а балл, разбор и
кластер те же, `upsert_matches` отвечает `unchanged`, `matched_at` не
двигается. Самые выгодные варианты — ровно те, о подешевении которых брокер
не узнает никогда.
*Воспроизведено на `run_match`:* 60 000 → 50 000 $ при `price_per_sqm` 705 → 588
→ «новых 0, обновлённых 0, без изменений 3», событий `[]`.

**H-3. `notify.kind: none` пишет «отправлено» и двигает окно.** `NullNotifier`
ничего не шлёт, но `run_notify` считает отправку успешной и пишет строку в
журнал. Канал, не настроенный по забывчивости, съедает события: когда Telegram
появится, он получит только то, что случилось после. То же со `stdout`: текст,
напечатанный в консоль, двигает окно Telegram (фаза 7 M3 так и сделала —
«дайджест ушёл через stdout того же журнала: окно сдвинуто»).
*Воспроизведено:* `kind: none` → `sent True, events 2`, в журнале строка на
2 события → переключение на `stdout` → `notify --hot --dry-run` видит **0**.

**H-4. Telegram просит подождать — сообщение шлётся заново целиком.** На 429
Bot API отвечает `parameters.retry_after`. Адаптер его не читает: отказ на
третьей части из пяти — `NotifyError`, окно не сдвинуто, и повтор шлёт все
пять, две из них во второй раз. Боевой дайджест M3 — 41 часть.
*Воспроизведено:* 5 частей, 429 на третьей → «доставлено 2 из 5 сообщений —
при повторе они придут ещё раз»; повтор → **7 доставок на 5 частей**.

**H-5. Сообщение ушло, а журнал не записался — трейсбек и повтор.**
`record_notification` после успешной отправки не обёрнут: `sqlite3.Error`
(диск полон, база заперта) даёт трейсбек, отчёт до человека не доходит, окно
не сдвинуто — следующий запуск шлёт то же самое.
*Воспроизведено:* `OperationalError: database or disk is full` из
`record_notification` → трейсбек из `run_notify`; следующий `--dry-run` — те же
2 события.

### Средние

**M-1. `notify.digest.include_retired` не читает никто.** Ключ стоит в обоих
конфигах с комментарием «тихий раздел „отпало“», тест конфига проверяет, что он
объявлен, — а код его не читает. `include_retired: false` → раздел «отпало»
на месте. *Воспроизведено.*

**M-2. `match.thresholds.hot: null` превращает «Звони сейчас» в дайджест.**
`null` у порога — «выключено»: подбор при нём не считает ни одного горячего.
`run_notify` передаёт `min_score=None`, а `collect_events` подставляет вместо
него порог **дайджеста**. «Звони сейчас» уходит с вариантами на 41 балл.
*Воспроизведено:* `hot: null` → 3 события, среди них «41 балл».

**M-3. Секция `notify` не проверяется на входе.** Правило плана QA после M2:
бессмысленное значение в конфиге отклоняется кодом 2. Здесь:
`enabled: "false"` (в кавычках) — **включено** (`bool("false")`);
`fallback_hours: -48` — окно из будущего («беру последние -48 ч (с 24.09…)»,
0 событий, строка в журнал); `fallback_hours: "abc"` — трейсбек `ValueError`.
Тот же трейсбек у любого `positive`/`score_threshold` со строкой вместо числа.
*Воспроизведено.*

**M-4. `matches --new` на старой схеме — трейсбек, без файла — пустая база
на диске.** Окно читается из журнала **до** проверки схемы:
`OperationalError: no such table: notifications`. Без локального файла
`connect()` заводит пустую базу на 4 096 байт, и следующая `matches` уже не
скачивает копию из хранилища, а отвечает «схема базы 0… накати миграции:
python -m listam recheck» — совет, который не поможет: базы нет вовсе.
Приёмка QA после M2 нашла на машине ровно такой файл: «`data/listam.sqlite` —
4 КБ и ни одной таблицы». *Воспроизведено.*

**M-5. «Событий: N» и `notifications.events` считают закрытия.** Открытый
пункт 3 приёмки M3. `notify --hot` из B-1: «Событий: 2, заявок: 1» — событий
для звонка там ноль. *Воспроизведено.*

**M-6. Пустой токен Telegram останавливает все команды прода.** `prod.yaml`
ссылается на `${TELEGRAM_BOT_TOKEN}`, а подстановка переменных отказывает при
загрузке конфига — для любой команды. Канал уведомлений — не ключ обхода, но
без него не стартуют ни `scrape`, ни `changes`, ни `match`.
*Воспроизведено:* `TELEGRAM_BOT_TOKEN= … python -m listam --env prod changes`
→ «Конфигурация не загрузилась: Переменная окружения TELEGRAM_BOT_TOKEN не
задана».

### Низкие

- **L-1.** Раздел заявки длиннее 4 096 символов режется по строкам, и вторая
  часть уходит без шапки: брокер пересылает клиенту кусок без имени заявки.
  Шапка «Что нового» при этом едет **отдельным сообщением**, если не влезла
  к первому разделу целиком. *Воспроизведено:* 40 строк по ~158 символов →
  3 части с первыми строками `Что нового`, `Заявка R-7 (Давид) — н…`,
  `  • 025 xxxx…`.

### Долг и рефакторинг

- **R-1.** Ветка `needs_schema` в `runner.working_session` мертва: `migrate()`
  стоит перед проверкой, и проверка не срабатывает никогда (фаза 6 M3 это
  записала, адрес — «M4»). Две команды передают `needs_schema=False`, которое
  ничего не меняет, а docstring обещает отказ, которого нет.
- **R-2.** `collect_events` и `collect_matches` открывают базу сами и каждая
  копирует проверку схемы. `run_notify` под замком держит соединение сессии —
  и открывает второе через `collect_events`. `_matches_new` открывает третье,
  без проверки (отсюда M-4).
- **R-3.** Пометка широкой заявки ставится разбором уже напечатанного текста
  по строкам (`_match_text`). Ошибку с префиксом `R-1`/`R-11` фаза 4 M3 уже
  ловила; способ, на котором она выросла, остался.

---

## Global Constraints (нарушать нельзя)

- Схема базы меняется **только** новой версионированной миграцией в
  `listam/migrations/`. В этом плане миграция одна — **011** (фаза 6). Больше
  никаких: понадобилась вторая — это находка в отчёт фазы, а не файл `012`.
- Новый метод порта или новый аргумент метода порта — это новый контрактный
  тест в `tests/contracts/`.
- Ни один путь, ключ, идентификатор и имя адаптера не зашит в код: внешнее — за
  портом, выбор — в конфиге, секреты — только в `.env`, подключение —
  в `listam/wiring.py`.
- Все отметки времени в UTC (`to_iso`/`from_iso` из
  `listam/adapters/db_sqlite.py`), пути относительные от корня проекта.
- **Пороги в конфиге не поднимаются, чтобы тест позеленел.** `hot` остаётся 70,
  `digest` — 40, `wide_request` — 15, `per_request` — 5 и 10.
- Ноль в пороге значит «ноль», а не «выключено»; выключается `null`. Читать
  пороги — только через `listam.config` (`threshold`, `positive`,
  `score_threshold`, а с фазы 5 ещё `switch` и `hours`).
- Бессмысленное значение — и в командной строке, и в конфиге — отклоняется
  на входе кодом возврата 2, а не истолковывается.
- След звонка (`matches.status`, `matches.reject_reason`) пишет только человек.
  Ни одна правка этого плана его не трогает.
- **`MATCH_COMPARED` не расширяется.** «Подешевело» чинится выборкой (фаза 3),
  а не новым полем в сравнении: сравнение `unchanged`/`updated` держит всю
  механику «нового».
- Решения 1–12 спеки M3 и 1–11 спеки M2 не пересматриваются. Уточнения — только
  там, где их называет заголовок фазы (фазы 2 и 3 уточняют модель события
  спеки M3, фаза 6 — решение 2).
- Тесты — только `.venv/Scripts/python.exe -m pytest -q`. Системный python
  не годится: в нём нет `openpyxl`.
- Любой прогон CLI из скрипта — только с `PYTHONIOENCODING=utf-8`.
- `listam/crawler.py` правится **только в фазе 4 и только** в мерке свежести
  копии (`_latest_run_at` → `_latest_write_at`). Порядок прогона не трогается.
- **Живой Telegram — только в фазе 8, одним явным шагом.** Батарея в чат не
  шлёт ничего: контракт `telegram` остаётся под `skipif`, живые тесты — под
  `TELEGRAM_LIVE=1`. Каждое живое сообщение приходит брокеру в личку.
- **Ни одного числа в отчёте фазы без команды, которая его напечатала.**
- Телефоны продавцов — M4, дашборд — M5. Не трогаем.
- Числа «ожидается N passed» — арифметика от 778 плюс тесты фазы. Разошлось
  на один-два — не повод подгонять: сверь, что именно добавилось, и поправь
  число в плане.

## Карта файлов

| Файл | Ответственность | Фаза |
| --- | --- | --- |
| `listam/matches_view.py` | закрытия по заказу, `EventsPage.calls`, `open_for_reading`, пометка широкой заявки | 1, 5, 7 |
| `listam/notifications.py` | счёт событий без закрытий, журнал после отправки, `tuning_for`, канал, порог `null` | 1, 4, 5, 6, 7 |
| `listam/domain/events.py` | двойник дешевле — «подешевел», подешевение по истории цен | 2, 3 |
| `listam/matching.py` | причина «не представитель кластера» — одна константа | 2 |
| `listam/adapters/db_sqlite.py` | выборка событий видит историю цен; канал в журнале | 3, 6 |
| `listam/ports/database.py` | docstring `match_events_since`; `last_notification(kind, channel)` | 3, 6 |
| `listam/crawler.py` | мерка свежести копии — последняя запись, а не последний прогон | 4 |
| `listam/config.py` | `switch`, `hours`, `_number`; необязательный секрет `${VAR:-}` | 5 |
| `listam/doctor.py` | секция `notify` проверяется, как `match` | 5 |
| `listam/cli.py` | `matches --new` через `open_for_reading` и канал | 1, 5, 6 |
| `listam/migrations/011_notification_channel.sql` | схема 10 → 11: канал отправки | 6 |
| `listam/domain/models.py` | `Notification.channel` | 6 |
| `listam/wiring.py` | `notify_channel` | 6 |
| `listam/adapters/notify_telegram.py` | 429 и `retry_after`; шапка на каждой части раздела | 6, 7 |
| `listam/runner.py` | без мёртвой ветки `needs_schema` | 7 |
| `config/prod.yaml` | ключи Telegram — необязательные | 5 |
| `tests/test_events_flow.py` | события на настоящем `run_match` | 2, 3 |
| `tests/test_fresher_copy.py` | какая копия свежее | 4 |
| `README.md` | итог плана | 8 |

---

## Стартовый промпт для фазы 1

```
Ты продолжаешь работу над инструментом мониторинга list.am
в C:\Users\Admin\Downloads\list.

Прочитай docs/superpowers/plans/2026-09-23-qa-hardening-after-m3.md:
разделы «Что нашёл аудит», «Global Constraints», «Карта файлов» и свою
«Фазу 1». Чужие фазы не трогай. Рядом лежат спеки M2 и M3 — их решения
в силе: docs/superpowers/specs/2026-09-22-m3-notifications-design.md
(решения 1–12), docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md
(решения 1–11).

Исходное состояние: HEAD 3a2ff7f, дерево чистое, батарея 778 passed,
25 skipped, схема базы 10.

Твоя задача — фаза 1: закрытие — не звонок.
«Звони сейчас» сегодня приносит раздел «отпало» (B-1), хотя решение 1 спеки
M3 запрещает это прямо; тумблер notify.digest.include_retired не читает никто
(M-1); а счётчик «Событий: N» и колонка notifications.events считают закрытия
вместе с вариантами для звонка (M-5). Это одно целое: закрытие перестаёт
выдавать себя за событие везде, где его считают или показывают.

Работай по шагам задач: на каждое поведение — падающий тест ДО правки.
Тесты гоняй только .venv/Scripts/python.exe -m pytest -q, любой прогон CLI
из скрипта — только с PYTHONIOENCODING=utf-8. Пороги в конфиге не поднимай.
Новый метод порта — это новый контрактный тест в tests/contracts/.
Схему меняет только миграция 011 (фаза 6); своих не заводи.
След звонка (matches.status, matches.reject_reason) не трогает ничто,
MATCH_COMPARED не расширяется. В Telegram не шли ничего.
Ни одного числа в отчёте без команды, которая его напечатала.

В конце сессии допиши в план раздел «Результат фазы 1»: что сделано, числа
батареи, что разошлось с планом и почему, и стартовый промпт для фазы 2.
Сделай коммит.
```

---

# Фаза 1. Закрытие — не звонок

**Одна сессия.** Закрывает **B-1**, **M-1**, **M-5**. Закрытие остаётся тихим
разделом дайджеста (решение 1 спеки M3), но перестаёт появляться в «горячем»,
слушается тумблера `include_retired` и не входит в счёт событий.

**Ожидается после фазы:** ~782 passed, 25 skipped, схема базы 10.

### Задача 1.1. Витрина событий отдаёт закрытия по заказу

**Файлы:**
- Изменить: `listam/matches_view.py` (`EventsPage`, `collect_events`)
- Тест: `tests/test_notifications.py`

**Interfaces — Produces:**
```python
# listam/matches_view.py
EventsPage.calls(self) -> list[MatchEvent]        # всё, кроме закрытий
collect_events(config, *, since, until, external_id=None, min_score=None,
               note="", include_retired: bool = True) -> EventsPage
```

- [x] **Шаг 1: падающие тесты**

В `tests/test_notifications.py` — хелпер и два теста в конец файла:

```python
def retire_everything(config) -> None:
    """Оба матча фикстуры закрываются подбором: «бюджет»."""
    database = build_database(config)
    database.connect()
    try:
        request = database.get_request("R-1")
        database.retire_matches(request.id, keep=set(),
                                now=datetime.now(timezone.utc),
                                reasons={"0": "бюджет", "1": "бюджет"},
                                default="бюджет")
    finally:
        database.close()


def test_the_hot_message_never_carries_closures(prepared):
    """Решение 1 спеки M3: немедленным уведомлением закрытие не шлётся никогда.
    До фазы 1 QA «Звони сейчас» приносил раздел «только закрытия / отпало 2»."""
    run_notify(prepared, kind="hot")             # оба варианта ушли брокеру
    retire_everything(prepared)

    report = run_notify(prepared, kind="hot", dry_run=True)

    assert "отпало" not in report.text
    assert "только закрытия" not in report.text
    assert "событий нет" in report.text


def test_the_digest_leaves_closures_out_when_told_so(prepared):
    """`include_retired: false` стоял в конфиге и не читался ничем."""
    retire_everything(prepared)
    prepared.data["notify"]["digest"]["include_retired"] = False

    report = run_notify(prepared, kind="digest", dry_run=True)

    assert "отпало" not in report.text
```

- [x] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_notifications.py -k "never_carries or leaves_closures"`
Ожидается: FAIL — `assert 'отпало' not in 'Звони сейчас: …отпало 2 (бюджет 2)'`.

- [x] **Шаг 3: `EventsPage.calls` и аргумент `include_retired`**

```python
# listam/matches_view.py — в dataclass EventsPage, после поля note
    def calls(self) -> list:
        """События, по которым звонят: всё, кроме закрытий.

        Закрытие — объяснение пропавшей карточки, а не повод звонить
        (решение 1 спеки M3). Считать его «событием» значило бы писать
        брокеру «Событий: 2» там, где звонить некому.
        """
        return [event for event in self.events if event.kind != RETIRED]
```

В `collect_events` — новый аргумент (последним, после `note`) и фильтр сразу
после `events_for`:

```python
def collect_events(config: Config, *, since, until,
                   external_id: str | None = None,
                   min_score: float | None = None,
                   note: str = "",
                   include_retired: bool = True) -> EventsPage:
```

```python
            found = events_for(rows, since, until, min_score=min_score)
            if not include_retired:
                # Тихий раздел «отпало» просили не показывать — ни в тексте,
                # ни в счёте: заявка, у которой только закрытия, раздела не
                # получает вовсе.
                found = [event for event in found if event.kind != RETIRED]
            if not found:
                continue
```

Docstring `collect_events` дополнить строкой: «`include_retired=False` —
закрытия не отдаются: так их просит «горячее» (никогда) и дайджест с
`notify.digest.include_retired: false`».

- [x] **Шаг 4: `run_notify` просит закрытия только у дайджеста**

```python
# listam/notifications.py — после per_request
def shows_closures(config: Config, kind: str) -> bool:
    """Идёт ли в текст тихий раздел «отпало».

    В «горячее» — никогда (решение 1 спеки M3): закрытие не повод звонить.
    В дайджест — по тумблеру `notify.digest.include_retired`.
    """
    if kind != "digest":
        return False
    return bool(threshold(config, "notify.digest.include_retired", True))
```

В `run_notify` вызов `collect_events` заменить:

```python
                page = collect_events(config, since=since, until=until,
                                      min_score=min_score, note=scope,
                                      include_retired=shows_closures(config, kind))
```

- [x] **Шаг 5: тесты проходят**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_notifications.py tests/test_matches_view.py`
Ожидается: PASS. `test_closures_do_not_make_a_request_wide` обязан остаться
зелёным: дайджест закрытия по-прежнему показывает.

- [x] **Шаг 6: коммит**

```bash
git add listam/matches_view.py listam/notifications.py tests/test_notifications.py
git commit -m "fix(notify): «Звони сейчас» не приносит закрытий, дайджест слушается include_retired"
```

### Задача 1.2. Событий столько, сколько звонков

**Файлы:**
- Изменить: `listam/notifications.py` (`NotifyReport`, `run_notify`),
  `listam/cli.py` (`_matches_new`)
- Тест: `tests/test_notifications.py`

**Interfaces — Consumes:** `EventsPage.calls()`, `shows_closures(config, kind)`
из задачи 1.1. **Produces:** `NotifyReport.retired: int`.

- [x] **Шаг 1: падающие тесты**

```python
def test_closures_are_not_counted_as_events(prepared):
    """«Событий: 67» на приёмке M3 было 13 новых и 54 закрытия. Счётчик
    отвечает на вопрос «сколько звонков», и закрытия в него не входят —
    ни в отчёте, ни в журнале."""
    retire_everything(prepared)

    report = run_notify(prepared, kind="digest")

    assert report.events == 0
    assert report.retired == 2
    assert report.requests == 0
    assert "отпало 2" in report.render()
    database = build_database(prepared)
    database.connect()
    assert database.last_notification("digest").events == 0
    database.close()


def test_the_slice_follows_the_digest_about_closures(prepared, capsys):
    """`matches --new` — это текст дайджеста в терминале (решение 10): раз
    дайджест закрытий не показывает, не показывает и срез."""
    from listam.cli import _matches_new

    retire_everything(prepared)
    prepared.data["notify"]["digest"]["include_retired"] = False

    assert _matches_new(prepared, None, None, None, None) == 0
    assert "отпало" not in capsys.readouterr().out
```

- [x] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_notifications.py -k "not_counted or follows_the_digest"`
Ожидается: FAIL — `AttributeError: 'NotifyReport' object has no attribute 'retired'`
и `assert 'отпало' not in …`.

- [x] **Шаг 3: отчёт знает про закрытия отдельно**

```python
# listam/notifications.py — в NotifyReport, после events
    retired: int = 0            # закрытий в тексте: они не события и не звонки
```

В `NotifyReport.render` — строку итога заменить:

```python
        closed = f" (и отпало {self.retired})" if self.retired else ""
        lines.append(
            f"Событий: {self.events}{closed}, заявок: {self.requests}, "
            + ("отправлено" if self.sent else
               "не отправлено (пробный прогон)" if self.dry_run else "не отправлено")
        )
```

В `run_notify`, ветка по заявкам:

```python
                calls = page.calls()
                report.events = len(calls)
                report.retired = len(page.events) - len(calls)
                # Заявка, у которой только закрытия, звонка не требует и
                # в счёт заявок не входит.
                report.requests = len({event.match.request_id for event in calls})
                report.text = _match_text(page, config, kind)
```

- [x] **Шаг 4: срез в терминале просит закрытия так же, как дайджест**

```python
# listam/cli.py — в _matches_new, импорт
    from listam.notifications import shows_closures, window_for
```

```python
        page = collect_events(config, since=since, until=until,
                              external_id=external_id, min_score=min_score, note=note,
                              include_retired=shows_closures(config, "digest"))
```

- [x] **Шаг 5: батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: 782 passed, 25 skipped. Если упал тест, который ждал старого
`report.requests` на заявке из одних закрытий, — это ожидаемое следствие:
поправь число и запиши в отчёт.

- [x] **Шаг 6: коммит**

```bash
git add listam/notifications.py listam/cli.py tests/test_notifications.py
git commit -m "fix(notify): «Событий: N» считает звонки, а не закрытия"
```

### Конец фазы 1

- [x] Замер на копии базы приёмки: `shutil.copy('data/listam-m3.sqlite', <scratchpad>/qa1.sqlite)`,
      конфиг-копия с `storage.db_filename` на неё и `notify.kind: stdout`;
      `notify --hot --dry-run` и `notify --digest --dry-run` — запиши
      «Событий: N (и отпало M)» обоих и есть ли в тексте `hot` слово «отпало».
- [x] Дописать раздел «Результат фазы 1»: что сделано, числа батареи, числа
      замера (с командой), что разошлось с планом и почему.
- [x] Дописать стартовый промпт для фазы 2 по шаблону в конце плана.
- [x] `git status --short` — чисто; коммит сделан.

## Результат фазы 1

**Сделано.** Два коммита по задачам плана, код — ровно по шагам:

- `fe8519e` — задача 1.1. `EventsPage.calls()` (всё, кроме закрытий);
  `collect_events(..., include_retired=True)` отбрасывает закрытия, и заявка
  из одних закрытий раздела не получает; `shows_closures(config, kind)`
  в `listam/notifications.py`: «горячее» — никогда, дайджест — по
  `notify.digest.include_retired`. `run_notify` передаёт его в выборку.
- `01de03f` — задача 1.2. `NotifyReport.retired`; `events` и
  `notifications.events` — это `len(page.calls())`, `requests` — заявки,
  у которых есть хоть один звонок; итог пишется «Событий: N (и отпало M)».
  `matches --new` просит закрытия так же, как дайджест
  (`shows_closures(config, "digest")`).
- Тесты — четыре, все в `tests/test_notifications.py`, каждый падал до правки:
  `test_the_hot_message_never_carries_closures`,
  `test_the_digest_leaves_closures_out_when_told_so`,
  `test_closures_are_not_counted_as_events`,
  `test_the_slice_follows_the_digest_about_closures`. Нового метода порта
  нет — контрактных тестов не прибавилось. Схема не менялась, `matches.status`
  и `reject_reason` не тронуты, в Telegram не ушло ничего (`notify.kind: stdout`
  во всех замерах, все прогоны — `--dry-run`).

**Батарея.** `.venv/Scripts/python.exe -m pytest -q` после `01de03f`:
**782 passed, 25 skipped** (86,01 с) — ровно 778 + 4, как ждал план.
Схема базы — 10.

**Замер на копии базы приёмки.** Копия `data/listam-m3.sqlite` → scratchpad
`qa1work/qa1.sqlite`; конфиг — `config/dev.yaml` с `env: qa1`, `storage` на
scratchpad, `db_filename: qa1.sqlite`, `rate.kind: fixed`, `notify.kind: stdout`.
Команда (из корня, с `PYTHONIOENCODING=utf-8`):
`.venv/Scripts/python.exe -m listam --env qa1 --config-dir <scratchpad>/qa1cfg notify --hot --dry-run`
(и `--digest`). Для сравнения тот же прогон на `3a2ff7f` — через
`git worktree` в scratchpad и `PYTHONPATH` на него (worktree потом удалён).

| Прогон | До фазы (`3a2ff7f`) | После фазы |
| --- | --- | --- |
| `notify --hot --dry-run`, окно «с прошлой отправки (22.09 20:58 UTC)» | `Событий: 65, заявок: 25`; строк «отпало» 20, разделов «только закрытия» 11 | `Событий: 20, заявок: 14`; «отпало» — 0 |
| `notify --digest --dry-run`, окно «с прошлой отправки (22.09 21:05 UTC)» | — | `Событий: 0, заявок: 0`, «событий нет» |
| `notify --digest --dry-run`, 48 ч (копия без строк дайджеста в журнале) | `Событий: 52233, заявок: 50`; строк «отпало» 20 | `Событий: 52188 (и отпало 45), заявок: 50`; строк «отпало» 20 |
| то же с `include_retired: false` | — | `Событий: 52188, заявок: 50`; «отпало» — 0 |

Закрытий в «горячем» окне было 45 (сумма чисел в строках «отпало» вывода
`3a2ff7f`), из них 44 — «не представитель кластера» и 1 — «бюджет»: почти всё,
что «горячее» до фазы приносило вместо звонков, — это B-2, предмет фазы 2.

**Что разошлось с планом.**

- Шаг 2 задачи 1.2: план ждал `AttributeError: … no attribute 'retired'`, а
  `test_closures_are_not_counted_as_events` упал раньше — на `assert 2 == 0`
  (`report.events`): проверка счётчика стоит в тесте первой. Причина падения
  та же — закрытия в счёте; тест не менялся.
- Замер дайджеста по плану («с прошлой отправки») ничего не показал: на копии
  дайджест уже ушёл в 21:05 UTC, окно пустое. Чтобы увидеть «(и отпало M)»
  и тумблер, сделана вторая копия (`qa1bwork/qa1.sqlite`) с удалёнными
  11 строками `kind='digest'` из `notifications` и `digest.fallback_hours: 48`.
  Оригинал `data/listam-m3.sqlite` не тронут.
- Окно в 48 ч на этой базе захватывает первое заполнение матчей, отсюда 52 188
  «событий». Это не находка фазы 1: так же считал и `3a2ff7f`, разница — ровно
  45 закрытий.

## Стартовый промпт для фазы 2

```
Ты продолжаешь работу над инструментом мониторинга list.am
в C:\Users\Admin\Downloads\list.

Прочитай docs/superpowers/plans/2026-09-23-qa-hardening-after-m3.md:
разделы «Что нашёл аудит», «Global Constraints», «Карта файлов»,
«Результат фазы 1» и свою «Фазу 2». Чужие фазы не трогай. Рядом
лежат спеки M2 и M3 — их решения в силе:
docs/superpowers/specs/2026-09-22-m3-notifications-design.md (решения 1–12),
docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md
(решения 1–11). Если фаза одно из них уточняет, это прямо написано
в её заголовке.

Исходное состояние: HEAD — коммит «docs: результат фазы 1 QA после M3»
(следующий за 01de03f), дерево чистое, батарея 782 passed, 25 skipped,
схема базы 10. База для замеров — копия data/listam-m3.sqlite в scratchpad,
не оригинал.

Твоя задача — фаза 2: та же квартира — не новый вариант (B-2).
Нашлась карточка той же квартиры дешевле — подбор пишет новый матч и
закрывает прежний с причиной «не представитель кластера», а классификатор
зовёт это «новый». Фаза уточняет модель события спеки M3: новый матч,
чей кластер в этом же окне потерял представителя, — «подешевел» с ценой
прежней карточки, при той же цене — не событие. Причина закрытия становится
одной константой на подбор и классификатор. На копии базы приёмки таких
закрытий в «горячем» окне было 44 из 45 (замер фазы 1).

Работай по шагам задач: на каждое поведение — падающий тест ДО правки.
Тесты гоняй только .venv/Scripts/python.exe -m pytest -q, любой прогон CLI
из скрипта — только с PYTHONIOENCODING=utf-8. Пороги в конфиге не поднимай.
Новый метод порта — это новый контрактный тест в tests/contracts/.
Схему меняет только миграция 011 (фаза 6); своих не заводи.
След звонка (matches.status, matches.reject_reason) не трогает ничто,
MATCH_COMPARED не расширяется. В Telegram — только фаза 8, одним шагом.
Ни одного числа в отчёте без команды, которая его напечатала.

В конце сессии допиши в план раздел «Результат фазы 2»: что сделано, числа
батареи, что разошлось с планом и почему, и стартовый промпт для фазы 3.
Сделай коммит.
```

---

# Фаза 2. Та же квартира — не новый вариант

**Одна сессия.** Закрывает **B-2**. **Уточняет модель события спеки M3:**
новый матч, чей `cluster_id` в этом же окне потерял прежнего представителя
(закрытие с причиной «не представитель кластера»), — это не `new`, а `cheaper`
с ценой прежней карточки; при той же цене — не событие вовсе. Решение 1
(«событие — новый, подешевевший, вернувшийся») этим не отменяется: квартира,
которую брокер уже видел, новой не становится оттого, что сменилась карточка.

**Ожидается после фазы:** ~787 passed, 25 skipped, схема базы 10.

### Задача 2.1. Домен склеивает двойников

**Файлы:**
- Изменить: `listam/domain/events.py`, `listam/matching.py` (одна строка)
- Тест: `tests/test_events.py`

**Interfaces — Produces:**
```python
# listam/domain/events.py
NOT_REPRESENTATIVE = "не представитель кластера"
events_for(rows, since, until, min_score) -> list[MatchEvent]   # подпись та же
```

- [x] **Шаг 1: падающие тесты**

```python
# tests/test_events.py — импорт дополнить NOT_REPRESENTATIVE, в конец файла
def card(listing_id: str, price: float) -> Listing:
    return Listing(id=listing_id, url=f"https://www.list.am/ru/item/{listing_id}",
                   district="Кентрон", price_usd=price, area=68.0, rooms=2)


def twins(old_born=BEFORE, reason=NOT_REPRESENTATIVE, new_price=145000.0):
    """Одна квартира, две карточки: прежняя уступила место новой в этом окне."""
    old = match(listing_id="old", cluster_id="c1", first_matched_at=old_born,
                matched_at=old_born, retired_at=INSIDE, retired_reason=reason)
    new = match(listing_id="new", cluster_id="c1")
    return [(old, card("old", 150000.0), None), (new, card("new", new_price), None)]


def test_a_cheaper_twin_is_a_cheaper_flat_and_not_a_new_one():
    """Брокер эту квартиру уже видел. Новое в ней одно — цена."""
    events = events_for(twins(), SINCE, UNTIL, min_score=None)

    assert [(event.kind, event.match.listing_id, event.price_before)
            for event in events] == [(CHEAPER, "new", 150000.0)]


def test_a_twin_at_the_same_price_is_not_an_event_at_all():
    """Та же квартира по той же цене под другой карточкой: звонить не о чем."""
    assert events_for(twins(new_price=150000.0), SINCE, UNTIL, min_score=None) == []


def test_a_twin_of_a_card_nobody_saw_is_new():
    """Прежняя карточка родилась и уступила место в одном окне — брокер её
    не видел, и квартира для него новая."""
    events = events_for(twins(old_born=INSIDE), SINCE, UNTIL, min_score=None)

    assert [(event.kind, event.match.listing_id) for event in events] == [(NEW, "new")]


def test_a_closure_for_another_reason_is_not_merged():
    """«Бюджет» — это другая история: вариант отпал, а не сменил карточку."""
    events = events_for(twins(reason="бюджет"), SINCE, UNTIL, min_score=None)

    assert sorted(event.kind for event in events) == sorted([NEW, RETIRED])
```

- [x] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_events.py`
Ожидается: FAIL — `ImportError: cannot import name 'NOT_REPRESENTATIVE'`.

- [x] **Шаг 3: константа и склейка**

```python
# listam/domain/events.py — после EVENT_LABELS
# Причина закрытия, которую пишет подбор, когда у кластера сменился
# представитель. Одна строка на подбор и на классификатор: по ней домен
# узнаёт, что квартира не ушла, а сменила карточку.
NOT_REPRESENTATIVE = "не представитель кластера"
```

`events_for` заменить целиком и добавить `_merge_twins`:

```python
def events_for(rows, since: datetime, until: datetime,
               min_score: float | None) -> list[MatchEvent]:
    """События окна, от лучшего к худшему.

    Двойники склеиваются **до** порога: закрытие прежней карточки и рождение
    новой — одно событие, и решать, проходит ли оно порог, надо по нему, а не
    по половинкам.

    `min_score` не трогает закрытия: закрытие объясняет пропавшую карточку,
    а балл у закрытого матча — вчерашний, и порог о нём ничего не знает.
    """
    classified: list[MatchEvent] = []
    for match, listing, price_before in rows:
        event = classify(match, listing, price_before, since, until)
        if event is not None:
            classified.append(event)

    events: list[MatchEvent] = []
    for event in _merge_twins(classified, since, until):
        if event.kind != RETIRED and min_score is not None \
                and (event.match.score or 0) < min_score:
            continue
        events.append(event)
    events.sort(key=lambda event: (-(event.match.score or 0), event.match.listing_id))
    return events


def _merge_twins(events: list[MatchEvent], since: datetime,
                 until: datetime) -> list[MatchEvent]:
    """Смена представителя кластера — не новая квартира.

    Подбор кладёт в `matches` самую дешёвую карточку кластера. Пришла карточка
    дешевле — у той же квартиры в одном окне два следа: новый матч на новую
    карточку и закрытие прежней с причиной «не представитель кластера».
    Назвать это «новый» — значит позвать брокера звонить по квартире, о которой
    он уже знает; настоящее событие здесь — «подешевела».
    """
    stepped_aside = {
        (event.match.request_id, event.match.cluster_id): event
        for event in events
        if event.kind == RETIRED and event.match.cluster_id
        and event.match.retired_reason == NOT_REPRESENTATIVE
    }
    if not stepped_aside:
        return events

    merged: list[MatchEvent] = []
    absorbed: set[int] = set()
    for event in events:
        partner = stepped_aside.get((event.match.request_id, event.match.cluster_id))
        if event.kind != NEW or partner is None:
            merged.append(event)
            continue
        absorbed.add(id(partner))
        before, now = partner.listing.price_usd, event.listing.price_usd
        if _inside(partner.match.first_matched_at, since, until) \
                or before is None or now is None:
            # Прежнюю карточку брокер не видел (родилась и уступила место
            # в одном окне) или цену не с чем сравнить — квартира для него новая.
            merged.append(event)
        elif now < before:
            merged.append(MatchEvent(kind=CHEAPER, match=event.match,
                                     listing=event.listing, price_before=before))
        # Та же цена — та же квартира по другой карточке: звонить не о чем.
    return [event for event in merged if id(event) not in absorbed]
```

- [x] **Шаг 4: подбор пишет причину той же константой**

```python
# listam/matching.py — импорт
from listam.domain.events import NOT_REPRESENTATIVE
```

```python
        reasons: dict[str, str] = dict.fromkeys(not_representatives,
                                                NOT_REPRESENTATIVE)
```

- [x] **Шаг 5: тесты проходят**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_events.py tests/test_matching.py`
Ожидается: PASS.

- [x] **Шаг 6: коммит**

```bash
git add listam/domain/events.py listam/matching.py tests/test_events.py
git commit -m "fix(events): карточка дешевле той же квартиры — «подешевел», а не «новый»"
```

### Задача 2.2. То же на настоящем подборе

**Файлы:**
- Создать: `tests/test_events_flow.py`

- [x] **Шаг 1: тест**

```python
"""События на настоящем подборе: что брокер увидит после `match`.

Модульные тесты `test_events.py` проверяют правила на готовых матчах; здесь
матчи пишет сам `run_match` — ровно так, как их потом прочитает уведомление.
Аудит QA после M3 нашёл три находки именно на этом стыке: правила были
верны, а подбор давал им не то, чего они ждали.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from listam.matches_view import collect_events, render_events
from listam.matching import run_match
from listam.wiring import build_database
from tests.contracts.test_database_contract import make_listing, make_request
from tests.test_matching import cfg, fill, suitable


def now() -> datetime:
    return datetime.now(timezone.utc)


def events_after(config, since, until=None):
    page = collect_events(config, since=since, until=until or now())
    return page, [(event.kind, event.match.listing_id) for event in page.events]


SAME_FLAT = dict(district="Кентрон", street="ул. Туманяна", rooms=3, floor=4,
                 floors_total=9, seller_type="agency")


def test_a_cheaper_card_of_the_same_flat_comes_as_cheaper(tmp_path):
    """B-2 аудита: `twin` дешевле `old` на $2 000, тот же дом и этаж — до
    фазы 2 брокер получал «новый: 1» и «отпало 1 (не представитель кластера)»."""
    config = cfg(tmp_path)
    fill(config,
         listings=[make_listing("old", area=85.0, price_usd=119_000.0, **SAME_FLAT)],
         requests=[make_request("R-1")])
    run_match(config)
    mark = now()
    database = build_database(config)
    database.connect()
    database.upsert_listing(
        make_listing("twin", area=85.5, price_usd=117_000.0, **SAME_FLAT), seen_at=now())
    database.close()

    run_match(config)

    page, found = events_after(config, mark)
    assert found == [("cheaper", "twin")]
    text = render_events(page, per_request=10)
    assert "подешевело с $119,000" in text
    assert "новый" not in text
    assert "отпало" not in text
```

(`replace` и `suitable` понадобятся фазе 3 — импорт ставится сразу, чтобы
фаза 3 файл только дописывала.)

- [x] **Шаг 2: тест проходит** (правка уже в задаче 2.1)

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_events_flow.py`
Ожидается: PASS. Проверь, что он **падал бы** без задачи 2.1:
`git stash` правок `listam/domain/events.py` → тест FAIL
(`[('retired', 'old'), ('new', 'twin')]`) → `git stash pop`. Запиши это в отчёт.

- [x] **Шаг 3: батарея и коммит**

Запуск: `.venv/Scripts/python.exe -m pytest -q` → 787 passed, 25 skipped.

```bash
git add tests/test_events_flow.py
git commit -m "test(events): двойник дешевле на настоящем подборе"
```

### Конец фазы 2

- [x] Замер на копии базы приёмки (`data/listam-m3.sqlite` → scratchpad): у
      любого сматченного объявления многочленного кластера завести двойника
      дешевле (как в B-2 приёмки QA после M2: соседняя площадь, та же улица и
      этаж, цена −5 000 $), `match --all`, `matches --new --hours 1`. Запиши:
      строку двойника (должна быть «подешевело с …»), есть ли в разделе
      «новый» по этому `cluster_id`, и «Закрыто матчей: N» подбора.
- [x] «Результат фазы 2», стартовый промпт фазы 3, `git status --short` чисто, коммит.

## Результат фазы 2

**Сделано.** Два коммита:

- `eddfe02` — задача 2.1 ровно по шагам. `NOT_REPRESENTATIVE` в
  `listam/domain/events.py` — одна строка на подбор и классификатор
  (`listam/matching.py` пишет причину ею). `events_for` склеивает двойников
  до порога (`_merge_twins`): новый матч, чей `(заявка, cluster_id)` в этом
  же окне потерял представителя с причиной «не представитель кластера», —
  `cheaper` с ценой прежней карточки; при той же цене — не событие; если
  прежнюю карточку брокер не видел (родилась в том же окне) — `new`;
  закрытие по другой причине не склеивается.
- `4b35a0a` — задача 2.2 и **правка сверх плана** (см. «Что разошлось»):
  `tests/test_events_flow.py` с тестом плана на полном проходе и вторым —
  на ежечасном `match --new`; `_write_matches` при частичном проходе
  закрывает матчи тех карточек, что перестали быть представителями
  кластера, — и только их.
- Тесты — шесть, каждый падал до правки (кроме границы про «бюджет», см.
  ниже): четыре в `tests/test_events.py`
  (`test_a_cheaper_twin_is_a_cheaper_flat_and_not_a_new_one`,
  `test_a_twin_at_the_same_price_is_not_an_event_at_all`,
  `test_a_twin_of_a_card_nobody_saw_is_new`,
  `test_a_closure_for_another_reason_is_not_merged`) и два в
  `tests/test_events_flow.py`
  (`test_a_cheaper_card_of_the_same_flat_comes_as_cheaper`,
  `test_a_cheaper_card_found_by_the_hourly_match_comes_as_cheaper`).
  Нового метода порта нет (`retire_matches` тот же, меняется только `keep`),
  контрактных тестов не прибавилось. Схема не менялась, `matches.status` и
  `reject_reason` не тронуты, `MATCH_COMPARED` не расширен, в Telegram не ушло
  ничего (`notify.kind: stdout`, все `notify` — `--dry-run`).

**Батарея.** `.venv/Scripts/python.exe -m pytest -q` после `4b35a0a`:
**788 passed, 25 skipped** (92,75 с) = 782 + 4 + 2. План ждал 787: шестой
тест — ежечасный путь. Схема базы — 10.

**Замер на копии базы приёмки.** Все копии — `data/listam-m3.sqlite` в
scratchpad, оригинал не тронут. Конфиг — `config/dev.yaml` с `env: qa2`,
`storage` на scratchpad, `db_filename: qa2.sqlite`, `requests.kind: none`,
`rate.kind: fixed`, `notify.kind: stdout`. Все прогоны из корня с
`PYTHONIOENCODING=utf-8`:
`.venv/Scripts/python.exe -m listam --env qa2 --config-dir <scratchpad>/qa2<копия>cfg <команда>`.

*Двойник.* Взят горячий матч многочленного кластера: `19416190` (Нор Норк,
Минска, 4 ком., 90 м², эт. 9/9, $120,000, кластер `5f28db624aaf6079`, 3
карточки, матчи у 11 заявок). Двойник `99000001` — 90,5 м², $115,000,
остальное то же — записан `upsert_listing` в две копии.

| Прогон | `match` | `matches --new --hours 1` |
| --- | --- | --- |
| `match --all` (копия `qa2all`) | «Матчи: новых 11, обновлённых 0, без изменений 52359»; «Закрыто матчей: 11» | строк двойника 11, все «подешевело с $120,000 · 4 объявления, разброс $5,000»; строк прежней карточки 0 |
| `match --new` (копия `qa2new`) | «Матчи: новых 11, обновлённых 0, без изменений 370»; «Закрыто матчей: 11» | то же: 11 строк «подешевело с $120,000 …», прежней карточки 0 |

Все 11 закрытий в обеих копиях — «не представитель кластера», у всех 11 пар
один `cluster_id` (выборка по `matches` скриптом в scratchpad). Строк
«новый» по этому кластеру нет.

*«Горячее» окно приёмки без двойника* (копия `qa2base`,
`notify --hot --dry-run`, окно «с прошлой отправки (22.09 20:58 UTC)»;
«до» — тот же прогон на `586042f` из `git worktree` в scratchpad, запуск из
его каталога, worktree потом удалён):

| | До фазы (`586042f`) | После фазы |
| --- | --- | --- |
| Итог | `Событий: 20, заявок: 14` | `Событий: 20, заявок: 14` |
| Шапки заявок, сумма | новый 11, вернулся 9 | подешевел 11, вернулся 9 |

Все 11 «новых» горячего окна были двойниками — теперь это «подешевел».
Разбор окна скриптом (`classify` по `match_events_since`, копия `qa2base`):
закрытий 45, из них «не представитель кластера» 43 и «бюджет» 2; из 43
склеились 14 (все 14 `new` окна, 11 из них — выше порога `hot`); 29 не
склеились — у их кластера живой представитель родился **до** окна (кластеры
слились пересчётом, обе карточки брокер уже видел). Эти 29 остаются тихим
разделом «отпало» дайджеста; в «горячее» закрытия не идут с фазы 1.

**Что разошлось с планом.**

- **План чинил только полный проход, а «Звони сейчас» ходит за
  `match --new`.** Проба на `run_match(only_new=True)` до правки: события
  окна `[('new', 'twin')]`, закрыто 0; следующий `match --all` —
  `[('retired', 'old')]` и одинокое «отпало 1 (не представитель кластера 1)»
  в чужом окне. Частичный проход по правилу M2 не закрывает ничего, чего нет
  в выборке, — но представителя кластера выбирает кластеризация **всей**
  базы, так что «не представитель» известен и ему. `_write_matches` при
  `--new` зовёт `retire_matches` с `keep = представители | снятые с ленты`:
  закрываются только уступившие двойнику. Тест
  `test_matching_only_the_new_ones_closes_nothing` зелёный — в его данных
  двойников нет. Отсюда 788, а не 787, и в `matching.py` не одна строка.
- Шаг 2 задачи 2.2 (`git stash` правки `events.py`): к этому шагу задача 2.1
  уже закоммичена, `stash` снимать нечего. Падение без 2.1 проверено иначе:
  `git checkout 586042f -- listam/domain/events.py listam/matching.py` →
  FAIL `[('retired', 'old'), ('new', 'twin')] == [('cheaper', 'twin')]` →
  `git checkout HEAD -- …`.
- Шаг 2 задачи 2.1: `ImportError`, как ждал план; после одной константы —
  3 failed из 4: тест про «бюджет» зелёный и до склейки — он держит границу.
- Фаза 1 записала «44 из 45 — не представитель, 1 — бюджет» (по строкам
  «отпало» текста). Сырые закрытия того же окна — 43 и 2 (скрипт выше).
  Число фазы 1 не правлю — это её замер; по сырым данным — 43.
- Отчёт подбора пишет «Закрыто матчей: 11 — вариант больше не подходит» и
  для смены представителя. Подпись неточна, но подбор так пишет с M3;
  не тронуто — адрес M4, вместе с вопросом, нужен ли дайджесту раздел
  «отпало» по слившимся кластерам (29 строк выше).

**Для фазы 3.** Склейка делает `cheaper` сама, минуя `classify`, и только из
`new`. Новое правило `cheaper` фазы 3 (по `price_history`, без `matched_at`)
её не касается: двойник родился в окне, `classify` зовёт его `new`. Ожидание
фазы 3 сдвигается на +1: ~792 passed.

## Стартовый промпт для фазы 3

```
Ты продолжаешь работу над инструментом мониторинга list.am
в C:\Users\Admin\Downloads\list.

Прочитай docs/superpowers/plans/2026-09-23-qa-hardening-after-m3.md:
разделы «Что нашёл аудит», «Global Constraints», «Карта файлов»,
«Результат фазы 2» и свою «Фазу 3». Чужие фазы не трогай. Рядом
лежат спеки M2 и M3 — их решения в силе:
docs/superpowers/specs/2026-09-22-m3-notifications-design.md (решения 1–12),
docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md
(решения 1–11). Если фаза одно из них уточняет, это прямо написано
в её заголовке.

Исходное состояние: HEAD — коммит «docs: результат фазы 2 QA после M3»
(следующий за 4b35a0a), дерево чистое, батарея 788 passed, 25 skipped,
схема базы 10. База для замеров — копия data/listam-m3.sqlite в scratchpad,
не оригинал.

Твоя задача — фаза 3: «подешевело» мерится ценой, а не пересчётом (H-1, H-2).
Сегодня cheaper требует, чтобы в окне сдвинулся matched_at, — его двигает
пересчёт, а не рынок. Поэтому подешевевшее теряется, если дайджест встал
между scrape и match, и не приходит вовсе, если скидка не сдвинула балл.
Фаза уточняет модель события спеки M3: cheaper — живой матч, рождённый до
окна, у объявления которого в окне есть точка price_history и цена ниже
цены на начало окна. Выборка match_events_since видит историю цен, условие
«matched_at в окне» снимается. Склейку двойников фазы 2 (_merge_twins) не
ломай: она делает cheaper из new сама.

Работай по шагам задач: на каждое поведение — падающий тест ДО правки.
Тесты гоняй только .venv/Scripts/python.exe -m pytest -q, любой прогон CLI
из скрипта — только с PYTHONIOENCODING=utf-8. Пороги в конфиге не поднимай.
Новый метод порта — это новый контрактный тест в tests/contracts/.
Схему меняет только миграция 011 (фаза 6); своих не заводи.
След звонка (matches.status, matches.reject_reason) не трогает ничто,
MATCH_COMPARED не расширяется. В Telegram — только фаза 8, одним шагом.
Ни одного числа в отчёте без команды, которая его напечатала.

В конце сессии допиши в план раздел «Результат фазы 3»: что сделано, числа
батареи, что разошлось с планом и почему, и стартовый промпт для фазы 4.
Сделай коммит.
```

---

# Фаза 3. «Подешевело» мерится ценой, а не пересчётом

**Одна сессия.** Закрывает **H-1** и **H-2**. **Уточняет модель события спеки
M3:** `cheaper` — это живой матч, рождённый до окна, у объявления которого в
окне есть точка `price_history`, и цена сейчас ниже цены на начало окна.
Условие «`matched_at` в окне» снимается: его двигает пересчёт, а не рынок, и
именно оно теряло подешевевшее в обоих случаях аудита. Спека сама говорит
«подешевело — факт про цену, спросить про него можно только `price_history`» —
фаза доводит эту мысль до выборки.

**Ожидается после фазы:** ~791 passed, 25 skipped, схема базы 10.

### Задача 3.1. Выборка событий видит историю цен

**Файлы:**
- Изменить: `listam/adapters/db_sqlite.py` (`match_events_since`),
  `listam/ports/database.py` (docstring)
- Тест: `tests/contracts/test_database_contract.py`

- [x] **Шаг 1: падающий контрактный тест**

```python
# tests/contracts/test_database_contract.py — после test_match_events_do_not_reach_past_the_window
def test_match_events_see_a_price_drop_the_recount_did_not_notice(db):
    """Цена упала, а балл — нет: глубокая скидка давно упёрлась в потолок
    фактора выгодности. Матч пересчётом не тронут, но у объявления в окне
    есть точка истории цен — и это событие."""
    db.upsert_listing(make_listing(price_usd=60000.0), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_match(Match(request_id=request.id, listing_id="24254997", score=80.0), NOW)
    db.upsert_listing(make_listing(price_usd=50000.0, price_raw="$50,000"), seen_at=LATER)

    rows = db.match_events_since(NOW, EVEN_LATER)

    assert len(rows) == 1
    match, listing, price_before = rows[0]
    assert price_before == 60000.0
    assert listing.price_usd == 50000.0
```

- [x] **Шаг 2: убедиться, что тест падает**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py -k recount_did_not_notice`
Ожидается: FAIL — `assert 0 == 1`.

- [x] **Шаг 3: пятое условие окна**

```python
# listam/adapters/db_sqlite.py — в match_events_since, условие WHERE целиком
            f" WHERE ((m.first_matched_at > :since AND m.first_matched_at <= :until) "
            f"     OR (m.matched_at      > :since AND m.matched_at      <= :until) "
            f"     OR (m.retired_at      > :since AND m.retired_at      <= :until) "
            f"     OR (m.revived_at      > :since AND m.revived_at      <= :until) "
            # Цена двинулась в окне — даже если пересчёт матча не заметил:
            # глубокая скидка не двигает балл, а дайджест, вставший между
            # обходом и подбором, видел бы падение цены раньше пересчёта.
            f"     OR EXISTS (SELECT 1 FROM price_history q "
            f"                 WHERE q.listing_id = m.listing_id "
            f"                   AND q.seen_at > :since AND q.seen_at <= :until))"
```

В docstring порта `match_events_since` абзац «Коснулось — это любая из четырёх
отметок…» заменить:

```python
        Коснулось — это любая из четырёх отметок матча: появился
        (`first_matched_at`), пересчитался (`matched_at`), закрылся
        (`retired_at`), вернулся (`revived_at`), — **или** у объявления в окне
        есть точка `price_history`. Пятое условие не про матч, а про рынок:
        цена падает и тогда, когда балл стоит на месте, и тогда, когда подбор
        ещё не прошёл. Что из этого считать событием, решает домен
        (`listam/domain/events.py`): база отдаёт сырьё, а не приговор.
```

- [x] **Шаг 4: тест проходит, контракт целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py`
Ожидается: PASS, включая `test_match_events_skip_what_did_not_move`.

- [x] **Шаг 5: коммит**

```bash
git add listam/adapters/db_sqlite.py listam/ports/database.py tests/contracts/test_database_contract.py
git commit -m "fix(db): выборка событий видит падение цены, которого не заметил пересчёт"
```

### Задача 3.2. Классификатор не ждёт пересчёта

**Файлы:**
- Изменить: `listam/domain/events.py` (`classify`)
- Тест: `tests/test_events.py`, `tests/test_events_flow.py`

- [x] **Шаг 1: падающие тесты**

```python
# tests/test_events.py — в конец
def test_a_price_drop_is_an_event_even_when_the_recount_did_not_move():
    """Глубокая скидка упирается в потолок фактора выгодности: балл тот же,
    `matched_at` не двигается, а квартира стала дешевле на $10 000."""
    quiet = match(first_matched_at=BEFORE, matched_at=BEFORE)

    event = classify(quiet, listing(50000.0), 60000.0, SINCE, UNTIL)

    assert event.kind == CHEAPER
    assert event.price_before == 60000.0
```

```python
# tests/test_events_flow.py — в конец
def cheaper(config, listing_id: str, price_usd: float, price_per_sqm: float) -> None:
    """Обход увидел новую цену: строка в `listings` и точка в `price_history`."""
    database = build_database(config)
    database.connect()
    try:
        listing = database.get_listing(listing_id)
        database.upsert_listing(
            replace(listing, price_usd=price_usd, price_per_sqm=price_per_sqm,
                    price_raw=f"{int(price_usd)} $"),
            seen_at=now(),
        )
    finally:
        database.close()


def test_a_deep_discount_that_did_not_move_the_score_is_still_an_event(tmp_path):
    """H-2 аудита: 60 000 → 50 000 $ у самой выгодной квартиры района —
    «обновлённых 0», и до фазы 3 брокер об этом не узнавал никогда."""
    config = cfg(tmp_path)
    fill(config,
         listings=[suitable("1", price_usd=60_000.0, price_per_sqm=705.0),
                   suitable("2"), suitable("3", price_usd=112_000.0)],
         requests=[make_request("R-1")])
    run_match(config)
    mark = now()
    cheaper(config, "1", 50_000.0, 588.0)

    report = run_match(config)

    assert report.updated == 0, "балл не сдвинулся — ради этого случая тест и написан"
    _, found = events_after(config, mark)
    assert ("cheaper", "1") in found


def test_a_price_drop_is_not_lost_when_the_digest_runs_before_the_match(tmp_path):
    """H-1 аудита: дайджест встал между `scrape` и `match`. До фазы 3 в его
    окне цена упала, но матч не пересчитан, а в следующем окне матч
    пересчитан, но «цена до окна» уже новая, — и подешевевшее терялось."""
    config = cfg(tmp_path)
    fill(config, listings=[suitable("1"), suitable("2", price_usd=118_000.0)],
         requests=[make_request("R-1")])
    run_match(config)
    previous = now()
    cheaper(config, "2", 100_000.0, 1176.0)
    digest_at = now()

    _, before_match = events_after(config, previous, digest_at)
    run_match(config)
    _, after_match = events_after(config, digest_at)

    both = before_match + after_match
    assert ("cheaper", "2") in both, "подешевевшее потеряно"
    assert both.count(("cheaper", "2")) == 1, "и пришло ровно один раз"
```

- [x] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_events.py tests/test_events_flow.py -k "recount_did_not_move or deep_discount or not_lost"`
Ожидается: FAIL в трёх тестах — `classify` отдаёт `None`.

- [x] **Шаг 3: правило `cheaper` без `matched_at`**

```python
# listam/domain/events.py — в classify, последнее правило целиком
    # Рождение в окне уже вернуло `new` выше. Здесь — матч, рождённый до окна,
    # чья цена сейчас ниже цены на начало окна. `matched_at` не спрашиваем:
    # его двигает пересчёт, а не рынок. Глубокая скидка балл не двигает, а
    # дайджест, вставший между обходом и подбором, видит цену раньше пересчёта.
    if price_before is not None and listing.price_usd is not None \
            and listing.price_usd < price_before:
        return MatchEvent(kind=CHEAPER, match=match, listing=listing,
                          price_before=price_before)
    return None
```

В docstring `classify` фразу «Подешевение — последним, потому что это
единственное правило, которое смотрит не на матч, а на цену карточки»
дополнить: «…и поэтому оно не спрашивает `matched_at`: цену двигает рынок,
а `matched_at` — пересчёт».

- [x] **Шаг 4: тесты проходят, батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: 791 passed, 25 skipped. Особо проверь
`test_a_match_that_only_got_recounted_is_not_an_event` и
`test_a_match_whose_listing_got_dearer_is_not_an_event` — они держат обратную
сторону: пересчёт без падения цены событием не стал.

- [x] **Шаг 5: коммит**

```bash
git add listam/domain/events.py tests/test_events.py tests/test_events_flow.py
git commit -m "fix(events): «подешевело» мерится ценой, а не сдвигом пересчёта"
```

### Конец фазы 3

- [x] Замер цены пятого условия на копии базы приёмки: `matches --new --hours 24`
      три раза до правки (`git stash`) и три раза после, время
      (`Measure-Command` или `time`) и число строк вывода. Выборка на 52 000
      матчей получила подзапрос в `price_history` по индексу
      `idx_price_history_listing` — запиши, во что он обошёлся.
- [x] Там же: `notify --digest --dry-run` — «подешевел: N» в шапках до и после
      правки. Разница — это подешевевшие, которых M3 не показывал.
- [x] «Результат фазы 3», стартовый промпт фазы 4, чисто, коммит.

## Результат фазы 3

**Сделано.** Два коммита, ровно по шагам задач:

- `ff80e40` — задача 3.1. `match_events_since` получил пятое условие окна:
  `EXISTS` точки `price_history` объявления в `(since, until]`. Docstring
  порта — абзац плана слово в слово. Нового метода и нового аргумента порта
  нет; новое поведение метода — новый контрактный тест
  `test_match_events_see_a_price_drop_the_recount_did_not_notice`.
- `d99521f` — задача 3.2. Последнее правило `classify` больше не спрашивает
  `matched_at`: `cheaper` — матч, рождённый до окна (рождение в окне уже
  вернуло `new`), не закрытый, цена карточки ниже цены на начало окна.
  Docstring `classify` дополнен. `_merge_twins` не тронут.
- Тесты — четыре, каждый падал до правки по той причине, что ждал план:
  контрактный — `assert 0 == 1`;
  `test_a_price_drop_is_an_event_even_when_the_recount_did_not_move` —
  `AttributeError: 'NoneType' object has no attribute 'kind'`;
  `test_a_deep_discount_that_did_not_move_the_score_is_still_an_event` и
  `test_a_price_drop_is_not_lost_when_the_digest_runs_before_the_match` —
  `('cheaper', …) in []`. Схема не менялась, `matches.status` и
  `reject_reason` не тронуты, `MATCH_COMPARED` не расширен, пороги те же,
  в Telegram не ушло ничего (`notify.kind: stdout`, `notify` — только
  `--dry-run`).

**Батарея.** `.venv/Scripts/python.exe -m pytest -q` после `d99521f`:
**792 passed, 25 skipped** (92,88 с) = 788 + 1 + 3. Контракт базы отдельно —
116 passed. Схема базы — 10.

**Замер на копии базы приёмки.** Копия `data/listam-m3.sqlite` в scratchpad
(оригинал не тронут), конфиг — `config/dev.yaml` с `env: qa3`, `storage` на
scratchpad, `db_filename: qa3.sqlite`, `requests.kind: none`,
`rate.kind: fixed`, `notify.kind: stdout`. «До» — `git worktree` на `b3b22a5`
в scratchpad, запуск из его каталога (проверено: `listam` грузится оттуда),
worktree потом удалён. Все прогоны — с `PYTHONIOENCODING=utf-8`. Замер шёл
в 22:05–22:10 UTC 22.09; последняя точка `price_history` копии — 20:55:00,
последний `matched_at` — 21:04:54.

*Цена пятого условия.* `matches --new --hours 24`, по три прогона
вперемешку (`time`, вся команда):

| | прогон 1 | прогон 2 | прогон 3 | строк вывода |
| --- | --- | --- | --- | --- |
| До (`b3b22a5`) | 12,275 с | 11,970 с | 12,742 с | 4 814 |
| После (`d99521f`) | 12,381 с | 12,156 с | 11,747 с | 4 814 |

Выводы до и после совпадают байт в байт (`cmp`): окно 24 ч захватывает
рождение всех матчей приёмки, всё в нём — «новый». Сама выборка
(`match_events_since`, лучшее из пяти, скрипт в scratchpad):

| Окно | До: строк / время | После: строк / время |
| --- | --- | --- |
| 21.09 22:05 → 22.09 22:05 | 52 415 / 10,639 с | 52 415 / 10,449 с |
| 22.09 20:08 → 22:08 | 24 478 / 4,872 с | 24 499 / 4,807 с |
| 22.09 20:30:08 → 22:08 | 24 478 / 4,818 с | 24 499 / 5,178 с |

Подзапрос в `price_history` по `idx_price_history_listing` не стоит ничего
измеримого: разброс между прогонами больше разницы. Пятое условие добавило
21 строку — объявления с точкой цены в окне, чьи матчи не сдвинулись.

*Сколько подешевевших M3 не показывал.*

- `matches --new --hours 2` (окно «с 22.09 20:08 UTC», сумма шапок заявок):
  до — новый 274, подешевел 34, вернулся 9; после — новый 274, подешевел
  **35**, вернулся 9. `diff` — одна строка шапки: `R-51 … подешевел: 9` →
  `подешевел: 10` (и хвост «…и ещё 21 из 71» → «22 из 72»). Разбор скриптом
  (`classify` по `match_events_since` того же окна): по новому правилу
  `cheaper` — 2, из них с `matched_at` в окне — 1; лишний — заявка 51,
  объявление `22107841`, балл 61, `first_matched_at` и `matched_at`
  22.09 16:45:46, $385,000 → $350,000. Это H-2 на настоящих данных: скидка
  $35,000, пересчёт её не заметил. Остальные 33 «подешевел» окна — склейка
  двойников фазы 2, она не изменилась.
- `notify --digest --dry-run` на копии как есть: до и после — «Что нового со
  вчера: событий нет (с прошлой отправки (22.09 21:05 UTC))», `Событий: 0`.
  Последний дайджест журнала копии закрыт в 21:05:33 — после всех точек цены
  и всех пересчётов; окно пустое для любого правила.
- Поэтому — настоящие окна дайджеста приёмки через `collect_events`
  (скрипт в scratchpad, порог дайджеста из конфига): окно дайджеста 21:00
  (`window_from` 20:30:08 → `window_to` 20:59:13, обход в нём — 20:53) и всё
  после него до 22:10.

  | Окно | До | После |
  | --- | --- | --- |
  | дайджест 21:00 | new 303, cheaper 1 | new 303, cheaper **2** (+ `22107841` заявки 51) |
  | после него | cheaper 13, retired 31, revived 9 | то же, те же 13 пар |

  `22107841` приходит ровно один раз: во втором окне его нет. 13 `cheaper`
  второго окна — двойники фазы 2, списки пар до и после совпадают.

**Что разошлось с планом.**

- План ждал ~791, фаза 2 — ~792. Вышло 792: +1 фазы 2 (ежечасный путь) и
  четыре теста этой фазы. Ожидание фазы 4 сдвигается на +1: ~796.
- «Три раза до правки (`git stash`)»: к замеру обе задачи закоммичены,
  `stash` снимать нечего. «До» — worktree на `b3b22a5`, как в фазе 2.
- `notify --digest --dry-run` на копии как есть разницы не показывает
  (0 и 0, см. выше) — журнал копии уже закрыл все окна с движением. Разница
  снята на настоящих окнах дайджеста скриптом через `collect_events` — той
  же выборкой, что у уведомления.
- H-1 (дайджест между обходом и подбором) на данных приёмки отдельным
  случаем не нашёлся: единственный `cheaper`, которого не было, — H-2. H-1
  держит тест `test_a_price_drop_is_not_lost_when_the_digest_runs_before_the_match`.
- Первый прогон `matches --new` наткнулся на **M-4**: без файла в `work_dir`
  команда завела там пустую базу на 4 096 байт и ответила «схема базы 0…
  накати миграции». Пустой файл удалён, копия положена и в `work_dir`, и в
  `storage.directory`. Чинит фаза 5; фазе 4 с её двумя `work_dir` — класть
  копию в каждый.
- Попутно: выборка `match_events_since` на всех 52 415 матчах занимает
  ~10,5 с и до фазы (таблица выше) — это почти всё время `matches --new
  --hours 24`. Не тронуто: к фазе 3 не относится, ни одна фаза плана её не
  чинит. Адрес — M4 или отдельная находка.

## Стартовый промпт для фазы 4

```
Ты продолжаешь работу над инструментом мониторинга list.am
в C:\Users\Admin\Downloads\list.

Прочитай docs/superpowers/plans/2026-09-23-qa-hardening-after-m3.md:
разделы «Что нашёл аудит», «Global Constraints», «Карта файлов»,
«Результат фазы 3» и свою «Фазу 4». Чужие фазы не трогай. Рядом
лежат спеки M2 и M3 — их решения в силе:
docs/superpowers/specs/2026-09-22-m3-notifications-design.md (решения 1–12),
docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md
(решения 1–11). Если фаза одно из них уточняет, это прямо написано
в её заголовке.

Исходное состояние: HEAD — коммит «docs: результат фазы 3 QA после M3»
(следующий за d99521f), дерево чистое, батарея 792 passed, 25 skipped,
схема базы 10. База для замеров — копия data/listam-m3.sqlite в scratchpad,
не оригинал; клади её и в work_dir, и в storage.directory — иначе
matches --new заведёт пустую базу (M-4, чинит фаза 5).

Твоя задача — фаза 4: журнал отправок не теряется (B-3, H-5). Копия базы,
в которой записана отправка, свежее той, где её нет: мерка свежести —
последняя запись (прогон или отправка), а не последний прогон, иначе вторая
машина шлёт брокеру всё повторно. А отправка, которую не удалось записать в
журнал, — внятная ошибка словами, а не трейсбек. listam/crawler.py правится
только в мерке свежести (_latest_run_at → _latest_write_at).

Работай по шагам задач: на каждое поведение — падающий тест ДО правки.
Тесты гоняй только .venv/Scripts/python.exe -m pytest -q, любой прогон CLI
из скрипта — только с PYTHONIOENCODING=utf-8. Пороги в конфиге не поднимай.
Новый метод порта — это новый контрактный тест в tests/contracts/.
Схему меняет только миграция 011 (фаза 6); своих не заводи.
След звонка (matches.status, matches.reject_reason) не трогает ничто,
MATCH_COMPARED не расширяется. В Telegram — только фаза 8, одним шагом.
Ни одного числа в отчёте без команды, которая его напечатала.

В конце сессии допиши в план раздел «Результат фазы 4»: что сделано, числа
батареи, что разошлось с планом и почему, и стартовый промпт для фазы 5.
Сделай коммит.
```

---

# Фаза 4. Журнал отправок не теряется

**Одна сессия.** Закрывает **B-3** и **H-5**. Копия базы, в которой записана
отправка, свежее той, в которой её нет; отправка, которую не удалось записать,
— внятная ошибка, а не трейсбек.

**Ожидается после фазы:** ~795 passed, 25 skipped, схема базы 10.

### Задача 4.1. Свежесть копии — последняя запись, а не последний прогон

**Файлы:**
- Изменить: `listam/crawler.py` (`_latest_run_at` → `_latest_write_at`,
  вызов в `take_the_fresher_copy`)
- Создать: `tests/test_fresher_copy.py`

**Interfaces — Produces:** `listam.crawler._latest_write_at(path) -> datetime | None`.
Подпись `take_the_fresher_copy(storage, remote_name, local_db) -> _Remote`
не меняется.

- [ ] **Шаг 1: падающие тесты**

```python
"""Какая копия базы свежее: мерка — последняя запись, а не последний прогон.

Журнал прогонов пишет только `scrape`. Подбор, заявки и уведомления пишут
базу, прогона не открывая: по старой мерке копия с отправленным дайджестом и
копия без него — ровесницы, и побеждала локальная. Вторая машина слала тот же
дайджест ещё раз, а её заливка стирала журнал отправок первой (B-3 аудита
QA после M3).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from listam.adapters.db_sqlite import SqliteDatabase
from listam.adapters.storage_local import LocalStorage
from listam.crawler import take_the_fresher_copy
from listam.domain.models import Match, Notification, Request
from tests.contracts.test_database_contract import make_listing
from tests.test_migrations import upto

RUN = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)


def base(path: Path, run_at: datetime = RUN, migrations_dir=None) -> SqliteDatabase:
    """Копия базы с одним прогоном в журнале. Отдаётся открытой."""
    path.parent.mkdir(parents=True, exist_ok=True)
    database = (SqliteDatabase(path, migrations_dir=migrations_dir)
                if migrations_dir else SqliteDatabase(path))
    database.connect()
    database.migrate()
    database.start_run(run_at, 390.0)
    return database


def fresher(tmp_path: Path):
    return take_the_fresher_copy(LocalStorage(tmp_path / "remote"), "listam.sqlite",
                                 tmp_path / "work" / "listam.sqlite")


def test_a_copy_that_sent_a_digest_is_fresher_than_one_that_did_not(tmp_path):
    remote = base(tmp_path / "remote" / "listam.sqlite")
    remote.record_notification(Notification(
        kind="digest", sent_at=RUN + timedelta(hours=1), window_to=RUN + timedelta(hours=1)))
    remote.close()
    base(tmp_path / "work" / "listam.sqlite").close()

    answer = fresher(tmp_path)

    assert "удалённая копия свежее" in answer.note
    here = SqliteDatabase(tmp_path / "work" / "listam.sqlite")
    here.connect()
    assert here.last_notification("digest") is not None, "отправка второй машины потеряна"
    here.close()


def test_a_copy_with_later_matches_is_not_overwritten_by_a_later_run(tmp_path):
    """Прогон на другой машине был позже, но подбор здесь — ещё позже:
    старая мерка затирала свежие матчи вчерашним обходом."""
    base(tmp_path / "remote" / "listam.sqlite", run_at=RUN + timedelta(hours=1)).close()
    local = base(tmp_path / "work" / "listam.sqlite")
    local.upsert_listing(make_listing("1"), RUN)
    local.upsert_request(Request(external_id="R-1"), RUN)
    request = local.get_request("R-1")
    local.upsert_match(Match(request_id=request.id, listing_id="1", score=80.0),
                       RUN + timedelta(hours=2))
    local.close()

    answer = fresher(tmp_path)

    assert "локальная копия не старее" in answer.note


def test_a_copy_on_an_old_schema_is_still_compared(tmp_path):
    """Таблицы журнала отправок в схеме 9 нет — это не повод считать копию
    нечитаемой: сравниваем по тому, что в ней есть."""
    base(tmp_path / "remote" / "listam.sqlite", run_at=RUN + timedelta(hours=1),
         migrations_dir=upto(tmp_path, 9)).close()
    base(tmp_path / "work" / "listam.sqlite").close()

    answer = fresher(tmp_path)

    assert "удалённая копия свежее" in answer.note
```

- [ ] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_fresher_copy.py`
Ожидается: FAIL в первых двух («локальная копия не старее удалённой» /
«удалённая копия свежее»); третий проходит и на старом коде — он сторожит,
чтобы новая мерка не потеряла старую.

- [ ] **Шаг 3: мерка «последняя запись»**

```python
# listam/crawler.py — вместо _latest_run_at целиком
# Где база помнит, что её писали. Прогоны пишет только `scrape`; подбор,
# заявки и уведомления прогона не открывают — без них копия с отправленным
# дайджестом и копия без него выглядели ровесницами (B-3 QA после M3).
WRITE_MARKS = (
    ("runs", "started_at"),
    ("runs", "finished_at"),
    ("matches", "matched_at"),
    ("matches", "retired_at"),
    ("requests", "updated_at"),
    ("requests", "matched_at"),
    ("notifications", "sent_at"),
)


def _latest_write_at(path) -> datetime | None:
    """Когда эту копию базы последний раз писали. Нечитаемая копия — None.

    Таблицы или колонки, которой в старой схеме нет, просто не участвуют:
    копия на схеме 9 без журнала отправок — не повод считать её нечитаемой.
    """
    if not Path(path).exists():
        return None
    try:
        connection = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    latest: datetime | None = None
    try:
        for table, column in WRITE_MARKS:
            try:
                row = connection.execute(f"SELECT MAX({column}) FROM {table}").fetchone()
            except sqlite3.Error:
                continue
            when = from_iso(row[0]) if row and row[0] else None
            if when is not None and (latest is None or when > latest):
                latest = when
    finally:
        connection.close()
    return latest
```

В `take_the_fresher_copy`:

```python
        there, here = _latest_write_at(candidate), _latest_write_at(local_db)
```

Docstring `take_the_fresher_copy` дополнить: «Свежесть — последняя запись
(`_latest_write_at`): прогон, подбор, заявки, отправка. Две копии, которые
писали обе, не сливаются — побеждает поздняя, и что было только в ранней,
теряется. Слияние журналов двух машин — вне этого плана».

- [ ] **Шаг 4: тесты проходят, батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: 794 passed, 25 skipped. Особо — `tests/test_upload_safety.py`,
`tests/test_concurrency.py` и `tests/test_crawler.py`: они держат старое
обещание «вторая машина не затирает общую базу старым снимком».

- [ ] **Шаг 5: коммит**

```bash
git add listam/crawler.py tests/test_fresher_copy.py
git commit -m "fix(storage): свежесть копии — последняя запись, а не последний прогон"
```

### Задача 4.2. Журнал не записался — ошибка словами

**Файлы:**
- Изменить: `listam/notifications.py` (`run_notify`)
- Тест: `tests/test_notifications.py`

- [ ] **Шаг 1: падающий тест**

```python
def test_a_journal_that_did_not_write_is_an_error_and_not_a_crash(prepared, monkeypatch):
    """Сообщение ушло, а строка журнала — нет: следующий запуск пошлёт то же
    самое. Об этом брокер должен узнать из отчёта, а не из трейсбека."""
    import sqlite3

    from listam.adapters.db_sqlite import SqliteDatabase

    def full(self, notification):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(SqliteDatabase, "record_notification", full)

    report = run_notify(prepared, kind="hot")

    assert report.sent is True
    assert report.errors == 1
    assert "журнал не записан" in report.notes
    assert "ещё раз" in report.notes
```

- [ ] **Шаг 2: убедиться, что тест падает**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_notifications.py -k did_not_write`
Ожидается: FAIL — `sqlite3.OperationalError: database or disk is full`.

- [ ] **Шаг 3: запись журнала обёрнута**

```python
# listam/notifications.py — в run_notify, после report.sent = True
            try:
                database.record_notification(Notification(
                    kind=kind, sent_at=datetime.now(timezone.utc),
                    window_from=since, window_to=until,
                    events=report.events, requests=report.requests, text=report.text,
                ))
            except Exception as exc:       # sqlite3.Error, OSError — база не приняла строку
                report.errors = 1
                notes.append(
                    f"сообщение ушло, но журнал не записан: {exc}. Окно осталось, "
                    f"где было, — следующий запуск пошлёт то же самое ещё раз"
                )
                return report
            publish(session, config, "база с журналом уведомлений")
            report.errors += session.failures
```

- [ ] **Шаг 4: батарея и коммит**

Запуск: `.venv/Scripts/python.exe -m pytest -q` → 795 passed, 25 skipped.

```bash
git add listam/notifications.py tests/test_notifications.py
git commit -m "fix(notify): журнал, который не записался, — строка отчёта, а не трейсбек"
```

### Конец фазы 4

- [ ] Живая проверка B-3 на двух рабочих папках: копия `config/` в scratchpad
      дважды (`work_dir` A и B, общий `storage.directory`, база — копия
      `data/listam-m3.sqlite`, `notify.kind: stdout`). На B —
      `notify --digest`; на A — `notify --digest --dry-run`. Запиши обе строки
      «Событий: N» и примечание о копии («удалённая копия свежее…»).
- [ ] «Результат фазы 4», стартовый промпт фазы 5, чисто, коммит.

---

# Фаза 5. Конфиг уведомлений на входе

**Одна сессия.** Закрывает **M-2**, **M-3**, **M-4**, **M-6**. Секция `notify`
читается разом и проверяется до замка, как `match`; порог `null` выключает
вид, а не подменяется чужим; ключ канала перестаёт быть ключом обхода;
`matches --new` открывает базу так же, как витрина.

**Ожидается после фазы:** ~809 passed, 25 skipped, схема базы 10.

### Задача 5.1. `switch`, `hours` и число, которое не число

**Файлы:**
- Изменить: `listam/config.py`
- Тест: `tests/test_config.py`

**Interfaces — Produces:**
```python
# listam/config.py
switch(config: Config, key: str, default: bool) -> bool
hours(config: Config, key: str, default: float) -> float
```

- [ ] **Шаг 1: падающие тесты**

```python
# tests/test_config.py — импорт дополнить hours, switch; в конец файла
def test_a_quoted_false_is_not_read_as_yes(tmp_path):
    """`bool("false")` — это True: вид уведомлений, выключенный человеком,
    продолжал слать."""
    d = write_cfg(tmp_path, "dev", 'notify:\n  digest:\n    enabled: "false"\n')
    config = load_config(env="dev", config_dir=d, dotenv_path=tmp_path / ".env")

    with pytest.raises(ConfigError, match="notify.digest.enabled"):
        switch(config, "notify.digest.enabled", True)


def test_a_negative_window_is_refused(tmp_path):
    """Окно −48 ч смотрит в будущее и отвечает «событий нет»."""
    d = write_cfg(tmp_path, "dev", "notify:\n  digest:\n    fallback_hours: -48\n")
    config = load_config(env="dev", config_dir=d, dotenv_path=tmp_path / ".env")

    with pytest.raises(ConfigError, match="notify.digest.fallback_hours"):
        hours(config, "notify.digest.fallback_hours", 24.0)
```

- [ ] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_config.py -k "quoted_false or negative_window"`
Ожидается: FAIL — `ImportError: cannot import name 'hours'`.

- [ ] **Шаг 3: три функции**

```python
# listam/config.py — после threshold
def _number(key: str, value: Any) -> float:
    """Число из yaml. `true` — не число, хотя Python считает его единицей,
    а строка «десять» давала трейсбек `ValueError` вместо ответа человеку."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{key} = {value!r} не годится: здесь нужно число, без кавычек.")
    return float(value)


def switch(config: Config, key: str, default: bool) -> bool:
    """Тумблер: `true` или `false`; `null` — выключено.

    Строка `"false"` в yaml — это не «нет», а непустая строка, и `bool()`
    читал её как «да»: выключенный человеком вид уведомлений продолжал слать.
    """
    value = threshold(config, key, default)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ConfigError(
            f"{key} = {value!r} не годится: тумблер — это true или false без "
            f"кавычек. Строка {value!r} — не ответ «да» или «нет»."
        )
    return value


def hours(config: Config, key: str, default: float) -> float:
    """Окно в часах: число не меньше нуля. `null` — «как по умолчанию».

    Отрицательное окно смотрит в будущее и отвечает «событий нет» — то есть
    выглядит как спокойный рынок. Ноль — это ноль: окно пустое, но честное.
    """
    value = threshold(config, key, default)
    if value is None:
        return float(default)
    number = _number(key, value)
    if number < 0:
        raise ConfigError(
            f"{key} = {value} не годится: окно в часах не бывает отрицательным — "
            f"оно смотрело бы в будущее и отвечало «событий нет»."
        )
    return number
```

В `positive` и `score_threshold` строку `number = float(value)` заменить на
`number = _number(key, value)`.

- [ ] **Шаг 4: тесты проходят, батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: **797 passed, 25 skipped**. Если упал тест,
который кладёт порог строкой (`"10"`), — это находка: такой порог раньше
молча читался `float()`. Запиши в отчёт, какой ключ и где, и реши вместе с
отчётом: поправить тест (если строка — его выдумка) или конфиг (если строка
стоит в поставляемом yaml).

- [ ] **Шаг 5: коммит**

```bash
git add listam/config.py tests/test_config.py
git commit -m "fix(config): тумблер в кавычках и окно из будущего отклоняются на входе"
```

### Задача 5.2. Секция `notify` читается разом и до замка

**Файлы:**
- Изменить: `listam/notifications.py`, `listam/cli.py` (`_matches_new`),
  `listam/doctor.py` (`notify_check`)
- Тест: `tests/test_notifications.py`, `tests/test_doctor.py`

**Interfaces — Produces:**
```python
# listam/notifications.py
@dataclass
class NotifyTuning:
    enabled: bool
    per_request: int | None
    fallback_hours: float
    wide_request: int | None = None
    include_retired: bool = False

tuning_for(config: Config, kind: str) -> NotifyTuning
```
`enabled`, `per_request` и `shows_closures` (фаза 1) **удаляются**; их
читатели переходят на `tuning_for`.

- [ ] **Шаг 1: падающие тесты**

```python
# tests/test_notifications.py — импорт: from listam.config import ConfigError
@pytest.mark.parametrize("key, value", [
    ("enabled", "false"),
    ("include_retired", "no"),
    ("fallback_hours", -48),
    ("fallback_hours", "сутки"),
    ("per_request", "десять"),
    ("wide_request", 0),
])
def test_a_senseless_notify_setting_is_refused_before_the_work(
        prepared, monkeypatch, key, value):
    """Бессмысленное значение отклоняется на входе, до замка и до базы —
    как опечатка в весе у `match`. До фазы 5 QA `enabled: "false"` слал,
    `fallback_hours: -48` смотрел в будущее, а «сутки» роняли трейсбек."""
    def no_work(*args, **kwargs):
        raise AssertionError("работа не должна начинаться")

    monkeypatch.setattr("listam.notifications.working_session", no_work)
    prepared.data["notify"]["digest"][key] = value

    with pytest.raises(ConfigError, match=f"notify.digest.{key}"):
        run_notify(prepared, kind="digest", dry_run=True)
```

```python
# tests/test_doctor.py — рядом с test_doctor_refuses_telegram_without_a_token
def test_doctor_calls_a_senseless_notify_setting_a_failure(tmp_path):
    """`doctor` отвечает то же, что ответит `notify`: вечером, когда дайджест
    не пришёл, узнавать об этом поздно."""
    from listam.doctor import notify_check

    check = notify_check(cfg(tmp_path, notify={"kind": "stdout",
                                               "digest": {"enabled": "false"}}))

    assert not check.ok
    assert "notify.digest.enabled" in check.details
```

- [ ] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_notifications.py tests/test_doctor.py -k "senseless"`
Ожидается: FAIL — `AssertionError: работа не должна начинаться` (значение
принято и работа пошла) и `ValueError` у «сутки»/«десять».

- [ ] **Шаг 3: `NotifyTuning` и `tuning_for`**

```python
# listam/notifications.py — импорт
from listam.config import Config, ConfigError, hours, positive, switch
```

Функции `enabled` и `per_request` заменить целиком:

```python
@dataclass
class NotifyTuning:
    """Ручки одного вида уведомления, прочитанные и проверенные разом."""

    enabled: bool
    per_request: int | None      # строк на заявку; у ленты — notify.feed.limit
    fallback_hours: float        # окно, когда отправок этого вида ещё не было
    wide_request: int | None = None
    include_retired: bool = False


def tuning_for(config: Config, kind: str) -> NotifyTuning:
    """Секция `notify.<kind>` целиком — или `ConfigError` с именем ключа.

    Читается до замка и до базы. `enabled: "false"` в кавычках — непустая
    строка, и `bool()` читал её как «да»; `fallback_hours: -48` давал окно
    из будущего. И то и другое отклоняется на входе, как `--limit 0`.

    Закрытия идут только в дайджест: в «горячее» — никогда (решение 1 спеки
    M3), и тумблера для него нет нарочно.
    """
    base = f"notify.{kind}"
    limit_key = f"{base}.limit" if kind == "feed" else f"{base}.per_request"
    limit = positive(config, limit_key, 10)
    tuning = NotifyTuning(
        enabled=switch(config, f"{base}.enabled", True),
        per_request=None if limit is None else int(limit),
        fallback_hours=hours(config, f"{base}.fallback_hours",
                             DEFAULT_FALLBACK_HOURS[kind]),
    )
    if kind == "digest":
        wide = positive(config, f"{base}.wide_request", 50)
        tuning.wide_request = None if wide is None else int(wide)
        tuning.include_retired = switch(config, f"{base}.include_retired", True)
    return tuning
```

Функцию `shows_closures` (фаза 1) удалить.

В `window_for` чтение запасного окна заменить:

```python
    fallback = tuning_for(config, kind).fallback_hours
    since = until - timedelta(hours=fallback)
```

В `run_notify` — начало до `try:` заменить:

```python
    if kind not in KINDS:
        raise ConfigError(f"вид уведомления {kind!r} не из списка: {', '.join(KINDS)}")

    # Секция `notify` проверяется до замка и до базы: бессмысленное значение
    # отклоняется на входе, а не посреди выборки под замком рабочей копии.
    knobs = tuning_for(config, kind)
    report = NotifyReport(kind=kind, dry_run=dry_run)
    if not knobs.enabled:
        report.text = f"уведомления вида {kind} выключены в конфиге (notify.{kind}.enabled)"
        report.scope = "тумблер выключен"
        return report

    # Канал собирается до работы: пустой секрет — отказ на входе, а не после
    # выборки под замком рабочей копии. Пробному прогону канал не нужен.
    notifier = None if dry_run else build_notifier(config)

    tuning = settings(config)
    min_score = tuning.hot if kind == "hot" else tuning.digest
    notes: list[str] = []
```

Внутри сессии:

```python
            if kind == "feed":
                report.text, report.events = _feed_text(database, knobs, since, until)
            else:
                page = collect_events(config, since=since, until=until,
                                      min_score=min_score, note=scope,
                                      include_retired=knobs.include_retired)
                calls = page.calls()
                report.events = len(calls)
                report.retired = len(page.events) - len(calls)
                report.requests = len({event.match.request_id for event in calls})
                report.text = _match_text(page, knobs, kind)
```

`_match_text(page, config, kind)` → `_match_text(page, knobs: NotifyTuning, kind)`:
`per_request(config, kind)` → `knobs.per_request`,
`positive(config, "notify.digest.wide_request", 50)` → `knobs.wide_request`.
`_feed_text(database, config, since, until)` → `_feed_text(database, knobs, since, until)`:
`per_request(config, "feed") or 0` → `knobs.per_request or 0`.

- [ ] **Шаг 4: CLI и `doctor` читают то же**

```python
# listam/cli.py — в _matches_new
    from listam.notifications import tuning_for, window_for
```

```python
                              include_retired=tuning_for(config, "digest").include_retired)
```

```python
# listam/doctor.py — в notify_check, вместо строки switches и расчёта «все выключены»
    from listam.notifications import tuning_for

    harm: list[str] = []
    warn: list[str] = []
    knobs = {}
    for name in NOTIFY_KINDS:
        try:
            knobs[name] = tuning_for(config, name)
        except ConfigError as exc:
            harm.append(str(exc))
    switches = ", ".join(
        f"{name}: {'вкл' if knobs[name].enabled else 'выкл'}"
        for name in NOTIFY_KINDS if name in knobs
    )
```

и ниже:

```python
    if knobs and all(not item.enabled for item in knobs.values()):
        warn.append("все три вида выключены — команда notify не пошлёт ничего")
```

(Импорт внутри функции нарочно: `listam.notifications` тянет `runner` и
`matches_view`, а `doctor` импортируется раньше них. Если циклического
импорта нет — перенеси наверх и запиши это в отчёт.)

- [ ] **Шаг 5: тесты проходят, батарея целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: 804 passed, 25 skipped.

- [ ] **Шаг 6: коммит**

```bash
git add listam/notifications.py listam/cli.py listam/doctor.py tests/test_notifications.py tests/test_doctor.py
git commit -m "fix(notify): секция notify читается разом и отклоняет бессмысленное на входе"
```

### Задача 5.3. Порог `null` выключает вид, а не подменяется чужим

**Файлы:**
- Изменить: `listam/notifications.py` (`run_notify`)
- Тест: `tests/test_notifications.py`

- [ ] **Шаг 1: падающий тест**

```python
def test_hot_switched_off_by_its_threshold_sends_nothing(prepared):
    """`hot: null` у подбора значит «горячих не бывает». До фазы 5 QA
    `collect_events` подставлял вместо него порог дайджеста, и «Звони сейчас»
    уходил с вариантами на 41 балл."""
    prepared.data["match"]["thresholds"]["hot"] = None

    report = run_notify(prepared, kind="hot")

    assert report.sent is False
    assert report.events == 0
    assert "match.thresholds.hot" in report.text
    database = build_database(prepared)
    database.connect()
    assert database.last_notification("hot") is None
    database.close()
```

- [ ] **Шаг 2: убедиться, что тест падает**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_notifications.py -k switched_off_by_its_threshold`
Ожидается: FAIL — `assert True is False` (отправлено).

- [ ] **Шаг 3: выход до работы**

```python
# listam/notifications.py — в run_notify, сразу после min_score = …
    if kind != "feed" and min_score is None:
        # Порог выключен (`null`) — подбор таких вариантов не считает вовсе.
        # Подставить чужой порог значило бы слать под именем «горячего»
        # то, что человек горячим не назвал.
        report.scope = "порог выключен"
        report.text = (f"порог match.thresholds.{kind} выключен (null): подбор "
                       f"таких вариантов не считает, слать нечего")
        return report
```

- [ ] **Шаг 4: батарея и коммит**

Запуск: `.venv/Scripts/python.exe -m pytest -q` → 805 passed, 25 skipped.

```bash
git add listam/notifications.py tests/test_notifications.py
git commit -m "fix(notify): порог null выключает вид, а не подменяется порогом дайджеста"
```

### Задача 5.4. Ключ канала — не ключ обхода

**Файлы:**
- Изменить: `listam/config.py` (`PLACEHOLDER`, `_substitute`), `config/prod.yaml`
- Тест: `tests/test_config.py`

- [ ] **Шаг 1: падающие тесты**

```python
# tests/test_config.py — в конец
def test_an_optional_placeholder_may_stay_empty(tmp_path, monkeypatch):
    """`${VAR:-}` — секрет, без которого конфиг грузится: отказать должен тот,
    кому он нужен, а не загрузка конфига."""
    d = write_cfg(tmp_path, "dev", "notify:\n  token: ${TELEGRAM_BOT_TOKEN:-}\n")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    config = load_config(env="dev", config_dir=d, dotenv_path=tmp_path / ".env")

    assert config.get("notify.token") == ""


def test_the_shipped_prod_config_loads_without_telegram_keys(tmp_path, monkeypatch):
    """Без токена бота `scrape` обязан стартовать; отказать вправе только
    `notify`. До фазы 5 QA пустой TELEGRAM_BOT_TOKEN останавливал все команды."""
    from listam.wiring import build_notifier

    monkeypatch.setenv("GDRIVE_FOLDER", "folder-abc123")
    monkeypatch.setenv("GDRIVE_CREDENTIALS_FILE", "credentials.json")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    config = load_config(env="prod", config_dir=ROOT / "config",
                         dotenv_path=tmp_path / ".env")

    assert config.get("notify.kind") == "telegram"
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        build_notifier(config)
```

- [ ] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_config.py -k "optional_placeholder or without_telegram_keys"`
Ожидается: FAIL — `ConfigError: Переменная окружения TELEGRAM_BOT_TOKEN не задана`
при загрузке.

- [ ] **Шаг 3: `${VAR:-}` в подстановке**

```python
# listam/config.py
# `${VAR}` — обязательный секрет: без него конфиг не грузится. `${VAR:-}` —
# необязательный: пусто — значит пусто, и отказать вправе только тот, кому
# он нужен. Канал уведомлений — не ключ обхода.
PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-)?\}")
```

В `_substitute.replace`:

```python
        def replace(m: re.Match) -> str:
            name, optional = m.group(1), m.group(2)
            value = os.environ.get(name)
            if value is None or value == "":
                if optional:
                    return ""
                raise ConfigError(
                    f"Переменная окружения {name} не задана, а конфиг на неё ссылается. "
                    f"Добавь её в .env (образец — .env.example)."
                )
            return value
```

```yaml
# config/prod.yaml — в секции notify
  token: ${TELEGRAM_BOT_TOKEN:-}      # необязательный: без него откажет только notify
  chat_id: ${TELEGRAM_CHAT_ID:-}      # чат брокера: клиентам он пересылает сам
```

- [ ] **Шаг 4: батарея и коммит**

Запуск: `.venv/Scripts/python.exe -m pytest -q` → **807 passed, 25 skipped**.
`test_docs.py` сравнивает секцию
`notify` README и `prod.yaml` — если он упал на новом синтаксисе, поправь
README (фаза 8 его всё равно дописывает) и запиши.

```bash
git add listam/config.py config/prod.yaml tests/test_config.py
git commit -m "fix(config): пустой токен бота останавливает только notify, а не обход"
```

### Задача 5.5. `matches --new` открывает базу, как витрина

**Файлы:**
- Изменить: `listam/matches_view.py` (`open_for_reading`, `collect_matches`,
  `collect_events`), `listam/cli.py` (`_matches_new`)
- Тест: `tests/test_notifications.py`

**Interfaces — Produces:**
```python
# listam/matches_view.py
open_for_reading(config: Config, what: str) -> Database   # MatchesError при отказе
```

- [ ] **Шаг 1: падающие тесты**

```python
def cli_args(config) -> list[str]:
    return ["--env", "test", "--config-dir", str(config.path.parent)]


def test_the_slice_on_an_old_schema_is_refused_in_words(prepared, capsys):
    """До фазы 5 QA окно читалось из журнала раньше проверки схемы:
    `OperationalError: no such table: notifications`."""
    import sqlite3

    from listam.cli import main
    from listam.wiring import database_path

    connection = sqlite3.connect(database_path(prepared))
    connection.execute("DROP TABLE notifications")
    connection.execute("DELETE FROM schema_version WHERE version >= 10")
    connection.commit()
    connection.close()

    assert main(cli_args(prepared) + ["matches", "--new"]) == 1
    assert "схема базы 9" in capsys.readouterr().err


def test_the_slice_does_not_leave_an_empty_base_behind(prepared, capsys):
    """`connect()` на отсутствующем пути заводит пустую базу. После неё
    `matches` не скачивала копию, а отвечала «схема базы 0… накати
    миграции» — совет, который не поможет: базы нет вовсе."""
    from listam.cli import main
    from listam.wiring import database_path

    path = database_path(prepared)
    path.unlink()

    assert main(cli_args(prepared) + ["matches", "--new"]) == 1
    assert not path.exists()
    assert "базы нет" in capsys.readouterr().err
```

- [ ] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_notifications.py -k "old_schema_is_refused or empty_base_behind"`
Ожидается: FAIL — `sqlite3.OperationalError: no such table: notifications`.

- [ ] **Шаг 3: одна дверь для чтения**

```python
# listam/matches_view.py — после display_limit
def open_for_reading(config: Config, what: str) -> Database:
    """База для витрины: без замка, без миграций и без создания файла.

    `connect()` на отсутствующем пути заводит пустую базу. Витрина, открывшая
    так файл, оставляла на диске 4 КБ без единой таблицы — и следующая
    `matches` уже не скачивала копию из хранилища, а отвечала «схема 0».
    `what` — чего не покажут при отказе: «Матчи», «События».
    """
    local_db = database_path(config)
    if not local_db.exists():
        storage = build_storage(config)
        if not storage.download(config.get("storage.db_filename", "listam.sqlite"),
                                local_db):
            raise MatchesError(
                f"{what} не показаны: базы нет ни здесь, ни в хранилище — "
                f"сначала python -m listam scrape"
            )
    database = build_database(config)
    database.connect()
    required = latest_schema_version()
    version = database.schema_version()
    if version < required:
        database.close()
        raise MatchesError(
            f"{what} не показаны: схема базы {version}, а код ждёт {required}. "
            f"Витрина ничего не мигрирует — накати миграции: python -m listam recheck"
        )
    return database
```

Импорт `Database` для аннотации: `from listam.ports.database import Database`.

В `collect_matches` блок от `storage = build_storage(config)` до конца
проверки схемы (`raise MatchesError(…)`) заменить одной строкой, `try:` —
сразу после неё:

```python
    database = open_for_reading(config, "Матчи")
    try:
        if external_id is None:
```

В `collect_events` так же:

```python
    database = open_for_reading(config, "События")
    try:
        if external_id is None:
```

- [ ] **Шаг 4: срез открывает базу той же дверью**

```python
# listam/cli.py — в _matches_new, вместо database = build_database(config) … finally
    from listam.matches_view import (MatchesError, collect_events, display_limit,
                                     open_for_reading, render_events)
```

```python
    try:
        database = open_for_reading(config, "События")
    except MatchesError as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        since, until, note = window_for(config, kind="digest", hours=hours,
                                        database=database)
    finally:
        database.close()
```

- [ ] **Шаг 5: батарея и коммит**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: **809 passed, 25 skipped**. Если упал тест витрины, ждавший
«схема базы 0» на отсутствующем файле, — это ожидаемое следствие: теперь там
«базы нет». Поправь ожидание и запиши.

```bash
git add listam/matches_view.py listam/cli.py tests/test_notifications.py
git commit -m "fix(matches): срез --new не падает на старой схеме и не заводит пустую базу"
```

### Конец фазы 5

- [ ] Живьём на копии конфига: `notify.digest.enabled: "false"` →
      `notify --digest --dry-run`, код и первая строка ошибки;
      `match.thresholds.hot: null` → `notify --hot --dry-run`;
      `TELEGRAM_BOT_TOKEN= … --env prod changes` (с `GDRIVE_*` из примера
      теста) и `… --env prod notify --hot` — коды обоих. Запиши вывод.
- [ ] `doctor --no-network` на `config/` — строка «Уведомления».
- [ ] «Результат фазы 5», стартовый промпт фазы 6, чисто, коммит.

---

# Фаза 6. Канал: журнал помнит, куда ушло, а Telegram — когда подождать

**Одна сессия.** Закрывает **H-3** и **H-4**. **Уточняет решение 2 спеки M3:**
окно следующего запуска — `window_to` последней успешной строки этого вида
**этого канала**. Строка без канала (до миграции 011) считается за любой канал:
считать её чужой значило бы послать в Telegram всё, что уже ушло.

**Ожидается после фазы:** ~817 passed, 25 skipped, **схема базы 11**.

### Задача 6.1. Миграция 011

**Файлы:**
- Создать: `listam/migrations/011_notification_channel.sql`
- Изменить: `tests/test_migrations.py:293` (`== 10` → `>= 10`)
- Тест: `tests/test_migrations.py`

- [ ] **Шаг 1: падающий тест**

```python
# tests/test_migrations.py — в конец
def test_migration_011_remembers_the_channel_of_a_send(tmp_path):
    """Текст, напечатанный в консоль, до брокера не дошёл: окно Telegram
    от него двигаться не должно — значит, журнал обязан помнить канал."""
    database = opened(tmp_path, MIGRATIONS_DIR)
    database.migrate()

    columns = {row["name"] for row in
               database.conn.execute("PRAGMA table_info(notifications)")}
    assert "channel" in columns
    assert database.schema_version() == 11
    database.close()
```

- [ ] **Шаг 2: убедиться, что тест падает**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_migrations.py -k 011`
Ожидается: FAIL — `assert 'channel' in {…}`.

- [ ] **Шаг 3: миграция**

```sql
-- Версия 11: журнал отправок помнит канал.
--
-- notifications.channel — куда ушла отправка: stdout | telegram. Окно
--   следующего запуска считается от последней строки того же канала: текст,
--   напечатанный в консоль, до брокера не дошёл, и окно Telegram от него
--   двигаться не должно.
--   NULL — строка из времён до этой миграции. Куда она ушла, неизвестно, и
--   такие строки считаются за любой канал: считать их чужими значило бы
--   послать в Telegram всё, что уже ушло.

ALTER TABLE notifications ADD COLUMN channel TEXT;
```

- [ ] **Шаг 4: тесты проходят**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_migrations.py`
Ожидается: PASS после правки `== 10` → `>= 10` в
`test_migration_010_…` (строка 293) — это ожидаемое следствие новой миграции.

- [ ] **Шаг 5: коммит**

```bash
git add listam/migrations/011_notification_channel.sql tests/test_migrations.py
git commit -m "feat(db): миграция 011 — журнал отправок помнит канал"
```

### Задача 6.2. Порт: окно одного канала

**Файлы:**
- Изменить: `listam/domain/models.py` (`Notification.channel`),
  `listam/ports/database.py`, `listam/adapters/db_sqlite.py`
  (`record_notification`, `last_notification`)
- Тест: `tests/contracts/test_database_contract.py`

**Interfaces — Produces:**
```python
Notification.channel: str | None = None
Database.last_notification(kind: str, channel: str | None = None) -> Notification | None
```

- [ ] **Шаг 1: падающие контрактные тесты**

```python
# tests/contracts/test_database_contract.py — после test_kinds_of_notification_do_not_mix
def test_the_journal_of_one_channel_does_not_move_another(db):
    """Напечатанное в консоль брокеру не пришло: окно Telegram от него
    не двигается (H-3 аудита QA после M3)."""
    from listam.domain.models import Notification

    db.record_notification(Notification(kind="digest", sent_at=LATER,
                                        window_to=LATER, channel="stdout"))

    assert db.last_notification("digest", channel="telegram") is None
    assert db.last_notification("digest", channel="stdout").window_to == LATER
    assert db.last_notification("digest", channel="stdout").channel == "stdout"


def test_a_send_from_before_the_channels_counts_for_every_channel(db):
    """Строки до миграции 011 канала не знают. Считать их чужими значило бы
    послать в Telegram всё, что уже ушло."""
    from listam.domain.models import Notification

    db.record_notification(Notification(kind="digest", sent_at=LATER, window_to=LATER))

    assert db.last_notification("digest", channel="telegram").window_to == LATER
```

- [ ] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py -k "channel"`
Ожидается: FAIL — `TypeError: Notification.__init__() got an unexpected keyword argument 'channel'`.

- [ ] **Шаг 3: модель, порт, адаптер**

```python
# listam/domain/models.py — в Notification, после notes
    channel: str | None = None              # stdout | telegram; None — до миграции 011
```

```python
# listam/ports/database.py — last_notification целиком
    @abstractmethod
    def last_notification(self, kind: str, channel: str | None = None
                          ) -> Notification | None:
        """Последняя отправка этого вида; не было ни одной — None.

        Виды не смешиваются: часовое «горячее» не двигает окно дневного
        дайджеста. `channel` задан — не смешиваются и каналы: текст,
        напечатанный в консоль, не двигает окно Telegram. Строка без канала
        (до миграции 011) считается за любой.
        """
```

```python
# listam/adapters/db_sqlite.py — record_notification: колонка channel
            cursor = self.conn.execute(
                "INSERT INTO notifications "
                "(kind, sent_at, window_from, window_to, events, requests, text, notes, "
                " channel) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (notification.kind, to_iso(notification.sent_at),
                 to_iso(notification.window_from), to_iso(notification.window_to),
                 int(notification.events), int(notification.requests),
                 notification.text, notification.notes, notification.channel),
            )
```

```python
# listam/adapters/db_sqlite.py — last_notification целиком
    def last_notification(self, kind: str, channel: str | None = None
                          ) -> Notification | None:
        query = "SELECT * FROM notifications WHERE kind = ?"
        params: list = [kind]
        if channel is not None:
            # Строка без канала — из времён до миграции 011: куда она ушла,
            # неизвестно, и считать её чужой значило бы послать всё заново.
            query += " AND (channel = ? OR channel IS NULL)"
            params.append(channel)
        query += " ORDER BY sent_at DESC, id DESC LIMIT 1"
        row = self.conn.execute(query, tuple(params)).fetchone()
        if row is None:
            return None
        return Notification(
            id=row["id"], kind=row["kind"], sent_at=from_iso(row["sent_at"]),
            window_from=from_iso(row["window_from"]),
            window_to=from_iso(row["window_to"]),
            events=row["events"], requests=row["requests"],
            text=row["text"], notes=row["notes"], channel=row["channel"],
        )
```

- [ ] **Шаг 4: контракт целиком и коммит**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/contracts/test_database_contract.py`
Ожидается: PASS.

```bash
git add listam/domain/models.py listam/ports/database.py listam/adapters/db_sqlite.py tests/contracts/test_database_contract.py
git commit -m "feat(db): окно отправок считается по своему каналу"
```

### Задача 6.3. `none` не двигает окно, `stdout` не двигает Telegram

**Файлы:**
- Изменить: `listam/wiring.py` (`notify_channel`), `listam/notifications.py`
  (`window_for`, `run_notify`), `listam/cli.py` (`_matches_new`)
- Тест: `tests/test_notifications.py`

**Interfaces — Produces:**
```python
# listam/wiring.py
notify_channel(config: Config) -> str          # "none" | "stdout" | "telegram"
# listam/notifications.py
window_for(config, kind, hours=None, database=None, channel=None) -> tuple[datetime, datetime, str]
```

- [ ] **Шаг 1: падающие тесты**

```python
def test_a_channel_that_goes_nowhere_does_not_move_the_window(prepared):
    """`kind: none` ничего не шлёт — значит, и не отправляло. До фазы 6 QA
    журнал писал «отправлено 2», и когда Telegram появлялся, он получал
    только то, что случилось после."""
    prepared.data["notify"]["kind"] = "none"

    report = run_notify(prepared, kind="hot")

    assert report.sent is False
    assert report.errors == 0
    assert "никуда не идут" in report.text
    database = build_database(prepared)
    database.connect()
    assert database.last_notification("hot") is None
    database.close()


def test_text_printed_to_the_console_does_not_move_the_telegram_window(
        prepared, monkeypatch):
    """Проверка текста в консоли не съедает события Telegram."""
    run_notify(prepared, kind="hot")                     # stdout: напечатано
    prepared.data["notify"].update({"kind": "telegram", "token": "t", "chat_id": "1"})
    sent = []
    monkeypatch.setattr("listam.adapters.notify_telegram.TelegramNotifier.send",
                        lambda self, text, to=None: sent.append(text))

    report = run_notify(prepared, kind="hot")

    assert report.events == 2
    assert "Заявка R-1" in sent[0]
```

- [ ] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_notifications.py -k "goes_nowhere or printed_to_the_console"`
Ожидается: FAIL — `assert True is False` и `assert 0 == 2`.

- [ ] **Шаг 3: имя канала — из конфига**

```python
# listam/wiring.py — после build_notifier
def notify_channel(config: Config) -> str:
    """Как называется канал в журнале отправок: `none`, `stdout`, `telegram`.

    Имя берётся из конфига, а не из собранного адаптера: пробному прогону
    канал не нужен (секретов может не быть), а окно он обязан показать то же,
    что увидит настоящая отправка.
    """
    kind = _kind(config, "notify", "none")
    return "none" if kind in ("none", "null") else kind
```

- [ ] **Шаг 4: окно по каналу, `none` — без журнала**

```python
# listam/notifications.py — импорт
from listam.wiring import build_notifier, notify_channel
```

`window_for` — новый аргумент и чтение журнала:

```python
def window_for(config: Config, kind: str, hours: float | None = None,
               database=None, channel: str | None = None
               ) -> tuple[datetime, datetime, str]:
```

```python
    last = (database.last_notification(kind, channel=channel)
            if database is not None else None)
```

В docstring `window_for` дописать: «`channel` — окно своего канала: текст,
напечатанный в консоль, не двигает окно Telegram».

В `run_notify` — после проверки порога `null` (задача 5.3):

```python
    channel = notify_channel(config)
    if channel == "none" and not dry_run:
        # Канал не настроен — значит, ничего и не ушло. Писать «отправлено»
        # значило бы съесть события: когда Telegram появится, он получил бы
        # только то, что случилось после.
        report.scope = "канал не настроен"
        report.text = ("notify.kind: none — уведомления никуда не идут. Окно не "
                       "сдвинуто: канал, когда появится, получит всё накопленное")
        return report
```

Вызов окна внутри сессии:

```python
            since, until, scope = window_for(config, kind, database=database,
                                             channel=channel)
```

Строка журнала:

```python
                database.record_notification(Notification(
                    kind=kind, sent_at=datetime.now(timezone.utc),
                    window_from=since, window_to=until,
                    events=report.events, requests=report.requests, text=report.text,
                    channel=channel,
                ))
```

- [ ] **Шаг 5: срез в терминале показывает окно того же канала**

```python
# listam/cli.py — в _matches_new
    from listam.wiring import notify_channel
```

```python
        since, until, note = window_for(config, kind="digest", hours=hours,
                                        database=database,
                                        channel=notify_channel(config))
```

- [ ] **Шаг 6: батарея и коммит**

Запуск: `.venv/Scripts/python.exe -m pytest -q`
Ожидается: 814 passed, 25 skipped. Тест `test_a_send_writes_one_line_in_the_journal`
обязан остаться зелёным: у `stdout` строка журнала по-прежнему пишется.

```bash
git add listam/wiring.py listam/notifications.py listam/cli.py tests/test_notifications.py
git commit -m "fix(notify): канал none не двигает окно, консоль не двигает Telegram"
```

### Задача 6.4. Telegram просит подождать — ждём, а не шлём заново

**Файлы:**
- Изменить: `listam/adapters/notify_telegram.py`
- Тест: `tests/test_notify_telegram.py`

- [ ] **Шаг 1: падающие тесты**

```python
# tests/test_notify_telegram.py — в конец
class TooMany:
    """Ответ Bot API на 429: сообщение не принято, подожди `retry_after` секунд."""

    ok, status_code = False, 429
    text = '{"ok":false,"error_code":429,"description":"Too Many Requests"}'

    def __init__(self, retry_after=5):
        self.retry_after = retry_after

    def json(self):
        return {"ok": False, "error_code": 429,
                "parameters": {"retry_after": self.retry_after}}


def five_sections() -> str:
    return "\n\n".join(f"Заявка R-{index} — новый: 1" for index in range(5))


def test_a_too_many_requests_answer_is_waited_out_and_not_resent(monkeypatch):
    """H-4 аудита: 429 на третьей части из пяти давал `NotifyError`, и повтор
    слал все пять — две из них брокеру во второй раз."""
    answers = iter([Answer(), Answer(), TooMany(), Answer(), Answer(), Answer()])
    sent, waited = [], []

    def post(url, json, timeout):
        answer = next(answers)
        if answer.ok:
            sent.append(json["text"])
        return answer

    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post", post)
    monkeypatch.setattr("listam.adapters.notify_telegram.time.sleep", waited.append)

    TelegramNotifier(token="t", chat_id="1").send(five_sections())

    assert len(sent) == 5
    assert len(set(sent)) == 5, "ни одна часть не пришла дважды"
    assert 5 in waited


def test_a_channel_that_keeps_saying_wait_gives_up_in_words(monkeypatch):
    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post",
                        lambda url, json, timeout: TooMany())

    with pytest.raises(NotifyError, match="429"):
        TelegramNotifier(token="t", chat_id="1").send("Заявка R-1 — новый: 1")


def test_a_wait_longer_than_a_minute_is_not_waited(monkeypatch):
    """Час ожидания в команде по расписанию — это зависшая команда. Дольше
    минуты не ждём: повтор сделает следующий запуск."""
    answers = iter([TooMany(retry_after=3600), Answer()])
    waited = []
    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post",
                        lambda url, json, timeout: next(answers))
    monkeypatch.setattr("listam.adapters.notify_telegram.time.sleep", waited.append)

    TelegramNotifier(token="t", chat_id="1").send("Заявка R-1 — новый: 1")

    assert max(waited) <= 60
```

- [ ] **Шаг 2: убедиться, что тесты падают**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_notify_telegram.py -k "too_many or keeps_saying or longer_than_a_minute"`
Ожидается: FAIL в первом и третьем — `NotifyError: Telegram отказал (код 429)`.

- [ ] **Шаг 3: повтор только там, где часть точно не принята**

```python
# listam/adapters/notify_telegram.py — после DEFAULT_PAUSE
MAX_RETRIES = 3          # сколько раз ждать, когда Telegram просит подождать
MAX_WAIT = 60.0          # дольше минуты не ждём: повторит следующий запуск
```

```python
def _retry_after(answer) -> float | None:
    """Сколько секунд Telegram просит подождать. Не 429 — не просит."""
    if getattr(answer, "status_code", None) != 429:
        return None
    try:
        return float(answer.json()["parameters"]["retry_after"])
    except (ValueError, KeyError, TypeError):
        return DEFAULT_PAUSE
```

`send` целиком и новый `_deliver`:

```python
    def send(self, text: str, to: str | None = None) -> None:
        chat = to or self.chat_id
        parts = split_message(text)
        for number, part in enumerate(parts):
            if number:
                time.sleep(self.pause)
            self._deliver(chat, part, number, parts)

    def _deliver(self, chat: str, part: str, number: int, parts: list[str]) -> None:
        """Одна часть. Повтор — только там, где Telegram её точно не принял.

        429 — «подожди N секунд», и сообщение не доставлено; соединение,
        которое не установилось, тоже ничего не доставило. А обрыв после
        отправки (таймаут чтения) не повторяется: часть могла уйти, и брокер
        получил бы её дважды.
        """
        for attempt in range(MAX_RETRIES + 1):
            last = attempt == MAX_RETRIES
            try:
                answer = requests.post(
                    f"{self.api_url}/bot{self.token}/sendMessage",
                    json={"chat_id": chat, "text": part,
                          "disable_web_page_preview": True},
                    timeout=self.timeout,
                )
            except requests.ConnectionError as exc:
                if not last:
                    time.sleep(self.pause)
                    continue
                raise NotifyError(self._hide(
                    f"Telegram не ответил: {exc}{self._progress(number, parts)}"
                )) from None
            except requests.RequestException as exc:
                raise NotifyError(self._hide(
                    f"Telegram не ответил: {exc}{self._progress(number, parts)}"
                )) from None
            if getattr(answer, "ok", False):
                return
            wait = _retry_after(answer)
            if wait is not None and not last:
                # Час ожидания в команде по расписанию — зависшая команда.
                # Ждём не дольше минуты; если Telegram всё ещё занят, он
                # ответит 429 снова, и на последней попытке это отказ словами.
                time.sleep(min(wait, MAX_WAIT))
                continue
            raise NotifyError(self._hide(
                f"Telegram отказал (код {answer.status_code}): {answer.text}"
                f"{self._progress(number, parts)}"
            ))
```

- [ ] **Шаг 4: тесты адаптера целиком**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_notify_telegram.py`
Ожидается: PASS. `test_a_network_failure_does_not_leak_the_token` бросает
`requests.ConnectionError` на каждый вызов — теперь их будет четыре вместо
одного, и отказ прежний. Если тест считает вызовы — поправь число и запиши.

- [ ] **Шаг 5: батарея и коммит**

Запуск: `.venv/Scripts/python.exe -m pytest -q` → 817 passed, 25 skipped.

```bash
git add listam/adapters/notify_telegram.py tests/test_notify_telegram.py
git commit -m "fix(telegram): 429 выжидается, и доставленные части не приходят дважды"
```

### Конец фазы 6

- [ ] Миграция 011 на копии базы приёмки: числа `notifications` до и после
      (`count(*)`, `max(id)`), `channel` у старых строк (ожидается `NULL`
      у всех), время `migrate()`.
- [ ] `notify.kind: none` на копии → `notify --hot`: текст и `count(*)`
      журнала до и после.
- [ ] Живого Telegram в этой фазе нет. 429 живьём не вызывается нарочно — он
      закрыт модульным тестом; запиши это в отчёт как «не проверено живьём».
- [ ] «Результат фазы 6», стартовый промпт фазы 7, чисто, коммит.

---

# Фаза 7. Долг: мёртвая ветка, одно соединение, пометка без разбора текста

**Одна сессия.** Закрывает **R-1**, **R-2**, **R-3** и **L-1**. Поведение для
брокера не меняется, кроме L-1: у каждой части разрезанного раздела есть шапка.

**Ожидается после фазы:** ~820 passed, 25 skipped, схема базы 11.

### Задача 7.1. `working_session` без `needs_schema`

**Файлы:**
- Изменить: `listam/runner.py`, `listam/recheck.py:57`,
  `listam/clustering_run.py:96`, `tests/test_runner.py:76`

- [ ] **Шаг 1: убедиться, что ветка мертва**

Запуск: `grep -n "needs_schema" -r listam tests`
Ожидается: `runner.py` (подпись, docstring, `if needs_schema:`), `recheck.py:57`,
`clustering_run.py:96`, `tests/test_runner.py:76`. `migrate()` стоит в
`working_session` **до** проверки — после него `schema_version()` равна
`latest_schema_version()` всегда, и отказ «накати миграции» не срабатывает
никогда. Это записано фазой 6 M3 («отказ „схема старая“ в каркасе не
срабатывает никогда»).

- [ ] **Шаг 2: удалить ветку**

```python
# listam/runner.py
@contextmanager
def working_session(config: Config) -> Iterator[Session]:
    """Открытая база под замком. Отказ — `SessionRefused` с внятной причиной.

    Миграции катятся здесь, у каждой команды на каркасе: база, отставшая на
    версию, догоняется молча (README, «Кто мигрирует базу»). Отказ по схеме
    остался только у тех, кто базу читает и не пишет, — у витрины, выгрузки,
    `changes` и `doctor`.
    """
```

Блок `if needs_schema: … raise SessionRefused(…)` удалить целиком. Импорт
`latest_schema_version` удалить, если он больше не нужен. В заголовочном
docstring модуля «проверить, что схема не старше кода» заменить на «накатить
миграции».

В `recheck.py`, `clustering_run.py` и `tests/test_runner.py` —
`working_session(config, needs_schema=False)` → `working_session(config)`.

- [ ] **Шаг 3: батарея и коммит**

Запуск: `.venv/Scripts/python.exe -m pytest -q` → 817 passed, 25 skipped.

```bash
git add listam/runner.py listam/recheck.py listam/clustering_run.py tests/test_runner.py
git commit -m "refactor(runner): без мёртвой ветки needs_schema"
```

### Задача 7.2. Уведомление читает базу своей сессии

**Файлы:**
- Изменить: `listam/matches_view.py` (`collect_events`), `listam/notifications.py`
- Тест: `tests/test_notifications.py`

**Interfaces — Produces:**
```python
collect_events(config, *, since, until, external_id=None, min_score=None,
               note="", include_retired=True, database: Database | None = None)
```

- [ ] **Шаг 1: падающий тест**

```python
def test_the_notification_reads_through_its_own_session(prepared, monkeypatch):
    """Под замком одна база — одно соединение. Второе, открытое мимо сессии,
    видело бы файл, а не то, что сессия в нём держит."""
    import listam.matches_view as view

    opened = []
    real = view.open_for_reading
    monkeypatch.setattr(view, "open_for_reading",
                        lambda *args, **kwargs: opened.append(args) or real(*args, **kwargs))

    run_notify(prepared, kind="hot", dry_run=True)

    assert opened == []
```

- [ ] **Шаг 2: убедиться, что тест падает**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_notifications.py -k own_session`
Ожидается: FAIL — `assert [(…)] == []`.

- [ ] **Шаг 3: база приходит аргументом**

```python
# listam/matches_view.py — collect_events
def collect_events(config: Config, *, since, until,
                   external_id: str | None = None,
                   min_score: float | None = None,
                   note: str = "",
                   include_retired: bool = True,
                   database: Database | None = None) -> EventsPage:
```

```python
    own = database is None
    if own:
        database = open_for_reading(config, "События")
    try:
        ...                                   # тело без изменений
    finally:
        if own:
            database.close()
```

Docstring дополнить: «`database` — открытая база сессии: уведомление под
замком читает ею, а не вторым соединением мимо сессии».

```python
# listam/notifications.py — в run_notify
                page = collect_events(config, since=since, until=until,
                                      min_score=min_score, note=scope,
                                      include_retired=knobs.include_retired,
                                      database=database)
```

- [ ] **Шаг 4: батарея и коммит**

Запуск: `.venv/Scripts/python.exe -m pytest -q` → 818 passed, 25 skipped.

```bash
git add listam/matches_view.py listam/notifications.py tests/test_notifications.py
git commit -m "refactor(notify): события читаются соединением сессии, а не вторым"
```

### Задача 7.3. Пометку широкой заявки ставит витрина

**Файлы:**
- Изменить: `listam/matches_view.py` (`render_events`), `listam/notifications.py`
  (`_match_text`)
- Тест: `tests/test_matches_view.py`

**Interfaces — Produces:**
```python
render_events(page, per_request, head="Что нового", wide: int | None = None) -> str
```

- [ ] **Шаг 1: падающий тест**

```python
# tests/test_matches_view.py — в конец
def test_the_view_marks_a_wide_request_by_itself():
    """Пометку ставит тот, кто печатает раздел, а не разбор уже напечатанного
    текста: разбор по строкам однажды уже вешал её на соседа `R-11`."""
    from listam.domain.events import NEW, MatchEvent
    from listam.domain.models import Listing, Match, Request
    from listam.matches_view import EventsPage, render_events

    request = Request(id=1, external_id="R-1", client_name="Ани")
    events = [MatchEvent(kind=NEW,
                         match=Match(request_id=1, listing_id=str(index), score=80.0),
                         listing=Listing(id=str(index), url=f"https://x/{index}",
                                         district="Кентрон", price_usd=100000.0))
              for index in range(3)]
    page = EventsPage(events=events, totals={"R-1": 3}, requests={"R-1": request})

    text = render_events(page, per_request=10, wide=2)

    assert "слишком широкая: 3 событий" in text
    assert render_events(page, per_request=10, wide=3).count("слишком широкая") == 0
```

- [ ] **Шаг 2: убедиться, что тест падает**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_matches_view.py -k wide_request_by_itself`
Ожидается: FAIL — `TypeError: render_events() got an unexpected keyword argument 'wide'`.

- [ ] **Шаг 3: пометка в самой витрине**

```python
# listam/matches_view.py — render_events: подпись
def render_events(page: EventsPage, per_request: int | None,
                  head: str = "Что нового", wide: int | None = None) -> str:
```

Сразу после строки `lines.append(f"Заявка {request.external_id or key}{who} — {counts}")`:

```python
        if wide is not None and len(alive) > wide:
            # Широту мерят события, а закрытие событием не является (решение 1
            # спеки M3): сотни «отпало» — ответ на сужение заявки.
            lines.append(
                f"  ⚠ заявка слишком широкая: {len(alive)} событий за окно. "
                f"Сузь районы или бюджет, иначе разговор не состоится"
            )
```

Docstring дополнить: «`wide` — сколько событий у заявки считается нормой;
больше — раздел помечается (решение 6 спеки M3)».

```python
# listam/notifications.py — _match_text целиком
def _match_text(page, knobs: NotifyTuning, kind: str) -> str:
    """Текст уведомления по заявкам. Широкую заявку помечает сама витрина.

    Пометка только в дайджесте: «горячее» отвечает на «кому звонить сейчас»,
    и совет «сузь заявку» ему не по размеру.
    """
    head = "Звони сейчас" if kind == "hot" else "Что нового со вчера"
    return render_events(page, per_request=knobs.per_request, head=head,
                         wide=knobs.wide_request if kind == "digest" else None)
```

Импорт `RETIRED` в `notifications.py` удалить, если больше не нужен.

- [ ] **Шаг 4: батарея и коммит**

Запуск: `.venv/Scripts/python.exe -m pytest -q` → 819 passed, 25 skipped.
Обязаны остаться зелёными `test_a_wide_request_is_marked`,
`test_closures_do_not_make_a_request_wide`,
`test_a_wide_mark_does_not_stick_to_a_neighbour` — они держат поведение.

```bash
git add listam/matches_view.py listam/notifications.py tests/test_matches_view.py
git commit -m "refactor(notify): широкую заявку помечает витрина, а не разбор текста"
```

### Задача 7.4. У каждой части раздела — шапка заявки

**Файлы:**
- Изменить: `listam/adapters/notify_telegram.py` (`split_message`)
- Тест: `tests/test_notify_telegram.py`

- [ ] **Шаг 1: падающий тест**

```python
def test_a_section_cut_in_parts_keeps_its_title_on_every_part():
    """L-1 аудита: вторая часть длинного раздела уходила без имени заявки, а
    шапка «Что нового» — отдельным сообщением. Брокер пересылает часть
    клиенту — и клиент получает кусок без контекста."""
    section = "Заявка R-7 (Давид) — новый: 40\n" + "\n".join(
        f"  • {index:03d} " + "x" * 150 for index in range(40))

    parts = split_message("Что нового\n\n" + section)

    assert parts[0].startswith("Что нового\n\nЗаявка R-7")
    assert all(part.startswith(("Что нового", "Заявка R-7")) for part in parts)
    assert all(len(part) <= LIMIT for part in parts)
    assert "\n".join(parts).count("• 039") == 1
```

- [ ] **Шаг 2: убедиться, что тест падает**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_notify_telegram.py -k keeps_its_title`
Ожидается: FAIL — `parts[0] == 'Что нового'`.

- [ ] **Шаг 3: шапка к первой части, заголовок — к продолжениям**

```python
# listam/adapters/notify_telegram.py — split_message целиком
CONTINUED = " (продолжение)"


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    """Сообщение, разрезанное по разделам так, чтобы ни одна строка не разорвалась.

    Шапка (первый блок, не начинающийся с «Заявка») едет с первой частью
    первого раздела: одна строка «Что нового со вчера» отдельным сообщением —
    шум. Раздел длиннее лимита режется по строкам, и каждое продолжение
    начинается с заголовка заявки: брокер пересылает часть клиенту, и кусок
    без имени заявки — это чужой разговор.
    """
    blocks = [block for block in text.split("\n\n") if block.strip()] or [text]
    head = blocks.pop(0) if len(blocks) > 1 and not blocks[0].startswith("Заявка") else None

    parts: list[str] = []
    for block in blocks:
        parts.extend(_split_section(block, limit))
    if head is not None:
        if parts and len(head) + 2 + len(parts[0]) <= limit:
            parts[0] = f"{head}\n\n{parts[0]}"
        else:
            parts.insert(0, head)
    return parts


def _split_section(block: str, limit: int) -> list[str]:
    """Раздел заявки: целиком — или по строкам, с заголовком на каждой части."""
    if len(block) <= limit:
        return [block]
    title = block.splitlines()[0][: limit // 4] + CONTINUED
    pieces = _split_lines(block, limit - len(title) - 1)
    return [pieces[0]] + [f"{title}\n{piece}" for piece in pieces[1:]]
```

Шапка «Что нового» влезет к первой части почти всегда: первая часть режется
под `limit − len(title) − 1`, то есть с запасом в длину заголовка. Если не
влезла — уходит отдельно, как раньше; это не ошибка, а длинная шапка.

- [ ] **Шаг 4: тесты адаптера, батарея, коммит**

Запуск: `.venv/Scripts/python.exe -m pytest -q tests/test_notify_telegram.py`,
затем `.venv/Scripts/python.exe -m pytest -q` → 820 passed, 25 skipped.
Если `test_a_section_longer_than_the_limit_is_cut_between_lines` проверяет
точное содержимое второй части — поправь ожидание на «заголовок + строки» и
запиши.

```bash
git add listam/adapters/notify_telegram.py tests/test_notify_telegram.py
git commit -m "fix(telegram): у каждой части длинного раздела — шапка заявки"
```

### Конец фазы 7

- [ ] `notify --digest --dry-run` на копии базы приёмки: число частей
      (`split_message` на тексте отчёта) и первая строка каждой — сравни
      с «41 часть» приёмки M3.
- [ ] «Результат фазы 7», стартовый промпт фазы 8, чисто, коммит.

---

# Фаза 8. Боевая приёмка и разбор

**Одна сессия.** Ни одной новой возможности. Каждая находка проверяется на
**копии** базы приёмки: то, что нельзя показать живьём, не считается
исправленным — и пишется в отчёт как «закрыто тестом, живьём не проверено».

**Ожидается после фазы:** батарея без изменений (~820 passed, 25 skipped),
схема базы приёмки 11.

### Задача 8.1. Копия базы и миграция 011

- [ ] **Шаг 1: копия, а не оригинал**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "import shutil; shutil.copy('data/listam-m3.sqlite', 'data/listam-before-qa-m3.sqlite')"
```

Если ключи Google Drive появились (`GDRIVE_FOLDER`, `GDRIVE_CREDENTIALS_FILE`
в `.env` не пусты) — приёмка идёт на **боевом** файле (его копии), и это
пишется в отчёт первой строкой; рецепт восстановления больше не нужен. Если
нет — база приёмки M3, и это тоже первой строкой.

- [ ] **Шаг 2: миграция на копии, числа до и после**

`SqliteDatabase(<копия>)`, `migrate()`: `schema_version() == 11`; число
объявлений, матчей и строк журнала до и после; `channel IS NULL` у всех
старых строк журнала; время `migrate()`.

Конфиг приёмки — как в фазе 7 M3: копия `config/` в scratchpad
(`<SCRATCH>/config` с `notify.kind: stdout` и `<SCRATCH>/config-tg` с
`notify.kind: telegram` и ссылками `${TELEGRAM_BOT_TOKEN:-}`/`${TELEGRAM_CHAT_ID:-}`).

### Задача 8.2. Проверки живьём

Каждая — команда и ожидаемый ответ. Записывай, что увидел на самом деле.

- [ ] **B-1.** Поднять цену сматченного объявления на копии, `match --all`,
      `notify --hot --dry-run` → в тексте нет «отпало» и «только закрытия».
- [ ] **B-2.** Двойник дешевле у сматченного объявления, `match --all`,
      `matches --new --hours 1` → «подешевело с …», «новый» по этому
      кластеру — нет.
- [ ] **B-3.** Две рабочие папки на одно хранилище: `notify --digest` на B
      (канал `stdout`), `notify --digest --dry-run` на A → «событий нет» и
      «удалённая копия свежее».
- [ ] **H-1.** Снизить цену (`upsert_listing` на копии), `notify --digest`
      (`stdout`, окно сдвигается) **до** `match`, затем `match --new`, затем
      `notify --digest --dry-run` → подешевевший пришёл в первом и не пришёл
      во втором.
- [ ] **H-2.** Объявление с `price_per_sqm` ниже 0,75 медианы района,
      цена −10 %, `match --all` (у этого матча `unchanged`) →
      `matches --new --hours 1` показывает «подешевело с …».
- [ ] **H-3.** `notify.kind: none` → `notify --hot` → «никуда не идут»,
      `count(*)` журнала не изменился.
- [ ] **M-1.** `include_retired: false` → `notify --digest --dry-run` без «отпало».
- [ ] **M-2.** `match.thresholds.hot: null` → `notify --hot` → «порог … выключен».
- [ ] **M-3.** `enabled: "false"` → код 2 с именем ключа; `doctor` — СБОЙ.
- [ ] **M-4.** `matches --new` на копии схемы 7 (`data/listam-before-m3.sqlite`
      в конфиге-копии) → код 1, «схема базы 7»; на отсутствующем файле →
      «базы нет», файл не появился.
- [ ] **M-5.** «Событий: N (и отпало M)» в дайджесте после закрытий из B-1.
- [ ] **M-6.** `TELEGRAM_BOT_TOKEN= TELEGRAM_CHAT_ID= … --env prod changes`
      стартует; `… --env prod notify --hot` — код 2.
- [ ] **L-1.** `notify --digest --dry-run`: первые строки частей.
- [ ] **H-4, H-5** — закрыты модульными тестами; 429 и полный диск живьём не
      вызываются нарочно. Записать как «живьём не проверено».

### Задача 8.3. Живой канал — один раз

- [ ] **Шаг 1: показать, что уйдёт**

`notify --digest --dry-run` на `config-tg`. Сохранить текст в scratchpad.

- [ ] **Шаг 2: одна живая отправка**

`notify --digest` на `config-tg` → код 0, «отправлено»; текст совпал с сухим
прогоном (`diff`); в журнале новая строка с `channel = 'telegram'`.
Повтор той же командой → «событий нет».

- [ ] **Шаг 3: окно Telegram не сдвинуто консолью**

Перед шагом 2 — `notify --digest` на `config` (`stdout`). Шаг 2 обязан
прислать то же, что сухой прогон шага 1, а не «событий нет». Запиши число
сообщений, ушедших брокеру в личку за фазу.

### Задача 8.4. README и закрытие

- [ ] **Шаг 1: README**

Дописать в `README.md`: «горячее» не приносит закрытий; «подешевел» мерится
историей цен и приходит и тогда, когда балл не сдвинулся; карточка дешевле
той же квартиры — «подешевел», а не «новый»; журнал помнит канал, `none` окно
не двигает, консоль не двигает Telegram; `${VAR:-}` — необязательный секрет;
свежесть копии — последняя запись; команды на каркасе мигрируют сами (уже
есть — проверь, что не противоречит правке 7.1).

- [ ] **Шаг 2: батарея и коммит**

```bash
.venv/Scripts/python.exe -m pytest -q
git add README.md docs/superpowers/plans/2026-09-23-qa-hardening-after-m3.md
git commit -m "docs: приёмка QA-ужесточения после M3 и разбор находок"
```

### Конец фазы 8

- [ ] «Результат фазы 8»: таблица «находка → как проверена → что увидели»,
      числа базы приёмки, числа батареи, сколько сообщений ушло в личку.
- [ ] «Что осталось открытым» — находки, которые решили не чинить, с причиной
      и адресом.
- [ ] **Стартовый промпт плана M4** — переписать с тем, что в
      `docs/superpowers/plans/2026-09-22-m3-notifications.md` («Стартовый
      промпт плана M4»): пункты 3 («Событий: N» считает закрытия) и 5
      (мёртвая ветка `needs_schema`) закрыты этим планом и из промпта
      уходят; пункты 1, 2, 4 (пустой `hot`, раздел «только закрытия»,
      подешевевший в хвосте) и живой тест при непрочитанном хвосте чата
      остаются; добавить всё, что фаза 8 оставила открытым. Исходное
      состояние — HEAD, батарея и схема **после** этой фазы.
- [ ] `git status --short` — чисто; коммит сделан.

---

## Шаблон стартового промпта (каждая фаза дописывает свой)

```
Ты продолжаешь работу над инструментом мониторинга list.am
в C:\Users\Admin\Downloads\list.

Прочитай docs/superpowers/plans/2026-09-23-qa-hardening-after-m3.md:
разделы «Что нашёл аудит», «Global Constraints», «Карта файлов»,
«Результат фазы <N-1>» и свою «Фазу <N>». Чужие фазы не трогай. Рядом
лежат спеки M2 и M3 — их решения в силе:
docs/superpowers/specs/2026-09-22-m3-notifications-design.md (решения 1–12),
docs/superpowers/specs/2026-09-22-m2-requests-and-matching-design.md
(решения 1–11). Если фаза одно из них уточняет, это прямо написано
в её заголовке.

Исходное состояние: HEAD <хэш>, дерево чистое, батарея <N> passed,
<M> skipped, схема базы <версия>. База для замеров — копия
data/listam-m3.sqlite в scratchpad, не оригинал.

Твоя задача — фаза <N>: <одна фраза о смысле фазы>.
<Три-четыре строки о том, что именно делается и почему это одно целое.>

Работай по шагам задач: на каждое поведение — падающий тест ДО правки.
Тесты гоняй только .venv/Scripts/python.exe -m pytest -q, любой прогон CLI
из скрипта — только с PYTHONIOENCODING=utf-8. Пороги в конфиге не поднимай.
Новый метод порта — это новый контрактный тест в tests/contracts/.
Схему меняет только миграция 011 (фаза 6); своих не заводи.
След звонка (matches.status, matches.reject_reason) не трогает ничто,
MATCH_COMPARED не расширяется. В Telegram — только фаза 8, одним шагом.
Ни одного числа в отчёте без команды, которая его напечатала.

В конце сессии допиши в план раздел «Результат фазы <N>»: что сделано, числа
батареи, что разошлось с планом и почему, и стартовый промпт для фазы <N+1>.
Сделай коммит.
```

---

## Что в этот план не входит

- **Решения про продукт, адресованные спеке M4** приёмкой M3: слать ли пустой
  `notify --hot`; собирать ли разделы «только закрытия» в одно сообщение;
  тонет ли подешевевший в хвосте при сортировке по баллу (решение 5 спеки M3).
  Это вопросы «как должно быть», а не «работает ли как обещано».
- **Слияние журналов двух разошедшихся копий.** Фаза 4 выбирает копию с
  последней записью; если писали обе машины, то, что было только в ранней,
  теряется. Слияние строк двух sqlite-файлов — отдельный дизайн.
- **Повтор части после обрыва чтения.** Таймаут после отправки не повторяется
  нарочно (часть могла уйти); отказ на середине пачки по-прежнему даёт
  повтор уже доставленных частей следующим запуском — отчёт об этом говорит.
- **Старые строки журнала.** `events` у строк до фазы 1 считали закрытия;
  пересчитывать историю незачем — окно от этого не зависит.
- **Живой тест длинного сообщения при непрочитанном хвосте чата** — адрес M4,
  как записала приёмка M3: проверить правку можно только десятками сообщений
  в личку.
- **Выравнивание колонок в Telegram, разметка, кнопки** — вне M3 и вне этого плана.
- **Телефоны продавцов (M4), дашборд (M5).**
- **`crawler` на общем каркасе** — решение фазы 7 QA после M2, в силе.
- **Живая проверка `gsheet`** — решение 11 спеки M2, в силе.
