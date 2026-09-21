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
    db.upsert_listing(
        make_listing(price_raw="$125,000", price_usd=125000.0), seen_at=LATER
    )
    history = db.price_history("24254997")
    assert [row.price_usd for row in history] == [132000.0, 125000.0]


def test_recomputed_price_alone_adds_no_history_point(db):
    """Курс сдвинулся — на сайте не изменилось ничего, истории цен тоже."""
    db.upsert_listing(make_listing(), seen_at=NOW, rate_amd_per_usd=385.0)

    outcome = db.upsert_listing(
        make_listing(price_amd=52_800_000.0), seen_at=LATER, rate_amd_per_usd=400.0
    )

    assert outcome == "unchanged"
    assert len(db.price_history("24254997")) == 1


def test_history_point_keeps_the_rate_it_was_written_with(db):
    db.upsert_listing(make_listing(), seen_at=NOW, rate_amd_per_usd=385.0)

    assert db.price_history("24254997")[0].rate_amd_per_usd == 385.0


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


def test_update_keeps_computed_prices_when_the_new_ones_are_missing(db):
    """Пустой пересчёт — это «не смог посчитать», а не «цены больше нет»."""
    db.upsert_listing(make_listing(), seen_at=NOW)

    blind = make_listing(price_usd=None, price_amd=None, price_per_sqm=None)
    db.upsert_listing(blind, seen_at=LATER)

    stored = db.get_listing("24254997")
    assert stored.price_usd == 132000.0
    assert stored.price_amd == 50820000.0
    assert stored.price_per_sqm == 1552.94


def test_update_overwrites_computed_prices_with_a_new_value(db):
    db.upsert_listing(make_listing(), seen_at=NOW)

    db.upsert_listing(make_listing(price_usd=125000.0), seen_at=LATER)

    assert db.get_listing("24254997").price_usd == 125000.0


def test_run_remembers_how_far_the_crawl_got(db):
    """Номер последней пройденной страницы — с неё продолжает `scrape --resume`."""
    run_id = db.start_run(NOW, 400.0)

    db.mark_page(run_id, 7)

    assert db.last_run().last_page == 7


def test_last_successful_run_skips_the_ones_with_errors(db):
    good = db.start_run(NOW, 400.0)
    db.finish_run(good, LATER, pages_fetched=9, errors=0)
    bad = db.start_run(LATER, 400.0)
    db.finish_run(bad, LATER, pages_fetched=2, errors=1)
    db.start_run(LATER, 400.0)              # ещё не закончился

    assert db.last_successful_run().id == good
    assert db.last_run().id != good


def test_snapshot_shows_what_has_not_reached_the_main_file_yet(db, tmp_path):
    """Снимок целен: в него попадает и то, что лежит ещё в соседнем `-wal`."""
    db.upsert_listing(make_listing(), seen_at=NOW)
    target = tmp_path / "snapshot.sqlite"

    db.snapshot(target)

    import sqlite3

    connection = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    try:
        count = connection.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    finally:
        connection.close()
    assert count == 1
    assert not target.with_name(target.name + "-wal").exists()


def test_currency_change_overwrites_recomputed_prices(db):
    """ВЫСОКИЙ 7: карточка переехала в евро — старые доллары не остаются висеть."""
    db.upsert_listing(
        make_listing(price_raw="100,000 $", currency="USD",
                     price_usd=100000.0, price_amd=None, price_per_sqm=2000.0),
        seen_at=NOW,
    )

    db.upsert_listing(
        make_listing(price_raw="90,000 €", currency="EUR",
                     price_usd=None, price_amd=None, price_per_sqm=None),
        seen_at=LATER,
    )

    stored = db.get_listing("24254997")
    assert stored.currency == "EUR"
    assert stored.price_raw == "90,000 €"
    assert stored.price_usd is None
    assert stored.price_amd is None
    assert stored.price_per_sqm is None


def test_amount_in_original_currency_survives_the_roundtrip(db):
    """ВЫСОКИЙ 9: курса EUR нет, но само число обязано лежать в базе."""
    db.upsert_listing(
        make_listing(price_raw="140,000 €", currency="EUR", price_amount=140000.0,
                     price_usd=None, price_amd=None, price_per_sqm=None),
        seen_at=NOW,
    )

    assert db.get_listing("24254997").price_amount == 140000.0
