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
