"""`listam doctor` — то, чем проверяется переезд на другой аккаунт."""
from __future__ import annotations

from pathlib import Path

from listam.adapters.db_sqlite import SqliteDatabase
from listam.config import Config
from listam.doctor import match_check, run_doctor
from listam.wiring import database_path


def cfg(tmp_path, **over) -> Config:
    data = {
        "env": "test",
        "storage": {
            "kind": "local",
            "directory": str(tmp_path / "remote"),
            "work_dir": str(tmp_path / "work"),
            "db_filename": "listam.sqlite",
        },
        "rate": {"kind": "fixed", "amd_per_usd": 363.25},
        "export": {"kind": "xlsx_local", "path": str(tmp_path / "out")},
        "scrape": {"base_url": "https://example.invalid", "delay_seconds": 0},
        "notify": {"kind": "none"},
    }
    data.update(over)
    return Config(data, env="test", path=Path("config/test.yaml"))


def test_healthy_setup_reports_ok(tmp_path):
    report = run_doctor(cfg(tmp_path), check_network=False)
    assert report.ok is True


def test_every_check_is_named_and_explained(tmp_path):
    report = run_doctor(cfg(tmp_path), check_network=False)
    names = [check.name for check in report.checks]
    assert "Конфиг" in names
    assert "Хранилище" in names
    assert "Схема базы" in names
    assert "Курс AMD→USD" in names
    assert "Выгрузка" in names
    assert all(check.details for check in report.checks)


def test_unwritable_export_directory_fails_the_report(tmp_path):
    blocker = tmp_path / "out"
    blocker.write_text("я файл, а не папка", encoding="utf-8")
    report = run_doctor(cfg(tmp_path), check_network=False)
    assert report.ok is False
    assert any(not check.ok and check.name == "Выгрузка" for check in report.checks)


def test_unknown_adapter_is_reported_not_raised(tmp_path):
    broken = cfg(tmp_path, storage={"kind": "dropbox"})
    report = run_doctor(broken, check_network=False)
    assert report.ok is False
    assert any("dropbox" in check.details for check in report.checks)


def test_rate_check_reports_the_number(tmp_path):
    report = run_doctor(cfg(tmp_path), check_network=False)
    rate_check = next(c for c in report.checks if c.name == "Курс AMD→USD")
    assert "363.25" in rate_check.details


def test_doctor_does_not_leave_junk_in_storage(tmp_path):
    run_doctor(cfg(tmp_path), check_network=False)
    remote = tmp_path / "remote"
    assert list(remote.glob("*")) == []


def test_report_renders_as_readable_text(tmp_path):
    text = run_doctor(cfg(tmp_path), check_network=False).render()
    assert "Хранилище" in text
    assert "OK" in text


# --- пороги прогона (F-13) -------------------------------------------
# `doctor` предупреждает, но не чинит: опасный порог — это ⚠ в отчёте и
# ненулевой код возврата на явном вредительстве. Конфиг — территория человека.

HEALTHY = {
    "base_url": "https://example.invalid",
    "delay_seconds": 0,
    "max_pages": None,
    "fresh_stop_after_known_pages": 2,
    "fresh_max_pages": 20,
    "expected_pages_min": None,
    "max_pages_drop_percent": 20,
    "max_gone_percent": 10,
}


def thresholds(report):
    return next(c for c in report.checks if c.name == "Пороги прогона")


def test_working_thresholds_are_listed_and_green(tmp_path):
    """Боевой конфиг: проверка зелёная и показывает, с чем прогон пойдёт."""
    report = run_doctor(cfg(tmp_path, scrape=dict(HEALTHY)), check_network=False)
    check = thresholds(report)

    assert check.ok is True and check.warn is False
    assert "max_gone_percent = 10" in check.details
    assert "fresh_stop_after_known_pages = 2" in check.details
    assert "max_pages = не задан" in check.details


def test_zero_gone_percent_is_reported_as_harm(tmp_path):
    """0 значит «пропало хоть что-то — сбой»: снятыми не будет помечено ничего."""
    broken = dict(HEALTHY, max_gone_percent=0)
    report = run_doctor(cfg(tmp_path, scrape=broken), check_network=False)

    assert report.ok is False
    assert thresholds(report).ok is False
    assert "снятыми не будет помечено ничего" in thresholds(report).details


def test_a_page_ceiling_in_the_config_is_a_warning_not_a_failure(tmp_path):
    """`max_pages: 2` — рабочая настройка окружения, но обход укорочен, и это видно."""
    report = run_doctor(cfg(tmp_path, scrape=dict(HEALTHY, max_pages=2)),
                        check_network=False)
    check = thresholds(report)

    assert check.warn is True
    assert check.ok is True
    assert report.ok is True
    assert "укорочен" in check.details


def test_fresh_without_a_stop_condition_is_a_warning(tmp_path):
    """`null` выключает остановку: `--fresh` пойдёт до потолка и кончится ошибкой."""
    report = run_doctor(cfg(tmp_path, scrape=dict(HEALTHY, fresh_stop_after_known_pages=None)),
                        check_network=False)

    assert thresholds(report).warn is True


def test_a_warning_is_marked_in_the_rendered_report(tmp_path):
    text = run_doctor(cfg(tmp_path, scrape=dict(HEALTHY, max_pages=2)),
                      check_network=False).render()

    assert "⚠" in text


def test_the_verdict_does_not_say_everything_is_fine_when_it_warns(tmp_path):
    """«Всё на месте» поверх ⚠ — это неправда: человек ради неё и читает итог."""
    text = run_doctor(cfg(tmp_path, scrape=dict(HEALTHY, max_pages=2)),
                      check_network=False).render()

    assert "Всё на месте" not in text
    assert "предупреждени" in text


# --- источник заявок (фаза 2 M2) --------------------------------------
# `doctor` отвечает на вопрос «матчинг заработает?». Ненастроенный источник —
# не сбой окружения, но и не «всё на месте»: без заявок матчить нечего.

def source(report):
    return next(c for c in report.checks if c.name == "Источник заявок")


def requests_csv(tmp_path, rows: str) -> Path:
    path = tmp_path / "requests.csv"
    path.write_text(rows, encoding="utf-8")
    return path


HEADER = ("id,client_name,client_phone,status,budget_max,budget_stretch,districts,"
          "districts_priority,rooms,area_min,area_max,floor_min,floor_max,"
          "no_first_floor,no_last_floor,must_have,nice_to_have,floor_rules,notes\n")
GOOD_ROW = "R-1,Ани,+374,active,120000,,Кентрон,,3,60,95,,,да,нет,,,,\n"
BAD_ROW = "R-2,Ваган,+374,active,примерно 100к,,Кентрон,,3,60,95,,,да,нет,,,,\n"


def test_a_source_that_is_not_configured_is_a_warning_not_a_failure(tmp_path):
    report = run_doctor(cfg(tmp_path, requests={"kind": "none"}), check_network=False)

    assert report.ok is True
    assert source(report).warn is True
    assert "не настроен" in source(report).details


def test_a_live_csv_source_is_green_and_counts_the_rows(tmp_path):
    path = requests_csv(tmp_path, HEADER + GOOD_ROW)
    report = run_doctor(cfg(tmp_path, requests={"kind": "csv", "path": str(path)}),
                        check_network=False)

    assert source(report).ok is True and source(report).warn is False
    assert "1" in source(report).details


def test_unparsed_rows_are_a_warning(tmp_path):
    path = requests_csv(tmp_path, HEADER + GOOD_ROW + BAD_ROW)
    report = run_doctor(cfg(tmp_path, requests={"kind": "csv", "path": str(path)}),
                        check_network=False)

    assert source(report).ok is True
    assert source(report).warn is True
    assert "1" in source(report).details


def test_an_unreachable_source_fails_the_report(tmp_path):
    report = run_doctor(
        cfg(tmp_path, requests={"kind": "csv", "path": str(tmp_path / "нет.csv")}),
        check_network=False,
    )

    assert report.ok is False
    assert source(report).ok is False


def test_a_source_without_active_requests_is_a_warning(tmp_path):
    """Таблица есть, а матчить нечего: это не сбой окружения, но и не «всё на месте»."""
    path = requests_csv(tmp_path, HEADER)
    report = run_doctor(cfg(tmp_path, requests={"kind": "csv", "path": str(path)}),
                        check_network=False)

    assert report.ok is True
    assert source(report).warn is True
    assert "матчить нечего" in source(report).details


# --- матчинг (фаза 4 M2) ----------------------------------------------
# Веса и пороги — территория человека, и `doctor` их не правит (решение 7
# плана QA M1). Но показать, с чем поедет матчинг, он обязан: балл 0–100
# ничего не говорит о том, из чего он сложился.

MATCH = {
    "weights": {"budget": 30, "district": 20, "price_per_sqm": 20,
                "area_rooms": 15, "floor": 10, "seller_type": 5},
    "thresholds": {"hot": 70, "digest": 40},
    "budget_stretch_percent": 10,
    "cluster": {"area_tolerance": 2},
    "limit": 50,
}


def matching(report):
    return next(c for c in report.checks if "матчинг" in c.name.lower())


def test_doctor_shows_the_matching_weights_and_thresholds(tmp_path):
    report = run_doctor(cfg(tmp_path, match=dict(MATCH)), check_network=False)
    line = matching(report)

    assert line.ok is True and line.warn is False
    assert "70" in line.details and "40" in line.details
    assert "budget = 30" in line.details


def test_a_hot_threshold_below_the_digest_one_is_a_warning(tmp_path):
    broken = dict(MATCH, thresholds={"hot": 30, "digest": 40})
    report = run_doctor(cfg(tmp_path, match=broken), check_network=False)

    assert matching(report).warn or not matching(report).ok


def test_zero_weights_everywhere_is_a_failure_not_a_silent_zero_score(tmp_path):
    # Ноль значит ноль: все веса по нулю — это не «выключено», это матчинг,
    # который всегда отдаёт 0 баллов. Такое надо показать, а не проглотить.
    zeroed = dict(MATCH, weights={key: 0 for key in MATCH["weights"]})
    report = run_doctor(cfg(tmp_path, match=zeroed), check_network=False)

    assert report.ok is False
    assert matching(report).ok is False


def test_a_missing_match_section_falls_back_to_the_spec_defaults(tmp_path):
    """Секции нет — матчинг работает на значениях спеки, но человек об этом знает."""
    report = run_doctor(cfg(tmp_path), check_network=False)
    line = matching(report)

    assert line.ok is True
    assert line.warn is True
    assert "по умолчанию" in line.details


def test_a_zero_area_tolerance_is_listed_and_not_read_as_off(tmp_path):
    """Ноль в допуске — рабочая настройка «площади обязаны совпадать», и её видно."""
    strict = dict(MATCH, cluster={"area_tolerance": 0})
    report = run_doctor(cfg(tmp_path, match=strict), check_network=False)

    assert "area_tolerance = 0" in matching(report).details


def test_doctor_calls_a_misspelled_weight_a_failure_and_not_a_warning(tmp_path):
    """Опечатка в имени фактора молча выбрасывает его вес из балла.

    До фазы 5 это было предупреждением: `doctor` говорил «посмотри», а
    подбор ехал и писал в базу другой балл. Теперь такой конфиг — сбой:
    `settings` на нём отказывается стартовать, и `doctor` обязан отвечать
    то же самое, что ответит команда.
    """
    config = cfg(tmp_path, match={"weights": {"budjet": 30}})
    check = match_check(config)
    assert check.ok is False
    assert "budjet" in check.details


def test_doctor_calls_a_weight_nobody_named_a_failure_too(tmp_path):
    """Фактор, выпавший из списка, в балл не войдёт — и это не видно ничем."""
    short = dict(MATCH, weights={"budget": 30, "district": 20})
    check = match_check(cfg(tmp_path, match=short))

    assert check.ok is False
    assert "seller_type" in check.details
    assert "0" in check.details, "отказ обязан сказать, чем фактор выключают"


def test_doctor_calls_a_threshold_outside_the_scale_a_failure(tmp_path):
    """`hot: 170` — это не «строгий порог», а выключенные уведомления.
    `settings` на таком конфиге не стартует, и `doctor` говорит то же."""
    thresholds = dict(MATCH["thresholds"], hot=170)
    check = match_check(cfg(tmp_path, match=dict(MATCH, thresholds=thresholds)))

    assert check.ok is False
    assert "match.thresholds.hot" in check.details
    assert "от 0 до 100" in check.details


# --- схема рабочей базы (фаза 5 QA) -----------------------------------
# Пробник во временной папке отвечает на вопрос «накатываются ли миграции
# этим кодом». Команда работает не с ним: рабочий файл может стоять на
# версии 4, и тогда `match`, `export` и `matches` откажутся работать.

def schema(report):
    return next(check for check in report.checks if "Схема" in check.name)


def test_doctor_names_the_version_of_the_working_database(tmp_path):
    config = cfg(tmp_path)
    database = SqliteDatabase(database_path(config))
    database.connect()
    database.migrate()
    database.conn.execute("DELETE FROM schema_version WHERE version >= 5")
    database.conn.commit()
    database.close()

    report = run_doctor(config, check_network=False)

    assert schema(report).ok is False
    assert "4" in schema(report).details, (
        "версия рабочего файла, а не временного пробника: команда откажется "
        "работать именно с ним"
    )


def test_doctor_does_not_complain_when_there_is_no_working_database_yet(tmp_path):
    report = run_doctor(cfg(tmp_path), check_network=False)

    assert schema(report).ok is True
    assert "ещё нет" in schema(report).details


# --- канал уведомлений (фаза 5 M3) ------------------------------------
# Канал, про который `doctor` молчит, включают вслепую.

def test_doctor_names_the_notification_channel(tmp_path):
    from listam.doctor import notify_check

    config = cfg(tmp_path, notify={"kind": "stdout", "hot": {"enabled": True},
                                   "digest": {"enabled": True},
                                   "feed": {"enabled": False}})

    check = notify_check(config)

    assert check.ok
    assert "stdout" in check.details
    assert "feed: выкл" in check.details        # выключенный вид назван, а не спрятан


def test_doctor_refuses_telegram_without_a_token(tmp_path):
    """Пустой секрет — это сбой, а не предупреждение: команда всё равно не пошлёт."""
    from listam.doctor import notify_check

    check = notify_check(cfg(tmp_path, notify={"kind": "telegram", "token": "", "chat_id": ""}))

    assert not check.ok
    assert "TELEGRAM_BOT_TOKEN" in check.details


def test_doctor_calls_a_senseless_notify_setting_a_failure(tmp_path):
    """`doctor` отвечает то же, что ответит `notify`: вечером, когда дайджест
    не пришёл, узнавать об этом поздно."""
    from listam.doctor import notify_check

    check = notify_check(cfg(tmp_path, notify={"kind": "stdout",
                                               "digest": {"enabled": "false"}}))

    assert not check.ok
    assert "notify.digest.enabled" in check.details


def test_doctor_describes_telegram_without_printing_the_token(tmp_path):
    """Отчёт `doctor` читают глазами и пересылают — токену там не место."""
    from listam.doctor import notify_check

    check = notify_check(cfg(tmp_path, notify={"kind": "telegram", "token": "8833:SECRET",
                                               "chat_id": "1930501720"}))

    assert check.ok
    assert "Telegram" in check.details
    assert "8833:SECRET" not in check.details


def test_the_channel_check_is_in_the_report(tmp_path):
    report = run_doctor(cfg(tmp_path, notify={"kind": "misspelled"}), check_network=False)

    channel = next(check for check in report.checks if check.name == "Уведомления")
    assert not channel.ok
    assert "misspelled" in channel.details
