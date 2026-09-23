"""Шаг воронки `pages`: страницы объявлений открываются только кандидатам.

Решения 9, 12, 13 спеки M3.5. Кандидат — представитель кластера, который
прошёл грубое сито живой заявки (район, комнаты, бюджет, площадь) с баллом
не ниже `match.thresholds.digest`, **и** у этой заявки есть пожелания из
словаря `funnel.wishes`. Заявке без таких слов страницы не нужны вовсе, и
запросов к сайту ради неё нет.

Никакого массового обхода: открывается не больше `funnel.max_opens_per_run`
страниц за прогон, с паузой `funnel.delay_seconds` между ними, лучшие по
грубому баллу первыми. Открытая страница ложится в `listing_pages` и
повторно открывается, только когда объявление изменилось (решение 13).

Матчей шаг не пишет: страницу читает ближайший `match`, для которого
открывшаяся страница — повод посмотреть на кандидата снова
(`listings_touched_since`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from listam.adapters.fetcher_files import FilesFetcher
from listam.clustering_run import area_tolerance
from listam.config import Config, ConfigError, positive, threshold
from listam.domain.clustering import clusters
from listam.domain.models import Listing, ListingPage
from listam.domain.scoring import rejection, score
from listam.domain.stats import median_price_per_sqm_by_district
from listam.domain.wishes import Wish, request_wishes, vocabulary
from listam.matching import Settings, settings
from listam.parsers.item_page import UNKNOWN, ItemPageMissing, parse_item_page
from listam.ports.database import Database
from listam.ports.fetcher import FetchError
from listam.runner import SessionRefused, publish, working_session
from listam.wiring import build_fetcher

DEFAULT_DELAY_SECONDS = 5.0
DEFAULT_MAX_OPENS = 30
DEFAULT_MAX_ATTEMPTS = 3

# Сайт ответил «такой страницы нет» — объявление снято, открывать больше нечего.
GONE_STATUSES = (404, 410)


@dataclass
class Funnel:
    """Настройки воронки — из конфига, проверенные до замка."""

    delay_seconds: float
    max_opens: int
    max_attempts: int
    wishes: dict[str, Wish]


def funnel_settings(config: Config) -> Funnel:
    """Секция `funnel`. Потолок обязателен: `null` здесь — не «выключено», а
    «открывай сколько угодно», то есть массовый обход, которого быть не может."""
    delay = threshold(config, "funnel.delay_seconds", DEFAULT_DELAY_SECONDS)
    if delay is None or isinstance(delay, bool) or not isinstance(delay, (int, float)) \
            or delay < 0:
        raise ConfigError(
            f"funnel.delay_seconds = {delay!r} не годится: пауза между страницами — "
            f"число секунд не меньше нуля, без кавычек."
        )
    limits = {}
    for key, default in (("max_opens_per_run", DEFAULT_MAX_OPENS),
                         ("max_attempts", DEFAULT_MAX_ATTEMPTS)):
        value = positive(config, f"funnel.{key}", default)
        if value is None:
            raise ConfigError(
                f"funnel.{key} = null не годится: без потолка воронка стала бы "
                f"массовым обходом страниц, которого быть не может. Нужно число."
            )
        limits[key] = int(value)
    return Funnel(delay_seconds=float(delay), max_opens=limits["max_opens_per_run"],
                  max_attempts=limits["max_attempts"], wishes=vocabulary(config))


@dataclass
class Candidate:
    listing: Listing
    coarse: int          # грубый балл: без страницы, фактор `wishes` не считается


@dataclass
class Plan:
    """Кого открывать. Считается без сети — его же показывает `doctor`."""

    requests: int = 0                     # заявок, которым нужны страницы
    candidates: list[Candidate] = field(default_factory=list)
    queue: list[Candidate] = field(default_factory=list)     # по порядку открытия
    from_cache: int = 0                   # страница свежая — открывать незачем
    closed: int = 0                       # снята или исчерпала попытки
    pages: dict[str, ListingPage] = field(default_factory=dict)


@dataclass
class PagesReport:
    """Что сделал шаг. Печатает это CLI, а не сам шаг."""

    requests: int = 0
    candidates: int = 0
    from_cache: int = 0
    closed: int = 0
    queued: int = 0
    ceiling: int = 0
    opened: int = 0             # запрошено у сайта в этом прогоне
    ok: int = 0
    gone: int = 0
    failed: int = 0
    unknown_labels: dict[str, int] = field(default_factory=dict)
    dry_run: bool = False
    would_open: list[str] = field(default_factory=list)
    errors: int = 0
    notes: str | None = None
    finished_at: datetime | None = None

    def render(self) -> str:
        if not self.requests:
            lines = ["Воронка: страницы не нужны — ни у одной живой заявки нет "
                     "пожеланий из словаря funnel.wishes"]
        else:
            lines = [
                f"Воронка: заявок с пожеланиями {self.requests}",
                f"Кандидатов: {self.candidates}, из кэша (страница свежая): "
                f"{self.from_cache}, не открываются (сняты или исчерпаны попытки): "
                f"{self.closed}, в очереди: {self.queued}",
            ]
            if self.dry_run:
                shown = ", ".join(self.would_open) or "—"
                lines.append(f"Пробный прогон: открылись бы {len(self.would_open)} "
                             f"(потолок {self.ceiling}): {shown}")
            else:
                lines.append(
                    f"Открыто: {self.opened} (потолок {self.ceiling}): разобрано "
                    f"{self.ok}, снято {self.gone}, сбоев {self.failed}"
                )
            if self.unknown_labels:
                named = ", ".join(f"{label} ×{count}" if count > 1 else label
                                  for label, count in sorted(self.unknown_labels.items()))
                lines.append(f"⚠ Неразобранных подписей: "
                             f"{sum(self.unknown_labels.values())} ({named})")
        if self.notes:
            lines.append(self.notes)
        return "\n".join(lines)


def _is_fresh(page: ListingPage, listing: Listing) -> bool:
    """Решение 13: открывать снова, только когда объявление изменилось."""
    if page.price_raw != listing.price_raw:
        return False
    if listing.returned_at is not None and page.fetched_at is not None \
            and listing.returned_at > page.fetched_at:
        return False
    return True


def plan_pages(database: Database, config: Config, funnel: Funnel,
               tuning: Settings) -> Plan:
    """Кандидаты и очередь открытия. Сети нет, база только читается."""
    plan = Plan()
    needing = []
    for request in database.iter_requests():
        must, nice = request_wishes(request, funnel.wishes)
        if must or nice:
            needing.append(request)
    plan.requests = len(needing)
    if not needing:
        return plan

    everything = database.listings_for_matching()
    representatives = {cluster.cheapest_id
                       for cluster in clusters(everything, area_tolerance(config))}
    medians = median_price_per_sqm_by_district(everything)

    best: dict[str, Candidate] = {}
    for listing in everything:
        if listing.id not in representatives:
            continue
        for request in needing:
            if rejection(request, listing, tuning.stretch_percent) is not None:
                continue
            coarse = score(request, listing, median_by_district=medians,
                           weights=tuning.weights,
                           stretch_percent=tuning.stretch_percent).value
            if tuning.digest is not None and coarse < tuning.digest:
                continue
            known = best.get(listing.id)
            if known is None or coarse > known.coarse:
                best[listing.id] = Candidate(listing=listing, coarse=coarse)

    plan.candidates = list(best.values())
    plan.pages = database.pages_for(best)
    for candidate in plan.candidates:
        page = plan.pages.get(candidate.listing.id)
        if page is None:
            plan.queue.append(candidate)
        elif page.status == "gone":
            plan.closed += 1
        elif page.status == "failed":
            if page.attempts >= funnel.max_attempts:
                plan.closed += 1
            else:
                plan.queue.append(candidate)
        elif _is_fresh(page, candidate.listing):
            plan.from_cache += 1
        else:
            plan.queue.append(candidate)

    # Лучшие по грубому баллу первыми; при равенстве — новее.
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    plan.queue.sort(key=lambda item: (item.coarse, item.listing.first_seen or oldest,
                                      item.listing.id), reverse=True)
    return plan


def _open(fetcher, candidate: Candidate, previous: ListingPage | None,
          keep_html: Path | None, report: PagesReport) -> ListingPage:
    """Одна страница: открыть, разобрать, сказать, что вышло."""
    listing = candidate.listing
    # Сбой копит попытки; удачная, но устаревшая страница начинает счёт заново.
    tried = previous.attempts if previous and previous.status == "failed" else 0
    page = ListingPage(listing_id=listing.id, status="failed", attempts=tried + 1,
                       price_raw=listing.price_raw)
    try:
        html = fetcher.get(listing.url)
    except FetchError as exc:
        if exc.status in GONE_STATUSES:
            page.status = "gone"
            report.gone += 1
        else:
            page.error = str(exc)
            report.failed += 1
        return page
    if keep_html is not None:
        keep_html.mkdir(parents=True, exist_ok=True)
        (keep_html / FilesFetcher.filename_for(listing.url)).write_text(
            html, encoding="utf-8")
    try:
        fields = parse_item_page(html)
    except ItemPageMissing as exc:
        page.error = str(exc)
        report.failed += 1
        return page
    for label in fields.values.get(UNKNOWN, []):
        report.unknown_labels[label] = report.unknown_labels.get(label, 0) + 1
    page.status = "ok"
    page.fields = fields
    page.fetched_at = datetime.now(timezone.utc)
    report.ok += 1
    return page


def run_pages(config: Config, *, max_opens: int | None = None, dry_run: bool = False,
              keep_html: bool = False) -> PagesReport:
    """Один проход воронки. Сводку печатает вызывающий."""
    # Конфиг — до замка: бессмысленное значение отклоняется на входе.
    funnel = funnel_settings(config)
    tuning = settings(config)
    ceiling = funnel.max_opens
    if max_opens is not None:
        if max_opens < 1:
            raise ConfigError(f"--max {max_opens} не годится: меньше одной страницы "
                              f"открывать нечего")
        if max_opens > ceiling:
            raise ConfigError(
                f"--max {max_opens} больше потолка funnel.max_opens_per_run = "
                f"{ceiling}: потолок — договор с сайтом, флаг его только сужает"
            )
        ceiling = max_opens

    report = PagesReport(ceiling=ceiling, dry_run=dry_run)
    notes: list[str] = []
    try:
        with working_session(config) as session:
            notes.extend(session.notes)
            session.notes = notes
            database = session.database

            plan = plan_pages(database, config, funnel, tuning)
            report.requests = plan.requests
            report.candidates = len(plan.candidates)
            report.from_cache = plan.from_cache
            report.closed = plan.closed
            report.queued = len(plan.queue)
            chosen = plan.queue[:ceiling]
            if dry_run:
                report.would_open = [item.listing.id for item in chosen]
                return report
            if not chosen:
                return report

            keep = Path(config.get("scrape.pages_dir", "./data/pages")) if keep_html else None
            fetcher = build_fetcher(config, delay_seconds=funnel.delay_seconds)
            try:
                for candidate in chosen:
                    page = _open(fetcher, candidate, plan.pages.get(candidate.listing.id),
                                 keep, report)
                    database.save_page(page)
                    report.opened += 1
            finally:
                fetcher.close()

            if report.ok + report.gone == 0:
                # Спека: код 1, если не открылась ни одна из запрошенных.
                report.errors += 1
                notes.append("не открылась ни одна из запрошенных страниц — "
                             "повтор в следующем прогоне")
            publish(session, config, "база со страницами")
            report.errors += session.failures
    except SessionRefused as exc:
        report.errors += 1
        notes.append(str(exc))
    finally:
        report.notes = "; ".join(note for note in notes if note) or None
        report.finished_at = datetime.now(timezone.utc)
    return report
