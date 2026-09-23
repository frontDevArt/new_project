# M3.5. Боевой запуск: план реализации

> **Для агента-исполнителя:** одна фаза — одна сессия. В начале сессии читаешь
> «Global Constraints», «Карта файлов», «Долги», «Результат фазы N−1» и свою
> фазу; чужие фазы не трогаешь. Рядом лежит спека —
> `docs/superpowers/specs/2026-09-23-m3-5-live-launch-design.md`, решения 1–18
> в фазах не пересматриваются. В конце сессии дописываешь в этот файл раздел
> «Результат фазы N» и стартовый промпт для следующей фазы, затем делаешь коммит.
> Шаги помечены `- [ ]` — отмечай по ходу. Исполнять план помогает скилл
> `superpowers:executing-plans` или `superpowers:subagent-driven-development`.
>
> План нарочно плотнее планов M2 и M3: код приведён только там, где он —
> договор (миграция, порты, конфиг, модель сообщения). Остальное — поведение,
> имя теста и файл. Тест пишется до правки, как и раньше.

**Цель:** система каждый день сама обходит ленту и шлёт брокеру уведомления;
«звони сейчас» приносит единицы вариантов, а не сотни; из сообщения за секунду
понятно, кому звонить и про какую квартиру.

**Архитектура:** ядро не переписывается. Появляются: цикл из конфига и
установщик расписания (`schedule`), вёрстка сообщения структурой (`layout`),
шаг воронки, открывающий страницы кандидатов (`pages`), кэш страниц и
исключения из отказов (миграция 012), седьмой фактор балла `wishes`,
быстрый поиск `find` и отметки `mark`.

**Стек:** Python 3.14 (`.venv`), SQLite, pytest, `requests`, Playwright
(уже в `requirements-playwright.txt`), PyYAML, openpyxl. Новая зависимость
одна — `tzdata` (фаза 1): `zoneinfo` на Windows без неё не знает часовых поясов.

**Исходное состояние (снято 23.09.2026):**

| Что | Значение |
| --- | --- |
| HEAD | `e12ef68`, дерево чистое |
| Батарея | **824 passed, 25 skipped** (44,4 с) |
| Схема базы | 11 |
| `.env` | Telegram заполнен; `GDRIVE_*`, `REQUESTS_SHEET` пусты |
| Боевая база | нет: `data/listam.sqlite` — пустой файл 4 КБ, `runs` пуст |
| Расписание | не настроено |

---

## Global Constraints (нарушать нельзя)

- Схема базы меняется **только** миграцией **012** (фаза 3). Понадобилась
  вторая — это находка в отчёт фазы, а не файл `013`.
- Новый метод порта — новый контрактный тест в `tests/contracts/`.
- Ни один аккаунт, путь, ключ, идентификатор и имя адаптера не зашит в код:
  внешнее — за портом, выбор — в конфиге, секреты — только в `.env`,
  подключение — в `listam/wiring.py`. Путь к интерпретатору и к проекту
  расписание вычисляет при установке (`sys.executable`, корень пакета),
  в репозиторий и README они не пишутся.
- Все отметки времени в базе — UTC (`to_iso`/`from_iso`). Человеческое время —
  через `locale.timezone`.
- **robots.txt:** никаких query-параметров фильтров. Лента — путевая
  пагинация, объявление — `/item/<id>`. Агент сам на list.am не ходит
  (WebFetch и подобное запрещены): страницы берёт только инструмент своим
  `Fetcher`.
- **Воронка:** страница объявления открывается только для кандидата под живую
  заявку, с паузой `funnel.delay_seconds` и потолком `funnel.max_opens_per_run`.
  Никакого массового обхода. Открытая страница кэшируется в `listing_pages`.
- **Вёрстка Telegram — строго по пункту 8** `docs/анализ-после-M3.md`: карточка
  из трёх строк, словарь значков (новых нет), HTML, резать только между
  карточками.
- Пороги не поднимаются, чтобы тест позеленел. Исключение — фаза 4, и каждое
  изменённое число приходит с замером.
- Ноль в пороге — «ноль», выключается `null`; бессмысленное значение — код 2
  на входе. Читать пороги — через `listam.config.threshold` / `positive` /
  `score_threshold` / `switch` / `hours`.
- След звонка (`matches.status`, `matches.reject_reason`) пишет только
  человек — командой `mark` (фаза 6). `MATCH_COMPARED` не расширяется:
  `origin` пишется при вставке и не сравнивается.
- Тесты — только `.venv/Scripts/python.exe -m pytest -q`. Любой прогон CLI
  из скрипта — только с `PYTHONIOENCODING=utf-8`.
- `listam/crawler.py` правится только там, где это прямо названо.
- **Живые проверки Playwright не исполняются** — они вписываются в раздел
  «Долги» (Д-2) с командой запуска. Отправлять в Telegram можно;
  читать отправленное обратно — нет.
- **Реальных заявок нет — работаем на примере** (раздел «Пример заявок»).
  Замер на примере помечается «предварительно» и попадает в долг Д-1.
- Ни одного числа в отчёте фазы без команды, которая его напечатала.
- Если для живого запуска чего-то не хватает (ключ, доступ, решение) —
  спроси пользователя, обходной путь не выдумывай.
- Числа «ожидается N passed» — арифметика от 824 плюс тесты фазы. Разошлось
  на один-два — не повод подгонять: сверь и поправь число в плане.

## Карта файлов

| Файл | Ответственность | Фаза |
| --- | --- | --- |
| `tests/test_matching.py`, `tests/test_events.py` | даты из календаря → от настоящих часов | 1 |
| `listam/notifications.py` | пустое «горячее» не шлётся; вёрстка; `origin` | 1, 2, 4 |
| `config/prod.yaml`, `config/dev.yaml` | `locale`, `schedule`, `funnel`, `feedback`, `notify.layout`, `wishes` | 1–6 |
| `listam/schedule.py` | цикл, лог, тревога, задачи `schtasks` / `crontab` | 1 |
| `listam/adapters/run_lock.py` | `busy()` — занят ли замок, не захватывая его | 1 |
| `listam/doctor.py` | строки «Расписание», «Воронка» | 1, 3 |
| `requirements.txt` | `tzdata` | 1 |
| `listam/layout.py` | `Message`, `Section`, карточка, HTML и простой текст | 2 |
| `listam/ports/notifier.py` | `send(message, to=None)` | 2 |
| `listam/adapters/notify_telegram.py` | HTML, превью выключено, резка между карточками | 2 |
| `listam/migrations/012_funnel.sql` | `listing_pages`, `request_exclusions`, `matches.origin` | 3 |
| `listam/domain/models.py` | `PageFields`, `ListingPage`, `Exclusion`, `Match.origin` | 3 |
| `listam/ports/database.py`, `listam/adapters/db_sqlite.py` | кэш страниц, исключения, `origin` | 3, 6 |
| `listam/parsers/item_page.py` | разбор страницы объявления | 3 |
| `tests/fixtures/item-*.html` | 2–3 настоящие страницы, снятые инструментом | 3 |
| `listam/domain/wishes.py` | словарь пожеланий | 3 |
| `listam/domain/scoring.py` | `must_have`, фактор `wishes`, исключения | 3, 4, 6 |
| `listam/pages.py` | шаг воронки | 3 |
| `listam/matching.py` | страницы в подборе, `origin`, «страница не открыта» не закрывает | 3, 4 |
| `listam/requests_sync.py` | неизвестное слово пожелания | 3 |
| `listam/find.py` | быстрый поиск | 5 |
| `listam/feedback.py` | `mark`, исключения | 6 |
| `listam/cli.py` | `cycle`, `schedule`, `pages`, `find`, `mark` | 1, 3, 5, 6 |
| `README.md`, `docs/устройство.md` | руководство оператора; подробности — в `docs/` | 1–7 |

---

## Пример заявок (пока нет реальных — долг Д-1)

Три синтетические заявки. Клиенты помечены словом «ПРИМЕР», чтобы брокер
не спутал их уведомления с настоящими. Кладутся в `data/requests.csv`
(папка `data/` не коммитится):

```csv
id,client_name,client_phone,status,budget_max,budget_stretch,districts,districts_priority,rooms,area_min,area_max,floor_min,floor_max,no_first_floor,no_last_floor,must_have,nice_to_have,floor_rules,notes
R-1,ПРИМЕР узкая,,active,120 000 $,,Арабкир,Арабкир,3,70,95,2,,да,нет,,,,без пожеланий: страницы не нужны
R-2,ПРИМЕР средняя,,active,175 000 $,190000,"Канакер-Зейтун, Нор Норк",Канакер-Зейтун,2-3,80,110,,,да,да,ремонт,"балкон, лифт",,пожелания со страницы
R-3,ПРИМЕР широкая,,active,250 000 $,,"Кентрон, Арабкир, Давташен",Кентрон,2-4,60,,,,нет,нет,не панель,евроремонт,,нарочно широкая: проверка первичной подборки
```

Когда пользователь пришлёт реальные заявки: они заменяют пример в
`data/requests.csv`, `python -m listam --env prod requests` закрывает R-1…R-3
(«закрыты (нет в источнике)»), а замеры фазы 4 повторяются на живых.

## Долги (ведутся всем планом)

Исполнитель **дописывает** сюда, а не исполняет. Прогоняет пользователь.

| # | Долг | Чей | Как закрыть |
| --- | --- | --- | --- |
| Д-1 | Реальные заявки клиентов | пользователь | прислать строки CSV по образцу выше; повторить замер фазы 4 |
| Д-2 | Живые проверки Telegram через Playwright | пользователь | `TELEGRAM_LIVE=1 .venv/Scripts/python.exe -m pytest -q tests/live -s`; список проверок — ниже |
| Д-3 | Компьютер не засыпает | пользователь | «Электропитание» → сон «Никогда» при питании от сети; задачи идут с `/it` — пользователь должен быть залогинен |
| Д-4 | Переезд на Linux вживую не проверен | пользователь | на сервере: `git clone`, `.env`, `pip install`, `python -m listam schedule show`, `schedule install` |
| Д-5 | «За секунду понятно» глазами брокера | пользователь | неделя фазы 7: отзыв брокера о сообщениях |

Живые проверки Д-2 (дописываются фазами):

| # | Что проверить глазами в Telegram Web | Фаза |
| --- | --- | --- |
| Т-1 | карточка из трёх строк, цена жирным, «Открыть» — ссылка, превью нет | 2 |
| Т-2 | длинная заявка режется между карточками, продолжение — с шапкой | 2 |
| Т-3 | символы `<`, `>`, `&` в адресе объявления не ломают сообщение | 2 |
| Т-4 | тревога цикла приходит одной строкой и не чаще `alert_every_hours` | 1 |
| Т-5 | L-1, H-4, H-5 приёмки M3 (продолжение раздела, 429, журнал не записан) | из M3 |

---

# Фаза 1. Боевой запуск на нынешнем коде

**Смысл:** с конца этой фазы система работает сама на Windows, и история цен
начинает копиться. Всё остальное в M3.5 доводится поверх живого прогона.

**Ожидается после фазы:** ~850 passed, 25 skipped, схема 11.

### Задача 1.1. Тесты не зависят от календаря

Приёмка QA после M3 нашла «бомбы»: `datetime(2026, 9, 23, …)` в
`tests/test_matching.py` (строки ~161, 427, 450, 482, 538, 578, 598) и
`tests/test_events.py` (~65). Сравниваются с настоящими часами — завтра упадут.

- [ ] `grep -n "datetime(2026" tests/` — список мест; для каждого решить,
  сравнивается ли дата с `now()` кода. Сравнивается — заменить на
  `datetime.now(timezone.utc) + timedelta(…)`. Не сравнивается — оставить.
- [ ] Оставшимся календарным датам — пометка в строке
  `# календарь: не сравнивается с часами`.
- [ ] Сторож `test_no_calendar_dates_against_real_clock` в `tests/test_docs.py`:
  ищет `datetime(20` в `tests/` и падает на строке без этой пометки. Новая
  бомба не проскочит.

### Задача 1.2. Пустое «звони сейчас» не шлётся (решение 5)

- [ ] Тест `test_an_empty_hot_is_not_sent_but_moves_the_window`
  (`tests/test_notifications.py`): окно без событий, `kind="hot"`, канал —
  подменный `Notifier`; `send` не зван, строка журнала `events=0` есть,
  повтор берёт окно от неё.
- [ ] Тест `test_an_empty_digest_is_still_sent`: у дайджеста «событий нет» —
  законный ответ раз в сутки.
- [ ] Правка `run_notify`: `kind == "hot" and report.events == 0` → не
  звать `send`, писать журнал, `report.notes = "событий нет — не отправлено"`.

### Задача 1.3. Боевой конфиг без облака

- [ ] Тест `test_prod_config_loads_without_google_keys` (`tests/test_config.py`):
  `load_config(env="prod")` при пустых `GDRIVE_*` не падает.
- [ ] `config/prod.yaml`:

```yaml
storage:
  kind: local                   # Drive — этап M3.6 (решение пользователя)
  directory: ./data/store       # общая копия базы и бэкапы
  work_dir: ./data
  db_filename: listam-prod.sqlite   # не делит рабочую копию с dev
requests:
  kind: csv
  path: ./data/requests.csv
locale:
  timezone: Asia/Yerevan
schedule:
  log_dir: ./data/logs
  keep_logs_days: 14
  task_prefix: listam
  alert_on_failure: true
  alert_every_hours: 6
  linux_xvfb: true
  cycles:
    hourly:  {every_minutes: 60, steps: ["scrape --fresh", "match --new", "notify --hot"]}
    nightly: {at: "04:00", steps: ["scrape", "match --all", "notify --feed"]}
    evening: {at: "20:00", steps: ["notify --digest"]}
notify:
  feed:
    enabled: true
```

  `dev.yaml` получает те же секции `locale` и `schedule` с `task_prefix: listam-dev`.
- [ ] `requirements.txt` + `tzdata`; `.venv/Scripts/pip install tzdata`.

### Задача 1.4. `listam cycle` (решения 3, 4)

Договор модуля:

```python
# listam/schedule.py
@dataclass
class Cycle:
    name: str
    steps: list[str]                 # «scrape --fresh» — аргументы CLI без «python -m listam»
    every_minutes: int | None = None
    at: time | None = None           # местное время locale.timezone

def cycles(config: Config) -> dict[str, Cycle]: ...          # проверка на входе, ConfigError
def run_cycle(config: Config, name: str, *, dispatch=None, notifier=None,
              now=None) -> int: ...                           # код возврата цикла
```

- [ ] `test_cycles_are_read_and_checked`: пустой `steps`, неизвестная команда
  шага (`"scarpe"` — разбирается `cli.build_parser()`), `at: "25:00"`, оба
  `every_minutes` и `at`, ни одного — `ConfigError` с именем ключа.
- [ ] `test_a_cycle_stops_at_the_first_failing_step`: подменный `dispatch`
  (шаг → код); второй вернул 1 — третий не зван, код цикла 1.
- [ ] `test_a_busy_lock_skips_the_cycle_quietly`: замок взят — ни одного
  шага, код 0, строка «пропущен: идёт другой прогон» в логе, тревоги нет.
  Для этого `RunLock.busy() -> str | None` (описание держателя; протухший
  замок — `None`) — тест `test_busy_does_not_take_the_lock` в
  `tests/test_concurrency.py`.
- [ ] `test_the_cycle_writes_its_log`: файл `cycle-YYYY-MM-DD.log` в
  `schedule.log_dir`, в нём шапка цикла, вывод каждого шага и код; логи
  старше `keep_logs_days` удаляются.
- [ ] `test_a_failed_cycle_alerts_once_per_window`: два падения подряд —
  одна отправка; отметка — файл `last-alert` в `log_dir` (не база: упавший
  шаг мог быть про базу). Отправка тревоги упала — строка в логе, код цикла
  прежний.
- [ ] Шаг исполняется в том же процессе: `cli.build_parser().parse_args(step.split())`
  и `cli._dispatch(args, config)`; вывод шага — в лог и в консоль
  (`contextlib.redirect_stdout` в тройник). Флаги `--env`/`--config-dir` цикла
  шаги наследуют через уже загруженный `config`.
- [ ] CLI: `listam cycle <имя>`; неизвестное имя — код 2 со списком известных.

### Задача 1.5. `listam schedule show|install|remove`

- [ ] Чистые функции, без системы:
  `windows_tasks(config, root, python, env) -> list[Task]` и
  `cron_lines(config, root, python, env) -> list[str]`. `Task` — имя,
  `schtasks`-аргументы и текст `.cmd`-обёртки.
- [ ] `test_windows_tasks`: `hourly` → `/sc minute /mo 60`; `evening` →
  `/sc daily /st HH:MM` в **местном времени хоста** (Yerevan 20:00 при
  хосте UTC → `16:00`); `/it`, `/f`; команда задачи — обёртка
  `data/schedule/<prefix>-<cycle>.cmd` (`schtasks /tr` не берёт больше
  261 символа), внутри `cd /d "<root>"` и `"<python>" -m listam --env <env> cycle <имя>`.
- [ ] `test_cron_lines`: `0 * * * *`, `0 16 * * *` для хоста в UTC;
  `cd "<root>" && xvfb-run -a "<python>" -m listam --env prod cycle hourly`;
  каждая строка с меткой `# <prefix>:<cycle>`; `linux_xvfb: false` — без
  `xvfb-run`.
- [ ] `test_no_path_is_hardcoded`: в `schedule.py`, `config/*.yaml` и README
  нет `C:\` и `/srv/` (корень и python приходят аргументами).
- [ ] `install`/`remove` зовут систему через внедрённый `run(cmd)`;
  на Linux читают `crontab -l`, убирают строки своей метки, дописывают новые.
  Тест на подменном `run`. Платформа — `sys.platform`; флаг
  `--platform windows|linux` для `show` (посмотреть чужую); `install`
  на чужой платформе — код 2.

### Задача 1.6. `doctor`: строка «Расписание»

- [ ] Тест: последний `fresh` старше 2 ч или последний `full` старше 26 ч —
  `⚠` с датой; прогонов нет — `⚠ по расписанию ещё не работало`. Мерка —
  `runs`, не системный планировщик: так строка одинакова на обеих платформах.

### Задача 1.7. README: расписание

- [ ] Раздел «Как запускать по расписанию» — `schedule show` / `install`,
  циклы из конфига, лог, тревога, переезд на Linux тремя командами.
  Примеры с `C:\Users\Admin\…` удалить. `tests/test_docs.py`
  (`test_the_schedule_sends_the_notifications`, `test_readme_names_every_command_the_cli_has`)
  остаются зелёными: `cycle` и `schedule` — в таблице команд.

### Задача 1.8. Запуск (операции, не код)

- [ ] `data/requests.csv` ← «Пример заявок» (или реальные, если пришли).
- [ ] `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m listam --env prod doctor` —
  без «СБОЙ».
- [ ] `--env prod requests`, затем полный `--env prod scrape` (ориентир —
  215 страниц за ~11 минут; окно браузера откроется — это нормально),
  `match --all`, `notify --hot --dry-run`, `notify --digest --dry-run`.
- [ ] Одна живая отправка: `--env prod notify --digest`. Читать в чате
  не нужно (решение 17).
- [ ] `--env prod schedule show`, затем `schedule install`;
  `schtasks /query /tn listam-hourly /v /fo list` — задача есть.
- [ ] Через час с лишним: в `runs` строка `fresh`, в `data/logs` — лог цикла.
- [ ] Сказать пользователю про Д-3 (сон) и что заявки-примеры шлют
  уведомления с пометкой «ПРИМЕР».

### Конец фазы 1

Отчёт: батарея, что установлено (`schedule show`), числа первого полного
обхода, первая строка `fresh` из `runs` (с командой), текст сухого дайджеста
(первые строки), долги, дописанные в раздел «Долги».

---

# Фаза 2. Вёрстка Telegram по пункту 8

**Смысл:** брокер за секунду понимает, кому звонить. Порт уведомления
принимает структуру, а не строку.

**Ожидается после фазы:** ~880 passed, 25 skipped, схема 11.

### Задача 2.1. Модель сообщения

```python
# listam/layout.py
@dataclass
class Span:
    text: str
    bold: bool = False
    href: str | None = None

Line = list[Span]

@dataclass
class Section:              # одна заявка = одно сообщение Telegram
    head: list[Line]        # шапка: повторяется на продолжении с « (продолжение)»
    cards: list[list[Line]] # карточки по три строки; резать можно только между ними
    tail: list[Line] = field(default_factory=list)

@dataclass
class Message:
    sections: list[Section]

def to_html(section: Section) -> str: ...   # экранирует <, >, &; <b>, <a href>
def to_plain(section: Section) -> str: ...  # без тегов; ссылка — «Открыть: <url>»
def plain(message: Message) -> str: ...     # для --dry-run и журнала
```

- [ ] `test_html_escapes_listing_text`: улица `ул. <Раффи> & Co` → `&lt;`, `&amp;`.
- [ ] `test_plain_has_no_tags`; `test_link_is_a_word_not_a_url` (HTML: `<a href="…">Открыть</a>`).

### Задача 2.2. Карточка, шапка, три вида сообщения

- [ ] Карточка события (`card(event, median, config)`), строго три строки:
  1. значок события (🆕 / 📉 / ♻️) · **цена** · площадь · `$/м² (−12% к району)`;
     у 📉 — «(было $171 000)»; у снятого — « · ❌ снято»;
  2. 📍 район, улица · этаж N/M;
  3. 👤 Собственник / 🏢 Агентство · «3 объявления, разброс $8 000» (если кластер > 1) ·
     🟢/🟡/⚪ балл N · 🔗 Открыть.
  Тесты: `test_card_has_three_lines`, `test_cheaper_card_names_the_old_price`,
  `test_score_circle_follows_config` (🟢 ≥ `notify.layout.score_green`, 🟡 ≥
  `match.thresholds.hot`, иначе ⚪), `test_percent_to_district_median` (знак `−`/`+`,
  медианы нет — скобок нет).
- [ ] Шапка заявки: `🔥 ЗВОНИ СЕЙЧАС · R-4 · Клиент 4`, строка заявки
  `Канакер-Зейтун · 2 комн. · 80–110 м² · до $175 000`, `━━━━━━━━━━━━━━━`.
  Незаданное в заявке не печатается.
- [ ] Хвост «горячего»: `➕ ещё 12 вариантов — в дайджесте вечером`. Никаких
  `python -m listam` в тексте сообщения (`test_no_cli_hints_in_messages`).
- [ ] Дайджест: первым сообщением сводка (`📋 ДАЙДЖЕСТ · 23.09 · 20:00` в
  `locale.timezone`; `🆕 новых — N   📉 подешевело — N   ♻️ вернулось — N`;
  `👥 заявок с находками — N из M`; строка на заявку с её счётчиками,
  `⚠️ слишком широкая`, `❌ N` для закрытий). Затем по сообщению на заявку
  со звонками. Заявка только с закрытиями своего сообщения не получает —
  `test_retired_only_request_is_a_summary_line_not_a_message` (решение 8).
- [ ] Лента: `🗞 НА ЛЕНТЕ ЗА СУТКИ`, `🆕 N новых · 📉 N подешевели · ❌ N снято`,
  «Самые выгодные против медианы района:», строки `💎` только у тех, кто
  дешевле медианы на `notify.feed.gem_percent` и больше;
  `test_feed_gem_only_below_threshold`.

### Задача 2.3. Порт `Notifier` принимает `Message`

- [ ] Контракт `tests/contracts/test_notifier_contract.py` переписать:
  `send(Message)` у `none`, `stdout`, `telegram` (последний — под `skipif`
  без токена, как было).
- [ ] `stdout` печатает `plain`. `telegram`: каждая `Section` → `to_html`;
  длиннее 4 096 — режется **между карточками**, продолжение начинается
  с шапки + « (продолжение)»; `parse_mode: "HTML"`,
  `link_preview_options: {"is_disabled": true}`. Прежний `split_message`
  по пустым строкам удаляется вместе со своими тестами — заменяется
  `test_split_only_between_cards`, `test_continuation_repeats_the_head`,
  `test_no_part_exceeds_the_limit`.
- [ ] Тревога цикла (`schedule.py`) шлёт `Message` из одной секции.

### Задача 2.4. `notify` на новой вёрстке

- [ ] `run_notify` собирает `Message` из той же выборки (`collect_events`,
  `_feed_text` → `feed_message`). Журнал `notifications.text` и
  `--dry-run` — `plain(message)`: `test_dry_run_equals_journal_text`.
- [ ] Витрина `matches --new` остаётся терминальной (моноширинная таблица
  для терминала уместна): выборка общая, подача разная (решение 10 спеки M3
  в силе).
- [ ] README, «Уведомления и дайджест»: примеры сообщений из пункта 8,
  словарь значков.

### Задача 2.5. Живой канал

- [ ] `--env prod notify --digest --dry-run` — посмотреть глазами простой текст.
- [ ] Следующий часовой цикл сам уйдёт в новой вёрстке. Отдельной отправки
  не нужно; проверки Т-1…Т-3 уже в «Долгах».

---

# Фаза 3. Двухступенчатая воронка

**Смысл:** `must_have` и `nice_to_have` начинают работать; страница
объявления открывается только для кандидатов.

**Ожидается после фазы:** ~940 passed, 25 skipped, схема **12**.

### Задача 3.1. Разведка (без кода в `listam/`)

- [ ] Скрипт `tmp/recon_item.py` (вне репозитория): `build_fetcher(config)`
  боевого конфига, `get("https://www.list.am/robots.txt")`. Если для `*`
  запрещён `/item/` или `/ru/item/` — **стоп, спросить пользователя**.
- [ ] Тем же `Fetcher` — 3 страницы разных домов (камень, панель, новостройка;
  id — из базы, пауза `funnel.delay_seconds`). Сохранить в
  `tests/fixtures/item-<id>.html`. Телефона на странице нет (он за кнопкой);
  если на странице есть имя продавца — заменить в фикстуре на «Продавец».
- [ ] Выписать в отчёт фазы: все подписи характеристик на странице (как
  на сайте, по-русски), значения, где лежат описание и фото. Сверить
  предварительный словарь спеки (`renovation`, `building_type`, `balcony`,
  `elevator`, `ceiling_height`) и поправить **имена и значения** в спеке
  и конфиге под то, что реально есть. Это единственное место фазы, где
  правится спека.

### Задача 3.2. Миграция 012 и модели

```sql
-- listam/migrations/012_funnel.sql
-- Версия 12: воронка и обратная связь.
-- listing_pages — кэш открытой страницы: разобранные поля, не HTML.
--   price_raw — цена карточки в момент открытия: сменилась — открыть снова.
-- request_exclusions — отказы клиента, сузившие заявку. Таблицу заявок
--   ведёт брокер, синхронизация её не перезаписывает — поэтому здесь.
-- matches.origin — market | request: кто родил матч. «Звони сейчас» — только market.
CREATE TABLE IF NOT EXISTS listing_pages (
    listing_id      TEXT PRIMARY KEY,
    fetched_at      TEXT,
    status          TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    price_raw       TEXT,
    fields          TEXT,
    parser_version  INTEGER NOT NULL DEFAULT 1,
    error           TEXT
);
CREATE TABLE IF NOT EXISTS request_exclusions (
    id          INTEGER PRIMARY KEY,
    request_id  INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    value       TEXT,
    reason      TEXT,
    match_id    INTEGER,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exclusions_request ON request_exclusions(request_id);
ALTER TABLE matches ADD COLUMN origin TEXT;
```

```python
# listam/domain/models.py
@dataclass
class PageFields:
    values: dict[str, object] = field(default_factory=dict)  # renovation → "euro", balcony → True
    description: str | None = None
    photos: list[str] = field(default_factory=list)           # ссылки; для M4

@dataclass
class ListingPage:
    listing_id: str
    status: str                      # ok | gone | failed
    fetched_at: datetime | None = None
    attempts: int = 0
    price_raw: str | None = None
    fields: PageFields | None = None
    error: str | None = None

# Match: origin: str | None = None  # market | request; не в MATCH_COMPARED
```

- [ ] `tests/test_migrations.py`: 11 → 12 на копии, строки не теряются,
  `origin IS NULL` у старых матчей.

### Задача 3.3. Порт: кэш страниц

```python
# listam/ports/database.py
@abstractmethod
def get_page(self, listing_id: str) -> ListingPage | None: ...
@abstractmethod
def save_page(self, page: ListingPage) -> None:
    """Апсерт по listing_id. attempts считает вызывающий."""
@abstractmethod
def pages_for(self, listing_ids: Iterable[str]) -> dict[str, ListingPage]: ...
@abstractmethod
def listings_paged_since(self, since: datetime) -> set[str]:
    """Чья страница открыта (status ok) после отметки: им пора в подбор."""
```

- [ ] Контрактные тесты на каждый метод; `listings_touched_since` включает
  `listings_paged_since` — `test_a_listing_whose_page_just_opened_is_touched`.
- [ ] `upsert_matches` пишет `origin` только при вставке:
  `test_origin_is_written_once_and_not_compared`.

### Задача 3.4. Разбор страницы

- [ ] `parse_item_page(html) -> PageFields`; снятое объявление —
  `ItemGone`. Тесты на трёх фикстурах: каждое поле из отчёта 3.1,
  `test_unknown_label_is_reported_not_fatal` (подпись, которой нет в разборе,
  уходит в `fields.values["_unknown"]` списком — прогон её считает).
  Парсер не падает из-за отсутствия любого поля (правило M0).

### Задача 3.5. Словарь пожеланий (решения 10, 11)

```python
# listam/domain/wishes.py
@dataclass
class Wish:
    word: str
    field: str
    any_of: list | None = None
    none_of: list | None = None
    is_: bool | None = None
    min: float | None = None

    def check(self, fields: PageFields | None) -> bool | None:
        """True/False — известно; None — поля нет или страница не открыта."""

def vocabulary(config: Config) -> dict[str, Wish]: ...     # ConfigError на кривом словаре
def parse_wishes(text: str | None, vocab) -> tuple[list[Wish], list[str]]:
    """Пожелания и неизвестные слова. Разделитель — запятая; регистр и пробелы не важны."""
```

- [ ] Тесты: `check` на всех видах условий; `parse_wishes("Ремонт, балкон ,лифт")`;
  неизвестное слово возвращается, а не глотается.
- [ ] `requests_sync`: неизвестное слово в `must_have` — отказ строки
  (`RequestError` с колонкой и словом); в `nice_to_have` — строка
  `⚠ nice_to_have: слово «…» не из словаря funnel.wishes — не учитывается`.

### Задача 3.6. Скоринг

- [ ] `score(request, listing, *, page=None, must=(), nice=(), …)`.
- [ ] `rejection`: `must` непусто и `page is None` → `"страница не открыта"`;
  `Wish.check is False` → подпись поля («ремонт», «тип дома»); `None` на
  открытой странице — не отказ.
- [ ] Фактор `wishes` в `DEFAULT_WEIGHTS` (вес 15 — предварительно,
  фаза 4 уточняет) и в обоих конфигах; `test_wishes_factor_counts_only_known_fields`,
  `test_no_wishes_no_factor` (фактор уходит из знаменателя).
- [ ] Тесты «названы все факторы» и README («шесть факторов») — на семь.

### Задача 3.7. Шаг `pages`

- [ ] `listam/pages.py`, `run_pages(config, *, max_opens=None, dry_run=False, keep_html=False)`
  на каркасе `working_session`:
  1. заявки, у которых `must` или `nice` непусты;
  2. представители кластеров, прошедшие `rejection` **без** страничных условий
     и с грубым баллом ≥ `match.thresholds.digest`;
  3. минус те, у кого страница свежая (решение 13), `gone` или исчерпала
     `max_attempts`;
  4. порядок — грубый балл по убыванию, при равенстве — новее `first_seen`;
  5. первые `funnel.max_opens_per_run`, пауза `funnel.delay_seconds`
     (`build_fetcher(config, delay_seconds=…)` — новый необязательный
     аргумент `wiring.build_fetcher`).
- [ ] Тесты на `FilesFetcher` с фикстурами: `test_only_candidates_are_opened`,
  `test_the_ceiling_is_respected`, `test_a_fresh_page_is_not_reopened`,
  `test_a_price_change_reopens_the_page`, `test_gone_page_is_never_reopened`,
  `test_failed_page_counts_attempts`, `test_request_without_wishes_opens_nothing`,
  `test_dry_run_opens_nothing`.
- [ ] Отчёт: кандидатов N, открыто K (потолок P), из кэша M, снято, сбоев,
  неразобранных подписей.
- [ ] CLI `listam pages [--max N] [--dry-run] [--keep-html]`; `--max` больше
  потолка — код 2 с именем ключа.

### Задача 3.8. Подбор со страницами

- [ ] `run_match` читает `pages_for(ids кандидатов)` одним запросом и отдаёт
  `score` страницу; пожелания заявки — `parse_wishes` по словарю.
- [ ] Отказ «страница не открыта» не пишется в матчи и **не закрывает**
  старый матч — `test_unopened_page_does_not_retire` (решение 12).
- [ ] `origin`: `request`, если заявка новая или правленая (`_is_edited`),
  иначе `market` — `test_new_request_matches_are_born_as_request`.
- [ ] Циклы в обоих конфигах: `pages` после `scrape` и перед `match`.

### Задача 3.9. `doctor` «Воронка» и живой шаг

- [ ] `doctor`: кандидатов в очереди (дёшево: без сети), страниц в кэше,
  доля `failed`.
- [ ] `--env prod pages --max 5` — один раз, вживую. Отчёт с числами.
  Дальше страницы открывает часовой цикл.

---

# Фаза 4. Единицы, а не сотни

**Смысл:** настройка балла по живым данным. Предмет фазы — числа, поэтому
она начинается с замера и кончается замером.

**Ожидается после фазы:** ~960 passed, 25 skipped, схема 12.

### Задача 4.1. Замер «до»

- [ ] Скрипт `tmp/measure_m35.py` на **копии** боевой базы (не оригинал):
  на каждую заявку — матчей, горячих, децили балла, сколько матчей `request`
  и `market`; по журналу `notifications` с фазы 1 — событий на заявку
  в каждом «горячем» сообщении (медиана, 95-й перцентиль).
- [ ] Записать таблицу в отчёт. На примере заявок — пометка «предварительно».

### Задача 4.2. Первичная подборка отдельно (решение 14)

- [ ] `collect_events`/«горячее»: событие `new` с `origin = request` в
  «звони сейчас» не идёт — `test_request_born_matches_skip_hot`; `NULL`
  (до миграции) считается `market`.
- [ ] Дайджест: такие события заявки собираются в раздел «первичная подборка:
  N лучших из M» (`notify.digest.initial_top`, по умолчанию 15) —
  `test_initial_selection_is_a_digest_section`.

### Задача 4.3. Разброс балла

Кандидаты-рычаги (решается замером, не вкусом): вес `price_per_sqm`;
`district` для непервоочередного района (0,5 → меньше); `area_rooms` —
близость к середине диапазона вместо «попал/не попал»; вес `wishes`;
порог `match.thresholds.hot`. Цель на живых заявках: медиана событий
на заявку в «горячем» ≤ 3, 95-й перцентиль ≤ `notify.hot.per_request`.

- [ ] Каждый рычаг — тест на его поведение и строка отчёта «было → стало»
  по скрипту 4.1. Изменения, не сдвинувшие замер, откатываются.
- [ ] Решение по «подешевевший тонет в хвосте» (открытый вопрос 3 приёмки M3):
  после настройки посчитать, сколько 📉 не попало в показанные. Ноль или
  единицы — «не чинится», записать. Иначе — 📉 первыми в пределах заявки,
  тест.

### Задача 4.4. Замер «после»

- [ ] Тот же скрипт, те же колонки. Итог — в отчёт и в долг Д-1 (повторить
  на реальных заявках).

---

# Фаза 5. `find` — быстрый поиск

**Смысл:** «Арабкир, 3 комнаты, до $120k» — одной командой, без CSV.

**Ожидается после фазы:** ~980 passed, 25 skipped, схема 12.

- [ ] Флаги → строка-словарь → `domain.requests.parse_row` → `Request`
  (решение 16): `--district` (повторяемый), `--rooms 2-3`, `--max-price`,
  `--area 60-90`, `--floor-not-first`, `--floor-not-last`, `--wish "…"`,
  `--owner`, `--limit`, `--open N`. Ошибки разбора — код 2 с названной
  колонкой (`test_find_rejects_nonsense`).
- [ ] Поиск читает базу без замка (`open_for_reading`), кластеры, медианы,
  `pages_for`; сортировка по баллу; печать — таблица витрины + строка
  на пожелание: «ремонт: известно у 34 из 120, подходит 12; неизвестно у 86 —
  `--open 20` откроет лучшие» (`test_find_counts_known_and_unknown`).
- [ ] `--open N`: под `working_session`, `pages` только по найденным,
  не больше `funnel.find_max_opens` (больше — код 2), затем перескоринг
  и печать (`test_find_open_respects_its_ceiling`).
- [ ] Только собственники: `--owner`. Заявку `find` не пишет —
  `test_find_writes_no_request`.
- [ ] README: таблица команд и короткий раздел с тремя примерами.

---

# Фаза 6. «Звонил» и «отказ»

**Смысл:** цикл «клиент отказал → следующие матчи уже»: закрывается
обратная связь из спеки.

**Ожидается после фазы:** ~1005 passed, 25 skipped, схема 12.

### Задача 6.1. Порт: исключения

```python
@abstractmethod
def add_exclusions(self, exclusions: list[Exclusion]) -> None: ...
@abstractmethod
def exclusions_for(self, request_id: int) -> list[Exclusion]: ...
@abstractmethod
def drop_exclusions(self, match_id: int) -> int: ...
```

- [ ] Контрактные тесты; `iter_requests`/`get_request` исключений не несут —
  подбор читает их отдельно одним запросом на прогон.

### Задача 6.2. `listam mark`

- [ ] `mark <заявка> <объявление> called|rejected|new [--reason "…"]`
  (`listam/feedback.py`, каркас `working_session`). Матч ищется по
  объявлению, а если его нет — по кластеру объявления (карточку могли
  сменить на дешёвую). Нет — код 1 со словами.
- [ ] `rejected`: всегда исключение `cluster`; причина разбирается по
  `feedback.reasons` — `first_floor`, `last_floor`, `district` (район этого
  объявления), поле страницы (значение этого объявления со страницы; поля
  нет — только запись причины, и команда так и говорит). Неизвестная
  причина — записана в `reject_reason`, заявку не сужает, строка об этом.
- [ ] `new` — откат: статус `new`, `reject_reason` пуст, `drop_exclusions(match_id)`.
- [ ] После отметки — подбор по этой заявке (`run_match(config, external_id=…)`),
  чтобы отпавшее ушло в вечерний дайджест со словами «клиент отказал: …».
- [ ] Тесты: `test_rejected_excludes_the_cluster_always`,
  `test_first_floor_reason_narrows_the_request`,
  `test_district_reason_uses_the_listing_district`,
  `test_unknown_reason_is_recorded_not_applied`, `test_new_undoes_the_mark`,
  `test_mark_finds_the_match_by_cluster`.

### Задача 6.3. Подбор и вёрстка видят исключения

- [ ] `rejection`: исключения заявки — ещё жёсткие критерии; причина
  закрытия — «клиент отказал: <слова>».
- [ ] Шапка заявки в сообщении: «· без 1-го этажа, без панели» (исключения
  словами; кластеры не перечисляются).
- [ ] README: раздел «Отметки и отказы».

---

# Фаза 7. Неделя приёмки и README

**Смысл:** предъявить критерий «Готово, когда». Неделя — время ожидания;
в неё же ужимается документация.

### Задача 7.1. Старт недели

- [ ] Батарея зелёная; `--env prod schedule install` заново (циклы изменились
  в фазе 3); `doctor` без «СБОЙ». Дата и время старта — в отчёт.
- [ ] Ни одного ручного запуска боевых команд до конца недели. Нужно
  посмотреть — только `matches`, `changes`, `find` без `--open`, `doctor`.

### Задача 7.2. README — руководство оператора (долг №8)

- [ ] README ≤ 25 КБ: что это, быстрый старт, команды, заявки (колонки),
  расписание, Telegram, воронка, отметки, переезд. Все объяснения «почему
  так» из разделов прогона, матчинга и уведомлений — **переносом, без
  потерь** — в `docs/устройство.md`, README ссылается на него.
- [ ] `tests/test_docs.py` зелёный: команды, колонки заявки, пороги,
  фраза «мигрируют базу сами» остаются в README (тесты это проверяют).
  Правка тестов допустима только там, где они проверяют перенесённый
  текст, и она называется в отчёте.

### Задача 7.3. Приёмка после 7 суток

- [ ] SQL на копии боевой базы, каждое число с командой:
  часовых `fresh` по часам (≥ 90 %), ночных `full` 7 из 7, ошибки и тревоги
  (лог); сообщений «горячее» и событий на живую заявку (медиана ≤ 3,
  95 % ≤ `per_request`); открыто страниц всего и за прогон (≤ потолка);
  ни одного открытия вне кандидатов.
- [ ] Отзыв брокера о сообщениях — Д-5; если его нет, так и записать.
- [ ] Обновить таблицу «Долги»; `docs/анализ-после-M3.md` — раздел «Как
  превращать этот документ в план» отметить сделанным со ссылкой на спеку.
- [ ] Стартовый промпт **M3.6 «Команда»** (ниже шаблон) с исходным
  состоянием и открытыми долгами.

### Итог M3.5

Таблица «обещание спеки → где показано», как у M3.

---

## Стартовый промпт фазы 1

```
Ты начинаешь этап M3.5 «Боевой запуск» инструмента мониторинга list.am
в C:\Users\Artur.A.Gevorgyan\Downloads\new_project.

Прочитай docs/superpowers/plans/2026-09-23-m3-5-live-launch.md: разделы
«Global Constraints», «Карта файлов», «Пример заявок», «Долги» и «Фазу 1».
Рядом спека docs/superpowers/specs/2026-09-23-m3-5-live-launch-design.md —
решения 1–18 не пересматриваются. Решения спек M2 и M3 в силе, кроме
прямо уточнённых спекой M3.5.

Исходное состояние: HEAD — коммит со спекой и планом M3.5, дерево чистое,
батарея 824 passed, 25 skipped, схема 11. Боевой базы нет. Telegram — ключи
в .env, чат — личка брокера: каждое живое сообщение приходит ему.

Твоя задача — фаза 1: система начинает работать сама на Windows.
Тесты без календарных бомб, пустое «звони сейчас» не шлётся, боевой конфиг
без облака, `listam cycle` и `listam schedule` из конфига (переезд на Linux —
без правки кода), строка «Расписание» в doctor, полный обход боевой базы
и установленные задачи.

Работай по шагам: на каждое поведение — падающий тест ДО правки. Тесты —
только .venv/Scripts/python.exe -m pytest -q, CLI из скрипта — только
с PYTHONIOENCODING=utf-8. Живые проверки Playwright не запускай — впиши
в «Долги». Сам на list.am не ходи. Реальных заявок нет — работай на примере.
Чего-то не хватает для запуска — спроси пользователя.
Ни одного числа в отчёте без команды, которая его напечатала.

В конце сессии допиши «Результат фазы 1»: что сделано, числа батареи, что
разошлось с планом и почему, стартовый промпт фазы 2. Сделай коммит.
```

## Шаблон стартового промпта (каждая фаза дописывает свой)

```
Ты продолжаешь этап M3.5 инструмента мониторинга list.am
в C:\Users\Artur.A.Gevorgyan\Downloads\new_project.

Прочитай docs/superpowers/plans/2026-09-23-m3-5-live-launch.md: разделы
«Global Constraints», «Карта файлов», «Долги», «Результат фазы <N-1>» и свою
«Фазу <N>». Чужие фазы не трогай. Спека —
docs/superpowers/specs/2026-09-23-m3-5-live-launch-design.md, решения 1–18
не пересматриваются.

Исходное состояние: HEAD <хэш>, дерево чистое, батарея <N> passed,
<M> skipped, схема <версия>. Боевая база — data/listam-prod.sqlite (замеры —
только на копии). Расписание установлено и работает: не останавливай его без
нужды, а если остановил — верни и напиши об этом.

Твоя задача — фаза <N>: <одна фраза>.
<Три-четыре строки о том, что делается и почему это одно целое.>

Работай по шагам: на каждое поведение — падающий тест ДО правки. Тесты —
только .venv/Scripts/python.exe -m pytest -q, CLI из скрипта — только
с PYTHONIOENCODING=utf-8. Пороги не поднимай (кроме фазы 4 с замером).
Новый метод порта — новый контрактный тест. Схему меняет только
миграция 012 (фаза 3). След звонка пишет только mark, MATCH_COMPARED
не расширяется. Живые проверки Playwright не запускай — впиши в «Долги».
Сам на list.am не ходи. Ни одного числа без команды, которая его напечатала.

В конце сессии допиши «Результат фазы <N>» и стартовый промпт фазы <N+1>.
Сделай коммит.
```

---

## Что в этот план не входит

- **Google Drive и Google Sheet вживую** — M3.6 (решение пользователя).
- **Команда:** участники, группы, `request_assignees`, `Assigner`, бот
  по приглашению, окно журнала на получателя — M3.6.
- **Телефоны, подборка для клиента, EUR и RUB** — M4. **Аналитика цен** — M5.
- **Отметки из Telegram** (кнопки, ответы боту): бот пока только отправляет.
- **Шлифовка, которую брокер не заметит:** «Событий: 0» при `kind: none`,
  слияние журналов двух копий, повтор части после обрыва чтения.
- **Живые проверки через Playwright** — долг Д-2, прогоняет пользователь.
- **Хранение HTML страниц** в базе: только разобранные поля.
