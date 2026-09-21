"""Парсер ленты category/60 на обрезанном снимке живой страницы.

Фикстура собрана из настоящих карточек 21.09.2026: доллар, драм, евро,
агентство, собственник, проверено по кадастру, плюс синтетическая карточка
без единого поля — парсер обязан её пережить.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from listam.parsers.category_list import parse_listing_cards, parse_next_page

FIXTURE = Path(__file__).parent / "fixtures" / "category-60-page1.html"


@pytest.fixture(scope="module")
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cards(html):
    return {c.id: c for c in parse_listing_cards(html)}


def test_top_block_is_skipped(html):
    """Блок «Топ объявления» повторяется на каждой странице и не сортирован по дате."""
    ids = [c.id for c in parse_listing_cards(html)]
    assert len(ids) == 6
    assert "24221707" not in ids  # карточка из #tp
    assert "24141093" not in ids


def test_usd_agency_card_is_parsed_whole(cards):
    card = cards["23973917"]
    assert card.url == "https://www.list.am/ru/item/23973917"
    assert card.title.startswith("2-комн. квартира в новостройке")
    assert card.currency == "USD"
    assert card.price_usd == 162000.0
    assert card.price_amd is None
    assert "162,000" in card.price_raw
    assert card.rooms == 2
    assert card.area == 57.0
    assert card.floor == 10
    assert card.floors_total == 19
    assert card.district == "Ачапняк"
    assert card.street == "Монте Мелконяна"
    assert card.seller_type == "agency"
    assert card.verified is False
    assert card.new_build is True
    assert card.status == "active"


def test_amd_price_lands_in_amd_column(cards):
    card = cards["24228087"]
    assert card.currency == "AMD"
    assert card.price_amd == 23800000.0
    assert card.price_usd is None  # пересчёт делает прогон, у него есть курс


def test_euro_price_is_kept_as_is(cards):
    card = cards["23598471"]
    assert card.currency == "EUR"
    assert card.price_usd is None
    assert card.price_amd is None
    assert "140,000" in card.price_raw


def test_card_without_agency_badge_is_owner(cards):
    assert cards["23311644"].seller_type == "owner"
    assert cards["23973917"].seller_type == "agency"


def test_cadastre_badge_means_verified(cards):
    assert cards["23987063"].verified is True


def test_broken_card_survives_without_fields(cards):
    card = cards["99999999"]
    assert card.url == "https://www.list.am/ru/item/99999999"
    assert card.title == "Квартира без подробностей"
    assert card.price_raw is None
    assert card.currency is None
    assert card.rooms is None
    assert card.area is None
    assert card.floor is None
    assert card.floors_total is None
    assert card.district is None
    assert card.street is None
    assert card.verified is None


def test_price_per_sqm_is_not_computed_here(cards):
    """Курса у парсера нет — значит и цены за метр быть не может."""
    assert all(c.price_per_sqm is None for c in cards.values())


def test_next_page_is_found(html):
    assert parse_next_page(html) == 2


def test_last_page_has_no_next():
    assert parse_next_page("<html><body><div id='contentr'></div></body></html>") is None
