"""Вёрстка сообщения брокеру — строго по пункту 8 `docs/анализ-после-M3.md`.

Сообщение — структура, а не строка (решение 7 спеки M3.5): раздел на заявку,
в разделе шапка, карточки и хвост. Резать длинное сообщение можно только
между карточками, и знает, где они, только тот, кто держит структуру, —
поэтому порт `Notifier` принимает `Message` целиком. HTML для Telegram и
простой текст для `--dry-run` и журнала — два рендера одной структуры:
что брокер прочёл в консоли, то и уйдёт в чат.

Значки — закрытый словарь пункта 8 (решение 6): 🔥 📋 🗞 🆕 📉 ♻️ ❌ 👤 🏢
🟢 🟡 ⚪ 💎 ⚠️ 📍 🔗 и ➕ хвоста из его же примера. Новых нет.

Модуль чистый: ни базы, ни сети. Выборку делает `notifications`, отправку —
адаптер канала.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from listam.config import Config, score_threshold
from listam.domain.events import (CHEAPER, NEW, RETIRED, REVIVED, MatchEvent, is_initial,
                                  limited)
from listam.domain.models import Exclusion, Listing, Request
from listam.domain.scoring import DEFAULT_HOT, refusal_words

MINUS = "−"          # настоящий минус U+2212: дефис в числе теряется
RULE = "━" * 15       # черта под шапкой заявки
DEFAULT_SCORE_GREEN = 90.0     # фаза 4 M3.5: было 80 — выше поднятого hot
DEFAULT_GEM_PERCENT = 15.0

ICONS = {NEW: "🆕", CHEAPER: "📉", REVIVED: "♻️", RETIRED: "❌"}
SELLERS = {"owner": "👤 Собственник", "agency": "🏢 Агентство"}


# ---------------------------------------------------------------- модель

@dataclass
class Span:
    text: str
    bold: bool = False
    href: str | None = None


Line = list[Span]


@dataclass
class Section:
    """Одна заявка — одно сообщение Telegram (или несколько, если длинно).

    `head` повторяется на каждом продолжении с « (продолжение)» в первой
    строке: брокер пересылает часть клиенту, и кусок без имени заявки —
    чужой разговор. Резать можно только между `cards`.
    """

    head: list[Line]
    cards: list[list[Line]]
    tail: list[Line] = field(default_factory=list)


@dataclass
class Message:
    sections: list[Section]


def to_html(section: Section) -> str:
    """HTML для `parse_mode: HTML`: `<`, `>`, `&` экранированы — иначе
    Telegram отклоняет сообщение целиком."""
    return _render(section, _html_line)


def to_plain(section: Section) -> str:
    """Простой текст без тегов; ссылка — «Открыть: <url>»."""
    return _render(section, _plain_line)


def plain(message: Message) -> str:
    """Весь `Message` простым текстом — для `--dry-run` и журнала отправок."""
    return "\n\n".join(to_plain(section) for section in message.sections)


def text_message(text: str) -> Message:
    """Сообщение из одной строки без карточек: тревога цикла, проверка канала."""
    return Message([Section(head=[[Span(line)] for line in text.splitlines()] or [[Span("")]],
                            cards=[])])


def _html_line(line: Line) -> str:
    out: list[str] = []
    for span in line:
        text = html.escape(span.text, quote=False)
        if span.bold:
            text = f"<b>{text}</b>"
        if span.href:
            text = f'<a href="{html.escape(span.href, quote=True)}">{text}</a>'
        out.append(text)
    return "".join(out)


def _plain_line(line: Line) -> str:
    return "".join(f"{span.text}: {span.href}" if span.href else span.text
                   for span in line)


def _render(section: Section, line_of) -> str:
    """Шапка, карточки, хвост — блоками через пустую строку.

    Карточки из одной строки (💎 ленты) идут подряд: пустая строка между
    ними растянула бы список вдвое без пользы.
    """
    def lines(block: list[Line]) -> str:
        return "\n".join(line_of(line) for line in block)

    blocks: list[str] = []
    if section.head:
        blocks.append(lines(section.head))
    if section.cards:
        gap = "\n\n" if any(len(card_) > 1 for card_ in section.cards) else "\n"
        blocks.append(gap.join(lines(card_) for card_ in section.cards))
    if section.tail:
        blocks.append(lines(section.tail))
    return "\n\n".join(blocks)


# ---------------------------------------------------------------- числа

def usd(value: float | None) -> str:
    """`$165 000`: пробел между тысячами, как в примере пункта 8."""
    return "" if value is None else f"${value:,.0f}".replace(",", " ")


def _percent(value: float, median: float) -> str:
    whole = round((value - median) / median * 100)
    if whole == 0:
        return "0%"
    return f"{'+' if whole > 0 else MINUS}{abs(whole)}%"


def _plural(count: int, one: str, few: str, many: str) -> str:
    count = abs(int(count))
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


def _area(listing: Listing) -> str | None:
    return None if listing.area is None else f"{listing.area:g} м²"


def _per_sqm(listing: Listing) -> str | None:
    return None if listing.price_per_sqm is None else f"{usd(listing.price_per_sqm)}/м²"


def _joined(parts: list[str | None]) -> str:
    return " · ".join(part for part in parts if part)


# ---------------------------------------------------------------- карточка

def circles(config: Config) -> tuple[float | None, float | None]:
    """Мерки кружка балла: 🟢 от `notify.layout.score_green`, 🟡 от порога hot."""
    green = score_threshold(config, "notify.layout.score_green", DEFAULT_SCORE_GREEN)
    yellow = score_threshold(config, "match.thresholds.hot", DEFAULT_HOT)
    return green, yellow


def gem_percent(config: Config) -> float | None:
    """На сколько процентов дешевле медианы района — уже 💎. `null` — 💎 нет."""
    return score_threshold(config, "notify.feed.gem_percent", DEFAULT_GEM_PERCENT)


def _circle(score: float, green: float | None, yellow: float | None) -> str:
    if green is not None and score >= green:
        return "🟢"
    if yellow is not None and score >= yellow:
        return "🟡"
    return "⚪"


def card(event: MatchEvent, median: float | None, config: Config) -> list[Line]:
    """Карточка события — строго три строки.

    1. значок события · **цена** · площадь · $/м² (процент к медиане района);
    2. 📍 район, улица · этаж;
    3. кто продаёт · кластер · кружок и балл · 🔗 Открыть.

    Незаданное не печатается: прочерк в телефоне читается как поломка.
    """
    listing = event.listing
    first: Line = [Span(f"{ICONS.get(event.kind, '🆕')} "),
                   Span(usd(listing.price_usd) or "цена не указана", bold=True)]
    if event.kind == CHEAPER and event.price_before is not None:
        first.append(Span(f" (было {usd(event.price_before)})"))
    per_sqm = _per_sqm(listing)
    if per_sqm and median:
        per_sqm += f" ({_percent(listing.price_per_sqm, median)} к району)"
    rest = _joined([_area(listing), per_sqm,
                    "❌ снято" if listing.status == "gone" else None])
    if rest:
        first.append(Span(f" · {rest}"))

    place = ", ".join(part for part in (listing.district, listing.street) if part)
    floor = None
    if listing.floor is not None:
        floor = f"этаж {listing.floor}" + (
            f"/{listing.floors_total}" if listing.floors_total is not None else "")
    second: Line = [Span(f"📍 {_joined([place, floor]) or 'адрес не указан'}")]

    match = event.match
    cluster = None
    if match.cluster_size and match.cluster_size > 1:
        # `is not None`, а не `if`: разброс $0 — «три карточки по одной цене».
        spread = (f", разброс {usd(match.cluster_spread_usd)}"
                  if match.cluster_spread_usd is not None else "")
        cluster = (f"{match.cluster_size} "
                   + _plural(match.cluster_size, "объявление", "объявления", "объявлений")
                   + spread)
    score = None
    if match.score is not None:
        whole = int(round(match.score))
        score = f"{_circle(whole, *circles(config))} балл {whole}"
    lead = _joined([SELLERS.get(listing.seller_type or ""), cluster, score])
    third: Line = [Span(f"{lead} · 🔗 " if lead else "🔗 "),
                   Span("Открыть", href=listing.url)]
    return [first, second, third]


# ---------------------------------------------------------------- шапка заявки

def _rooms(rooms: list[int]) -> str | None:
    if not rooms:
        return None
    ordered = sorted(set(rooms))
    if len(ordered) > 1 and ordered == list(range(ordered[0], ordered[-1] + 1)):
        return f"{ordered[0]}–{ordered[-1]} комн."
    return f"{', '.join(str(room) for room in ordered)} комн."


def _area_range(request: Request) -> str | None:
    low, high = request.area_min, request.area_max
    if low is not None and high is not None:
        return f"{low:g}–{high:g} м²"
    if low is not None:
        return f"от {low:g} м²"
    if high is not None:
        return f"до {high:g} м²"
    return None


def request_title(icon_title: str, request: Request) -> str:
    return _joined([icon_title, request.external_id or f"#{request.id}",
                    request.client_name])


def request_head(icon_title: str, request: Request,
                 refused: Sequence[Exclusion] = ()) -> list[Line]:
    """Шапка заявки: кто клиент и что он просил — чтобы не вспоминать.

    Незаданное в заявке не печатается; заявка без единого пожелания — без
    второй строки вовсе. `refused` — отказы клиента (решение 15): словами
    в конце второй строки, «· без 1-го этажа, без панели».
    """
    words = refusal_words(refused)
    wishes = _joined([", ".join(request.districts) or None, _rooms(request.rooms),
                      _area_range(request),
                      f"до {usd(request.budget_max)}" if request.budget_max else None,
                      ", ".join(words) or None])
    lines: list[Line] = [[Span(request_title(icon_title, request), bold=True)]]
    if wishes:
        lines.append([Span(wishes)])
    lines.append([Span(RULE)])
    return lines


# ---------------------------------------------------------------- по заявкам

def _by_request(page) -> list[tuple[Request, list[MatchEvent]]]:
    """События, разложенные по заявкам в порядке заявок страницы."""
    owners = {request.id: request for request in page.requests.values()}
    grouped: dict[int | None, list[MatchEvent]] = {}
    for event in page.events:
        grouped.setdefault(event.match.request_id, []).append(event)
    ordered = [(owners[rid], grouped[rid]) for rid in owners if rid in grouped]
    ordered += [(Request(id=rid, external_id=f"#{rid}"), events)
                for rid, events in grouped.items() if rid not in owners]
    return ordered


def _refused(page, request: Request) -> list[Exclusion]:
    """Исключения заявки, которые принесла страница событий (`EventsPage`)."""
    return list(page.exclusions.get(request.id, ()))


def _cards(events: list[MatchEvent], medians: dict[str, float],
           config: Config) -> list[list[Line]]:
    return [card(event, medians.get(event.listing.district or ""), config)
            for event in events]


def _variants(count: int) -> str:
    return f"{count} " + _plural(count, "вариант", "варианта", "вариантов")


HOT = "🔥 ЗВОНИ СЕЙЧАС"
DIGEST = "📋 ДАЙДЖЕСТ"
FEED = "🗞 НА ЛЕНТЕ ЗА СУТКИ"


def hot_message(page, medians: dict[str, float], config: Config,
                per_request: int | None) -> Message:
    """«Звони сейчас»: сообщение на заявку, лучшие сверху, человеческий хвост."""
    sections: list[Section] = []
    for request, events in _by_request(page):
        alive = [event for event in events if event.kind != RETIRED]
        if not alive:
            continue
        shown, total = limited(alive, per_request)
        tail = ([[Span(f"➕ ещё {_variants(total - len(shown))} — в дайджесте вечером")]]
                if total > len(shown) else [])
        sections.append(Section(head=request_head(HOT, request, _refused(page, request)),
                                cards=_cards(shown, medians, config), tail=tail))
    if not sections:
        sections.append(Section(head=[[Span(f"{HOT} · событий нет", bold=True)]], cards=[]))
    return Message(sections)


def _counts(events: list[MatchEvent]) -> dict[str, int]:
    counts = {kind: 0 for kind in (NEW, CHEAPER, REVIVED, RETIRED)}
    for event in events:
        counts[event.kind] = counts.get(event.kind, 0) + 1
    return counts


def digest_message(page, medians: dict[str, float], config: Config,
                   per_request: int | None, wide: int | None, active: int,
                   at: datetime, initial_top: int | None = None) -> Message:
    """Дайджест: сначала сводка, затем сообщение на каждую заявку со звонками.

    Заявка только с закрытиями сообщения не получает — строка в сводке
    (решение 8 спеки M3.5). `active` — сколько заявок в работе, `at` —
    момент отправки; время в сводке — в `locale.timezone`.

    Первичная подборка новой заявки (решение 14) — отдельный раздел
    «первичная подборка: N лучших из M» после рыночного. Широту она не мерит:
    сотни матчей у только что пришедшей заявки — её природа, а не ошибка.
    """
    from listam.schedule import local_zone    # schedule шлёт тревогу через layout

    local = at.astimezone(local_zone(config))
    grouped = _by_request(page)
    total = _counts([event for event in page.events if not is_initial(event)])
    initial_total = sum(1 for event in page.events if is_initial(event))
    with_calls = sum(1 for _, events in grouped
                     if any(event.kind != RETIRED for event in events))

    summary_tail: list[Line] = [
        [Span(f"🆕 новых — {total[NEW]}   📉 подешевело — {total[CHEAPER]}   "
              f"♻️ вернулось — {total[REVIVED]}"
              + (f"   📋 первичная подборка — {initial_total}" if initial_total else ""))],
        [Span(f"👥 заявок с находками — {with_calls} из {active}")],
    ]
    rows: list[Line] = []
    sections: list[Section] = []
    for request, events in grouped:
        initial = [event for event in events if is_initial(event)]
        alive = [event for event in events
                 if event.kind != RETIRED and not is_initial(event)]
        gone = [event for event in events if event.kind == RETIRED]
        counts = _counts(alive)
        marks = [f"{ICONS[kind]} {counts[kind]}" for kind in (NEW, CHEAPER, REVIVED)
                 if counts[kind]]
        if initial:
            marks.append(f"📋 первичная подборка {len(initial)}")
        too_wide = wide is not None and len(alive) > wide
        if too_wide:
            marks.append("⚠️ слишком широкая")
        if gone:
            reasons: dict[str, int] = {}
            for event in gone:
                reason = event.match.retired_reason or "причина не записана"
                reasons[reason] = reasons.get(reason, 0) + 1
            marks.append(f"❌ {len(gone)} ("
                         + ", ".join(f"{reason} {count}"
                                     for reason, count in reasons.items()) + ")")
        rows.append([Span(f"{request_title('', request)} — "
                          + "  ".join(marks))])
        refused = _refused(page, request)
        sections.extend(_market_section(request, alive, medians, config,
                                        per_request, too_wide, refused))
        if initial:
            best, count = limited(initial, initial_top)
            head = request_head(DIGEST, request, refused)
            head[0][0].text += (f" — первичная подборка: {len(best)} "
                                f"{_plural(len(best), 'лучший', 'лучших', 'лучших')} "
                                f"из {count}")
            sections.append(Section(head=head, cards=_cards(best, medians, config)))
    if rows:
        summary_tail.append([])
        summary_tail.extend(rows)
    summary = Section(head=[[Span(f"{DIGEST} · {local:%d.%m · %H:%M}", bold=True)]],
                      cards=[], tail=summary_tail)
    return Message([summary] + sections)


def _market_section(request: Request, alive: list[MatchEvent],
                    medians: dict[str, float], config: Config,
                    per_request: int | None, too_wide: bool,
                    refused: Sequence[Exclusion] = ()) -> list[Section]:
    """Раздел рыночных событий заявки; нет событий — нет раздела."""
    if not alive:
        return []
    shown, count = limited(alive, per_request)
    head = request_head(DIGEST, request, refused)
    if too_wide:
        # Широту мерят события, а закрытие событием не является (решение 1
        # спеки M3): сотни «отпало» — ответ на сужение заявки.
        events_word = _plural(len(alive), "событие", "события", "событий")
        head.insert(-1, [Span(f"⚠️ слишком широкая: {len(alive)} {events_word} за окно — "
                              f"сузь районы или бюджет")])
    tail = ([[Span(f"➕ ещё {_variants(count - len(shown))} ниже по баллу")]]
            if count > len(shown) else [])
    return [Section(head=head, cards=_cards(shown, medians, config), tail=tail)]


# ---------------------------------------------------------------- лента

def feed_message(fresh: list[Listing], cheaper: int, gone: int,
                 medians: dict[str, float], config: Config,
                 limit: int | None) -> Message:
    """Лента вне заявок: счётчики и 💎 — только заметно дешевле медианы района.

    «Заметно» — `notify.feed.gem_percent`; объявление без медианы района
    или без цены за метр 💎 не получает: сравнивать не с чем.
    """
    gem = gem_percent(config)

    def cheapness(item: Listing) -> float | None:
        # Помеченное аномалией в медиану не входит (`stats.clean`) и 💎 не
        # получает: «дешевле медианы на 100%» — опечатка, а не находка.
        if item.anomaly:
            return None
        median = medians.get(item.district or "")
        if not median or item.price_per_sqm is None:
            return None
        return (median - item.price_per_sqm) / median * 100

    gems = []
    for item in fresh:
        below = cheapness(item)
        # Допуск на плавающую точку: ровно −15% — это «на 15% и больше».
        if below is not None and gem is not None and below >= gem - 1e-9:
            gems.append((below, item))
    gems.sort(key=lambda pair: (-pair[0], pair[1].id))
    shown = gems if limit is None else gems[:limit]

    head: list[Line] = [
        [Span(FEED, bold=True)],
        [Span(f"🆕 {len(fresh)} новых · 📉 {cheaper} подешевели · ❌ {gone} снято")],
    ]
    cards: list[list[Line]] = []
    tail: list[Line] = []
    if shown:
        head += [[], [Span("Самые выгодные против медианы района:")]]
        for _, item in shown:
            median = medians[item.district or ""]
            per_sqm = f"{_per_sqm(item)} ({_percent(item.price_per_sqm, median)})"
            cards.append([[Span("💎 "), Span(usd(item.price_usd) or "цена не указана",
                                             bold=True),
                           Span(f" · {_joined([_area(item), per_sqm, item.district])} · 🔗 "),
                           Span("Открыть", href=item.url)]])
        if len(gems) > len(shown):
            tail.append([Span(f"➕ ещё {len(gems) - len(shown)} заметно дешевле медианы")])
    else:
        tail.append([Span("💎 заметно дешевле медианы района — нет")])
    return Message([Section(head=head, cards=cards, tail=tail)])
