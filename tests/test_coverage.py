"""M4: прогон обязан заметить, что вёрстка уехала.

`seller_type` по умолчанию «owner», `verified` и `district` тихо становятся
пустыми — переименовали класс, и вся база молча стала собственниками без
района. Ловим это долей заполненности за прогон, пороги — из конфига.
"""
from __future__ import annotations

from listam.config import Config
from listam.domain.coverage import Coverage, rules_from
from listam.domain.models import Listing

RULES = {
    "min_sample": 10,
    "min_filled": {"district": 0.98},
    "min_share": {"seller_type": {"agency": 0.05}},
}


def cards(count: int, **over) -> list[Listing]:
    fields = dict(district="Аван", seller_type="owner")
    fields.update(over)
    return [Listing(id=str(n), url="u", **fields) for n in range(count)]


def failures(listings, rules=RULES) -> list[str]:
    coverage = Coverage(rules)
    for listing in listings:
        coverage.add(listing)
    return coverage.failures()


def test_healthy_run_has_nothing_to_say():
    healthy = cards(19) + cards(1, seller_type="agency")

    assert failures(healthy) == []


def test_district_that_stopped_being_parsed_is_noticed():
    assert any("district" in message for message in failures(cards(20, district=None)))


def test_everyone_becoming_an_owner_is_noticed():
    """Переименовали класс ge3 — и агентств в ленте не стало ни одного."""
    messages = failures(cards(20))

    assert any("agency" in message for message in messages)


def test_the_message_names_the_threshold_it_broke():
    message = failures(cards(20, district=None))[0]

    assert "coverage.min_filled.district" in message
    assert "%" in message


def test_a_short_run_is_not_judged():
    """На трёх карточках доля ничего не значит — молчим."""
    assert failures(cards(3, district=None)) == []


def test_rules_come_from_the_config():
    config = Config({"coverage": RULES}, env="test", path="test.yaml")

    assert rules_from(config) == RULES


def test_no_coverage_section_means_no_checks():
    """Порогов не задали — прогон не выдумывает их сам."""
    config = Config({}, env="test", path="test.yaml")

    assert failures(cards(50, district=None), rules_from(config)) == []


def test_nested_thresholds_from_the_config_arrive_whole():
    """Находка 13, второй конец: вложенные пороги не должны схлопываться."""
    config = Config(
        {"coverage": {"min_sample": 10, "min_filled": {"title": 0.9, "district": 0.98}}},
        env="test",
        path="test.yaml",
    )

    rules = rules_from(config)

    assert rules["min_filled"] == {"title": 0.9, "district": 0.98}


def test_a_short_run_says_out_loud_that_it_was_not_judged():
    """Находка 14: молчание проверки нельзя путать с её успехом.

    Прогон на 96 карточках при `min_sample: 100` не проверял вёрстку вообще,
    а выглядел ровно как прогон, у которого всё в порядке.
    """
    coverage = Coverage({"min_sample": 100, "min_filled": {"district": 0.98}})
    for listing in cards(96, district=None):
        coverage.add(listing)

    assert coverage.failures() == []
    assert coverage.skipped_note() == (
        "карточек меньше coverage.min_sample = 100, проверка вёрстки пропущена: "
        "в прогоне их 96"
    )


def test_a_full_run_has_nothing_to_say_about_being_skipped():
    coverage = Coverage(RULES)
    for listing in cards(19) + cards(1, seller_type="agency"):
        coverage.add(listing)

    assert coverage.skipped_note() is None


def test_without_thresholds_there_is_nothing_to_skip():
    """Порогов нет — проверки нет, и предупреждать не о чем."""
    coverage = Coverage(rules_from(Config({}, env="test", path="test.yaml")))
    for listing in cards(3):
        coverage.add(listing)

    assert coverage.skipped_note() is None
