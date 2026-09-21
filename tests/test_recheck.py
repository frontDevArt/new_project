"""Блокер 5: пересчёт аномалий по всей базе — отдельная команда.

Правила проверки и колонки появились после того, как боевая база была набрана:
в ней 20 569 строк без `anomaly` и без `price_amount`. Прогон такую базу не
чинит — он ходит по ленте и трогает только те карточки, что встретил сегодня.
Поэтому `recheck`: накатить миграции, пересчитать помеченное по всем строкам,
показать сводку. Разовая операция, а не работа каждого прогона.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from listam.adapters.db_sqlite import (
    MIGRATIONS_DIR,
    SqliteDatabase,
    latest_schema_version,
)
from listam.config import Config
from listam.recheck import run_recheck
from listam.wiring import database_path, run_lock_path

CONFIG_RATE = 400.0


@pytest.fixture
def project(tmp_path) -> Config:
    return Config(
        {
            "storage": {
                "kind": "local",
                "directory": str(tmp_path / "remote"),
                "work_dir": str(tmp_path / "work"),
                "db_filename": "listam.sqlite",
                "keep_backups": 5,
            },
            "rate": {"kind": "fixed", "amd_per_usd": CONFIG_RATE},
            "validate": {"price_usd": {"min": 5000, "max": 5_000_000}},
        },
        env="test",
        path=tmp_path / "config" / "test.yaml",
    )


def database_at_schema(config: Config, version: int) -> Path:
    """Рабочая копия базы, на которую накатили только первые миграции."""
    older = Path(config.path).parent / f"migrations-{version}"
    older.mkdir(parents=True, exist_ok=True)
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if int(path.name.split("_", 1)[0]) <= version:
            shutil.copyfile(path, older / path.name)
    path = database_path(config)
    database = SqliteDatabase(path, migrations_dir=older)
    database.connect()
    database.migrate()
    database.close()
    return path


ROWS = (
    # id, url, price_raw, currency, price_usd, area — как их набрала боевая база
    ("24076812", "$ 87,000,000", "USD", 87_000_000.0, 82.0),
    ("24100001", "$ 100,000", "USD", 100_000.0, 50.0),
    ("23598471", "€ 140,000", "EUR", None, 70.0),
)


def fill(path: Path) -> None:
    import sqlite3

    connection = sqlite3.connect(path)
    for listing_id, raw, currency, price_usd, area in ROWS:
        connection.execute(
            "INSERT INTO listings (id, url, price_raw, currency, price_usd, area,"
            " price_per_sqm, status, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'active', '2026-09-20T00:00:00+00:00',"
            " '2026-09-20T00:00:00+00:00')",
            (
                listing_id,
                f"https://www.list.am/ru/item/{listing_id}",
                raw,
                currency,
                price_usd,
                area,
                round(price_usd / area, 2) if price_usd else None,
            ),
        )
    connection.commit()
    connection.close()


def opened(config: Config) -> SqliteDatabase:
    database = SqliteDatabase(database_path(config))
    database.connect()
    return database


def test_recheck_marks_the_typo_and_leaves_clean_rows_alone(project):
    """«$ 87,000,000» за 82 м² — это опечатка продавца, а не цена."""
    path = database_at_schema(project, 1)
    fill(path)

    report = run_recheck(project)

    database = opened(project)
    marked = database.get_listing("24076812")
    clean = database.get_listing("24100001")
    database.close()

    assert "price_usd" in marked.anomaly
    assert clean.anomaly is None
    assert report.marked == 1
    assert report.listings == 3


def test_recheck_brings_the_base_to_the_schema_the_code_expects(project):
    path = database_at_schema(project, 1)
    fill(path)

    run_recheck(project)

    database = opened(project)
    version = database.schema_version()
    database.close()

    assert version == latest_schema_version()


def test_recheck_fills_the_amount_in_the_original_currency(project):
    """У EUR пересчёта нет и не будет, но само число терять нельзя."""
    path = database_at_schema(project, 1)
    fill(path)

    report = run_recheck(project)

    database = opened(project)
    euro = database.get_listing("23598471")
    database.close()

    assert euro.price_amount == 140_000
    assert euro.price_usd is None       # курса EUR мы не выдумываем
    assert report.amounts == 3


def test_recheck_does_not_move_prices_or_dates(project):
    """Пересчёт трогает помеченное, а не карточку: это не прогон по ленте."""
    path = database_at_schema(project, 1)
    fill(path)

    run_recheck(project)

    database = opened(project)
    listing = database.get_listing("24100001")
    history = database.price_history("24100001")
    database.close()

    assert listing.price_usd == 100_000
    assert listing.last_seen.isoformat().startswith("2026-09-20")
    assert history == []


def test_recheck_releases_the_lock(project):
    path = database_at_schema(project, 1)
    fill(path)

    run_recheck(project)

    assert not run_lock_path(project).exists()


def test_recheck_refuses_to_start_while_a_run_is_going(project):
    from listam.wiring import build_run_lock

    database_at_schema(project, 1)
    lock = build_run_lock(project).acquire()
    try:
        report = run_recheck(project)
    finally:
        lock.release()

    assert report.errors == 1
    assert "Прогон уже идёт" in report.notes


def test_recheck_uploads_the_rechecked_base(project):
    """База в хранилище — общая: пересчитанная копия обязана туда уехать."""
    path = database_at_schema(project, 1)
    fill(path)

    run_recheck(project)

    remote = Path(project.get("storage.directory")) / "listam.sqlite"
    assert remote.exists()
    database = SqliteDatabase(remote)
    database.connect()
    marked = database.get_listing("24076812")
    database.close()
    assert marked.anomaly
