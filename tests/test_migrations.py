"""M2: миграция накатывается целиком или никак.

Падение посреди 002 не должно оставлять половину схемы без строки в
schema_version — иначе следующий запуск попробует накатить её заново и
упадёт уже на «таблица существует».
"""
from __future__ import annotations

import sqlite3

import pytest

from listam.adapters.db_sqlite import SqliteDatabase


def migrations(tmp_path, **files):
    directory = tmp_path / "migrations"
    directory.mkdir()
    for name, body in files.items():
        (directory / name).write_text(body, encoding="utf-8")
    return directory


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

    assert database.schema_version() == 3
    columns = {
        row[1] for row in database.conn.execute("PRAGMA table_info(listings)").fetchall()
    }
    assert "price_amount" in columns
    database.close()
