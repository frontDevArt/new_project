"""Подставной list.am: лента и страница объявления в разметке сайта.

Разметка — по фикстурам tests/fixtures/category-60-page1.html и
item-*.html: разбирают её настоящие парсеры, как живую. Каждое обращение
пишется в журнал — по нему отчёт считает открытия страниц (С-5, С-6).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from html import escape
from urllib.parse import urlparse

from listam.ports.fetcher import FetchError, Fetcher
from tests.sim.market import Flat, Market

PER_PAGE = 95          # столько карточек отдаёт страница ленты (216 страниц на 20 675)
MIN_CARDS = 20         # scrape.min_cards_per_page боевого конфига
FEED = re.compile(r"/category/60(?:/(\d+))?/?$")
ITEM = re.compile(r"/item/(\d+)")


def page_slices(count: int, per_page: int = PER_PAGE) -> list[tuple[int, int]]:
    """Страницы поровну, не больше `per_page`: хвост меньше MIN_CARDS обход
    счёл бы сбоем. Страниц ⌈n/95⌉ — на каждой не меньше 47 при n ≥ 96."""
    if count < MIN_CARDS:
        raise ValueError(f"на рынке {count} объявлений — меньше {MIN_CARDS}, ленты не выйдет")
    pages = max(1, math.ceil(count / per_page))
    base, extra = divmod(count, pages)
    slices, start = [], 0
    for index in range(pages):
        end = start + base + (1 if index < extra else 0)
        slices.append((start, end))
        start = end
    return slices


def _amount(flat: Flat) -> str:
    if flat.currency == "USD":
        return (f'<span class="category-data-list-card__currency">$</span>{flat.price:,}')
    return f'{flat.price:,}<span class="category-data-list-card__currency">֏</span>'


def card_html(flat: Flat) -> str:
    verified = {
        True: '<span class="pr93 clickable">Проверено по кадастру</span>',
        False: '<span class="pr93 clickable unverified">Недвижимость не проверена</span>',
        None: "",
    }[flat.verified]
    agency = '<span class="ge4">Агентство</span>' if flat.agency else ""
    return (
        f'<a class="category-data-list-grid-card__destination" href="/ru/item/{flat.id}?ld_src=2">'
        f'<div class="p"><span class="category-data-list-card__amount">{_amount(flat)}</span></div>'
        f'<div class="l">{escape(flat.title)}</div>'
        f'<div class="at">{flat.rooms} ком., {flat.area:g} кв.м., {flat.floor}/{flat.floors_total} этаж</div>'
        f'<div class="po78">{verified}{agency}</div>'
        f'<div class="category-data-list-card__grid-bottom">'
        f'<div class="at category-data-list-card__location">{escape(flat.district)}</div></div></a>'
    )


def feed_page(flats: list[Flat], number: int) -> str | None:
    slices = page_slices(len(flats))
    if not 1 <= number <= len(slices):
        return None
    start, end = slices[number - 1]
    cards = "\n".join(card_html(flat) for flat in flats[start:end])
    following = (f'<a href="/category/60/{number + 1}">{number + 1}</a>'
                 if number < len(slices) else "")
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8"></head><body>'
        '<div id="main"><div id="contentr"><div class="dl"><div class="gl">\n'
        f"{cards}\n</div></div>"
        f'<div class="dlf"><span class="pp"><span class="c">{number}</span>{following}</span></div>'
        "</div></div></body></html>"
    )


def item_page(flat: Flat) -> str:
    fields = {
        "Общая площадь": f"{flat.area:g} кв.м.",
        "Этаж": str(flat.floor),
        "Этажей в доме": str(flat.floors_total),
        "Количество комнат": str(flat.rooms),
        **flat.page,
    }
    items = "".join(f'<div class="at3"><p>{escape(value)}</p><p>{escape(label)}</p></div>'
                    for label, value in fields.items())
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8"></head><body>'
        f'<div class="vi"><div class="attr">{items}</div>'
        f'<div itemprop="description">Квартира {escape(flat.id)} — имитация</div></div>'
        "</body></html>"
    )


@dataclass(frozen=True)
class Fetch:
    at: datetime
    kind: str                 # feed | item
    key: str                  # номер страницы ленты или id объявления
    status: int
    cycle: str | None
    facts: dict | None = None


class SimFetcher(Fetcher):
    def __init__(self, market: Market, clock):
        self.market = market
        self.clock = clock
        self.journal: list[Fetch] = []
        self.cycle: str | None = None

    @property
    def requests_made(self) -> int:
        return len(self.journal)

    def _log(self, kind: str, key: str, status: int, facts: dict | None = None) -> None:
        self.journal.append(Fetch(self.clock.now(), kind, key, status, self.cycle, facts))

    def get(self, url: str) -> str:
        path = urlparse(url).path if "://" in url else url.split("?")[0]
        item = ITEM.search(path)
        if item:
            flat = self.market.get(item.group(1))
            if flat is None or flat.status != "active":
                self._log("item", item.group(1), 404)
                raise FetchError(f"имитация: объявления {item.group(1)} нет", status=404)
            self._log("item", flat.id, 200, {
                "district": flat.district, "rooms": flat.rooms, "area": flat.area,
                "currency": flat.currency, "price": flat.price})
            return item_page(flat)
        feed = FEED.search(path)
        if feed:
            number = int(feed.group(1) or 1)
            html = feed_page(self.market.feed(), number)
            if html is None:
                self._log("feed", str(number), 404)
                raise FetchError(f"имитация: страницы ленты {number} нет", status=404)
            self._log("feed", str(number), 200)
            return html
        raise FetchError(f"имитация не знает адреса {url}", status=404)
