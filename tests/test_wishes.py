"""Словарь пожеланий (решения 10, 11): слово брокера → условие на поле страницы."""
from __future__ import annotations

from pathlib import Path

import pytest

from listam.config import Config, ConfigError
from listam.domain.models import PageFields
from listam.domain.wishes import Wish, field_label, parse_wishes, vocabulary

WISHES = {
    "ремонт": {"field": "renovation", "any_of": ["косметический", "евроремонт", "дизайнерский"]},
    "не панель": {"field": "building_type", "none_of": ["панельное"]},
    "балкон": {"field": "balcony", "is": True},
    "лифт": {"field": "elevator", "is": True},
    "высокие потолки": {"field": "ceiling_height", "min": 3},
}


def config(wishes) -> Config:
    return Config({"funnel": {"wishes": wishes}}, env="test", path=Path("config/test.yaml"))


def page(**values) -> PageFields:
    return PageFields(values=values)


@pytest.fixture
def vocab():
    return vocabulary(config(WISHES))


def test_any_of(vocab):
    wish = vocab["ремонт"]
    assert wish.check(page(renovation="косметический")) is True
    assert wish.check(page(renovation="частичный")) is False


def test_none_of(vocab):
    wish = vocab["не панель"]
    assert wish.check(page(building_type="каменное")) is True
    assert wish.check(page(building_type="панельное")) is False


def test_is(vocab):
    assert vocab["лифт"].check(page(elevator=True)) is True
    assert vocab["лифт"].check(page(elevator=False)) is False


def test_min(vocab):
    wish = vocab["высокие потолки"]
    assert wish.check(page(ceiling_height=3.0)) is True
    assert wish.check(page(ceiling_height=2.7)) is False


def test_values_compare_without_case_and_spaces():
    wish = Wish(word="ремонт", field="renovation", any_of=["Косметический"])
    assert wish.check(page(renovation=" косметический ")) is True


def test_a_field_that_is_not_on_the_page_is_unknown(vocab):
    """Страница открыта, поля на ней нет — «неизвестно», а не «нет»."""
    assert vocab["лифт"].check(page(renovation="косметический")) is None


def test_a_page_that_was_not_opened_is_unknown(vocab):
    assert vocab["лифт"].check(None) is None


def test_a_value_of_the_wrong_kind_is_unknown(vocab):
    assert vocab["лифт"].check(page(elevator="есть")) is None
    assert vocab["высокие потолки"].check(page(ceiling_height="высокие")) is None


def test_parse_wishes_splits_by_comma_ignoring_case_and_spaces(vocab):
    wishes, unknown = parse_wishes("Ремонт, балкон ,лифт", vocab)
    assert [wish.word for wish in wishes] == ["ремонт", "балкон", "лифт"]
    assert unknown == []


def test_an_unknown_word_is_returned_not_swallowed(vocab):
    wishes, unknown = parse_wishes("лифт, бассейн", vocab)
    assert [wish.word for wish in wishes] == ["лифт"]
    assert unknown == ["бассейн"]


def test_empty_text_means_no_wishes(vocab):
    assert parse_wishes(None, vocab) == ([], [])
    assert parse_wishes(" , ", vocab) == ([], [])


def test_a_word_of_two_words_is_one_wish(vocab):
    wishes, unknown = parse_wishes("не  панель", vocab)
    assert [wish.word for wish in wishes] == ["не панель"]
    assert unknown == []


def test_no_vocabulary_is_an_empty_one():
    assert vocabulary(Config({}, env="test", path=Path("x"))) == {}


@pytest.mark.parametrize("broken", [
    {"лифт": {"is": True}},                                  # нет поля
    {"лифт": {"field": "elevator"}},                         # нет условия
    {"лифт": {"field": "elevator", "is": True, "min": 3}},   # два условия
    {"лифт": {"field": "elevator", "is": "да"}},             # не тумблер
    {"ремонт": {"field": "renovation", "any_of": []}},       # пустой список
    {"ремонт": {"field": "renovation", "any_of": "евро"}},   # не список
    {"потолки": {"field": "ceiling_height", "min": "три"}},  # не число
    {"лифт": {"field": "elevator", "equals": True}},         # неизвестное условие
    ["лифт"],                                                # не словарь
])
def test_a_crooked_vocabulary_is_refused(broken):
    with pytest.raises(ConfigError) as error:
        vocabulary(config(broken))
    assert "funnel.wishes" in str(error.value)


def test_field_labels_are_words_for_a_human():
    assert field_label("renovation") == "ремонт"
    assert field_label("building_type") == "тип дома"
    assert field_label("что-то новое") == "что-то новое"
