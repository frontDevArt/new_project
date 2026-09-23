"""Объявление и его точка истории пишутся вместе или никак.

Транзакциями база управляет сама (isolation_level=None) — значит, границы
транзакции надо ставить руками. Иначе упавшая на полпути запись оставит
объявление без точки цены.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from listam.adapters.db_sqlite import SqliteDatabase
from listam.domain.models import Listing

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)  # календарь: не сравнивается с часами


@pytest.fixture
def db(tmp_path) -> SqliteDatabase:
    database = SqliteDatabase(tmp_path / "test.sqlite")
    database.connect()
    database.migrate()
    yield database
    database.close()


def listing(listing_id="1", **over) -> Listing:
    fields = dict(id=listing_id, url="https://www.list.am/ru/item/1", title="Квартира",
                  price_raw="$100,000", currency="USD", price_usd=100000.0, area=50.0)
    fields.update(over)
    return Listing(**fields)


def explode(*args, **kwargs):
    raise RuntimeError("диск кончился на середине записи")


def test_a_new_listing_without_its_price_point_is_not_written(db, monkeypatch):
    monkeypatch.setattr(db, "_add_price_point", explode)

    with pytest.raises(RuntimeError):
        db.upsert_listing(listing(), seen_at=NOW)

    assert db.get_listing("1") is None
    assert db.count_listings() == 0


def test_a_price_change_that_fails_leaves_the_old_row_alone(db, monkeypatch):
    db.upsert_listing(listing(), seen_at=NOW)
    monkeypatch.setattr(db, "_add_price_point", explode)

    with pytest.raises(RuntimeError):
        db.upsert_listing(listing(price_raw="$90,000", price_usd=90000.0), seen_at=NOW)

    assert db.get_listing("1").price_raw == "$100,000"
    assert len(db.price_history("1")) == 1
