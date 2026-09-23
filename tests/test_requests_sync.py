"""`listam requests`: прочитать источник и положить заявки в базу.

Проверяется поведение, названное решением 2 спеки: одна опечатка не стоит
брокеру остальных заявок, но уехавший формат таблицы — это сбой, а не
сорок девять опечаток подряд.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from listam.config import Config
from listam.domain.requests import COLUMNS
from listam.requests_sync import run_requests_sync
from listam.wiring import build_database, database_path

GOOD = {
    "id": "R-1", "client_name": "Ани", "client_phone": "+374 00 000000",
    "status": "active", "budget_max": "120 000 $", "budget_stretch": "",
    "districts": "Кентрон, Арабкир", "districts_priority": "Кентрон", "rooms": "2-3",
    "area_min": "60", "area_max": "95", "floor_min": "", "floor_max": "",
    "no_first_floor": "да", "no_last_floor": "нет",
    "must_have": "", "nice_to_have": "", "floor_rules": "", "notes": "",
}
OTHER = dict(GOOD, id="R-2", client_name="Ваган", budget_max="90000",
             districts="Арабкир", districts_priority="")
BROKEN = dict(GOOD, id="R-3", budget_max="примерно 100к")


def write_csv(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def cfg(tmp_path: Path, requests_path: Path) -> Config:
    return Config(
        {
            "env": "test",
            "storage": {
                "kind": "local",
                "directory": str(tmp_path / "remote"),
                "work_dir": str(tmp_path / "work"),
                "db_filename": "listam.sqlite",
            },
            "requests": {"kind": "csv", "path": str(requests_path)},
        },
        env="test",
        path=Path("config/test.yaml"),
    )


@pytest.fixture
def csv_config(tmp_path):
    return cfg(tmp_path, write_csv(tmp_path / "requests.csv", [GOOD, OTHER]))


@pytest.fixture
def csv_config_with_one_bad_row(tmp_path):
    return cfg(tmp_path, write_csv(tmp_path / "requests.csv", [GOOD, BROKEN]))


@pytest.fixture
def csv_config_all_bad(tmp_path):
    return cfg(tmp_path, write_csv(tmp_path / "requests.csv", [BROKEN, dict(BROKEN, id="R-4")]))


@pytest.fixture
def csv_config_missing(tmp_path):
    return cfg(tmp_path, tmp_path / "нет-такого-файла.csv")


def stored(config) -> list[str]:
    database = build_database(config)
    database.connect()
    try:
        return [item.external_id for item in database.iter_requests()]
    finally:
        database.close()


def test_requests_are_read_from_the_source_and_stored(csv_config):
    report = run_requests_sync(csv_config)

    assert (report.new, report.updated, report.rejected) == (2, 0, [])
    assert report.errors == 0
    assert stored(csv_config) == ["R-1", "R-2"]


def test_the_report_names_the_source(csv_config):
    report = run_requests_sync(csv_config)

    assert "CSV" in report.source
    assert "CSV" in report.render()


def test_reading_the_same_table_twice_changes_nothing(csv_config):
    run_requests_sync(csv_config)
    report = run_requests_sync(csv_config)

    assert (report.new, report.updated, report.unchanged) == (0, 0, 2)


def test_an_edited_request_is_counted_as_updated(csv_config, tmp_path):
    run_requests_sync(csv_config)
    write_csv(tmp_path / "requests.csv", [dict(GOOD, budget_max="130000"), OTHER])
    report = run_requests_sync(csv_config)

    assert (report.new, report.updated, report.unchanged) == (0, 1, 1)


def test_one_broken_row_does_not_stop_the_others(csv_config_with_one_bad_row):
    report = run_requests_sync(csv_config_with_one_bad_row)

    assert report.new == 1
    assert len(report.rejected) == 1
    assert "budget_max" in report.render()
    assert report.errors == 0        # опечатка в одной строке — не сбой прогона
    assert stored(csv_config_with_one_bad_row) == ["R-1"]


def test_a_rejected_request_is_not_written_to_the_base(csv_config_with_one_bad_row):
    run_requests_sync(csv_config_with_one_bad_row)

    assert "R-3" not in stored(csv_config_with_one_bad_row)


def test_a_table_where_nothing_parses_is_an_error(csv_config_all_bad):
    report = run_requests_sync(csv_config_all_bad)

    assert report.errors == 1
    assert "формат" in report.render().lower()
    # Базы нет вовсе: до записи дело не дошло, и это сильнее, чем «ноль строк».
    assert not database_path(csv_config_all_bad).exists()


def test_a_missing_file_is_an_error_and_the_base_is_untouched(csv_config_missing):
    report = run_requests_sync(csv_config_missing)

    assert report.errors == 1
    assert report.new == 0
    assert not database_path(csv_config_missing).exists()


def test_an_empty_table_is_not_an_error(tmp_path):
    config = cfg(tmp_path, write_csv(tmp_path / "requests.csv", []))
    report = run_requests_sync(config)

    assert report.errors == 0
    assert (report.new, report.updated, report.unchanged) == (0, 0, 0)


def test_the_source_is_not_configured_and_that_is_not_a_crash(tmp_path):
    config = Config(
        {
            "storage": {"kind": "local", "directory": str(tmp_path / "remote"),
                        "work_dir": str(tmp_path / "work"), "db_filename": "listam.sqlite"},
            "requests": {"kind": "none"},
        },
        env="test", path=Path("config/test.yaml"),
    )
    report = run_requests_sync(config)

    assert report.errors == 0
    assert "не настроен" in report.render()


def test_a_row_deleted_from_the_table_closes_the_request(tmp_path, csv_config):
    run_requests_sync(csv_config)

    write_csv(tmp_path / "requests.csv", [GOOD])
    report = run_requests_sync(csv_config)

    assert report.closed == 1
    assert "R-2" in report.render()
    assert stored(csv_config) == ["R-1"]


def test_an_empty_table_closes_nothing_and_says_why(tmp_path):
    config = cfg(tmp_path, write_csv(tmp_path / "requests.csv", [GOOD]))
    run_requests_sync(config)

    write_csv(tmp_path / "requests.csv", [])
    report = run_requests_sync(config)

    assert report.closed == 0
    assert "ни одной заявки" in (report.notes or ""), (
        "пустая таблица — это чаще сбой доступа, чем «все клиенты ушли»; "
        "закрывать по ней всю базу заявок нельзя"
    )
    assert stored(config) == ["R-1"]


def test_the_rendered_report_never_prints_none(csv_config_with_one_bad_row):
    text = run_requests_sync(csv_config_with_one_bad_row).render()

    assert "None" not in text


def test_a_broken_database_is_a_message_and_not_a_traceback(tmp_path):
    """Битый файл базы даёт `sqlite3.DatabaseError`, а не `OSError`.

    Соседи (`match`, `cluster`) в этой ситуации отвечают человеку строкой
    отчёта; `requests` падал трейсбеком.
    """
    config = cfg(tmp_path, write_csv(tmp_path / "requests.csv", [GOOD]))
    path = database_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a database at all")

    report = run_requests_sync(config)

    assert report.errors == 1
    assert "файл базы недоступен" in (report.notes or ""), \
        "человек должен прочитать, что случилось, а не разбирать трейсбек"
    assert "file is not a database" in report.notes, "и что именно сломалось"


# --- пожелания из словаря funnel.wishes (фаза 3 M3.5) --------------------

WISHES = {"ремонт": {"field": "renovation", "any_of": ["косметический"]},
          "лифт": {"field": "elevator", "is": True}}


def wished_config(tmp_path: Path, rows: list[dict]) -> Config:
    config = cfg(tmp_path, write_csv(tmp_path / "requests.csv", rows))
    config.data["funnel"] = {"wishes": WISHES}
    return config


def test_known_wishes_are_read(tmp_path):
    config = wished_config(tmp_path, [dict(GOOD, must_have="Ремонт", nice_to_have="лифт")])

    report = run_requests_sync(config)

    assert (report.new, report.rejected, report.warnings) == (1, [], [])


def test_an_unknown_must_have_word_rejects_the_row(tmp_path):
    """Жёсткий критерий наугад не истолковывается (решение 10): строка
    отклоняется с колонкой и словом, остальные читаются."""
    config = wished_config(tmp_path, [dict(GOOD, must_have="ремонт, бассейн"), OTHER])

    report = run_requests_sync(config)

    assert report.new == 1
    assert len(report.rejected) == 1
    rendered = report.render()
    assert "must_have" in rendered
    assert "бассейн" in rendered
    assert "R-1" in rendered
    assert stored(config) == ["R-2"]


def test_an_unknown_nice_to_have_word_is_a_warning(tmp_path):
    config = wished_config(tmp_path, [dict(GOOD, nice_to_have="лифт, бассейн")])

    report = run_requests_sync(config)

    assert report.new == 1
    assert report.rejected == []
    assert report.errors == 0
    assert ("⚠ R-1 · nice_to_have: слово «бассейн» не из словаря funnel.wishes — "
            "не учитывается") in report.render()
    assert stored(config) == ["R-1"]


def test_a_crooked_vocabulary_stops_before_the_base(tmp_path):
    config = wished_config(tmp_path, [GOOD])
    config.data["funnel"] = {"wishes": {"лифт": {"field": "elevator"}}}

    report = run_requests_sync(config)

    assert report.errors == 1
    assert "funnel.wishes" in report.render()
    assert not database_path(config).exists()
