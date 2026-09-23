"""Парсер страницы объявления (`/item/<id>`): то, чего нет в карточке ленты.

Чистая функция: HTML на входе, `PageFields` на выходе. Сети, базы и конфига
здесь нет.

Вёрстка (снята 23.09.2026 с трёх настоящих страниц, фикстуры
`tests/fixtures/item-*.html`): характеристики лежат блоками `div.attr`,
каждая — `div.at3`, над блоком — заголовок `div.gt` («О здании»,
«О квартире», «Парковка», «Виды из окон»). Пункт из двух строк — подпись и
значение, **но в верхнем блоке значение стоит над подписью, а в остальных —
под ней**. Поэтому подпись узнаётся по словарю, а не по месту. Пункт из
одной строки — признак: серый (`disabled`) значит «нет», яркий — «есть».

Правило M0: нет поля — нет значения, страница всё равно разобрана.
Подпись, которой разбор не знает, уходит списком в `values["_unknown"]`:
прогон её считает, а не молчит.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from listam.domain.models import PageFields
from listam.domain.money import to_number

# Растёт, когда меняется смысл разобранного: строка кэша помнит, чем
# её разобрали (`listing_pages.parser_version`).
PARSER_VERSION = 1

UNKNOWN = "_unknown"

# Блок объявления. Нет его — перед нами не страница объявления.
ITEM_BLOCK = "div.vi"
GALLERY = re.compile(r"po113\.init\([^{]*\{\s*img\s*:\s*\[([^\]]*)\]")
GALLERY_URL = re.compile(r'"([^"]+)"')


NUMBER = re.compile(r"\d[\d\s.,]*\d|\d")


def _number(text: str) -> float | None:
    """«80 кв.м.», «2.8 м», «12» — число с разбором разрядов из `money`."""
    found = NUMBER.search(text)
    return to_number(found.group(0)) if found else None


def _integer(text: str) -> int | None:
    number = _number(text)
    return None if number is None else int(number)


def _word(text: str) -> str:
    """Слово сайта строчными: словарь пожеланий пишется теми же словами."""
    return re.sub(r"\s+", " ", text).strip().lower()


YES = {"да", "есть"}
NO = {"нет"}


def _yes_no(text: str) -> bool | None:
    word = _word(text)
    if word in YES:
        return True
    if word in NO:
        return False
    return None


# Подпись с двумя строками → (поле, как читать значение). Слова — как на
# сайте по-русски. `None` от чтения — значение непонятно, в `_unknown`.
VALUED = {
    "Общая площадь": ("area", _number),
    "Этаж": ("floor", _integer),
    "Этажей в доме": ("floors_total", _integer),
    "Высота потолков": ("ceiling_height", _number),
    "Количество комнат": ("rooms", _integer),
    "Количество санузлов": ("bathrooms", _integer),
    "Тип здания": ("building_type", _word),
    "Новостройка": ("new_build", _yes_no),
    "Лифт": ("elevator", _yes_no),
    "Мебель": ("furniture", _word),
    "Ремонт": ("renovation", _word),
    "Балкон": ("balcony_type", _word),     # «открытый», «закрытый»; флаг — ниже
}

# Признак из одной строки → поле. Парковка читается под своим заголовком:
# «Открытая» без него ничего не значит.
FLAGS = {
    "Домофон": "intercom",
    "Консьерж": "concierge",
    "Детская площадка": "playground",
    "Вид на двор": "view_yard",
    "Вид на улицу": "view_street",
    "Вид на город": "view_city",
    "Вид на парк": "view_park",
    "Вид на Арарат": "view_ararat",
    "Парковка/Открытая": "parking_outdoor",
    "Парковка/Закрытая": "parking_covered",
    "Парковка/Гараж": "garage",
    # Пункт из двух строк, пришедший серым одной строкой, — «нет».
    "Лифт": "elevator",
    "Балкон": "balcony",
}


class ItemPageMissing(Exception):
    """На странице нет блока объявления — разбирать нечего."""


def _text(node: Tag) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def _heading(block: Tag) -> str | None:
    head = block.find_previous_sibling("div", class_="gt")
    if head is None:
        return None
    # «Регион» несёт в себе пустой span карты; заголовок — только текст.
    return _text(head) or None


def _read_valued(label: str, raw: str, values: dict, unknown: list[str]) -> None:
    name, read = VALUED[label]
    value = read(raw)
    if value is None or value == "":
        unknown.append(f"{label}: {raw}")
        return
    values[name] = value
    if name == "balcony_type":
        values["balcony"] = value not in NO


def _read_item(item: Tag, heading: str | None, first_block: bool,
               values: dict, unknown: list[str]) -> None:
    texts = [_text(p) for p in item.find_all("p")]
    texts = [text for text in texts if text]
    if not texts:
        return
    switched_off = "disabled" in (item.get("class") or [])

    if len(texts) == 1:
        label = texts[0]
        key = f"{heading}/{label}" if heading and f"{heading}/{label}" in FLAGS else label
        if key in FLAGS:
            values[FLAGS[key]] = not switched_off
        else:
            unknown.append(label)
        return

    # Две строки: какая из них подпись — решает словарь, а не порядок.
    if texts[1] in VALUED:
        label, raw = texts[1], texts[0]
    elif texts[0] in VALUED:
        label, raw = texts[0], texts[1]
    else:
        # Незнакомая подпись: называем её по обычаю блока — в верхнем
        # подпись вторая, в остальных первая.
        unknown.append(texts[1] if first_block else texts[0])
        return
    _read_valued(label, raw, values, unknown)


def _description(soup: BeautifulSoup) -> str | None:
    node = soup.select_one("[itemprop=description]")
    if node is None:
        return None
    for mark in node.select(".trans"):          # «Переведено с армянского»
        mark.decompose()
    return _text(node) or None


def _photos(html: str) -> list[str]:
    found = GALLERY.search(html)
    if not found:
        return []
    return ["https:" + url if url.startswith("//") else url
            for url in GALLERY_URL.findall(found.group(1))]


def parse_item_page(html: str) -> PageFields:
    """Поля страницы объявления. Блока объявления нет — `ItemPageMissing`."""
    soup = BeautifulSoup(html, "lxml")
    item_block = soup.select_one(ITEM_BLOCK)
    if item_block is None:
        raise ItemPageMissing("на странице нет блока объявления (div.vi)")

    values: dict[str, object] = {}
    unknown: list[str] = []
    for index, block in enumerate(item_block.select("div.attr")):
        heading = _heading(block)
        first_block = index == 0 and heading is None
        for item in block.select("div.at3"):
            _read_item(item, heading, first_block, values, unknown)

    if unknown:
        values[UNKNOWN] = unknown
    return PageFields(values=values, description=_description(soup),
                      photos=_photos(html))
