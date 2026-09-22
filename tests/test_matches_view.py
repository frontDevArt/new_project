"""Витрина: потолок доходит до выборки, а «…и ещё N» остаётся правдой."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from listam.domain.models import Listing, Match, Request
from listam.matches_view import MatchesPage, collect_matches, render_matches
from listam.wiring import build_database

from tests.test_matching import cfg

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def prepared(tmp_path):
    """Конфиг с базой во временной папке и тремя матчами одной заявки.

    Конфиг собирается тем же помощником, что и у `tests/test_matching.py`:
    два способа собрать конфиг в тестах разойдутся так же, как разошлись
    пять оркестраторов.
    """
    config = cfg(tmp_path)
    database = build_database(config)
    database.connect()
    database.migrate()
    for index in range(3):
        database.upsert_listing(
            Listing(id=str(index), url=f"https://www.list.am/ru/item/{index}",
                    district="Кентрон", price_usd=100000.0 + index, area=60.0,
                    rooms=2, status="active"),
            seen_at=NOW,
        )
    database.upsert_request(Request(external_id="R-1", client_name="Ани"), now=NOW)
    request = database.get_request("R-1")
    database.upsert_matches([
        Match(request_id=request.id, listing_id=str(index), score=90.0 - index)
        for index in range(3)
    ], NOW)
    database.close()
    return config


def test_the_page_knows_how_many_there_are_in_total(prepared):
    page = collect_matches(prepared, limit=1)

    assert isinstance(page, MatchesPage)
    assert len(page.rows) == 1
    assert page.totals["R-1"] == 3


def test_the_tail_counts_rows_that_were_never_read(prepared):
    """«…и ещё 2» берётся из счётчика, а не из длины прочитанного: иначе
    потолок в выборке превратил бы обещание в ложь."""
    page = collect_matches(prepared, limit=1)

    printed = render_matches(page, limit=1)

    assert "…и ещё 2" in printed


def test_a_request_without_matches_is_not_a_section(prepared):
    page = collect_matches(prepared, external_id="R-1", min_score=99.0)

    assert page.rows == []
    assert render_matches(page, limit=50) == "Подобранных вариантов нет."


from datetime import timedelta

from listam.domain.events import NEW
from listam.matches_view import collect_events, render_events


def test_the_slice_shows_only_what_moved_inside_the_window(prepared):
    """Главное, ради чего фаза: подешевевшая квартира стояла на 150-м месте
    из 627 и человеку не показывалась никогда.

    Матч заводится внутри окна, а не пересчитывается в нём: пересчёт —
    не событие (правило `listam/domain/events.py`), и окно обязано принести
    ровно одну строку из четырёх.
    """
    database = build_database(prepared)
    database.connect()
    database.upsert_listing(
        Listing(id="3", url="https://www.list.am/ru/item/3", district="Кентрон",
                price_usd=99000.0, area=60.0, rooms=2, status="active"),
        seen_at=NOW + timedelta(hours=5),
    )
    request = database.get_request("R-1")
    database.upsert_matches(
        [Match(request_id=request.id, listing_id="3", score=95.0)],
        NOW + timedelta(hours=5),
    )
    database.close()

    page = collect_events(prepared, since=NOW + timedelta(hours=1),
                          until=NOW + timedelta(hours=6))

    assert [event.match.listing_id for event in page.events] == ["3"]


def test_the_slice_says_when_there_is_nothing(prepared):
    page = collect_events(prepared, since=NOW + timedelta(hours=10),
                          until=NOW + timedelta(hours=11))

    assert page.events == []
    assert "событий нет" in render_events(page, per_request=5)


def test_the_slice_names_the_kind_of_each_event(prepared):
    page = collect_events(prepared, since=NOW - timedelta(hours=1),
                          until=NOW + timedelta(hours=1))

    printed = render_events(page, per_request=5)

    assert "новый" in printed
    assert all(event.kind == NEW for event in page.events)


def test_closings_are_counted_at_the_bottom_and_not_among_the_options(prepared):
    """Закрытие — не повод звонить, а объяснение, куда делась вчерашняя
    карточка. Порог его не режет (`events_for`), поэтому место ему одной
    строкой внизу раздела, а не среди вариантов."""
    database = build_database(prepared)
    database.connect()
    request = database.get_request("R-1")
    database.retire_matches(request.id, keep={"1", "2"},
                            now=NOW + timedelta(hours=2), reason="бюджет")
    database.close()

    page = collect_events(prepared, since=NOW + timedelta(hours=1),
                          until=NOW + timedelta(hours=3))

    printed = render_events(page, per_request=5)

    assert [event.match.listing_id for event in page.events] == ["0"]
    assert "отпало 1 (бюджет 1)" in printed
    assert "https://www.list.am/ru/item/0" not in printed
