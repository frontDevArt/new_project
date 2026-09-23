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

RUN = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)  # календарь: не сравнивается с часами


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
