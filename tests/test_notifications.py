"""Уведомление: окно, текст, журнал, тумблеры. Сети здесь нет."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from listam.config import ConfigError, load_config
from listam.domain.models import Listing, Match, Request
from listam.notifications import run_notify
from listam.wiring import build_database

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)  # календарь: не сравнивается с часами

CONFIG = """
env: test
storage:
  kind: local
  directory: {remote}
  work_dir: {work}
  db_filename: listam.sqlite
rate:
  kind: fixed
  amd_per_usd: 363.25
match:
  limit: 50
  thresholds:
    hot: 70
    digest: 40
notify:
  kind: stdout
  hot:
    enabled: true
    per_request: 5
    fallback_hours: 2
  digest:
    enabled: true
    per_request: 10
    wide_request: 15
    include_retired: true
    fallback_hours: 24
  feed:
    enabled: true
    limit: 15
    fallback_hours: 24
"""


def make_config(tmp_path: Path):
    """Настоящий файл конфига в tmp: команда `matches --new` зовётся через
    `--config-dir`, а значит, конфиг обязан лежать на диске, а не в памяти."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "test.yaml").write_text(
        CONFIG.format(remote=(tmp_path / "remote").as_posix(),
                      work=(tmp_path / "work").as_posix()),
        encoding="utf-8",
    )
    return load_config(env="test", config_dir=config_dir, dotenv_path=tmp_path / ".env")


@pytest.fixture
def prepared(tmp_path):
    """Конфиг, база и одна заявка с двумя свежими матчами."""
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


def test_closures_do_not_make_a_request_wide(prepared):
    """Закрытие — не событие (решение 1 спеки), и широту заявки оно не мерит.

    Брокер сузил заявку, и сотни вариантов отпали: совет «сузь районы или
    бюджет» в ответ на это — неправда. Фаза 6 получила его живьём: R-2
    с бюджетом 150 000 $ и без Кентрона — «отпало 830» и пометка широкой.
    """
    prepared.data["notify"]["digest"]["wide_request"] = 1
    database = build_database(prepared)
    database.connect()
    request = database.get_request("R-1")
    database.retire_matches(request.id, keep=set(), now=datetime.now(timezone.utc),
                            reasons={"0": "бюджет", "1": "район"}, default="бюджет")
    database.close()

    report = run_notify(prepared, kind="digest", dry_run=True)

    assert "отпало 2 (бюджет 1, район 1)" in report.text
    assert "слишком широкая" not in report.text


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


def test_the_view_and_the_message_measure_the_same_window(prepared, capsys):
    """Витрина `matches --new` и `notify --digest` берут окно из одного
    журнала: иначе сличить отправляемое глазами невозможно."""
    from listam.cli import main

    run_notify(prepared, kind="digest")          # окно сдвинулось
    capsys.readouterr()

    assert main(["--env", "test", "--config-dir", str(prepared.path.parent),
                 "matches", "--new"]) == 0
    assert "с прошлой отправки" in capsys.readouterr().out


def test_without_a_send_the_view_says_so_out_loud(prepared, capsys):
    """Пустой список без объяснения читался бы как «на рынке тишина»."""
    from listam.cli import main

    assert main(["--env", "test", "--config-dir", str(prepared.path.parent),
                 "matches", "--new"]) == 0
    assert "отправок ещё не было" in capsys.readouterr().out


def fresh_listings(config, count: int) -> None:
    """Объявления, попавшие на ленту внутри окна ленты.

    Объявления фикстуры лежат на отметке NOW — вчерашней относительно «сейчас»,
    и в суточное окно ленты не попадают. Это не мелочь теста: `first_seen`
    у существующей строки не двигается повторной встречей, поэтому «новое
    на ленте» — это всегда новая строка, а не новый проход.
    """
    now = datetime.now(timezone.utc)
    database = build_database(config)
    database.connect()
    try:
        for index in range(count):
            database.upsert_listing(
                Listing(id=f"fresh-{index}",
                        url=f"https://www.list.am/ru/item/fresh-{index}",
                        district="Кентрон", price_usd=90000.0 + index,
                        area=60.0, price_per_sqm=1500.0 + index, rooms=2),
                seen_at=now,
            )
    finally:
        database.close()


def test_the_feed_message_counts_what_came_outside_the_requests(prepared):
    """Лента — отдельный разговор: брокеру нужно видеть её и тогда, когда
    ни одна заявка ничего не взяла."""
    fresh_listings(prepared, 2)

    report = run_notify(prepared, kind="feed", dry_run=True)

    assert report.events == 2
    assert "На ленте: новых 2" in report.text
    assert "https://www.list.am/ru/item/fresh-0" in report.text


def test_the_feed_message_keeps_the_tail_honest(prepared):
    """Потолок ленты режет строки, но не счётчик: «новых 2» остаётся правдой."""
    fresh_listings(prepared, 2)
    prepared.data["notify"]["feed"]["limit"] = 1

    report = run_notify(prepared, kind="feed", dry_run=True)

    assert report.events == 2
    assert "На ленте: новых 2" in report.text
    assert "…и ещё 1 — python -m listam changes" in report.text


def test_a_wide_mark_does_not_stick_to_a_neighbour(prepared):
    """`R-1` — префикс `R-11`. Пометка, приклеенная по началу строки, вешала
    на узкого соседа чужой счётчик: на боевой базе их выходило 77 на 50 заявок.

    Широкая здесь именно `R-1` — два события против потолка в одно, — а `R-11`
    с одним событием обязан остаться неотмеченным.
    """
    database = build_database(prepared)
    database.connect()
    database.upsert_request(Request(external_id="R-11", client_name="Тигран"), now=NOW)
    neighbour = database.get_request("R-11")
    database.upsert_listing(
        Listing(id="2", url="https://www.list.am/ru/item/2", district="Кентрон",
                price_usd=100000.0, area=60.0, rooms=2),
        seen_at=NOW,
    )
    database.upsert_matches(
        [Match(request_id=neighbour.id, listing_id="2", score=80.0)],
        datetime.now(timezone.utc),
    )
    database.close()
    prepared.data["notify"]["digest"]["wide_request"] = 1

    report = run_notify(prepared, kind="digest", dry_run=True)

    assert report.text.count("слишком широкая") == 1
    marked = [line for line in report.text.splitlines() if "слишком широкая" in line]
    assert "2 событий" in marked[0]


def test_telegram_without_a_token_is_refused_before_the_work(prepared, monkeypatch):
    """Пустой секрет — отказ на входе (спека, «Ошибки и отказы»), а не после
    десяти секунд выборки на боевой базе и не под замком рабочей копии."""
    from listam.config import ConfigError

    def no_work(*args, **kwargs):
        raise AssertionError("выборка не должна начинаться без канала")

    monkeypatch.setattr("listam.notifications.collect_events", no_work)
    prepared.data["notify"].update({"kind": "telegram", "token": "", "chat_id": ""})

    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        run_notify(prepared, kind="hot")


def test_a_dry_run_does_not_need_the_channel(prepared):
    """Посмотреть текст можно и до того, как ключи заведены."""
    prepared.data["notify"].update({"kind": "telegram", "token": "", "chat_id": ""})

    report = run_notify(prepared, kind="hot", dry_run=True)

    assert report.errors == 0
    assert not report.sent


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


def test_the_view_does_not_leave_an_empty_base_behind(prepared, capsys):
    """То же у `matches` без `--new`: дверь для чтения у витрины одна."""
    from listam.cli import main
    from listam.wiring import database_path

    path = database_path(prepared)
    path.unlink()

    assert main(cli_args(prepared) + ["matches"]) == 1
    assert not path.exists()
    assert "базы нет" in capsys.readouterr().err


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
