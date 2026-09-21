"""Проверка карточек на мусор: опечатку продавца нельзя считать данными.

Объявление с аномалией мы храним как есть — выбрасывать чужие данные нельзя, —
но помечаем, чтобы оно не попадало в медианы и было видно глазами в выгрузке.
"""
from __future__ import annotations

import pytest

from listam.config import Config
from listam.domain.models import Listing
from listam.domain.stats import median_price_per_sqm_by_district
from listam.domain.validate import RULES, detect_anomalies, rules_from


def listing(**over) -> Listing:
    fields = dict(
        id="1",
        url="https://www.list.am/ru/item/1",
        district="Центр",
        price_usd=120_000.0,
        price_per_sqm=1500.0,
        area=80.0,
        rooms=3,
        floor=4,
        floors_total=9,
    )
    fields.update(over)
    return Listing(**fields)


def test_a_clean_card_gets_no_mark():
    assert detect_anomalies(listing(), RULES) is None


def test_three_rooms_in_one_square_meter_is_marked():
    """id 24164310 на боевой базе: «3-комн., 1 кв.м.»."""
    marks = detect_anomalies(listing(area=1.0, rooms=3, price_per_sqm=120_000.0), RULES)

    assert "area" in marks
    assert "rooms_vs_area" in marks


def test_a_price_of_eighty_seven_million_is_marked():
    """id 24076812: «$ 87,000,000» за 82 м² — это 1 060 975 $/м²."""
    marks = detect_anomalies(
        listing(price_usd=87_000_000.0, area=82.0, price_per_sqm=1_060_975.61), RULES
    )

    assert "price_usd" in marks
    assert "price_per_sqm" in marks


def test_a_price_under_five_thousand_is_marked():
    assert "price_usd" in detect_anomalies(listing(price_usd=1200.0), RULES)


def test_floor_above_the_number_of_floors_is_marked():
    """id 21543108: 12/2."""
    assert "floor" in detect_anomalies(listing(floor=12, floors_total=2), RULES)


def test_empty_fields_are_not_anomalies():
    """Нет поля — это «нет поля», а не «мусор»: парсер имеет право не найти."""
    assert detect_anomalies(
        listing(price_usd=None, price_per_sqm=None, area=None, rooms=None,
                floor=None, floors_total=None),
        RULES,
    ) is None


def test_thresholds_come_from_the_rules_not_from_the_code():
    loose = dict(RULES, area={"min": 0.5, "max": 5000}, min_area_per_room=0.1)

    assert detect_anomalies(listing(area=1.0, rooms=1), loose) is None


def test_median_by_district_ignores_marked_listings():
    rows = [
        listing(id="1", price_per_sqm=1000.0),
        listing(id="2", price_per_sqm=2000.0),
        listing(id="3", price_per_sqm=1_060_975.0, anomaly="price_per_sqm"),
    ]

    assert median_price_per_sqm_by_district(rows) == {"Центр": 1500.0}


def test_median_skips_listings_without_a_price_per_sqm():
    rows = [
        listing(id="1", price_per_sqm=1000.0),
        listing(id="2", price_per_sqm=None),
    ]

    assert median_price_per_sqm_by_district(rows) == {"Центр": 1000.0}


def test_district_without_a_single_clean_listing_is_absent():
    rows = [listing(id="1", price_per_sqm=1_060_975.0, anomaly="price_per_sqm")]

    assert median_price_per_sqm_by_district(rows) == {}


@pytest.mark.parametrize("rule", ["price_usd", "price_per_sqm", "area"])
def test_every_threshold_rule_has_both_ends(rule):
    assert set(RULES[rule]) == {"min", "max"}


def test_a_single_threshold_from_the_config_keeps_the_rest():
    """Находка 13: `validate: {price_usd: {min: 1000}}` не должен уносить `max`.

    Плоский merge подменял весь словарь правила целиком: верхняя граница
    пропадала, и `$ 87,000,000` переставал быть аномалией.
    """
    config = Config({"validate": {"price_usd": {"min": 1000}}}, env="test", path="test.yaml")

    rules = rules_from(config)

    assert rules["price_usd"] == {"min": 1000, "max": 5_000_000}
    assert rules["area"] == RULES["area"]
    assert rules["min_area_per_room"] == RULES["min_area_per_room"]
    assert "price_usd" in detect_anomalies(listing(price_usd=87_000_000.0), rules)


def test_the_config_still_wins_where_it_speaks():
    config = Config(
        {"validate": {"price_usd": {"min": 1000, "max": 9_000}, "min_area_per_room": 1}},
        env="test",
        path="test.yaml",
    )

    rules = rules_from(config)

    assert rules["price_usd"] == {"min": 1000, "max": 9_000}
    assert rules["min_area_per_room"] == 1


def test_defaults_are_not_touched_by_a_config():
    """Правила по умолчанию — общие для процесса: их нельзя править на месте."""
    config = Config({"validate": {"price_usd": {"min": 1000}}}, env="test", path="test.yaml")

    rules_from(config)

    assert RULES["price_usd"] == {"min": 5_000, "max": 5_000_000}
