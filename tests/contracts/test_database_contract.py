"""Контрактный тест порта Database: одинаков для любой реализации."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from listam.adapters.db_sqlite import SqliteDatabase
from listam.domain.models import Listing, ListingPage, Match, PageFields, Request
from listam.ports.database import Database

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)  # календарь: не сравнивается с часами
LATER = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)  # календарь: не сравнивается с часами
EVEN_LATER = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)  # календарь: не сравнивается с часами


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


def test_set_computed_touches_only_the_marks(db):
    """Блокер 5: пересчёт переписывает пометку и сумму — и ничего больше."""
    db.upsert_listing(make_listing(price_raw="$ 100,000", currency="USD",
                                   price_usd=100000.0), seen_at=NOW)
    before = db.get_listing("24254997")
    history_before = db.price_history("24254997")

    db.set_computed("24254997", anomaly="price_usd", price_amount=100000.0)

    after = db.get_listing("24254997")
    assert after.anomaly == "price_usd"
    assert after.price_amount == 100000.0
    assert after.price_usd == before.price_usd
    assert after.last_seen == before.last_seen
    assert db.price_history("24254997") == history_before


def test_set_computed_can_clear_a_mark(db):
    """Пометка снимается так же, как ставится: порог мог измениться."""
    db.upsert_listing(make_listing(), seen_at=NOW)
    db.set_computed("24254997", anomaly="area", price_amount=None)

    db.set_computed("24254997", anomaly=None, price_amount=None)

    assert db.get_listing("24254997").anomaly is None


def test_mark_gone_marks_only_what_was_active(db):
    db.upsert_listing(make_listing("1"), seen_at=NOW)
    db.upsert_listing(make_listing("2"), seen_at=NOW)

    marked = db.mark_gone({"1"}, gone_at=LATER)

    assert marked == 1
    assert db.get_listing("1").status == "gone"
    assert db.get_listing("1").gone_at == LATER
    assert db.get_listing("2").status == "active"
    assert db.get_listing("2").gone_at is None


def test_mark_gone_does_not_move_the_date_of_an_already_gone_listing(db):
    """Объявление снято один раз. Второй обход не имеет права молодить дату."""
    db.upsert_listing(make_listing("1"), seen_at=NOW)
    db.mark_gone({"1"}, gone_at=LATER)

    assert db.mark_gone({"1"}, gone_at=EVEN_LATER) == 0
    assert db.get_listing("1").gone_at == LATER


def test_mark_gone_leaves_last_seen_alone(db):
    """Снятие — не встреча: объявление никто не видел, и дата встречи стоит на месте."""
    db.upsert_listing(make_listing("1"), seen_at=NOW)
    db.mark_gone({"1"}, gone_at=LATER)

    assert db.get_listing("1").last_seen == NOW


def test_a_listing_back_on_the_feed_forgets_that_it_was_gone(db):
    db.upsert_listing(make_listing("1"), seen_at=NOW)
    db.mark_gone({"1"}, gone_at=LATER)

    db.upsert_listing(make_listing("1"), seen_at=EVEN_LATER)

    back = db.get_listing("1")
    assert back.status == "active"
    assert back.gone_at is None
    assert back.first_seen == NOW          # то же объявление, а не новое


def test_active_ids_skips_the_gone_ones(db):
    db.upsert_listing(make_listing("1"), seen_at=NOW)
    db.upsert_listing(make_listing("2"), seen_at=NOW)
    db.mark_gone({"2"}, gone_at=LATER)

    assert db.active_ids() == {"1"}
    assert db.known_ids() == {"1", "2"}   # известны по-прежнему обе


def test_run_remembers_its_mode_and_delta_counters(db):
    run_id = db.start_run(NOW, rate_amd_per_usd=400.0, mode="fresh")
    db.finish_run(run_id, LATER, price_changed=3, gone_marked=7,
                  stop_reason="2 страниц подряд без новых объявлений")

    stored = db.last_run()
    assert stored.mode == "fresh"
    assert stored.price_changed == 3
    assert stored.gone_marked == 7
    assert stored.stop_reason == "2 страниц подряд без новых объявлений"


def test_the_yardstick_is_the_last_full_run_not_the_last_short_one(db):
    """Мерка полноты — только полный обход: инкрементальный прогон на двух
    страницах не имеет права стать нормой для следующего полного."""
    full = db.start_run(NOW, 400.0, mode="full")
    db.finish_run(full, NOW, pages_fetched=215, errors=0)
    fresh = db.start_run(LATER, 400.0, mode="fresh")
    db.finish_run(fresh, LATER, pages_fetched=2, errors=0)
    short = db.start_run(LATER, 400.0, mode="partial")
    db.finish_run(short, LATER, pages_fetched=5, errors=0)

    assert db.last_successful_run().pages_fetched == 215


def test_last_run_can_be_asked_about_one_mode(db):
    """`--resume` продолжает прерванный полный обход, а не инкрементальный,
    который прошёл между ними."""
    full = db.start_run(NOW, 400.0, mode="full")
    db.mark_page(full, 12)
    fresh = db.start_run(LATER, 400.0, mode="fresh")
    db.finish_run(fresh, LATER, pages_fetched=2, errors=0)

    assert db.last_run().mode == "fresh"
    assert db.last_run(mode="full").last_page == 12


def test_a_listing_back_from_the_dead_reports_itself(db):
    """«Вернулось на рынок» — событие рынка, а не правка поля.

    Апсерт обязан сказать об этом отдельно: иначе возврат проходит как
    `unchanged` и не виден нигде — ни счётчиком, ни разделом дельты.
    """
    db.upsert_listing(make_listing(), seen_at=NOW)
    db.mark_gone(["24254997"], gone_at=LATER)

    assert db.upsert_listing(make_listing(), seen_at=EVEN_LATER) == "returned"


def test_a_listing_that_came_back_cheaper_is_still_a_return(db):
    """Возврат со сменой цены — всё равно возврат.

    Точка в истории цен ставится как обычно, но исход прогон читает как
    `returned`: «вернулось» — событие крупнее, чем «подвинуло цену», и в
    обновления оно не входит.
    """
    db.upsert_listing(make_listing(), seen_at=NOW)
    db.mark_gone(["24254997"], gone_at=LATER)

    cheaper = make_listing(price_raw="$120,000", price_usd=120000.0)
    outcome = db.upsert_listing(cheaper, seen_at=EVEN_LATER, rate_amd_per_usd=400.0)

    assert outcome == "returned"
    assert [point.price_usd for point in db.price_history("24254997")] == [132000.0, 120000.0]


def test_a_listing_still_on_the_feed_is_not_a_return(db):
    """Обычная встреча возвратом не становится: иначе счётчик считал бы всю ленту."""
    db.upsert_listing(make_listing(), seen_at=NOW)

    assert db.upsert_listing(make_listing(), seen_at=LATER) == "unchanged"


def test_returned_listings_are_the_ones_that_came_back_after_the_mark(db):
    """Раздел «Вернулись» спрашивается у даты возврата, а не у `last_seen`.

    `last_seen` двигает каждый прогон всей ленте: по нему возврат от обычной
    встречи не отличить.
    """
    db.upsert_listing(make_listing("1"), seen_at=NOW)
    db.upsert_listing(make_listing("2"), seen_at=NOW)
    db.mark_gone(["1"], gone_at=NOW)
    db.upsert_listing(make_listing("1"), seen_at=EVEN_LATER)
    db.upsert_listing(make_listing("2"), seen_at=EVEN_LATER)   # просто встретилось снова

    assert [item.id for item in db.listings_returned_since(LATER)] == ["1"]


def test_a_return_before_the_mark_is_out_of_the_window(db):
    db.upsert_listing(make_listing("1"), seen_at=NOW)
    db.mark_gone(["1"], gone_at=NOW)
    db.upsert_listing(make_listing("1"), seen_at=NOW)

    assert db.listings_returned_since(LATER) == []


def test_the_crawl_to_resume_is_just_the_interrupted_full_one(db):
    """Продолжений не было — продолжать надо сам прерванный полный обход."""
    full = db.start_run(NOW, 400.0, mode="full")
    db.mark_page(full, 100)
    db.finish_run(full, LATER, pages_fetched=100, errors=1)

    assert db.crawl_to_resume().last_page == 100


def test_the_crawl_to_resume_counts_the_pages_its_continuations_walked(db):
    """Обход — это полный прогон плюс продолжающие его `resume`.

    Мерка — самая дальняя пройденная страница среди них, а не последняя
    строка журнала: иначе второе продолжение выбрасывает всё, что прошло первое.
    """
    full = db.start_run(NOW, 400.0, mode="full")
    db.mark_page(full, 100)
    db.finish_run(full, LATER, pages_fetched=100, errors=1)
    first = db.start_run(LATER, 400.0, mode="resume")
    db.mark_page(first, 150)
    db.finish_run(first, LATER, pages_fetched=50, errors=1)
    second = db.start_run(EVEN_LATER, 400.0, mode="resume")
    db.mark_page(second, 190)
    db.finish_run(second, EVEN_LATER, pages_fetched=40, errors=1)

    assert db.crawl_to_resume().last_page == 190


def test_a_crawl_finished_by_a_continuation_has_nothing_left_to_resume(db):
    """Продолжение дошло до конца ленты без ошибок — обход закрыт.

    Следующий `--resume` обязан увидеть это и пойти с первой страницы,
    а не досматривать ленту, которую уже досмотрели.
    """
    full = db.start_run(NOW, 400.0, mode="full")
    db.mark_page(full, 100)
    db.finish_run(full, LATER, pages_fetched=100, errors=1)
    done = db.start_run(LATER, 400.0, mode="resume")
    db.mark_page(done, 215)
    db.finish_run(done, EVEN_LATER, pages_fetched=115, errors=0)

    crawl = db.crawl_to_resume()

    assert crawl.finished_at is not None and not crawl.errors


def test_a_successful_full_crawl_has_nothing_to_resume(db):
    full = db.start_run(NOW, 400.0, mode="full")
    db.mark_page(full, 215)
    db.finish_run(full, LATER, pages_fetched=215, errors=0)

    crawl = db.crawl_to_resume()

    assert crawl.finished_at is not None and not crawl.errors


def test_a_fresh_run_is_not_a_continuation_of_the_full_one(db):
    """`--fresh` идёт по голове ленты, а не по тому месту, где встал полный.

    Его страницы обходу не засчитываются: иначе продолжение прыгнуло бы
    вперёд, ни разу не увидев середины ленты.
    """
    full = db.start_run(NOW, 400.0, mode="full")
    db.mark_page(full, 100)
    db.finish_run(full, LATER, pages_fetched=100, errors=1)
    fresh = db.start_run(LATER, 400.0, mode="fresh")
    db.mark_page(fresh, 2)
    db.finish_run(fresh, LATER, pages_fetched=2, errors=0)

    assert db.crawl_to_resume().last_page == 100


def test_an_empty_journal_has_no_crawl_to_resume(db):
    assert db.crawl_to_resume() is None


def test_legacy_runs_without_a_mode_count_as_full(db):
    """Прогоны M0 писались без режима. Они были полными — так их и читаем."""
    run_id = db.start_run(NOW, 400.0)
    db.conn.execute("UPDATE runs SET mode = NULL WHERE id = ?", (run_id,))
    db.finish_run(run_id, LATER, pages_fetched=215, errors=0)

    assert db.last_successful_run().pages_fetched == 215


def test_new_listings_are_the_ones_first_seen_after_the_mark(db):
    db.upsert_listing(make_listing("1"), seen_at=NOW)
    db.upsert_listing(make_listing("2"), seen_at=EVEN_LATER)

    fresh = db.listings_first_seen_since(LATER)

    assert [item.id for item in fresh] == ["2"]


def test_a_price_change_carries_the_price_it_had_before(db):
    db.upsert_listing(
        make_listing("1", price_raw="100,000", price_usd=100_000.0), seen_at=NOW
    )
    db.upsert_listing(
        make_listing("1", price_raw="90,000", price_usd=90_000.0), seen_at=LATER
    )

    changes = db.price_changes_since(LATER)

    assert len(changes) == 1
    item, was, now = changes[0]
    assert (item.id, was, now) == ("1", 100_000.0, 90_000.0)


def test_the_first_point_of_a_listing_is_not_a_price_change(db):
    """Появление объявления — не смена цены: прежней цены у него нет."""
    db.upsert_listing(make_listing("1", price_usd=100_000.0), seen_at=LATER)

    assert db.price_changes_since(NOW) == []


def test_gone_listings_are_the_ones_marked_after_the_mark(db):
    db.upsert_listing(make_listing("1"), seen_at=NOW)
    db.upsert_listing(make_listing("2"), seen_at=NOW)
    db.mark_gone({"1"}, gone_at=NOW)
    db.mark_gone({"2"}, gone_at=EVEN_LATER)

    assert [item.id for item in db.listings_gone_since(LATER)] == ["2"]


def test_the_gone_yardstick_is_the_last_finished_full_run(db):
    """Снятых ставит только полный обход — по нему и считается раздел «Снято».
    Ошибка прогона его не отменяет: она могла случиться уже при заливке."""
    full = db.start_run(NOW, 400.0, mode="full")
    db.finish_run(full, NOW, pages_fetched=215, errors=1)
    fresh = db.start_run(LATER, 400.0, mode="fresh")
    db.finish_run(fresh, LATER, pages_fetched=2, errors=0)
    db.start_run(EVEN_LATER, 400.0, mode="full")      # ещё идёт, снятых не ставил

    assert db.last_run_that_could_mark_gone().id == full


def test_an_empty_journal_has_no_gone_yardstick(db):
    assert db.last_run_that_could_mark_gone() is None


def test_two_moves_of_one_listing_collapse_into_one_row(db):
    """Человеку важно «было → стало» за окно, а не каждая точка истории.
    Две строки на одну квартиру он читает как две квартиры."""
    db.upsert_listing(
        make_listing("1", price_raw="$59,500", price_usd=59_500.0), seen_at=NOW
    )
    db.upsert_listing(
        make_listing("1", price_raw="$100,000", price_usd=100_000.0), seen_at=LATER
    )
    db.upsert_listing(
        make_listing("1", price_raw="$110,000", price_usd=110_000.0), seen_at=EVEN_LATER
    )

    rows = db.price_changes_since(LATER)

    assert len(rows) == 1
    item, was, now = rows[0]
    assert (item.id, was, now) == ("1", 59_500.0, 110_000.0)


def test_a_listing_born_inside_the_window_is_not_a_price_change(db):
    """Появилось и тут же подвинулось — это «Новое», а не «Цены».
    Цены на начало окна у него нет: показывать было бы нечего."""
    db.upsert_listing(
        make_listing("1", price_raw="$100,000", price_usd=100_000.0), seen_at=LATER
    )
    db.upsert_listing(
        make_listing("1", price_raw="$90,000", price_usd=90_000.0), seen_at=EVEN_LATER
    )

    assert db.price_changes_since(LATER) == []


# --- заявки ---------------------------------------------------------------

def make_request(external_id="R-1", **over) -> Request:
    fields = dict(
        external_id=external_id, client_name="Ани", client_phone="+374 00 000000",
        status="active", budget_max=120_000.0, districts=["Кентрон", "Арабкир"],
        districts_priority=["Кентрон"], rooms=[2, 3], area_min=60.0, area_max=95.0,
        floor_min=2, no_first_floor=True,
    )
    fields.update(over)
    return Request(**fields)


def test_a_new_request_is_stored_and_read_back_whole(db):
    assert db.upsert_request(make_request(), NOW) == "new"
    stored = db.get_request("R-1")
    assert stored.districts == ["Кентрон", "Арабкир"]
    assert stored.districts_priority == ["Кентрон"]
    assert stored.rooms == [2, 3]
    assert stored.no_first_floor is True
    assert stored.created_at == NOW


def test_the_same_request_read_twice_is_not_a_change(db):
    db.upsert_request(make_request(), NOW)
    assert db.upsert_request(make_request(), LATER) == "unchanged"


def test_a_changed_request_is_an_update_and_keeps_its_birthday(db):
    db.upsert_request(make_request(), NOW)
    assert db.upsert_request(make_request(budget_max=150_000.0), LATER) == "updated"
    stored = db.get_request("R-1")
    assert stored.budget_max == 150_000.0
    assert stored.created_at == NOW
    assert stored.updated_at == LATER


def test_rereading_the_same_table_does_not_move_the_update_stamp(db):
    """«Не изменилась» значит не изменилась: перечитывание раз в час не имеет
    права выглядеть как правка всех пятидесяти заявок разом."""
    db.upsert_request(make_request(), NOW)
    db.upsert_request(make_request(), LATER)
    assert db.get_request("R-1").updated_at == NOW


def test_only_active_requests_are_iterated_by_default(db):
    db.upsert_request(make_request("R-1"), NOW)
    db.upsert_request(make_request("R-2", status="paused"), NOW)
    assert [item.external_id for item in db.iter_requests()] == ["R-1"]
    assert len(list(db.iter_requests(status=None))) == 2


def test_an_unknown_request_is_none_and_not_an_error(db):
    assert db.get_request("R-404") is None


def test_a_request_gone_from_the_source_is_closed(db):
    db.upsert_request(make_request("R-1"), NOW)
    db.upsert_request(make_request("R-2"), NOW)

    closed = db.close_requests_missing_from({"R-1"}, LATER)

    assert closed == 1
    assert [item.external_id for item in db.iter_requests()] == ["R-1"]
    gone = db.get_request("R-2")
    assert gone.status == "closed"
    assert gone.updated_at == LATER


def test_closing_twice_closes_nothing_the_second_time(db):
    db.upsert_request(make_request("R-1"), NOW)
    assert db.close_requests_missing_from(set(), LATER) == 1
    assert db.close_requests_missing_from(set(), LATER) == 0


def test_a_request_the_human_paused_is_not_closed_by_absence(db):
    db.upsert_request(make_request("R-1", status="paused"), NOW)
    assert db.close_requests_missing_from(set(), LATER) == 0, (
        "закрываются только активные: приостановленную заявку человек "
        "мог убрать из таблицы нарочно, и её статус — его решение"
    )


# --- кластеры и выборка для матчинга -----------------------------------
def test_cluster_ids_are_stored_and_only_changed_rows_count(db):
    db.upsert_listing(make_listing("1"), NOW)
    db.upsert_listing(make_listing("2"), NOW)
    assert db.set_cluster_ids({"1": "abc", "2": "abc"}) == 2
    assert db.set_cluster_ids({"1": "abc", "2": "abc"}) == 0
    assert db.get_listing("1").cluster_id == "abc"


def test_a_listing_that_moved_to_another_cluster_counts_as_changed(db):
    db.upsert_listing(make_listing("1"), NOW)
    db.set_cluster_ids({"1": "abc"})
    assert db.set_cluster_ids({"1": "xyz"}) == 1
    assert db.get_listing("1").cluster_id == "xyz"


def test_matching_takes_only_active_and_clean_listings(db):
    db.upsert_listing(make_listing("1"), NOW)
    db.upsert_listing(make_listing("2", anomaly="цена за метр вне порога"), NOW)
    db.upsert_listing(make_listing("3"), NOW)
    db.mark_gone(["3"], LATER)
    assert [item.id for item in db.listings_for_matching()] == ["1"]


def test_matching_since_a_mark_takes_only_what_appeared_after_it(db):
    db.upsert_listing(make_listing("1"), NOW)
    db.upsert_listing(make_listing("2"), EVEN_LATER)
    assert [item.id for item in db.listings_for_matching(since=LATER)] == ["2"]


def test_touched_since_finds_the_newcomer(db):
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_listing(make_listing("L-2"), LATER)
    found = {item.id for item in db.listings_touched_since(LATER)}
    assert found == {"L-2"}


def test_touched_since_finds_the_one_that_changed_its_price(db):
    db.upsert_listing(make_listing("L-1", price_usd=130_000.0,
                                   price_raw="130000 $", currency="USD"), NOW)
    db.upsert_listing(make_listing("L-1", price_usd=118_000.0,
                                   price_raw="118000 $", currency="USD"), LATER)
    found = {item.id for item in db.listings_touched_since(LATER)}
    assert found == {"L-1"}, (
        "квартира, которая наконец влезла в бюджет, — главное событие рынка "
        "и не имеет права ждать ночного полного пересчёта"
    )


def test_touched_since_finds_the_one_that_came_back(db):
    db.upsert_listing(make_listing("L-1"), NOW)
    db.mark_gone(["L-1"], NOW)
    db.upsert_listing(make_listing("L-1"), LATER)
    assert {item.id for item in db.listings_touched_since(LATER)} == {"L-1"}


def test_touched_since_skips_the_untouched(db):
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_listing(make_listing("L-1"), LATER)     # та же карточка, ничего не менялось
    assert db.listings_touched_since(LATER) == []


def test_touched_since_keeps_the_rules_of_the_matching_selection(db):
    db.upsert_listing(make_listing("L-1"), LATER)
    db.upsert_listing(make_listing("L-2"), LATER)
    db.set_computed("L-2", anomaly="цена", price_amount=1.0)
    assert {item.id for item in db.listings_touched_since(LATER)} == {"L-1"}


def test_marking_a_request_matched_is_not_an_edit_of_it(db):
    db.upsert_request(make_request(), NOW)
    request = db.get_request("R-1")
    db.mark_requests_matched([request.id], LATER)
    stored = db.get_request("R-1")
    assert stored.matched_at == LATER
    assert stored.updated_at == NOW, "подбор заявку не правит"
    assert db.upsert_request(make_request(), LATER) == "unchanged"


# --- матчи --------------------------------------------------------------
def stored_request(db, external_id="R-1"):
    db.upsert_request(make_request(external_id), NOW)
    return db.get_request(external_id)


def test_a_new_match_is_stored_with_both_timestamps(db):
    request = stored_request(db)
    db.upsert_listing(make_listing("1"), NOW)
    assert db.upsert_match(Match(request_id=request.id, listing_id="1", score=82.0,
                                 breakdown={"budget": [30, 30]}), NOW) == "new"
    match = db.matches_for_request(request.id)[0]
    assert match.first_matched_at == NOW and match.matched_at == NOW
    assert match.breakdown == {"budget": [30, 30]}


def test_rematching_updates_the_score_and_keeps_the_birthday(db):
    request = stored_request(db)
    db.upsert_listing(make_listing("1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="1", score=82.0), NOW)
    assert db.upsert_match(Match(request_id=request.id, listing_id="1", score=91.0),
                           LATER) == "updated"
    match = db.matches_for_request(request.id)[0]
    assert match.score == 91.0
    assert match.first_matched_at == NOW
    assert match.matched_at == LATER


def test_a_status_set_by_a_human_survives_the_recount(db):
    # Решение 7: статус — это след звонка, а не вычисленное значение.
    request = stored_request(db)
    db.upsert_listing(make_listing("1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="1", score=82.0), NOW)
    match = db.matches_for_request(request.id)[0]
    db.set_match_status(match.id, "called", reject_reason="первый этаж не смотрим")
    db.upsert_match(Match(request_id=request.id, listing_id="1", score=91.0), LATER)
    after = db.matches_for_request(request.id)[0]
    assert after.status == "called"
    assert after.reject_reason == "первый этаж не смотрим"
    assert after.score == 91.0


def test_the_same_match_twice_is_not_a_change(db):
    request = stored_request(db)
    db.upsert_listing(make_listing("1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="1", score=82.0), NOW)
    assert db.upsert_match(Match(request_id=request.id, listing_id="1", score=82.0),
                           LATER) == "unchanged"


def test_an_unchanged_match_does_not_move_the_recount_stamp(db):
    """«Когда мы это в последний раз видели годным» — не «когда считали»."""
    request = stored_request(db)
    db.upsert_listing(make_listing("1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="1", score=82.0), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="1", score=82.0), LATER)
    assert db.matches_for_request(request.id)[0].matched_at == NOW


def test_a_new_run_alone_does_not_make_an_unchanged_match_an_update(db):
    """Прогон сменился, балл и кластер — нет: это всё ещё `unchanged`.

    Расписание — `scrape && match --all`: между двумя пересчётами номер
    прогона меняется всегда. Если он идёт в сравнение, первый же ночной
    подбор объявляет обновлёнными все матчи разом и двигает им отметку
    пересчёта — и «что изменилось со вчера» перестаёт отвечать на вопрос.
    Номер прогона у матча значит «в каком прогоне он в последний раз
    менялся», как и `matched_at`.
    """
    request = stored_request(db)
    db.upsert_listing(make_listing("1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="1", score=82.0,
                          run_id=8), NOW)
    assert db.upsert_match(Match(request_id=request.id, listing_id="1", score=82.0,
                                 run_id=9), LATER) == "unchanged"
    after = db.matches_for_request(request.id)[0]
    assert after.matched_at == NOW
    assert after.run_id == 8

    assert db.upsert_match(Match(request_id=request.id, listing_id="1", score=91.0,
                                 run_id=9), LATER) == "updated"
    assert db.matches_for_request(request.id)[0].run_id == 9


def test_a_changed_cluster_snapshot_is_an_update_too(db):
    """Тот же балл, но двойников стало больше — это другой разговор с клиентом."""
    request = stored_request(db)
    db.upsert_listing(make_listing("1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="1", score=82.0,
                          cluster_id="abc", cluster_size=1), NOW)
    assert db.upsert_match(Match(request_id=request.id, listing_id="1", score=82.0,
                                 cluster_id="abc", cluster_size=3,
                                 cluster_spread_usd=11_000.0), LATER) == "updated"
    after = db.matches_for_request(request.id)[0]
    assert after.cluster_size == 3
    assert after.cluster_spread_usd == 11_000.0


def test_matches_come_back_ranked_and_can_be_cut_by_score_and_count(db):
    request = stored_request(db)
    for number, value in (("1", 55.0), ("2", 91.0), ("3", 73.0)):
        db.upsert_listing(make_listing(number), NOW)
        db.upsert_match(Match(request_id=request.id, listing_id=number, score=value), NOW)
    assert [m.listing_id for m in db.matches_for_request(request.id)] == ["2", "3", "1"]
    assert [m.listing_id for m in db.matches_for_request(request.id, min_score=70)] == ["2", "3"]
    assert len(db.matches_for_request(request.id, limit=1)) == 1


def test_matches_of_another_request_do_not_leak_in(db):
    first = stored_request(db, "R-1")
    second = stored_request(db, "R-2")
    db.upsert_listing(make_listing("1"), NOW)
    db.upsert_match(Match(request_id=first.id, listing_id="1", score=82.0), NOW)
    assert db.matches_for_request(second.id) == []


def test_a_match_on_a_listing_that_went_away_is_kept(db):
    # Решение 8: «мы звонили по этой квартире» переживает снятие объявления.
    request = stored_request(db)
    db.upsert_listing(make_listing("1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="1", score=82.0), NOW)
    db.mark_gone(["1"], LATER)
    assert len(db.matches_for_request(request.id)) == 1


def test_matches_are_counted_for_the_whole_base_and_for_one_request(db):
    first = stored_request(db, "R-1")
    second = stored_request(db, "R-2")
    for number in ("1", "2"):
        db.upsert_listing(make_listing(number), NOW)
    db.upsert_match(Match(request_id=first.id, listing_id="1", score=82.0), NOW)
    db.upsert_match(Match(request_id=first.id, listing_id="2", score=55.0), NOW)
    db.upsert_match(Match(request_id=second.id, listing_id="1", score=61.0), NOW)
    assert db.count_matches() == 3
    assert db.count_matches(request_id=first.id) == 2


# --- конец жизни матча (B-1, B-2) --------------------------------------

def test_a_match_that_stopped_matching_is_retired_and_leaves_the_window(db):
    request = stored_request(db)
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_listing(make_listing("L-2"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="L-1", score=80.0), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="L-2", score=70.0), NOW)

    closed = db.retire_matches(request.id, keep={"L-1"}, now=LATER, reasons={}, default="бюджет")

    assert closed == 1
    alive = [item.listing_id for item in db.matches_for_request(request.id)]
    assert alive == ["L-1"]
    everything = db.matches_for_request(request.id, include_retired=True)
    assert {item.listing_id for item in everything} == {"L-1", "L-2"}


def test_retiring_a_match_does_not_touch_the_call_trace(db):
    request = stored_request(db)
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="L-1", score=80.0), NOW)
    stored = db.matches_for_request(request.id)[0]
    db.set_match_status(stored.id, "called", "дорого")

    db.retire_matches(request.id, keep=set(), now=LATER, reasons={}, default="бюджет")

    closed = db.matches_for_request(request.id, include_retired=True)[0]
    assert closed.status == "called"
    assert closed.reject_reason == "дорого"
    assert closed.retired_at == LATER
    assert closed.retired_reason == "бюджет"


def test_a_match_that_matches_again_comes_back_to_the_window(db):
    request = stored_request(db)
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="L-1", score=80.0), NOW)
    db.retire_matches(request.id, keep=set(), now=LATER, reasons={}, default="бюджет")

    db.upsert_match(Match(request_id=request.id, listing_id="L-1", score=75.0), LATER)

    alive = db.matches_for_request(request.id)
    assert [item.listing_id for item in alive] == ["L-1"]
    assert alive[0].retired_at is None


def test_a_match_confirmed_with_the_very_same_score_comes_back_too(db):
    """Балл не изменился — это не повод оставлять вариант закрытым."""
    request = stored_request(db)
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="L-1", score=80.0), NOW)
    db.retire_matches(request.id, keep=set(), now=LATER, reasons={}, default="бюджет")

    db.upsert_match(Match(request_id=request.id, listing_id="L-1", score=80.0), LATER)

    alive = db.matches_for_request(request.id)
    assert [item.listing_id for item in alive] == ["L-1"]
    assert alive[0].retired_reason is None


def test_retiring_twice_closes_nothing_the_second_time(db):
    request = stored_request(db)
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="L-1", score=80.0), NOW)
    assert db.retire_matches(request.id, keep=set(), now=LATER, reasons={}, default="бюджет") == 1
    assert db.retire_matches(request.id, keep=set(), now=LATER, reasons={}, default="бюджет") == 0


def test_each_retired_match_gets_its_own_reason(db):
    """«Бюджет» и «не представитель кластера» — разные ответы на вопрос
    «почему пропала вчерашняя карточка». Одна фраза на всех не отвечает."""
    request = stored_request(db)
    for listing_id in ("1", "2"):
        db.upsert_listing(make_listing(listing_id), NOW)
    db.upsert_matches([
        Match(request_id=request.id, listing_id="1", score=80.0),
        Match(request_id=request.id, listing_id="2", score=70.0),
    ], NOW)

    closed = db.retire_matches(
        request.id, keep=set(), now=LATER,
        reasons={"1": "бюджет", "2": "не представитель кластера"},
        default="проход больше не подтверждает этот вариант",
    )

    assert closed == 2
    reasons = {match.listing_id: match.retired_reason
               for match in db.matches_for_request(request.id, include_retired=True)}
    assert reasons == {"1": "бюджет", "2": "не представитель кластера"}


def test_a_match_without_a_known_reason_gets_the_general_one(db):
    """Причины нет — значит объявление выпало из выборки, не получив отказа.
    Врать про «бюджет» в этом случае хуже, чем сказать общее."""
    request = stored_request(db)
    db.upsert_listing(make_listing("1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="1", score=80.0), NOW)

    db.retire_matches(request.id, keep=set(), now=LATER, reasons={},
                      default="проход больше не подтверждает этот вариант")

    match = db.matches_for_request(request.id, include_retired=True)[0]
    assert match.retired_reason == "проход больше не подтверждает этот вариант"


def test_retiring_one_request_does_not_touch_another(db):
    first = stored_request(db, "R-1")
    second = stored_request(db, "R-2")
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_match(Match(request_id=first.id, listing_id="L-1", score=80.0), NOW)
    db.upsert_match(Match(request_id=second.id, listing_id="L-1", score=80.0), NOW)

    db.retire_matches(first.id, keep=set(), now=LATER, reasons={}, default="бюджет")

    assert len(db.matches_for_request(second.id)) == 1


def test_a_match_status_outside_the_list_is_refused(db):
    """След звонка — закрытый список слов, а не свободная строка.

    Выдуманное слово лежало бы в базе и выходило в выгрузку как есть,
    а витрина переводит на русский только то, что знает.
    """
    db.upsert_request(make_request(), NOW)
    request = db.get_request("R-1")
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="L-1", score=80.0), NOW)
    stored = db.matches_for_request(request.id)[0]

    with pytest.raises(ValueError) as exc:
        db.set_match_status(stored.id, "ПОЖАЛУЙ НЕТ")
    assert "new" in str(exc.value), "отказ обязан перечислить, какие статусы бывают"


def test_a_batch_of_matches_gives_the_same_counts_as_one_by_one(db):
    """Пачка считает то же самое, что счёт по одному: new / updated / unchanged."""
    request = stored_request(db)
    for number in range(3):
        db.upsert_listing(make_listing(f"L-{number}"), NOW)
    batch = [Match(request_id=request.id, listing_id=f"L-{number}",
                   score=float(70 + number)) for number in range(3)]

    assert db.upsert_matches(batch, NOW) == {"new": 3, "updated": 0, "unchanged": 0}
    assert db.upsert_matches(batch, LATER) == {"new": 0, "updated": 0, "unchanged": 3}

    batch[0].score = 99.0
    assert db.upsert_matches(batch, LATER) == {"new": 0, "updated": 1, "unchanged": 2}


def test_a_batch_does_not_touch_the_call_trace(db):
    """Решение 7: `status` и `reject_reason` пишет только человек."""
    request = stored_request(db)
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_matches([Match(request_id=request.id, listing_id="L-1", score=70.0)], NOW)
    stored = db.matches_for_request(request.id)[0]
    db.set_match_status(stored.id, "called", "дорого")

    db.upsert_matches([Match(request_id=request.id, listing_id="L-1", score=90.0)], LATER)

    again = db.matches_for_request(request.id)[0]
    assert again.score == 90.0
    assert again.status == "called"
    assert again.reject_reason == "дорого"


def test_a_batch_brings_a_retired_match_back(db):
    """Подтверждение гасит закрытие — так же, как у `upsert_match`."""
    request = stored_request(db)
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_matches([Match(request_id=request.id, listing_id="L-1", score=80.0)], NOW)
    db.retire_matches(request.id, keep=set(), now=LATER, reasons={}, default="бюджет")

    assert db.upsert_matches(
        [Match(request_id=request.id, listing_id="L-1", score=80.0)], EVEN_LATER
    ) == {"new": 0, "updated": 1, "unchanged": 0}
    alive = db.matches_for_request(request.id)
    assert [item.listing_id for item in alive] == ["L-1"]
    assert alive[0].retired_reason is None


def test_a_batch_keeps_the_first_time_a_match_was_found(db):
    """`first_matched_at` — про находку, `matched_at` — про пересчёт."""
    request = stored_request(db)
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_matches([Match(request_id=request.id, listing_id="L-1", score=80.0)], NOW)
    db.upsert_matches([Match(request_id=request.id, listing_id="L-1", score=90.0)], LATER)

    stored = db.matches_for_request(request.id)[0]
    assert stored.first_matched_at == NOW
    assert stored.matched_at == LATER


def test_an_empty_batch_writes_nothing(db):
    request = stored_request(db)

    assert db.upsert_matches([], NOW) == {"new": 0, "updated": 0, "unchanged": 0}
    assert db.matches_for_request(request.id) == []


def test_matches_come_with_their_listings_in_one_go(db):
    """Витрине нужны матч и карточка вместе: порознь это запрос на строку."""
    request = stored_request(db)
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="L-1", score=80.0), NOW)

    rows = db.matches_with_listings(request.id)

    assert len(rows) == 1
    match, listing = rows[0]
    assert match.listing_id == "L-1"
    assert listing.district == "Центр"


def test_a_gone_listing_still_comes_with_its_match(db):
    request = stored_request(db)
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="L-1", score=80.0), NOW)
    db.mark_gone(["L-1"], LATER)

    rows = db.matches_with_listings(request.id)

    assert [listing.status for _, listing in rows] == ["gone"], (
        "решение 8: снятое витрина помечает, а не прячет"
    )


def test_a_retired_match_does_not_come_to_the_window(db):
    request = stored_request(db)
    db.upsert_listing(make_listing("L-1"), NOW)
    db.upsert_listing(make_listing("L-2"), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="L-1", score=80.0), NOW)
    db.upsert_match(Match(request_id=request.id, listing_id="L-2", score=70.0), NOW)
    db.retire_matches(request.id, keep={"L-1"}, now=LATER, reasons={}, default="бюджет")

    rows = db.matches_with_listings(request.id)

    assert [match.listing_id for match, _ in rows] == ["L-1"]


def test_the_window_narrows_by_score_and_by_count(db):
    """Те же сужения, что у `matches_for_request`, и тот же порядок."""
    request = stored_request(db)
    for number, value in enumerate([90.0, 50.0, 70.0]):
        db.upsert_listing(make_listing(f"L-{number}"), NOW)
        db.upsert_match(
            Match(request_id=request.id, listing_id=f"L-{number}", score=value), NOW
        )

    ordered = [match.score for match, _ in db.matches_with_listings(request.id)]
    assert ordered == [90.0, 70.0, 50.0]
    assert [match.score for match, _
            in db.matches_with_listings(request.id, min_score=60.0)] == [90.0, 70.0]
    assert [match.score for match, _
            in db.matches_with_listings(request.id, limit=1)] == [90.0]


def test_a_revived_match_remembers_when_it_came_back(db):
    """Подтвердился снова — в строке остаётся след возврата, а не только
    погашенное закрытие: иначе воскресение неотличимо от пересчёта."""
    db.upsert_listing(make_listing(), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_match(Match(request_id=request.id, listing_id="24254997", score=80.0), NOW)
    db.retire_matches(request.id, keep=set(), now=LATER, reasons={}, default="бюджет")

    db.upsert_match(
        Match(request_id=request.id, listing_id="24254997", score=80.0), EVEN_LATER
    )

    match = db.matches_for_request(request.id)[0]
    assert match.retired_at is None
    assert match.retired_reason is None
    assert match.revived_at == EVEN_LATER


def test_a_match_that_was_never_retired_has_no_revival_mark(db):
    """Обычный пересчёт отметку возврата не ставит: вернуться неоткуда."""
    db.upsert_listing(make_listing(), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_match(Match(request_id=request.id, listing_id="24254997", score=80.0), NOW)

    db.upsert_matches(
        [Match(request_id=request.id, listing_id="24254997", score=91.0)], LATER
    )

    assert db.matches_for_request(request.id)[0].revived_at is None


def test_a_batch_revival_is_marked_too(db):
    """Пачка и одиночная запись ведут себя одинаково: у подбора путь один —
    пачка, и правило, проверенное только на `upsert_match`, в бою не работает."""
    db.upsert_listing(make_listing(), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_matches(
        [Match(request_id=request.id, listing_id="24254997", score=80.0)], NOW
    )
    db.retire_matches(request.id, keep=set(), now=LATER, reasons={}, default="бюджет")

    counts = db.upsert_matches(
        [Match(request_id=request.id, listing_id="24254997", score=80.0)], EVEN_LATER
    )

    assert counts == {"new": 0, "updated": 1, "unchanged": 0}
    assert db.matches_for_request(request.id)[0].revived_at == EVEN_LATER


def test_match_events_bring_the_match_the_listing_and_the_old_price(db):
    """Событие — это матч, карточка и цена до окна: без цены «подешевело»
    не показать, а без карточки не позвонить."""
    db.upsert_listing(make_listing(price_usd=200000.0), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_match(Match(request_id=request.id, listing_id="24254997", score=80.0), LATER)

    rows = db.match_events_since(NOW, EVEN_LATER)

    assert len(rows) == 1
    match, listing, price_before = rows[0]
    assert match.listing_id == "24254997"
    assert listing.district == "Центр"
    assert price_before == 200000.0      # точка истории от upsert_listing


def test_match_events_skip_what_did_not_move(db):
    """Матч, которого окно не коснулось, событием не считается."""
    db.upsert_listing(make_listing(), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_match(Match(request_id=request.id, listing_id="24254997", score=80.0), NOW)

    assert db.match_events_since(LATER, EVEN_LATER) == []


def test_match_events_include_the_retired_ones(db):
    """Закрытый матч из выборки не выпадает: дайджест обязан сказать, почему
    вчерашняя карточка пропала. Показывать ли его — решает домен."""
    db.upsert_listing(make_listing(), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_match(Match(request_id=request.id, listing_id="24254997", score=80.0), NOW)
    db.retire_matches(request.id, keep=set(), now=LATER, reasons={}, default="бюджет")

    rows = db.match_events_since(NOW, EVEN_LATER)

    assert len(rows) == 1
    assert rows[0][0].retired_at == LATER
    assert rows[0][0].retired_reason == "бюджет"


def test_match_events_can_be_narrowed_to_one_request(db):
    """Витрина одной заявки не читает события всех пятидесяти."""
    db.upsert_listing(make_listing(), seen_at=NOW)
    for external_id in ("R-1", "R-2"):
        db.upsert_request(Request(external_id=external_id), now=NOW)
        request = db.get_request(external_id)
        db.upsert_match(
            Match(request_id=request.id, listing_id="24254997", score=80.0), LATER
        )

    only = db.match_events_since(NOW, EVEN_LATER, request_id=db.get_request("R-2").id)

    assert len(only) == 1
    assert only[0][0].request_id == db.get_request("R-2").id


def test_match_events_do_not_reach_past_the_window(db):
    """Верхняя граница окна — не украшение: событие, случившееся после неё,
    уйдёт в следующую отправку, а не в эту."""
    db.upsert_listing(make_listing(), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_match(
        Match(request_id=request.id, listing_id="24254997", score=80.0), EVEN_LATER
    )

    assert db.match_events_since(NOW, LATER) == []


def test_match_events_see_a_price_drop_the_recount_did_not_notice(db):
    """Цена упала, а балл — нет: глубокая скидка давно упёрлась в потолок
    фактора выгодности. Матч пересчётом не тронут, но у объявления в окне
    есть точка истории цен — и это событие."""
    db.upsert_listing(make_listing(price_usd=60000.0), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_match(Match(request_id=request.id, listing_id="24254997", score=80.0), NOW)
    db.upsert_listing(make_listing(price_usd=50000.0, price_raw="$50,000"), seen_at=LATER)

    rows = db.match_events_since(NOW, EVEN_LATER)

    assert len(rows) == 1
    match, listing, price_before = rows[0]
    assert price_before == 60000.0
    assert listing.price_usd == 50000.0


def test_the_journal_remembers_the_last_successful_send(db):
    """Окно следующего запуска — window_to последней успешной строки."""
    from listam.domain.models import Notification

    db.record_notification(Notification(
        kind="digest", sent_at=LATER, window_from=NOW, window_to=LATER,
        events=7, requests=3, text="Заявка R-1 — 7 новых",
    ))

    last = db.last_notification("digest")

    assert last.window_to == LATER
    assert last.events == 7
    assert last.text.startswith("Заявка R-1")


def test_kinds_of_notification_do_not_mix(db):
    """Часовое «горячее» не двигает окно дневного дайджеста и наоборот."""
    from listam.domain.models import Notification

    db.record_notification(Notification(kind="hot", sent_at=LATER, window_to=LATER))

    assert db.last_notification("hot").window_to == LATER
    assert db.last_notification("digest") is None


def test_the_journal_of_one_channel_does_not_move_another(db):
    """Напечатанное в консоль брокеру не пришло: окно Telegram от него
    не двигается (H-3 аудита QA после M3)."""
    from listam.domain.models import Notification

    db.record_notification(Notification(kind="digest", sent_at=LATER,
                                        window_to=LATER, channel="stdout"))

    assert db.last_notification("digest", channel="telegram") is None
    assert db.last_notification("digest", channel="stdout").window_to == LATER
    assert db.last_notification("digest", channel="stdout").channel == "stdout"


def test_a_send_from_before_the_channels_counts_for_every_channel(db):
    """Строки до миграции 011 канала не знают. Считать их чужими значило бы
    послать в Telegram всё, что уже ушло."""
    from listam.domain.models import Notification

    db.record_notification(Notification(kind="digest", sent_at=LATER, window_to=LATER))

    assert db.last_notification("digest", channel="telegram").window_to == LATER


def test_the_journal_answers_none_before_the_first_send(db):
    assert db.last_notification("digest") is None


def test_alive_matches_are_counted_without_reading_them(db):
    """Счётчик считает строки, а не читает их: на боевых числах это разница
    между 4,0 с и 0,22 с."""
    db.upsert_listing(make_listing("1"), seen_at=NOW)
    db.upsert_listing(make_listing("2"), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_matches([
        Match(request_id=request.id, listing_id="1", score=80.0),
        Match(request_id=request.id, listing_id="2", score=50.0),
    ], NOW)

    assert db.count_matches_alive(request.id) == 2
    assert db.count_matches_alive(request.id, min_score=70.0) == 1


def test_a_retired_match_is_not_counted(db):
    """Счётчик и выборка считают одно и то же: закрытых не видит ни один."""
    db.upsert_listing(make_listing("1"), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")
    db.upsert_match(Match(request_id=request.id, listing_id="1", score=80.0), NOW)
    db.retire_matches(request.id, keep=set(), now=LATER, reasons={}, default="бюджет")

    assert db.count_matches_alive(request.id) == 0


def test_a_match_without_a_listing_cannot_exist_at_all(db):
    """Счётчик обязан совпадать с выборкой: она идёт JOIN'ом и матч без
    карточки не отдаёт — иначе «…и ещё 1» обещало бы то, чего не получить.

    Разойтись им не на чем: база отказывается записать матч на карточку,
    которой нет. Это не «счётчик умный», а «сироты не бывает».
    """
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")

    with pytest.raises(Exception):
        db.upsert_match(
            Match(request_id=request.id, listing_id="сгинувшее", score=80.0), NOW)

    assert db.count_matches_alive(request.id) == 0
    assert db.matches_with_listings(request.id) == []


# --- кэш страниц объявлений (фаза 3 M3.5) -------------------------------

def make_page(listing_id="24254997", **over) -> ListingPage:
    values = dict(
        listing_id=listing_id, status="ok", fetched_at=LATER, attempts=1,
        price_raw="$132,000",
        fields=PageFields(values={"renovation": "косметический", "elevator": True,
                                  "ceiling_height": 2.7, "_unknown": ["Сауна"]},
                          description="Продаётся квартира", photos=["//img/1.webp"]),
    )
    values.update(over)
    return ListingPage(**values)


def test_a_page_nobody_opened_is_none(db):
    assert db.get_page("24254997") is None


def test_a_saved_page_reads_back_with_its_fields(db):
    """Поля страницы — данные, по которым решается матч: вернуться они
    обязаны теми же, включая список неразобранных подписей."""
    db.save_page(make_page())

    page = db.get_page("24254997")

    assert page.status == "ok"
    assert page.attempts == 1
    assert page.fetched_at == LATER
    assert page.price_raw == "$132,000"
    assert page.fields.values == {"renovation": "косметический", "elevator": True,
                                  "ceiling_height": 2.7, "_unknown": ["Сауна"]}
    assert page.fields.description == "Продаётся квартира"
    assert page.fields.photos == ["//img/1.webp"]


def test_saving_a_page_again_replaces_it(db):
    """Апсерт по объявлению: вторая запись — не вторая строка."""
    db.save_page(make_page(status="failed", attempts=1, fields=None, error="таймаут"))
    db.save_page(make_page(status="ok", attempts=2))

    page = db.get_page("24254997")

    assert page.status == "ok"
    assert page.attempts == 2
    assert page.error is None
    assert page.fields.values["elevator"] is True


def test_a_failed_page_keeps_no_fields(db):
    db.save_page(make_page(status="failed", fields=None, error="таймаут", fetched_at=None))

    page = db.get_page("24254997")

    assert page.status == "failed"
    assert page.fields is None
    assert page.fetched_at is None
    assert page.error == "таймаут"


def test_pages_come_for_many_listings_at_once(db):
    """Подбор спрашивает страницы всех кандидатов одним запросом."""
    db.save_page(make_page("1"))
    db.save_page(make_page("2", status="gone", fields=None))

    pages = db.pages_for(["1", "2", "3"])

    assert set(pages) == {"1", "2"}
    assert pages["2"].status == "gone"
    assert db.pages_for([]) == {}


def test_listings_paged_since_are_only_the_opened_ones(db):
    """Пора в подбор — тем, чья страница открылась после отметки. Сбой
    и снятое ничего нового подбору не несут."""
    db.save_page(make_page("1", fetched_at=NOW))
    db.save_page(make_page("2", fetched_at=EVEN_LATER))
    db.save_page(make_page("3", fetched_at=EVEN_LATER, status="failed", fields=None))

    assert db.listings_paged_since(LATER) == {"2"}


def test_a_listing_whose_page_just_opened_is_touched(db):
    """Кандидат ждал страницу; она открылась — `match --new` обязан его
    увидеть, хотя на ленте с ним ничего не случилось (решение 9)."""
    db.upsert_listing(make_listing(), seen_at=NOW)
    assert db.listings_touched_since(LATER) == []

    db.save_page(make_page(fetched_at=EVEN_LATER))

    assert [item.id for item in db.listings_touched_since(LATER)] == ["24254997"]


def test_origin_is_written_once_and_not_compared(db):
    """`origin` ставится при вставке. Пересчёт его не сравнивает и не
    переписывает: рыночный матч не становится «заявочным» оттого, что
    заявку поправили."""
    db.upsert_listing(make_listing(), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")

    db.upsert_matches([Match(request_id=request.id, listing_id="24254997",
                             score=80.0, origin="market")], NOW)
    counts = db.upsert_matches([Match(request_id=request.id, listing_id="24254997",
                                      score=80.0, origin="request")], LATER)
    assert counts == {"new": 0, "updated": 0, "unchanged": 1}

    db.upsert_matches([Match(request_id=request.id, listing_id="24254997",
                             score=90.0, origin="request")], EVEN_LATER)
    db.upsert_match(Match(request_id=request.id, listing_id="24254997",
                          score=91.0, origin="request"), EVEN_LATER)

    stored = db.matches_for_request(request.id)[0]
    assert stored.score == 91.0
    assert stored.origin == "market"


def test_a_single_match_is_born_with_its_origin(db):
    db.upsert_listing(make_listing(), seen_at=NOW)
    db.upsert_request(Request(external_id="R-1"), now=NOW)
    request = db.get_request("R-1")

    db.upsert_match(Match(request_id=request.id, listing_id="24254997",
                          score=80.0, origin="request"), NOW)

    assert db.matches_for_request(request.id)[0].origin == "request"


def test_pages_are_counted_by_status(db):
    """`doctor` показывает кэш целиком — счётом, без чтения полей."""
    assert db.page_counts() == {}
    db.save_page(make_page("1"))
    db.save_page(make_page("2"))
    db.save_page(make_page("3", status="failed", fields=None))
    db.save_page(make_page("4", status="gone", fields=None))

    assert db.page_counts() == {"ok": 2, "failed": 1, "gone": 1}
