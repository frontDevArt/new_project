"""Разбор страницы объявления (фаза 3 M3.5).

Фикстуры сняты инструментом (боевой `Fetcher`, 23.09.2026): монолитная
новостройка в Кентроне, каменный дом в Арабкире, панельный в Нор Норке.
Имя продавца в них заменено на «Продавец».
"""
from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from listam.parsers.item_page import ItemPageMissing, PARSER_VERSION, parse_item_page

FIXTURES = Path(__file__).parent / "fixtures"


def page(listing_id: str) -> str:
    return (FIXTURES / f"item-{listing_id}.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def monolith():
    return parse_item_page(page("23814256"))


@pytest.fixture(scope="module")
def stone():
    return parse_item_page(page("24032612"))


@pytest.fixture(scope="module")
def panel():
    return parse_item_page(page("24041732"))


def test_the_parser_has_a_version():
    assert PARSER_VERSION >= 1


def test_the_building_type_is_the_word_of_the_site(monolith, stone, panel):
    """Значение — слово сайта строчными: словарь пожеланий пишется теми же
    словами, что видит брокер на странице."""
    assert monolith.values["building_type"] == "монолит"
    assert stone.values["building_type"] == "каменное"
    assert panel.values["building_type"] == "панельное"


def test_renovation(monolith, stone, panel):
    assert monolith.values["renovation"] == "дизайнерский"
    assert stone.values["renovation"] == "частичный"
    assert panel.values["renovation"] == "косметический"


def test_yes_and_no_become_booleans(monolith, stone, panel):
    assert monolith.values["elevator"] is True        # «Есть»
    assert stone.values["elevator"] is False          # «Нет»
    assert panel.values["elevator"] is True
    assert monolith.values["new_build"] is True       # «Да»
    assert stone.values["new_build"] is False


def test_balcony_is_a_flag_and_its_kind(monolith, panel):
    assert monolith.values["balcony"] is True
    assert monolith.values["balcony_type"] == "открытый"
    assert panel.values["balcony"] is True
    assert panel.values["balcony_type"] == "закрытый"


def test_furniture(panel):
    assert panel.values["furniture"] == "с мебелью"


def test_numbers_of_the_top_block(monolith, stone, panel):
    """Верхний блок пишет значение над подписью, нижние — под ней: разбор
    узнаёт подпись по словарю, а не по месту."""
    assert monolith.values["area"] == 113.0
    assert monolith.values["floor"] == 8
    assert monolith.values["floors_total"] == 12
    assert monolith.values["ceiling_height"] == 3.0
    assert monolith.values["rooms"] == 3
    assert monolith.values["bathrooms"] == 2
    assert stone.values["ceiling_height"] == 2.8
    assert panel.values["ceiling_height"] == 2.7


def test_switched_off_features_are_false_and_switched_on_are_true(monolith, stone):
    """Серые пункты («Домофон» с классом disabled) — «нет», яркие — «есть»."""
    assert monolith.values["intercom"] is False
    assert monolith.values["concierge"] is False
    assert monolith.values["playground"] is False
    assert monolith.values["view_ararat"] is True
    assert monolith.values["view_yard"] is False
    assert stone.values["view_yard"] is True


def test_parking_is_read_under_its_heading(monolith, panel):
    """«Открытая» и «Закрытая» без заголовка «Парковка» ничего не значат."""
    assert monolith.values["parking_covered"] is True
    assert monolith.values["parking_outdoor"] is False
    assert monolith.values["garage"] is False
    assert "parking_covered" not in panel.values     # блока на странице нет


def test_description_without_the_translation_mark(panel, monolith):
    assert panel.description.startswith("Продается трехкомнатная квартира")
    assert panel.description.endswith("облицована плиткой.")
    assert "Переведено" not in monolith.description


def test_photos_come_from_the_gallery(monolith, stone, panel):
    assert len(monolith.photos) == 9
    assert len(stone.photos) == 8
    assert len(panel.photos) == 13
    assert monolith.photos[0] == "https://img.list.am/f/195/98357195.webp"


def test_nothing_on_the_real_pages_is_unknown(monolith, stone, panel):
    for fields in (monolith, stone, panel):
        assert fields.values.get("_unknown", []) == []


def test_unknown_label_is_reported_not_fatal():
    """Новая подпись на сайте не роняет разбор: она уходит списком
    в `_unknown`, и прогон её считает."""
    html = page("24041732").replace(">Лифт<", ">Сауна<")

    fields = parse_item_page(html)

    assert "elevator" not in fields.values
    assert fields.values["_unknown"] == ["Сауна"]
    assert fields.values["building_type"] == "панельное"


def test_an_unknown_value_of_a_yes_no_field_is_reported():
    html = page("24041732").replace(">Есть<", ">Иногда<", 1)

    fields = parse_item_page(html)

    assert "elevator" not in fields.values
    assert fields.values["_unknown"] == ["Лифт: Иногда"]


def test_a_missing_field_is_just_missing():
    """Правило M0: нет поля — нет значения, страница всё равно разобрана."""
    soup = BeautifulSoup(page("24041732"), "lxml")
    for label in soup.find_all("p", string="Тип здания"):
        label.find_parent("div", class_="at3").decompose()

    fields = parse_item_page(str(soup))

    assert "building_type" not in fields.values
    assert fields.values.get("_unknown", []) == []
    assert fields.values["renovation"] == "косметический"


def test_a_page_without_the_item_is_refused():
    """Страница без блока объявления — не «всё неизвестно», а сбой: иначе
    уехавшая вёрстка выглядела бы как страница без полей."""
    with pytest.raises(ItemPageMissing):
        parse_item_page("<html><body><p>Что-то другое</p></body></html>")
