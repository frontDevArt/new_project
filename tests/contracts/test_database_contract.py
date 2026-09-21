"""Контрактный тест порта Database: одинаков для любой реализации."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from listam.adapters.db_sqlite import SqliteDatabase
from listam.domain.models import Listing
from listam.ports.database import Database

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(params=["sqlite"])
def db(request, tmp_path):
    database = SqliteDatabase(tmp_path / "test.sqlite")
    database.connect()
    database.migrate()
    yield database
    database.close()


def make_listing(listing_id="24254997", **over) -> Listing:
    fields = dict(
        id=listing_id,
        url=f"https://www.list.am/ru/item/{listing_id}",
        title="3 комн. квартира, 85 м²",
        district="Центр",
        street="ул. Туманяна",
        price_raw="$132,000",
        currency="USD",
        price_usd=132000.0,
        price_amd=50820000.0,
        area=85.0,
        rooms=3,
        floor=4,
        floors_total=9,
        price_per_sqm=1552.94,
        seller_type="owner",
        verified=True,
        new_build=False,
    )
    fields.update(over)
    return Listing(**fields)


def test_is_a_database(db):
    assert isinstance(db, Database)


def test_migrate_is_idempotent(db):
    db.migrate()
    db.migrate()
    assert db.schema_version() >= 1


def test_all_spec_tables_exist(db):
    expected = {"listings", "price_history", "requests", "matches", "contacts", "runs"}
    assert expected <= db.table_names()


def test_new_listing_is_reported_as_new(db):
    assert db.upsert_listing(make_listing(), seen_at=NOW) == "new"


def test_new_listing_is_stored_and_read_back(db):
    db.upsert_listing(make_listing(), seen_at=NOW)
    stored = db.get_listing("24254997")
    assert stored.price_usd == 132000.0
    assert stored.district == "Центр"
    assert stored.seller_type == "owner"


def test_first_seen_is_kept_and_last_seen_moves(db):
    db.upsert_listing(make_listing(), seen_at=NOW)
    db.upsert_listing(make_listing(), seen_at=LATER)
    stored = db.get_listing("24254997")
    assert stored.first_seen == NOW
    assert stored.last_seen == LATER


def test_same_listing_seen_again_is_unchanged(db):
    db.upsert_listing(make_listing(), seen_at=NOW)
    assert db.upsert_listing(make_listing(), seen_at=LATER) == "unchanged"


def test_changed_price_is_reported(db):
    db.upsert_listing(make_listing(), seen_at=NOW)
    cheaper = make_listing(price_usd=125000.0, price_raw="$125,000")
    assert db.upsert_listing(cheaper, seen_at=LATER) == "price_changed"


def test_price_history_gets_a_row_per_price(db):
    db.upsert_listing(make_listing(), seen_at=NOW)
    db.upsert_listing(make_listing(), seen_at=LATER)  # та же цена — не пишем
    db.upsert_listing(make_listing(price_usd=125000.0), seen_at=LATER)
    history = db.price_history("24254997")
    assert [row.price_usd for row in history] == [132000.0, 125000.0]


def test_known_ids_returns_stored_ids(db):
    db.upsert_listing(make_listing("1"), seen_at=NOW)
    db.upsert_listing(make_listing("2"), seen_at=NOW)
    assert db.known_ids() == {"1", "2"}


def test_missing_listing_reads_as_none(db):
    assert db.get_listing("нет-такого") is None


def test_listing_survives_missing_optional_fields(db):
    sparse = make_listing("777", district=None, area=None, rooms=None, price_usd=None)
    db.upsert_listing(sparse, seen_at=NOW)
    assert db.get_listing("777").area is None


def test_run_is_journaled(db):
    run_id = db.start_run(started_at=NOW, rate_amd_per_usd=385.0)
    db.finish_run(run_id, finished_at=LATER, pages_fetched=20, new_listings=5,
                  updated_listings=2, errors=0)
    run = db.last_run()
    assert run.pages_fetched == 20
    assert run.new_listings == 5
    assert run.rate_amd_per_usd == 385.0
    assert run.finished_at == LATER


def test_timestamps_round_trip_as_utc(db):
    db.upsert_listing(make_listing(), seen_at=NOW)
    assert db.get_listing("24254997").last_seen.tzinfo is not None
