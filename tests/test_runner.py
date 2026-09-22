"""Каркас оркестрации: пять команд делали одно и то же пятью способами.

Здесь проверяется только общий кусок — замок, копия из хранилища, миграции,
проверка схемы и заливка. Что считать ошибкой и что печатать человеку,
решает каждая команда сама, и это проверяется в её собственных тестах.
"""
from __future__ import annotations

import pytest

from listam.runner import SessionRefused, publish, working_session


def test_the_session_gives_an_open_database_and_releases_the_lock(runner_config):
    with working_session(runner_config) as session:
        assert session.database.schema_version() > 0
    with working_session(runner_config) as again:
        assert again.database.schema_version() > 0, "замок отпущен"


def test_a_busy_lock_is_a_refusal_and_not_a_crash(runner_config):
    with working_session(runner_config):
        with pytest.raises(SessionRefused) as exc:
            with working_session(runner_config):
                pass
    assert "замок" in str(exc.value).lower()


def test_a_broken_database_is_a_refusal_naming_the_trouble(runner_config):
    """Битый файл — отказ с причиной, а не трейсбек поверх всего остального.

    Одна из пяти копий ловила здесь `OSError` и падала на `sqlite3.Error`
    (находка M-8): человек видел трейсбек вместо строки отчёта.
    """
    from listam.wiring import database_path

    path = database_path(runner_config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a database")
    with pytest.raises(SessionRefused) as exc:
        with working_session(runner_config):
            pass
    assert "файл базы недоступен" in str(exc.value)
    assert "not a database" in str(exc.value)


def test_a_schema_older_than_the_code_is_a_refusal_naming_both_versions(
        runner_config, monkeypatch):
    """Код ждёт больше, чем есть в базе, — работать нельзя.

    Версию кода задаёт старшая миграция на диске; подменяется именно она,
    потому что базу сеанс сперва мигрирует, и отстать она может только от
    кода, которого на диске ещё нет.
    """
    import listam.runner as runner

    with working_session(runner_config) as session:
        current = session.database.schema_version()
    monkeypatch.setattr(runner, "latest_schema_version", lambda: current + 1)

    with pytest.raises(SessionRefused) as exc:
        with working_session(runner_config):
            pass
    assert str(current) in str(exc.value) and str(current + 1) in str(exc.value)


def test_a_migrating_command_works_on_an_old_schema(runner_config, monkeypatch):
    """`recheck` — та самая команда, которой отказ по схеме советует накатить
    миграции. Отказывать ей самой значило бы запереть базу насовсем."""
    import listam.runner as runner

    with working_session(runner_config) as session:
        current = session.database.schema_version()
    monkeypatch.setattr(runner, "latest_schema_version", lambda: current + 1)

    with working_session(runner_config, needs_schema=False) as session:
        assert session.database.schema_version() == current


def test_publishing_closes_the_database_and_puts_a_copy_in_storage(runner_config):
    from listam.wiring import build_storage

    with working_session(runner_config) as session:
        assert publish(session, runner_config, "база") is True
        assert session.closed is True

    assert build_storage(runner_config).exists("listam.sqlite")
    assert "база залита в хранилище" in session.notes
    assert not session.local_db.with_name(session.local_db.name + ".snapshot").exists()


def test_a_failed_upload_is_a_note_and_not_a_traceback(runner_config):
    """База уже записана: отказ сети — это строка отчёта, а не потеря работы."""
    with working_session(runner_config) as session:
        def refuse(*args, **kwargs):
            raise OSError("сеть недоступна")

        session.storage.upload = refuse
        assert publish(session, runner_config, "база") is True

    assert any("не залита в хранилище" in note for note in session.notes)
