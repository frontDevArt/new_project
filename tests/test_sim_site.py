"""Подставной list.am: его разметку разбирают настоящие парсеры."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from listam.parsers.category_list import parse_listing_cards, parse_next_page
from listam.parsers.item_page import UNKNOWN, parse_item_page
from listam.ports.fetcher import FetchError
from tests.sim.clock import SimClock
from tests.sim.market import Market, MarketParams
from tests.sim.site import SimFetcher, feed_page, item_page, page_slices

NOW = datetime(2026, 9, 23, 23, 0, tzinfo=timezone.utc)  # календарь: не сравнивается с часами


@pytest.fixture
def market():
    return Market.synthetic(300, seed=9, params=MarketParams(), now=NOW)


@pytest.mark.parametrize("count", [20, 25, 300, 20650])
def test_every_page_holds_between_twenty_and_a_hundred_twenty_cards(count):
    slices = page_slices(count)
    assert sum(end - start for start, end in slices) == count
    assert all(20 <= end - start <= 120 for start, end in slices)


def test_a_feed_page_parses_back_to_the_same_flats(market):
    flats = market.feed()
    cards = parse_listing_cards(feed_page(flats, 1))
    first = page_slices(len(flats))[0]
    assert [c.id for c in cards] == [f.id for f in flats[first[0]:first[1]]]
    for card, flat in zip(cards, flats):
        assert (card.district, card.rooms, card.area, card.floor, card.floors_total) == \
               (flat.district, flat.rooms, flat.area, flat.floor, flat.floors_total)
        assert card.currency == flat.currency and card.price_amount == flat.price
        assert card.seller_type == ("agency" if flat.agency else "owner")
        assert card.verified == flat.verified


def test_the_paginator_leads_to_the_last_page_and_stops(market):
    flats = market.feed()
    pages = len(page_slices(len(flats)))
    assert parse_next_page(feed_page(flats, 1)) == 2
    assert parse_next_page(feed_page(flats, pages)) is None
    assert feed_page(flats, pages + 1) is None


def test_an_item_page_parses_to_its_fields(market):
    flat = market.feed()[0]
    values = parse_item_page(item_page(flat)).values
    assert UNKNOWN not in values
    assert values["renovation"] == flat.page["Ремонт"]
    assert values["building_type"] == flat.page["Тип здания"]
    assert values["elevator"] is (flat.page["Лифт"] == "есть")
    assert values["balcony"] is (flat.page["Балкон"] != "нет")
    assert (values["area"], values["floor"], values["rooms"]) == (flat.area, flat.floor, flat.rooms)


def test_the_fetcher_serves_feed_and_items_and_journals_them(market):
    clock = SimClock(NOW)
    fetcher = SimFetcher(market, clock)
    fetcher.cycle = "hourly@test"
    flat = market.feed()[0]
    assert parse_listing_cards(fetcher.get("https://www.list.am/ru/category/60"))
    assert parse_listing_cards(fetcher.get("https://www.list.am/ru/category/60/2"))
    parse_item_page(fetcher.get(f"https://www.list.am/ru/item/{flat.id}"))
    assert [(f.kind, f.key, f.status, f.cycle) for f in fetcher.journal] == [
        ("feed", "1", 200, "hourly@test"), ("feed", "2", 200, "hourly@test"),
        ("item", flat.id, 200, "hourly@test")]
    assert fetcher.journal[-1].facts["district"] == flat.district
    assert fetcher.requests_made == 3


def test_a_gone_listing_answers_404_like_the_site(market):
    fetcher = SimFetcher(market, SimClock(NOW))
    flat = market.feed()[0]
    flat.status = "gone"
    with pytest.raises(FetchError) as caught:
        fetcher.get(f"https://www.list.am/ru/item/{flat.id}")
    assert caught.value.status == 404
    assert fetcher.journal[-1].status == 404


def test_an_unknown_address_is_an_error_not_a_page(market):
    with pytest.raises(FetchError):
        SimFetcher(market, SimClock(NOW)).get("https://www.list.am/ru/category/61")
