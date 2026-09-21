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
from pathlib import Path

from listam.adapters.db_sqlite import from_iso
from listam.adapters.filenames import drop_wal_sidecars
from listam.adapters.run_lock import LockBusy
from listam.config import Config
from listam.domain.coverage import Coverage, rules_from as coverage_rules_from
from listam.domain.models import Listing, Run
from listam.domain.money import Money
from listam.domain.validate import detect_anomalies, rules_from
from listam.parsers.category_list import DEFAULT_BASE_URL, parse_listing_cards, parse_next_page
from listam.ports.fetcher import FetchError
from listam.ports.rate import Rate, RateError
from listam.wiring import (
    build_database,
    build_fetcher,
    build_rate_provider,
    build_run_lock,
    build_storage,
    database_path,
)


DEFAULT_MIN_CARDS = 20        # здоровая страница ленты отдаёт под сотню карточек
DEFAULT_KEEP_BACKUPS = 5      # сколько прежних копий базы держим в хранилище
DEFAULT_MAX_SHRINK = 10       # на сколько процентов база может усохнуть без разрешения
DEFAULT_HARD_PAGE_LIMIT = 1000  # потолок обхода: категория 60 — это 215 страниц


def page_path(category: int | str, page: int) -> str:
    """Пагинация только путевая: /category/60, /category/60/2.

    Query-параметры (`n=`, `price1=`, `srt=`, …) запрещены в robots.txt сайта.
    """
    return f"/category/{category}" if page <= 1 else f"/category/{category}/{page}"


def apply_rate(listing: Listing, rate_amd_per_usd: float | None) -> Listing:
    """Достраивает вторую валюту и цену за метр. Парсер этого не умеет: он не знает курса.

    Посчитать не вышло — оставляем как было. Пустой пересчёт означает «не смог»,
    а не «цены больше нет»: затирать им уже посчитанное нельзя.
    """
    amount = listing.price_usd if listing.currency == "USD" else listing.price_amd
    money = Money(raw=listing.price_raw, amount=amount, currency=listing.currency)
    listing.price_usd = money.to_usd(rate_amd_per_usd) or listing.price_usd
    listing.price_amd = money.to_amd(rate_amd_per_usd) or listing.price_amd
    if listing.price_usd is not None and listing.area:
        listing.price_per_sqm = round(listing.price_usd / listing.area, 2)
    return listing



def _latest_run_at(path) -> datetime | None:
    """Когда по этой копии базы последний раз ходил прогон. Нечитаемая копия — None."""
    import sqlite3

    if not Path(path).exists():
        return None
    try:
        connection = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        try:
            row = connection.execute("SELECT MAX(started_at) AS at FROM runs").fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    return from_iso(row[0]) if row and row[0] else None


def _listings_in(path) -> int | None:
    import sqlite3

    if not Path(path).exists():
        return None
    try:
        connection = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        try:
            row = connection.execute("SELECT COUNT(*) FROM listings").fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    return int(row[0]) if row else None


@dataclass
class _Remote:
    """Что мы знаем об удалённой копии базы к началу прогона."""

    note: str
    listings: int | None = None


def take_the_fresher_copy(storage, remote_name: str, local_db: Path) -> _Remote:
    """Сверяет локальную копию с удалённой и оставляет более свежую.

    Без этого вторая машина или откат папки data/ молча затирает общую базу
    старым снимком: заливка-то происходит всегда, а скачивание — только когда
    локального файла нет.
    """
    if not local_db.exists():
        if not storage.download(remote_name, local_db):
            return _Remote(note="в хранилище копии нет — база заводится заново")
        return _Remote(
            note="локальной копии не было — взята из хранилища",
            listings=_listings_in(local_db),
        )

    candidate = local_db.with_name(local_db.name + ".remote")
    try:
        if not storage.download(remote_name, candidate):
            return _Remote(note="в хранилище копии нет — идём с локальной")
        there, here = _latest_run_at(candidate), _latest_run_at(local_db)
        listings = _listings_in(candidate)
        if there is not None and (here is None or there > here):
            candidate.replace(local_db)
            drop_wal_sidecars(local_db)   # спутники относились к прежнему файлу
            return _Remote(
                note=f"удалённая копия свежее ({there:%Y-%m-%d %H:%M} UTC) — взята она",
                listings=listings,
            )
        return _Remote(note="локальная копия не старее удалённой", listings=listings)
    finally:
        candidate.unlink(missing_ok=True)


def shrink_refusal(
    before: int | None, after: int, max_shrink: float, allowed: bool
) -> str | None:
    """Заливка, срезающая базу больше порога, не выполняется без явного разрешения."""
    if allowed or not before or max_shrink <= 0:
        return None
    if after >= before * (1 - max_shrink):
        return None
    return (
        f"заливка отменена: база сжалась с {before} объявлений до {after}, "
        f"это больше порога storage.max_shrink_percent = {max_shrink * 100:.0f}%. "
        f"Если так и задумано — scrape --allow-shrink"
    )


DEFAULT_MAX_PAGES_DROP = 20   # на столько процентов обход может быть короче прошлого


def pages_shortfall(
    pages_fetched: int,
    expected_min: int | None,
    previous_pages: int | None,
    drop_percent: float,
) -> str | None:
    """Недобор страниц: обход кончился заметно раньше, чем должен был.

    Лента категории — это сотни страниц. Обход, вставший на второй из-за
    пропавшего пагинатора, записывает проценты рынка, и без этой проверки
    такой прогон выглядит удачным. Сверяемся с явным порогом из конфига и с
    прошлым удачным прогоном: сколько страниц он прошёл — столько их и есть.
    """
    if expected_min and pages_fetched < int(expected_min):
        return (
            f"обход оборвался: пройдено страниц {pages_fetched}, это меньше порога "
            f"scrape.expected_pages_min = {int(expected_min)}"
        )
    if previous_pages and drop_percent > 0:
        floor = int(previous_pages) * (1 - float(drop_percent) / 100)
        if pages_fetched < floor:
            return (
                f"обход оборвался: пройдено страниц {pages_fetched}, а прошлый удачный "
                f"прогон прошёл {int(previous_pages)} — падение больше порога "
                f"scrape.max_pages_drop_percent = {float(drop_percent):.0f}%"
            )
    return None


def upload_refusal(
    *, errors: int, shrink: str | None, allowed_errors: bool
) -> str | None:
    """Причина, по которой эта копия не уезжает в хранилище. None — заливаем.

    Заливка подменяет общую копию. Прогон, в котором были ошибки, записал в базу
    неполную картину: уехала вёрстка, оборвался обход, не открылась страница.
    Залить такую — значит испортить ту, что лежала в хранилище и была в порядке.
    Локальная копия при этом остаётся на диске: её можно посмотреть глазами и,
    если всё в порядке, залить следующим прогоном с флагом.
    """
    if shrink:
        return shrink
    if errors > 0 and not allowed_errors:
        return (
            f"база не залита: ошибок в прогоне — {errors}, в хранилище осталась "
            f"прежняя копия. Локальная копия на диске — посмотри её и, если всё "
            f"в порядке, scrape --allow-upload-with-errors"
        )
    return None


def rotate_backups(storage, remote_name: str, keep: int, work_dir: Path) -> None:
    """Кладёт прежнюю копию рядом под именем с отметкой времени и подчищает старые."""
    if keep <= 0 or not storage.exists(remote_name):
        return
    stem, suffix = Path(remote_name).stem, Path(remote_name).suffix
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    work_dir.mkdir(parents=True, exist_ok=True)
    spare = work_dir / f".{stem}-backup{suffix}"
    try:
        if storage.download(remote_name, spare):
            storage.upload(spare, f"{stem}-{stamp}{suffix}")
    finally:
        spare.unlink(missing_ok=True)
    kept = storage.names(prefix=f"{stem}-")
    for name in kept[:-keep]:
        storage.delete(name)


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


def run_scrape(
    config: Config,
    *,
    max_pages: int | None = None,
    dry_run: bool = False,
    allow_shrink: bool = False,
    resume: bool = False,
    allow_upload_with_errors: bool = False,
) -> Run:
    """Один проход по ленте категории. Возвращает журнал прогона.

    `dry_run` разбирает страницы и считает, но не пишет ни строки: ни объявлений,
    ни журнала, ни базы в хранилище.

    `resume` продолжает прерванный обход с последней пройденной страницы:
    215 страниц с паузой между запросами — это часы, начинать их заново из-за
    оборванной связи незачем.
    """
    limit = max_pages if max_pages is not None else config.get("scrape.max_pages")
    limit = int(limit) if limit else None
    category = config.get("scrape.category", 60)
    base_url = config.get("scrape.base_url") or DEFAULT_BASE_URL

    min_cards = int(config.get("scrape.min_cards_per_page", DEFAULT_MIN_CARDS) or 0)
    hard_limit = int(config.get("scrape.hard_page_limit", DEFAULT_HARD_PAGE_LIMIT) or 0)
    rules = rules_from(config)
    coverage = Coverage(coverage_rules_from(config))

    counters = _Counters()
    started_at = datetime.now(timezone.utc)

    # Курс — предусловие прогона, а не его счётчик: без него все пересчитанные
    # цены вышли бы пустыми и затёрли бы посчитанное прошлым прогоном.
    # Поэтому его берём до того, как тронули базу, и без него не идём дальше.
    fetcher = build_fetcher(config)
    rate, note = _fetch_rate(build_rate_provider(config, fetcher))
    if rate is None:
        fetcher.close()
        counters.errors += 1
        return Run(
            id=None,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            rate_amd_per_usd=None,
            errors=counters.errors,
            notes=f"{note}; прогон не начат: без курса база не трогается",
        )
    rate_value = rate.value

    # Замок: одновременно по ленте ходит ровно один процесс. Два прогона разом —
    # это гонка, в которой побеждает тот, кто позже залил файл в хранилище.
    lock = None
    if not dry_run:
        lock = build_run_lock(config)
        try:
            lock.acquire()
        except LockBusy as exc:
            fetcher.close()
            counters.errors += 1
            return Run(
                id=None,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                rate_amd_per_usd=rate_value,
                errors=counters.errors,
                notes=f"{note}; {exc}",
            )

    # Пробный прогон не трогает диск вообще: ни скачивания базы, ни файла,
    # ни миграций — иначе «ничего не записано» было бы неправдой.
    storage = None
    local_db = database_path(config)
    remote_name = config.get("storage.db_filename", "listam.sqlite")
    database = None
    remote = _Remote(note="")
    if not dry_run:
        storage = build_storage(config)
        remote = take_the_fresher_copy(storage, remote_name, local_db)
        note = f"{note}; {remote.note}" if remote.note else note
        database = build_database(config)
        database.connect()
        database.migrate()

    # Прошлый удачный прогон — мерка полноты обхода: столько страниц в ленте и есть.
    previous_success = database.last_successful_run() if database is not None else None

    start_page = 1
    if resume and database is not None:
        previous = database.last_run()
        start_page = max(1, previous.last_page if previous else 1)
        if start_page > 1:
            note = f"{note}; обход продолжен со страницы {start_page}"

    run_id = None if dry_run else database.start_run(started_at, rate_value)
    seen_ids: set[str] = set()
    try:
        # Падение посреди обхода — это ошибка прогона, а не «ничего не было».
        # Без этого журнал оставался с errors = 0, а испорченная половинная база
        # уезжала в хранилище как удачный прогон.
        try:
            page = start_page
            while True:
                try:
                    html = fetcher.get(page_path(category, page))
                except FetchError as exc:
                    counters.errors += 1
                    note = f"{note}; страница {page} не получена: {exc}"
                    break

                cards = parse_listing_cards(html, base_url=base_url)
                if len(cards) < min_cards:
                    counters.errors += 1
                    note = (
                        f"{note}; страница {page}: карточек {len(cards)}, "
                        f"это меньше порога scrape.min_cards_per_page = {min_cards}. "
                        f"Так выглядит смена вёрстки или заглушка Cloudflare с кодом 200"
                    )
                    break
                counters.pages_fetched += 1   # страница засчитана: она разобралась

                for listing in cards:
                    counters.listings_seen += 1
                    if listing.id in seen_ids:
                        continue          # одно и то же объявление бывает на двух страницах подряд
                    seen_ids.add(listing.id)
                    coverage.add(listing)
                    apply_rate(listing, rate_value)
                    listing.anomaly = detect_anomalies(listing, rules)
                    if dry_run:
                        continue
                    outcome = database.upsert_listing(
                        listing,
                        seen_at=datetime.now(timezone.utc),
                        rate_amd_per_usd=rate_value,
                    )
                    if outcome == "new":
                        counters.new_listings += 1
                    elif outcome in ("updated", "price_changed"):
                        counters.updated_listings += 1

                if run_id is not None:
                    database.mark_page(run_id, page)    # убитый прогон продолжится отсюда

                if limit is not None and counters.pages_fetched >= limit:
                    break
                if hard_limit and counters.pages_fetched >= hard_limit:
                    counters.errors += 1
                    note = (
                        f"{note}; обход упёрся в потолок scrape.hard_page_limit = "
                        f"{hard_limit}: похоже, пагинатор зациклился"
                    )
                    break
                following = parse_next_page(html)
                if following is None or following <= page:
                    break
                page = following

            # Укороченный обход сверяем с полнотой только тогда, когда его никто
            # не укорачивал нарочно: с --max-pages и --resume это норма, а не сбой.
            if limit is None and not resume:
                shortfall = pages_shortfall(
                    counters.pages_fetched,
                    config.get("scrape.expected_pages_min"),
                    previous_success.pages_fetched if previous_success else None,
                    float(config.get("scrape.max_pages_drop_percent", DEFAULT_MAX_PAGES_DROP) or 0),
                )
                if shortfall:
                    counters.errors += 1
                    note = f"{note}; {shortfall}"

            for failure in coverage.failures():
                counters.errors += 1
                note = f"{note}; {failure}"

            # Продолженный обход законно кончается на первой же своей странице —
            # это конец ленты, а не сломанный пагинатор.
            alone = counters.pages_fetched == 1 and not (resume and start_page > 1)
            if counters.errors == 0 and alone and limit != 1:
                counters.errors += 1
                note = (
                    f"{note}; пагинатор не дал следующей страницы: обход кончился "
                    f"на первой, хотя scrape.max_pages = {limit}"
                )
        except BaseException as exc:
            counters.errors += 1
            if isinstance(exc, KeyboardInterrupt):
                note = f"{note}; прогон прерван человеком"
            else:
                note = f"{note}; прогон упал: {type(exc).__name__}: {exc}"
            raise
    finally:
        finished_at = datetime.now(timezone.utc)

        def journal() -> None:
            """Записывает журнал прогона тем, что известно к этой минуте.

            Зовётся не один раз: отказ от заливки и несделанный снимок — это тоже
            ошибки прогона, и узнаём мы о них уже после того, как строка записана.
            """
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

        refusal = None
        snapshot = None
        if storage is not None:
            shrink = shrink_refusal(
                remote.listings,
                database.count_listings(),
                float(config.get("storage.max_shrink_percent", DEFAULT_MAX_SHRINK)) / 100,
                allow_shrink,
            )
            if shrink:
                counters.errors += 1
                note = f"{note}; {shrink}"
            journal()
            # Снимок — после журнала и на ещё открытой базе: в хранилище уезжает
            # копия, в которой этот прогон уже записан.
            snapshot = local_db.with_name(local_db.name + ".snapshot")
            try:
                database.snapshot(snapshot)
            except Exception as exc:      # sqlite3.Error, OSError — заливать нечего
                counters.errors += 1
                note = f"{note}; снимок базы не сделан: {exc}"
                snapshot = None
            refusal = upload_refusal(
                errors=counters.errors,
                shrink=shrink,
                allowed_errors=allow_upload_with_errors,
            )
            if refusal and refusal is not shrink:
                note = f"{note}; {refusal}"
        journal()
        if database is not None:
            database.close()
        fetcher.close()
        if storage is not None and refusal is None and snapshot is not None:
            rotate_backups(
                storage,
                remote_name,
                int(config.get("storage.keep_backups", DEFAULT_KEEP_BACKUPS) or 0),
                local_db.parent,
            )
            storage.upload(snapshot, remote_name)
        if snapshot is not None:
            snapshot.unlink(missing_ok=True)
        if lock is not None:
            lock.release()

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
