"""Прогон по ленте: страницы → объявления → база.

Один проход устроен так: сначала фиксируется курс (он уходит в журнал, чтобы
старые цифры воспроизводились), потом страницы идут одна за другой по путевой
пагинации, и каждая карточка кладётся в базу апсертом.

Откуда берутся страницы, куда ложится база и какой курс — решает конфиг.
Здесь про это ничего не известно: только порты.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from listam.config import Config
from listam.domain.models import Listing, Run
from listam.domain.money import Money
from listam.parsers.category_list import DEFAULT_BASE_URL, parse_listing_cards, parse_next_page
from listam.ports.fetcher import FetchError
from listam.ports.rate import Rate, RateError
from listam.wiring import build_fetcher, build_rate_provider, build_storage, database_path


def page_path(category: int | str, page: int) -> str:
    """Пагинация только путевая: /category/60, /category/60/2.

    Query-параметры (`n=`, `price1=`, `srt=`, …) запрещены в robots.txt сайта.
    """
    return f"/category/{category}" if page <= 1 else f"/category/{category}/{page}"


def apply_rate(listing: Listing, rate_amd_per_usd: float | None) -> Listing:
    """Достраивает вторую валюту и цену за метр. Парсер этого не умеет: он не знает курса."""
    amount = listing.price_usd if listing.currency == "USD" else listing.price_amd
    money = Money(raw=listing.price_raw, amount=amount, currency=listing.currency)
    listing.price_usd = money.to_usd(rate_amd_per_usd)
    listing.price_amd = money.to_amd(rate_amd_per_usd)
    if listing.price_usd is not None and listing.area:
        listing.price_per_sqm = round(listing.price_usd / listing.area, 2)
    return listing


@dataclass
class _Counters:
    pages_fetched: int = 0
    listings_seen: int = 0
    new_listings: int = 0
    updated_listings: int = 0
    errors: int = 0


def _fetch_rate(provider) -> tuple[Rate | None, str]:
    try:
        rate = provider.amd_per_usd()
    except RateError as exc:
        return None, f"курс не получен: {exc}"
    banks = f", банков: {rate.banks_counted}" if rate.banks_counted else ""
    return rate, f"курс {rate.value} ({rate.source}{banks})"


def run_scrape(config: Config, *, max_pages: int | None = None, dry_run: bool = False) -> Run:
    """Один проход по ленте категории. Возвращает журнал прогона.

    `dry_run` разбирает страницы и считает, но не пишет ни строки: ни объявлений,
    ни журнала, ни базы в хранилище.
    """
    from listam.adapters.db_sqlite import SqliteDatabase

    limit = max_pages if max_pages is not None else config.get("scrape.max_pages")
    limit = int(limit) if limit else None
    category = config.get("scrape.category", 60)
    base_url = config.get("scrape.base_url") or DEFAULT_BASE_URL

    storage = build_storage(config)
    local_db = database_path(config)
    remote_name = config.get("storage.db_filename", "listam.sqlite")
    if not local_db.exists():
        storage.download(remote_name, local_db)

    fetcher = build_fetcher(config)
    database = SqliteDatabase(local_db)
    database.connect()
    database.migrate()

    counters = _Counters()
    started_at = datetime.now(timezone.utc)
    rate, note = _fetch_rate(build_rate_provider(config, fetcher))
    if rate is None:
        counters.errors += 1
    rate_value = rate.value if rate else None

    run_id = None if dry_run else database.start_run(started_at, rate_value)
    seen_ids: set[str] = set()
    try:
        page = 1
        while True:
            try:
                html = fetcher.get(page_path(category, page))
            except FetchError as exc:
                counters.errors += 1
                note = f"{note}; страница {page} не получена: {exc}"
                break
            counters.pages_fetched += 1

            for listing in parse_listing_cards(html, base_url=base_url):
                counters.listings_seen += 1
                if listing.id in seen_ids:
                    continue          # одно и то же объявление бывает на двух страницах подряд
                seen_ids.add(listing.id)
                apply_rate(listing, rate_value)
                if dry_run:
                    continue
                outcome = database.upsert_listing(listing, seen_at=datetime.now(timezone.utc))
                if outcome == "new":
                    counters.new_listings += 1
                elif outcome in ("updated", "price_changed"):
                    counters.updated_listings += 1

            if limit is not None and counters.pages_fetched >= limit:
                break
            following = parse_next_page(html)
            if following is None or following <= page:
                break
            page = following
    finally:
        finished_at = datetime.now(timezone.utc)
        if run_id is not None:
            database.finish_run(
                run_id,
                finished_at,
                pages_fetched=counters.pages_fetched,
                listings_seen=counters.listings_seen,
                new_listings=counters.new_listings,
                updated_listings=counters.updated_listings,
                errors=counters.errors,
                notes=note,
            )
        database.close()
        fetcher.close()
        if not dry_run:
            storage.upload(local_db, remote_name)

    return Run(
        id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        rate_amd_per_usd=rate_value,
        pages_fetched=counters.pages_fetched,
        listings_seen=counters.listings_seen,
        new_listings=counters.new_listings,
        updated_listings=counters.updated_listings,
        errors=counters.errors,
        notes=note,
    )
