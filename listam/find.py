"""`find` — быстрый поиск: «Арабкир, 3 комнаты, до $120k» одной командой.

Решение 16 спеки M3.5: `find` — это заявка, которую не записывают. Флаги
собираются в строку таблицы заявок и идут через тот же `parse_row`, что
CSV, а варианты оцениваются тем же `score()` с весами, растяжкой, порогами
и `secondary_district` из `settings(config)`, что подбор. Иначе «find
показал 85, а в витрине 71» — и верить нельзя ни тому, ни другому.

Три вещи, которые здесь важнее удобства:

- **Заявка не пишется, матчи тоже.** Без `--open` база открывается на
  чтение и без замка (`open_for_reading`): искать можно посреди прогона.
- **Страница открывается только по `--open N`** и только у найденных,
  не больше `funnel.find_max_opens` за раз, с паузой воронки. Это не обход
  «про запас», а просьба человека про конкретный список.
- **Неизвестное поле страницы не выдумывается.** `--wish` — пожелание
  (`nice_to_have`), седьмой фактор балла: вариант без открытой страницы
  остаётся в выдаче, а строка пожелания честно говорит, у скольких поле
  известно.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from listam.changes import money
from listam.clustering_run import area_tolerance
from listam.config import Config, ConfigError, positive
from listam.domain.clustering import Cluster, clusters
from listam.domain.models import Listing, ListingPage, Match, Request
from listam.domain.requests import RequestParseError, parse_row
from listam.domain.scoring import Score, score
from listam.domain.stats import median_price_per_sqm_by_district
from listam.domain.wishes import Wish, parse_wishes
from listam.matches_view import MatchesError, display_limit, match_lines, open_for_reading
from listam.matching import Settings, known_fields, settings
from listam.pages import (Candidate, Funnel, PagesReport, funnel_settings, is_fresh,
                          open_page)
from listam.ports.database import Database
from listam.runner import SessionRefused, publish, working_session
from listam.wiring import build_fetcher

DEFAULT_FIND_MAX_OPENS = 20

# Колонка заявки → флаг, которым человек её задал: ошибка разбора называет оба.
FLAGS = {
    "districts": "--district", "rooms": "--rooms", "budget_max": "--max-price",
    "area_min": "--area", "area_max": "--area", "nice_to_have": "--wish",
}

SPAN = re.compile(r"\s*([^-–—]*?)\s*[-–—]\s*([^-–—]*?)\s*")


class FindError(Exception):
    """Бессмысленный флаг: код 2 и строка человеку, до базы."""


@dataclass
class Query:
    """Флаги команды как есть — строками, как в ячейке таблицы."""

    districts: list[str] = field(default_factory=list)
    rooms: str | None = None
    max_price: str | None = None
    area: str | None = None
    not_first: bool = False
    not_last: bool = False
    wish: str | None = None
    owner: bool = False
    limit: int | None = None
    open: int | None = None


@dataclass
class Found:
    listing: Listing
    score: Score
    cluster: Cluster
    page: ListingPage | None


@dataclass
class WishCount:
    """Одно пожелание по всей выдаче: у скольких поле известно и подходит."""

    word: str
    known: int = 0
    fits: int = 0
    unknown: int = 0


@dataclass
class FindReport:
    request: Request
    tuning: Settings
    owner: bool = False
    limit: int = 0
    ceiling: int = 0                       # funnel.find_max_opens
    rows: list[Found] = field(default_factory=list)
    wishes: list[WishCount] = field(default_factory=list)
    openable: int = 0                      # у скольких найденных страницу можно открыть
    asked_open: int | None = None
    opened: int = 0
    pages: PagesReport = field(default_factory=PagesReport)
    errors: int = 0
    notes: str | None = None
    finished_at: datetime | None = None

    @property
    def hot(self) -> int:
        bar = self.tuning.hot
        return 0 if bar is None else sum(row.score.value >= bar for row in self.rows)

    @property
    def digest(self) -> int:
        low, high = self.tuning.digest, self.tuning.hot
        if low is None:
            return 0
        return sum(row.score.value >= low and (high is None or row.score.value < high)
                   for row in self.rows)

    def _asked(self) -> str:
        request = self.request
        parts = []
        if request.districts:
            parts.append(", ".join(request.districts))
        if request.rooms:
            low, high = min(request.rooms), max(request.rooms)
            parts.append(f"{low} ком." if low == high else f"{low}–{high} ком.")
        if request.budget_max is not None:
            budget = f"до {money(request.budget_max)}"
            ceiling = request.stretch(self.tuning.stretch_percent)
            if ceiling is not None and ceiling > request.budget_max:
                budget += f" (с растяжкой — до {money(ceiling)})"
            parts.append(budget)
        if request.area_min is not None or request.area_max is not None:
            low = f"{request.area_min:g}" if request.area_min is not None else ""
            high = f"{request.area_max:g}" if request.area_max is not None else ""
            parts.append(f"{low}–{high} м²")
        if request.no_first_floor:
            parts.append("не первый этаж")
        if request.no_last_floor:
            parts.append("не последний этаж")
        if request.nice_to_have:
            parts.append(f"пожелания: {request.nice_to_have}")
        if self.owner:
            parts.append("только собственники")
        return " · ".join(parts)

    def render(self) -> str:
        lines = [f"Поиск: {self._asked()}"]
        hot = "выключен" if self.tuning.hot is None else f"от {self.tuning.hot:g}"
        digest = "выключен" if self.tuning.digest is None else f"от {self.tuning.digest:g}"
        lines.append(f"Кластеров: найдено {len(self.rows)}, горячих {self.hot} "
                     f"(балл {hot}), в дайджест {self.digest} ({digest})")
        if self.asked_open is not None:
            pages = self.pages
            lines.append(f"Открыто страниц: {self.opened} (просили {self.asked_open}, "
                         f"потолок {self.ceiling}): разобрано {pages.ok}, снято "
                         f"{pages.gone}, сбоев {pages.failed}")
            if pages.unknown_labels:
                named = ", ".join(f"{label} ×{count}" if count > 1 else label
                                  for label, count in sorted(pages.unknown_labels.items()))
                lines.append(f"⚠ Неразобранных подписей: "
                             f"{sum(pages.unknown_labels.values())} ({named})")
        if not self.rows:
            lines.append("Ничего не нашлось.")
        for row in self.rows[:self.limit]:
            match = Match(listing_id=row.listing.id, score=float(row.score.value),
                          cluster_id=row.cluster.cluster_id, cluster_size=row.cluster.size,
                          cluster_spread_usd=row.cluster.spread_usd)
            lines.extend(match_lines(match, row.listing))
        left = len(self.rows) - len(self.rows[:self.limit])
        if left > 0:
            lines.append(f"  …и ещё {left}")
        for count in self.wishes:
            lines.append(self._wish_line(count))
        if self.notes:
            lines.append(self.notes)
        return "\n".join(lines)

    def _wish_line(self, count: WishCount) -> str:
        line = (f"{count.word}: известно у {count.known} из {len(self.rows)}, "
                f"подходит {count.fits}")
        if not count.unknown:
            return line
        line += f"; неизвестно у {count.unknown}"
        if self.openable:
            return line + f" — --open {min(self.openable, self.ceiling)} откроет лучшие"
        return line + " — страницы не открываются (сняты или исчерпаны попытки)"


def find_ceiling(config: Config) -> int:
    """`funnel.find_max_opens`. `null` — не «выключено», а обход без потолка."""
    value = positive(config, "funnel.find_max_opens", DEFAULT_FIND_MAX_OPENS)
    if value is None:
        raise ConfigError(
            "funnel.find_max_opens = null не годится: find --open без потолка — "
            "массовое открытие страниц, которого быть не может. Нужно число."
        )
    return int(value)


def _area(text: str | None) -> tuple[str, str]:
    """«60-90», «60», «60-», «-90» → две ячейки. Числа проверяет `parse_row`."""
    if text is None or not text.strip():
        return "", ""
    span = SPAN.fullmatch(text)
    if span:
        if not span.group(1) and not span.group(2):
            raise FindError(f"--area {text!r} не годится: нужен диапазон площади, "
                            f"например 60-90, 60- или -90")
        return span.group(1), span.group(2)
    return text.strip(), ""


def build_request(query: Query, vocab: dict[str, Wish]) -> tuple[Request, list[Wish]]:
    """Флаги → строка таблицы → `parse_row` → заявка (решение 16)."""
    area_min, area_max = _area(query.area)
    if not (query.districts or query.rooms or query.max_price or area_min or area_max):
        raise FindError("Искать не по чему: нужно хотя бы одно условие — --district, "
                        "--rooms, --max-price или --area. Вся база — это не поиск.")
    row = {
        "id": "find",
        "districts": ", ".join(query.districts),
        "rooms": query.rooms or "",
        "budget_max": query.max_price or "",
        "area_min": area_min,
        "area_max": area_max,
        "no_first_floor": "да" if query.not_first else "нет",
        "no_last_floor": "да" if query.not_last else "нет",
        "nice_to_have": query.wish or "",
    }
    try:
        request = parse_row(row)
    except RequestParseError as exc:
        flag = FLAGS.get(exc.column, exc.column)
        raise FindError(f"{flag} не годится: {exc.column} = {exc.value!r} — "
                        f"{exc.message}") from exc
    wishes, unknown = parse_wishes(request.nice_to_have, vocab)
    if unknown:
        known = ", ".join(sorted(vocab)) or "словарь пуст"
        raise FindError(f"--wish не годится: nice_to_have = {', '.join(unknown)!r} — "
                        f"таких слов нет в funnel.wishes (известны: {known})")
    return request, wishes


def _check_flags(query: Query, wishes: list[Wish], ceiling: int) -> None:
    if query.limit is not None and query.limit < 1:
        raise FindError(f"--limit {query.limit} не годится: меньше одной строки "
                        f"показывать нечего. Нужно число больше нуля.")
    if query.open is None:
        return
    if query.open < 1:
        raise FindError(f"--open {query.open} не годится: меньше одной страницы "
                        f"открывать нечего.")
    if query.open > ceiling:
        raise FindError(f"--open {query.open} больше потолка funnel.find_max_opens = "
                        f"{ceiling}: потолок — договор с сайтом, флаг его только сужает")
    if not wishes:
        raise FindError("--open без --wish открывать незачем: балл страница меняет "
                        "только через пожелания. Добавь --wish «…».")


def _openable(found: Found, max_attempts: int) -> bool:
    """Страницу стоит открыть: её нет, сбой с попытками в запасе или устарела."""
    page = found.page
    if page is None:
        return True
    if page.status == "gone":
        return False
    if page.status == "failed":
        return page.attempts < max_attempts
    return not is_fresh(page, found.listing)


def search(database: Database, config: Config, request: Request, wishes: list[Wish],
           tuning: Settings, funnel: Funnel, owner: bool) -> list[Found]:
    """Найденные представители кластеров, лучший балл первым. База только читается."""
    everything = database.listings_for_matching()
    representatives = {cluster.cheapest_id: cluster
                       for cluster in clusters(everything, area_tolerance(config))}
    medians = median_price_per_sqm_by_district(everything)
    candidates = [listing for listing in everything
                  if listing.id in representatives
                  and (not owner or listing.seller_type == "owner")]
    pages = database.pages_for([listing.id for listing in candidates]) if wishes else {}
    found: list[Found] = []
    for listing in candidates:
        page = pages.get(listing.id)
        result = score(request, listing, median_by_district=medians,
                       weights=tuning.weights, stretch_percent=tuning.stretch_percent,
                       page=known_fields(page, funnel.max_attempts) if wishes else None,
                       nice=wishes, secondary_district=tuning.secondary_district)
        if result.rejected_by is not None:
            continue
        found.append(Found(listing=listing, score=result,
                           cluster=representatives[listing.id], page=page))
    found.sort(key=lambda item: (-item.score.value, item.listing.price_usd or 0,
                                 item.listing.id))
    return found


def _tell(report: FindReport, found: list[Found], wishes: list[Wish],
          max_attempts: int) -> None:
    report.rows = found
    report.openable = sum(_openable(item, max_attempts) for item in found)
    report.wishes = []
    for wish in wishes:
        count = WishCount(word=wish.word)
        for item in found:
            answer = wish.check(known_fields(item.page, max_attempts))
            if answer is None:
                count.unknown += 1
            else:
                count.known += 1
                count.fits += bool(answer)
        report.wishes.append(count)


def run_find(config: Config, query: Query) -> FindReport:
    """Один поиск. Бессмысленный флаг — `FindError`; сводку печатает вызывающий."""
    # Конфиг и флаги — до базы: бессмысленное значение отклоняется на входе.
    tuning = settings(config)
    funnel = funnel_settings(config)
    ceiling = find_ceiling(config)
    request, wishes = build_request(query, funnel.wishes)
    _check_flags(query, wishes, ceiling)
    limit = query.limit if query.limit is not None else display_limit(config)

    report = FindReport(request=request, tuning=tuning, owner=query.owner, limit=limit,
                        ceiling=ceiling, asked_open=query.open)
    notes: list[str] = []
    try:
        if query.open is None:
            database = open_for_reading(config, "Поиск")
            try:
                found = search(database, config, request, wishes, tuning, funnel,
                               query.owner)
            finally:
                database.close()
            _tell(report, found, wishes, funnel.max_attempts)
            return report

        # `--open` пишет кэш страниц — значит, под замком и с заливкой, как `pages`.
        with working_session(config) as session:
            notes.extend(session.notes)
            session.notes = notes
            database = session.database
            found = search(database, config, request, wishes, tuning, funnel, query.owner)
            chosen = [item for item in found
                      if _openable(item, funnel.max_attempts)][:query.open]
            if not chosen:
                notes.append("открывать нечего: у найденных страницы уже известны "
                             "или не открываются")
            else:
                fetcher = build_fetcher(config, delay_seconds=funnel.delay_seconds)
                try:
                    for item in chosen:
                        page = open_page(fetcher,
                                         Candidate(listing=item.listing,
                                                   coarse=item.score.value),
                                         item.page, None, report.pages)
                        database.save_page(page)
                        report.opened += 1
                finally:
                    fetcher.close()
                if report.pages.ok + report.pages.gone == 0:
                    report.errors += 1
                    notes.append("не открылась ни одна из запрошенных страниц")
                # Перескоринг: открытые страницы меняют балл и строку пожелания.
                found = search(database, config, request, wishes, tuning, funnel,
                               query.owner)
                publish(session, config, "база со страницами")
                report.errors += session.failures
            _tell(report, found, wishes, funnel.max_attempts)
    except (MatchesError, SessionRefused) as exc:
        report.errors += 1
        notes.append(str(exc))
    finally:
        report.notes = "; ".join(note for note in notes if note) or None
        report.finished_at = datetime.now(timezone.utc)
    return report
