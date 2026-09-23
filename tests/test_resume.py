"""Продолжение прерванного обхода: `--resume` идёт дальше, а не по кругу.

Обход по полной ленте — это не одна строка журнала: полный прогон и
продолжающие его `resume` идут по одной и той же ленте. Мерка продолжения —
самая дальняя пройденная страница среди них.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from listam.adapters.db_sqlite import SqliteDatabase
from listam.crawler import resume_start_page

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)  # календарь: не сравнивается с часами


@pytest.fixture
def database(tmp_path) -> SqliteDatabase:
    db = SqliteDatabase(tmp_path / "test.sqlite")
    db.connect()
    db.migrate()
    yield db
    db.close()


def broken(db, mode: str, *, last_page: int, minutes: int) -> int:
    """Прогон, который дошёл до страницы и оборвался."""
    stamp = NOW + timedelta(minutes=minutes)
    run_id = db.start_run(stamp, 400.0, mode=mode)
    db.mark_page(run_id, last_page)
    db.finish_run(run_id, stamp, pages_fetched=last_page, errors=1)
    return run_id


def test_a_second_resume_goes_on_from_where_the_first_one_stopped(database):
    """Полный встал на 100-й, `--resume` дошёл до 150-й и тоже встал.

    Второй `--resume` обязан идти со 151-й, а не выбрасывать 50 страниц:
    последняя строка журнала — это продолжение, и её `last_page` дальше.
    """
    broken(database, "full", last_page=100, minutes=0)
    broken(database, "resume", last_page=150, minutes=30)

    page, note = resume_start_page(database.crawl_to_resume())

    assert page == 151
    assert "151" in (note or "")
