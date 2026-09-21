"""Парсер ленты продажи квартир (category/60).

Вёрстка на сайте меняется, поэтому правило одно: нет поля — `None`, карточка
всё равно возвращается. Потерять одно поле не страшно, уронить прогон на
трёхсотой странице — страшно.

Курса здесь нет и быть не может: парсер видит только страницу. Сумма кладётся
в колонку своей валюты, пересчёт в USD и `price_per_sqm` считает прогон.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from listam.domain.models import Listing
from listam.domain.money import parse_price, to_number

DEFAULT_BASE_URL = "https://www.list.am/ru"

# Контейнер ленты. Карточки живут только внутри него: ссылки `/item/` есть
# и в шапке, и в подвале, и в блоке «похожие».
FEED_CONTAINER = "contentr"


class ListingContainerMissing(Exception):
    """На странице нет контейнера ленты — разбирать нечего."""


ITEM_ID = re.compile(r"/item/(\d+)")
NEXT_PAGE = re.compile(r"/category/\d+/(\d+)$")

# "2 ком., 56 кв.м., 7/18 этаж" — части бывают поодиночке и в любом сочетании
ROOMS = re.compile(r"(\d+)\s*ком")
# Площадь пишут по-разному: «56», «56,5», «1 200», «1,200», «1.200»,
# с неразрывным пробелом внутри разряда и «кв м» без точек. Разбирает число
# тот же to_number, что и цены: своего разбора у парсера быть не должно.
AREA = re.compile(r"(\d[\d\s  .,']*\d|\d)\s*кв\.?\s*м")
FLOORS = re.compile(r"(\d+)\s*/\s*(\d+)\s*этаж")

# "на ул. Ачаряна в Аване", "на пр. Комитаса в Арабкире"
STREET = re.compile(r"\bна\s+(?:ул|пр|просп|ул-це)\.?\s+([^,]+?)\s+в\s+", re.IGNORECASE)

AGENCY_BADGE = "агентство"
VERIFIED_HINTS = ("кадастр", "подтвержд", "собственник")


def _text(node: Tag | None) -> str | None:
    if node is None:
        return None
    value = node.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", value) or None


def _card_nodes(soup: BeautifulSoup) -> list[Tag]:
    """Карточки ленты без блока «Топ объявления».

    Топ-блок (`#tp`) повторяется на каждой странице и не подчиняется сортировке
    по дате — из-за него инкрементальный прогон на M1 останавливался бы сразу.
    """
    container = soup.find(id=FEED_CONTAINER)
    if not isinstance(container, Tag):
        # Откат на весь документ собирал бы ссылки шапки и подвала: пяток
        # карточек-огрызков вместо ленты, и прогон считал бы это удачей.
        raise ListingContainerMissing(
            f"контейнер ленты не найден: на странице нет #{FEED_CONTAINER}. "
            f"Так выглядит заглушка Cloudflare с кодом 200 или смена вёрстки"
        )
    top = container.find(id="tp")
    top_cards = set()
    if isinstance(top, Tag):
        top_cards = {id(node) for node in top.select('a[href*="/item/"]')}
    return [
        node
        for node in container.select('a[href*="/item/"]')
        if id(node) not in top_cards
    ]


def _price(
    card: Tag,
) -> tuple[str | None, str | None, float | None, float | None, float | None]:
    """Сырая строка, валюта, сумма в валюте оригинала и две валюты, что знаем.

    price_amount — число ровно как на сайте: у EUR и RUB пересчёта не будет
    никогда, и без этой колонки цена терялась бы совсем.
    """
    raw = _text(card.select_one(".category-data-list-card__amount"))
    money = parse_price(raw)
    price_usd = money.amount if money.currency == "USD" else None
    price_amd = money.amount if money.currency == "AMD" else None
    return raw, money.currency, money.amount, price_usd, price_amd


def _attributes(card: Tag) -> tuple[int | None, float | None, int | None, int | None]:
    line = None
    for node in card.select("div.at"):
        classes = node.get("class") or []
        if "category-data-list-card__location" in classes:
            continue
        line = _text(node)
        break
    if not line:
        return None, None, None, None
    rooms = ROOMS.search(line)
    area = AREA.search(line)
    floors = FLOORS.search(line)
    return (
        int(rooms.group(1)) if rooms else None,
        to_number(area.group(1)) if area else None,
        int(floors.group(1)) if floors else None,
        int(floors.group(2)) if floors else None,
    )


def _seller_type(card: Tag) -> str | None:
    """Агентские объявления сайт помечает значком, частные — ничем."""
    for node in card.select("span.ge3"):
        if AGENCY_BADGE in (_text(node) or "").lower():
            return "agency"
    return "owner"


def _verified(card: Tag) -> bool | None:
    node = card.select_one("span.pr93")
    if node is None:
        return None
    classes = node.get("class") or []
    if "unverified" in classes:
        return False
    text = (_text(node) or "").lower()
    if any(hint in text for hint in VERIFIED_HINTS):
        return True
    return None


def parse_card(card: Tag, base_url: str = DEFAULT_BASE_URL) -> Listing | None:
    href = card.get("href") or ""
    match = ITEM_ID.search(href)
    if not match:
        return None
    title = _text(card.select_one("div.l"))
    street = STREET.search(title) if title else None
    raw, currency, price_amount, price_usd, price_amd = _price(card)
    rooms, area, floor, floors_total = _attributes(card)
    return Listing(
        id=match.group(1),
        url=urljoin(base_url.rstrip("/") + "/", f"item/{match.group(1)}"),
        title=title,
        district=_text(card.select_one(".category-data-list-card__location")),
        street=street.group(1).strip() if street else None,
        price_raw=raw,
        currency=currency,
        price_amount=price_amount,
        price_usd=price_usd,
        price_amd=price_amd,
        area=area,
        rooms=rooms,
        floor=floor,
        floors_total=floors_total,
        seller_type=_seller_type(card) if title or raw else None,
        verified=_verified(card),
        new_build=("новостройк" in title.lower()) if title else None,
    )


def parse_listing_cards(html: str, base_url: str = DEFAULT_BASE_URL) -> list[Listing]:
    """Все карточки страницы в порядке выдачи (сверху — самые свежие)."""
    soup = BeautifulSoup(html, "lxml")
    listings: list[Listing] = []
    seen: set[str] = set()
    for node in _card_nodes(soup):
        listing = parse_card(node, base_url=base_url)
        if listing is None or listing.id in seen:
            continue
        seen.add(listing.id)
        listings.append(listing)
    return listings


def parse_next_page(html: str) -> int | None:
    """Номер следующей страницы по пагинатору. Фильтры в URL не трогаем."""
    soup = BeautifulSoup(html, "lxml")
    numbers = []
    for link in soup.select("div.dlf a[href]"):
        path = urlparse(link["href"]).path
        found = NEXT_PAGE.search(path)
        if found:
            numbers.append(int(found.group(1)))
    if not numbers:
        return None
    current = soup.select_one("div.dlf span.pp span.c")
    page = int(current.get_text(strip=True)) if current and current.get_text(strip=True).isdigit() else 1
    following = [n for n in numbers if n > page]
    return min(following) if following else None
