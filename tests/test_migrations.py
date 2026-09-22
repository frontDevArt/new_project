"""M2: миграция накатывается целиком или никак.

Падение посреди 002 не должно оставлять половину схемы без строки в
schema_version — иначе следующий запуск попробует накатить её заново и
упадёт уже на «таблица существует».
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from listam.adapters.db_sqlite import MIGRATIONS_DIR, SqliteDatabase
from listam.domain.models import Listing


def migrations(tmp_path, **files):
    directory = tmp_path / "migrations"
    directory.mkdir()
    for name, body in files.items():
        (directory / name).write_text(body, encoding="utf-8")
    return directory


def upto(tmp_path: Path, version: int) -> Path:
    """Папка с миграциями по N-ю включительно: имитация базы, отставшей на версию."""
    partial = tmp_path / f"migrations-{version}"
    partial.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if int(path.name.split("_", 1)[0]) <= version:
            shutil.copyfile(path, partial / path.name)
    return partial


def opened(tmp_path, directory) -> SqliteDatabase:
    database = SqliteDatabase(tmp_path / "test.sqlite", migrations_dir=directory)
    database.connect()
    return database


def test_all_statements_of_a_migration_are_applied(tmp_path):
    directory = migrations(
        tmp_path,
        **{"001_initial.sql": "CREATE TABLE a (id INTEGER);\nCREATE TABLE b (id INTEGER);"},
    )
    database = opened(tmp_path, directory)

    database.migrate()

    assert {"a", "b"} <= database.table_names()
    assert database.schema_version() == 1
    database.close()


def test_a_migration_that_fails_halfway_leaves_no_trace(tmp_path):
    directory = migrations(
        tmp_path,
        **{
            "001_initial.sql": "CREATE TABLE a (id INTEGER);",
            "002_broken.sql": "CREATE TABLE b (id INTEGER);\nЭТО НЕ SQL;",
        },
    )
    database = opened(tmp_path, directory)

    with pytest.raises(sqlite3.Error):
        database.migrate()

    assert "b" not in database.table_names()
    assert database.schema_version() == 1
    database.close()


def test_the_next_run_applies_the_fixed_migration(tmp_path):
    """Половины схемы не осталось — значит, исправленная 002 накатывается начисто."""
    directory = migrations(
        tmp_path,
        **{
            "001_initial.sql": "CREATE TABLE a (id INTEGER);",
            "002_broken.sql": "CREATE TABLE b (id INTEGER);\nЭТО НЕ SQL;",
        },
    )
    database = opened(tmp_path, directory)
    with pytest.raises(sqlite3.Error):
        database.migrate()
    database.close()

    (directory / "002_broken.sql").write_text("CREATE TABLE b (id INTEGER);", encoding="utf-8")
    database = opened(tmp_path, directory)
    database.migrate()

    assert "b" in database.table_names()
    assert database.schema_version() == 2
    database.close()


def test_the_real_migrations_apply_to_an_empty_database(tmp_path):
    database = SqliteDatabase(tmp_path / "real.sqlite")
    database.connect()
    database.migrate()

    assert database.schema_version() >= 2
    assert {"listings", "price_history", "runs"} <= database.table_names()
    database.close()


def test_migration_003_adds_the_amount_in_original_currency(tmp_path):
    database = SqliteDatabase(tmp_path / "real.sqlite")
    database.connect()
    database.migrate()

    assert database.schema_version() >= 3
    columns = {
        row[1] for row in database.conn.execute("PRAGMA table_info(listings)").fetchall()
    }
    assert "price_amount" in columns
    database.close()


def test_a_trigger_survives_the_split_into_statements(tmp_path):
    """Находка 17: `;` внутри BEGIN…END резал триггер на огрызки.

    Разрез по каждому `;` превращал тело триггера в отдельный оператор:
    миграция падала на первой же половине, а схема оставалась без триггера.
    """
    directory = migrations(
        tmp_path,
        **{
            "001_initial.sql": (
                "CREATE TABLE a (id INTEGER, note TEXT);\n"
                "CREATE TABLE log (id INTEGER);\n"
                "CREATE TRIGGER a_logged AFTER INSERT ON a\n"
                "BEGIN\n"
                "  INSERT INTO log(id) VALUES (NEW.id);\n"
                "  UPDATE a SET note = 'записано' WHERE id = NEW.id;\n"
                "END;\n"
            )
        },
    )
    database = opened(tmp_path, directory)

    database.migrate()

    database.conn.execute("INSERT INTO a(id) VALUES (7)")
    assert database.conn.execute("SELECT id FROM log").fetchone()["id"] == 7
    assert database.schema_version() == 1
    database.close()


def test_a_semicolon_inside_a_literal_is_not_a_statement_break(tmp_path):
    directory = migrations(
        tmp_path,
        **{"001_initial.sql": "CREATE TABLE a (id INTEGER, note TEXT DEFAULT 'раз; два');"},
    )
    database = opened(tmp_path, directory)

    database.migrate()

    database.conn.execute("INSERT INTO a(id) VALUES (1)")
    assert database.conn.execute("SELECT note FROM a").fetchone()["note"] == "раз; два"
    database.close()


def test_two_dashes_inside_a_literal_are_not_a_comment(tmp_path):
    """`--` в строке — это данные, а не начало комментария."""
    directory = migrations(
        tmp_path,
        **{"001_initial.sql": "CREATE TABLE a (note TEXT DEFAULT 'до -- после');"},
    )
    database = opened(tmp_path, directory)

    database.migrate()

    database.conn.execute("INSERT INTO a DEFAULT VALUES")
    assert database.conn.execute("SELECT note FROM a").fetchone()["note"] == "до -- после"
    database.close()


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
    assert database.schema_version() >= 4
    columns = {row["name"] for row in database.conn.execute("PRAGMA table_info(listings)")}
    assert "gone_at" in columns
    run_columns = {row["name"] for row in database.conn.execute("PRAGMA table_info(runs)")}
    assert {"mode", "price_changed", "gone_marked", "stop_reason"} <= run_columns
    assert database.get_listing("1").price_raw == "100,000"   # данные на месте
    database.close()


def test_migrations_005_and_006_add_the_return_columns_to_a_filled_database(tmp_path):
    """Схема 4 с данными доезжает до 6, ничего не потеряв.

    Считать возвраты задним числом нечем, и это нормально: колонки заводятся
    пустыми, а заполняет их первый же прогон новым кодом.
    """
    database = SqliteDatabase(tmp_path / "listam.sqlite", migrations_dir=upto(tmp_path, 4))
    database.connect()
    database.migrate()
    database.upsert_listing(
        Listing(id="1", url="https://www.list.am/ru/item/1", price_raw="100,000", currency="USD"),
        seen_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
    )
    run_id = database.start_run(datetime(2026, 9, 21, tzinfo=timezone.utc), 400.0)
    database.finish_run(run_id, datetime(2026, 9, 21, tzinfo=timezone.utc), pages_fetched=9)
    database.close()

    database = SqliteDatabase(tmp_path / "listam.sqlite")   # все миграции с диска
    database.connect()
    database.migrate()

    assert database.schema_version() >= 6     # дальше идут миграции M2
    run_columns = {row["name"] for row in database.conn.execute("PRAGMA table_info(runs)")}
    assert "returned" in run_columns
    columns = {row["name"] for row in database.conn.execute("PRAGMA table_info(listings)")}
    assert "returned_at" in columns
    assert database.get_listing("1").price_raw == "100,000"   # данные на месте
    assert database.get_listing("1").returned_at is None
    assert database.last_run().returned == 0
    assert database.last_run().pages_fetched == 9
    database.close()


def test_migration_007_adds_request_and_match_columns(tmp_path):
    """Схема 7: колонки, на которые опирается весь матчинг M2."""
    db = SqliteDatabase(tmp_path / "m7.sqlite")
    db.connect()
    db.migrate()

    assert db.schema_version() >= 7     # дальше идут миграции QA-ужесточения
    requests_columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(requests)")}
    assert {"districts_priority", "floor_min", "floor_max",
            "no_first_floor", "no_last_floor", "updated_at", "source_row"} <= requests_columns
    matches_columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(matches)")}
    assert {"run_id", "breakdown", "cluster_id", "cluster_size",
            "cluster_spread_usd", "first_matched_at"} <= matches_columns
    runs_columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(runs)")}
    assert "new_matches" in runs_columns
    db.close()


def test_migration_008_adds_the_end_of_a_match_life(tmp_path):
    """Схема 8: у матча появляется конец жизни, а след звонка остаётся жив."""
    db = SqliteDatabase(tmp_path / "m8.sqlite")
    db.connect()
    db.migrate()

    assert db.schema_version() >= 8     # дальше идут миграции QA-ужесточения
    columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(matches)")}
    assert {"retired_at", "retired_reason"} <= columns
    db.close()


def test_migration_009_remembers_when_a_request_was_matched(tmp_path):
    """Схема 9: заявка помнит, когда по ней последний раз шёл подбор."""
    db = SqliteDatabase(tmp_path / "m9.sqlite")
    db.connect()
    db.migrate()

    assert db.schema_version() >= 9     # дальше идут миграции M3
    columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(requests)")}
    assert "matched_at" in columns
    db.close()


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
    assert database.schema_version() >= 10
    database.close()


def test_migration_010_keeps_what_was_in_the_base(tmp_path):
    """Миграция ничего не закрывает и ничего не считает отправленным."""
    database = opened(tmp_path, upto(tmp_path, 9))
    database.migrate()
    database.conn.execute(
        "INSERT INTO listings (id, url, status, first_seen, last_seen) "
        "VALUES ('1', 'u', 'active', '2026-09-21T10:00:00+00:00', "
        "'2026-09-21T10:00:00+00:00')"
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
