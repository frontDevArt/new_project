"""Подбор объявлений под заявки: три выборки, один проход.

Оркестрация по образцу `listam/changes.py` и `listam/clustering_run.py`:
решают домены (`clustering`, `stats`, `scoring`), здесь — кто с кем
сравнивается и что из этого записывается.

Три вещи, которые здесь важнее скорости:

- **Кластеры считаются по всей базе, а не по выборке.** Иначе `--new` не
  узнает, что у свежего объявления уже есть тридцать двойников, и клиент
  получит тридцать первый звонок про ту же квартиру.
- **Отказы не пишутся.** Пара «заявка × объявление» в боевой базе даёт
  миллион строк, и девятьсот девяносто тысяч из них — «район не тот».
  В базе нужны те, по которым можно звонить.
- **Пересчёт не трогает след звонка.** `status` и `reject_reason` пишет
  только человек (решение 7); `upsert_match` их не перечисляет вовсе.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from listam.adapters.db_sqlite import latest_schema_version
from listam.adapters.run_lock import LockBusy
from listam.changes import DASH, MINUS, money, per_sqm, since_point
from listam.clustering_run import area_tolerance, cluster_database
from listam.config import Config, threshold
from listam.crawler import rotate_backups, take_the_fresher_copy
from listam.domain.clustering import clusters
from listam.domain.models import Listing, Match, Request
from listam.domain.scoring import DEFAULT_STRETCH_PERCENT, DEFAULT_WEIGHTS, score
from listam.domain.stats import median_price_per_sqm_by_district
from listam.ports.database import Database
from listam.wiring import build_database, build_run_lock, build_storage, database_path

DEFAULT_KEEP_BACKUPS = 5
DEFAULT_HOT = 70.0
DEFAULT_DIGEST = 40.0


@dataclass
class MatchReport:
    """Что сделал подбор. Печатает это CLI, а не сам подбор."""

    scope: str = ""             # «заявка R-1», «новые объявления…», «вся база»
    requests: int = 0
    listings: int = 0           # объявлений в выборке
    considered: int = 0         # из них представителей кластеров
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    retired: int = 0            # матчей закрыто: проход их больше не подтверждает
    hot: int = 0                # score >= match.thresholds.hot
    digest: int = 0             # hot > score >= digest
    errors: int = 0
    notes: str | None = None
    finished_at: datetime | None = None

    def render(self) -> str:
        lines = [
            f"Подбор: {self.scope}",
            f"Заявок: {self.requests}, объявлений в выборке: {self.listings}, "
            f"из них представителей кластеров: {self.considered}",
            f"Матчи: новых {self.new}, обновлённых {self.updated}, "
            f"без изменений {self.unchanged}",
        ]
        if self.retired:
            lines.append(f"Закрыто матчей: {self.retired} — вариант больше не подходит")
        lines.append(f"Из них горячих: {self.hot}, в дайджест: {self.digest}")
        if self.notes:
            lines.append(self.notes)
        return "\n".join(lines)


@dataclass
class Settings:
    """Веса и пороги подбора — из конфига, а не из кода."""

    weights: dict[str, float]
    stretch_percent: float
    hot: float | None
    digest: float | None


def settings(config: Config) -> Settings:
    """Читает секцию `match`. Ноль значит ноль, `null` — выключено."""
    weights = config.get("match.weights", None)
    stretch = threshold(config, "match.budget_stretch_percent", DEFAULT_STRETCH_PERCENT)
    hot = threshold(config, "match.thresholds.hot", DEFAULT_HOT)
    digest = threshold(config, "match.thresholds.digest", DEFAULT_DIGEST)
    return Settings(
        weights=dict(weights) if weights else dict(DEFAULT_WEIGHTS),
        # Растяжка выключена — значит не растягиваем вовсе, а не «берём
        # десять процентов по умолчанию»: человек сказал «нет», а не промолчал.
        stretch_percent=0.0 if stretch is None else float(stretch),
        hot=None if hot is None else float(hot),
        digest=None if digest is None else float(digest),
    )


def _requests_to_match(database: Database, external_id: str | None
                       ) -> tuple[list[Request], str | None]:
    """Заявки выборки и причина, если выборки нет.

    Заявка, названная руками, обязана быть активной: «подобрал ноль» на
    приостановленной заявке — это не ответ, а молчание.
    """
    if external_id is None:
        return list(database.iter_requests()), None

    request = database.get_request(external_id)
    if request is None:
        return [], f"заявки {external_id} в базе нет — сначала прочитай источник: " \
                   f"python -m listam requests"
    if request.status != "active":
        return [], f"заявка {external_id} со статусом {request.status}: " \
                   f"матчатся только active"
    return [request], None


def _listings_scope(database: Database, only_new: bool, config: Config
                    ) -> tuple[list, str]:
    """Выборка объявлений и как она называется по-человечески."""
    if not only_new:
        return database.listings_for_matching(), "вся база"
    # Мерка та же, что у `changes`: начало последнего прогона. Но берём не
    # «появившееся с неё», а «тронувшееся с неё»: подешевевшая квартира новой
    # не стала, а звонить по ней надо сегодня.
    mark, note = since_point(
        database, None, fallback_hours=config.get("changes.fallback_hours", 24)
    )
    return (database.listings_touched_since(mark),
            f"новое и подешевевшее: {note}")


def _is_edited(request: Request) -> bool:
    """Заявку тронули после её последнего подбора?

    Ни разу не подбиравшаяся заявка — тоже «правленая»: по выборке последнего
    прогона она увидит три вчерашних объявления вместо всей базы.
    """
    if request.matched_at is None:
        return True
    return (request.updated_at or request.created_at or request.matched_at) \
        > request.matched_at


def run_match(config: Config, *, external_id: str | None = None,
              only_new: bool = False, recount_all: bool = False) -> MatchReport:
    """Один проход подбора. Сводку печатает вызывающий.

    `recount_all` выборку не меняет и не должен: «все активные заявки по
    всей базе» — это и есть подбор без сужений. Флаг существует, чтобы
    полный пересчёт нельзя было запустить молчанием: команда без флагов
    отклоняется кодом 2, а не истолковывается как «пересчитай всё».
    """
    report = MatchReport(scope="заявка " + external_id if external_id else "вся база")

    # Тот же замок, что у прогона и пересчёта: подбор переписывает общую базу
    # и может по дороге проставить кластеры.
    lock = build_run_lock(config)
    try:
        lock.acquire()
    except LockBusy as exc:
        report.errors = 1
        report.notes = str(exc)
        report.finished_at = datetime.now(timezone.utc)
        return report

    database = None
    storage = None
    local_db = database_path(config)
    remote_name = config.get("storage.db_filename", "listam.sqlite")
    notes: list[str] = []
    try:
        try:
            storage = build_storage(config)
            remote = take_the_fresher_copy(storage, remote_name, local_db)
            if remote.note:
                notes.append(remote.note)
            database = build_database(config)
            database.connect()
            database.migrate()
        except Exception as exc:       # OSError, sqlite3.Error — базы нет
            report.errors = 1
            notes.append(f"файл базы недоступен: {exc}")
            return report

        required = latest_schema_version()
        version = database.schema_version()
        if version < required:
            report.errors = 1
            notes.append(
                f"Подбор не сделан: схема базы {version}, а код ждёт {required}. "
                f"Команда ничего не мигрирует — накати миграции: "
                f"python -m listam recheck"
            )
            return report

        requests, refusal = _requests_to_match(database, external_id)
        if refusal is not None:
            report.errors = 1
            notes.append(refusal)
            return report
        report.requests = len(requests)
        if not requests:
            # Не ошибка: заявок может не быть ещё или уже. Но и не тишина —
            # пустой подбор обязан сказать, почему он пустой.
            notes.append("подбирать не под что: нет активных заявок")
            return report

        _count_clusters(database, config, notes)

        # Кластеры считаются по всей базе, а не по выборке: у свежего
        # объявления двойники могли появиться задолго до него.
        everything = database.listings_for_matching()
        found = clusters(everything, area_tolerance(config))
        representatives = {cluster.cheapest_id: cluster for cluster in found}
        medians = median_price_per_sqm_by_district(everything)

        selection, scope = _listings_scope(database, only_new, config)
        report.scope = f"заявка {external_id}" if external_id else scope
        report.listings = len(selection)
        candidates = [item for item in selection if item.id in representatives]
        report.considered = len(candidates)

        # Чего проход не видел: снятое с ленты и отложенное аномалией.
        # Закрывать по такому нельзя — см. `_write_matches`.
        off_the_feed = database.known_ids() - {item.id for item in everything}

        last_run = database.last_run()
        run_id = last_run.id if last_run else None
        tuning = settings(config)

        # Заявка, которую тронули после её последнего подбора, выборкой
        # объявлений не покрывается: изменился не рынок, а условия. Такую
        # ведём по всей базе — иначе поднятый бюджет заработает только ночью.
        edited = [request for request in requests
                  if only_new and _is_edited(request)]
        fresh = [request for request in requests if request not in edited]
        if edited:
            notes.append(f"правленых заявок: {len(edited)} — по всей базе")
            whole = [item for item in everything if item.id in representatives]
            _write_matches(database, report, edited, whole, representatives,
                           medians, run_id, tuning,
                           full_sweep=True, off_the_feed=off_the_feed)
        if fresh:
            _write_matches(database, report, fresh, candidates, representatives,
                           medians, run_id, tuning,
                           full_sweep=not only_new, off_the_feed=off_the_feed)
        database.mark_requests_matched(
            [request.id for request in requests if request.id is not None],
            datetime.now(timezone.utc),
        )

        if report.new or report.updated or report.retired:
            if _upload(config, database, storage, local_db, remote_name,
                       report, notes):
                database = None    # заливка закрыла базу: снимок делается с живой
        return report
    finally:
        if database is not None:
            database.close()
        lock.release()
        report.notes = "; ".join(note for note in notes if note) or None
        report.finished_at = datetime.now(timezone.utc)


def _count_clusters(database: Database, config: Config,
                    notes: list[str]) -> None:
    """Кластеры перед подбором — каждый раз, а не когда в базе есть пустые.

    Спека: пересчёт идёт автоматически перед матчингом. Команду `cluster`
    никто не обязан помнить, а без кластеров клиент получает одну квартиру
    тридцать раз.

    Признак «есть объявление без cluster_id» ловит только появление. Уход
    с ленты состав кластера тоже меняет, колонок не трогая: кластер из трёх
    становится кластером из двух и получает другое имя, а `listings.cluster_id`
    остаётся вчерашним — и база начинает спорить со снимком в матче, который
    считается заново каждым подбором.

    Замка здесь второго нет: `cluster_database` работает по уже открытой
    базе — ровно для этого он и отделён от команды.
    """
    counted = cluster_database(database, area_tolerance(config))
    notes.append(
        f"кластеры пересчитаны: объявлений {counted.listings}, "
        f"кластеров {counted.clusters}, изменено строк {counted.changed}"
    )


def _write_matches(database: Database, report: MatchReport, requests, candidates,
                   representatives: dict, medians: dict[str, float],
                   run_id: int | None, tuning: Settings,
                   full_sweep: bool, off_the_feed: set[str]) -> None:
    """Пара «заявка × представитель» → балл → строка в `matches`.

    `full_sweep` — прошли ли по всей базе. Только полный проход имеет право
    закрывать матчи: по выборке `--new` «не подтвердился» значит «его не было
    в выборке», и закрытие выкинуло бы из витрины всё, кроме свежего.

    `off_the_feed` — объявления, которых проход не видел вовсе: снятые с
    ленты и отложенные аномалией. Их матчи не закрываются даже полным
    проходом: решение 8 спеки велит витрине снятое помечать, а не прятать,
    и «мы звонили по этой квартире» уходу объявления не подчиняется.
    """
    now = datetime.now(timezone.utc)
    for request in requests:
        confirmed: set[str] = set()
        for listing in candidates:
            result = score(request, listing, median_by_district=medians,
                           weights=tuning.weights,
                           stretch_percent=tuning.stretch_percent)
            if result.rejected_by is not None:
                # Отказ в базу не пишется: их миллионы, и звонить по ним некуда.
                continue
            cluster = representatives[listing.id]
            outcome = database.upsert_match(Match(
                request_id=request.id,
                listing_id=listing.id,
                score=float(result.value),
                run_id=run_id,
                breakdown=result.breakdown or None,
                cluster_id=cluster.cluster_id,
                cluster_size=cluster.size,
                cluster_spread_usd=cluster.spread_usd,
            ), now)
            confirmed.add(listing.id)
            setattr(report, outcome, getattr(report, outcome) + 1)
            if tuning.hot is not None and result.value >= tuning.hot:
                report.hot += 1
            elif tuning.digest is not None and result.value >= tuning.digest:
                report.digest += 1
        if full_sweep:
            report.retired += database.retire_matches(
                request.id, keep=confirmed | off_the_feed, now=now,
                reason="проход больше не подтверждает этот вариант",
            )


def _upload(config: Config, database: Database, storage, local_db: Path,
            remote_name: str, report: MatchReport, notes: list[str]) -> bool:
    """Снимок базы в хранилище; отдаёт, закрыта ли база.

    Порядок тот же, что у прогона и пересчёта: снимок с живой базы, ротация
    копий, заливка. Закрывать базу здесь приходится потому, что залить надо
    именно снимок, а не файл, в который ещё пишут.
    """
    snapshot = local_db.with_name(local_db.name + ".snapshot")
    try:
        database.snapshot(snapshot)
    except Exception as exc:       # sqlite3.Error, OSError — заливать нечего
        report.errors += 1
        notes.append(f"снимок базы не сделан: {exc}")
        return False
    database.close()
    rotate_backups(
        storage,
        remote_name,
        int(config.get("storage.keep_backups", DEFAULT_KEEP_BACKUPS) or 0),
        Path(local_db).parent,
    )
    storage.upload(snapshot, remote_name)
    snapshot.unlink(missing_ok=True)
    notes.append("база с матчами залита в хранилище")
    return True


# --- витрина -----------------------------------------------------------
#
# Показывается кластер, а не объявление: «N объявлений, разброс $X» лежит
# в самом матче (`cluster_size`, `cluster_spread_usd`) — снимок, сделанный
# подбором. Считать его заново незачем, да и нечестно: витрина обязана
# показывать то, по чему звонили, а не то, что стало после.

MatchRow = tuple[Request, Match, Listing]

SELLER_TYPES = {"owner": "собственник", "agency": "агентство"}
MATCH_STATUSES = {"new": "новый", "sent": "отправлен",
                  "called": "звонили", "rejected": "отказ"}
DEFAULT_LIMIT = 50


class MatchesError(Exception):
    """Витрину попросили показать то, чего в базе нет или чего она не поймёт."""


def display_limit(config: Config) -> int:
    """Сколько строк показывает витрина. Порог из конфига, а не число в коде."""
    value = threshold(config, "match.limit", DEFAULT_LIMIT)
    return DEFAULT_LIMIT if value is None else int(value)


def collect_matches(config: Config, *, external_id: str | None = None,
                    min_score: float | None = None,
                    limit: int | None = None) -> list[MatchRow]:
    """Матчи для витрины: заявка, матч и объявление одной строкой.

    Объявление берётся `get_listing` даже когда оно снято (решение 8):
    витрина его помечает, а не прячет. `external_id` не задан — все
    активные заявки, по убыванию балла внутри каждой.

    `min_score` не задан — берётся порог дайджеста из конфига: витрина
    показывает то, о чём есть смысл разговаривать. `limit` не задан —
    не сужаем: сколько строк **показать**, решает `render_matches`,
    и только так «…и ещё 12» может быть правдой.

    Замка здесь нет и записи тоже: витрина читает базу, а не чинит её.
    """
    if min_score is None:
        min_score = settings(config).digest

    storage = build_storage(config)
    local_db = database_path(config)
    if not local_db.exists():
        storage.download(config.get("storage.db_filename", "listam.sqlite"), local_db)

    database = build_database(config)
    database.connect()
    try:
        required = latest_schema_version()
        version = database.schema_version()
        if version < required:
            raise MatchesError(
                f"Матчи не показаны: схема базы {version}, а код ждёт {required}. "
                f"Витрина ничего не мигрирует — накати миграции: "
                f"python -m listam recheck"
            )

        if external_id is None:
            requests = list(database.iter_requests())
        else:
            one = database.get_request(external_id)
            if one is None:
                raise MatchesError(
                    f"заявки {external_id} в базе нет — сначала прочитай источник: "
                    f"python -m listam requests"
                )
            # Статус здесь не проверяется, в отличие от подбора: «кому мы уже
            # звонили по этой квартире» переживает и паузу заявки.
            requests = [one]

        rows: list[MatchRow] = []
        for request in requests:
            for match in database.matches_for_request(request.id, min_score=min_score,
                                                      limit=limit):
                listing = database.get_listing(match.listing_id)
                if listing is None:
                    # Объявления нет в базе вовсе — показывать нечего, и это
                    # не «снято»: снятое лежит на месте с пометкой.
                    continue
                rows.append((request, match, listing))
        return rows
    finally:
        database.close()


def _plural(count: int, one: str, few: str, many: str) -> str:
    count = abs(int(count))
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


def _score(value: float | None) -> str:
    if value is None:
        return DASH
    whole = int(round(value))
    return f"{whole} " + _plural(whole, "балл", "балла", "баллов")


def _place(listing: Listing) -> str:
    return f"{listing.district or DASH}, {listing.street or DASH}"


def _what(listing: Listing) -> str:
    rooms = f"{listing.rooms} ком." if listing.rooms is not None else DASH
    area = f"{listing.area:g} м²" if listing.area is not None else DASH
    if listing.floor is None:
        floor = f"эт. {DASH}"
    elif listing.floors_total is None:
        floor = f"эт. {listing.floor}"
    else:
        floor = f"эт. {listing.floor}/{listing.floors_total}"
    seller = SELLER_TYPES.get(listing.seller_type, listing.seller_type) or DASH
    return f"{rooms}, {area}, {floor}, {seller}"


def _note(match: Match, listing: Listing) -> str:
    """Вторая строка матча: что важно знать до звонка.

    «Снято» идёт первым: это единственное, что отменяет звонок целиком.
    """
    parts: list[str] = []
    if listing.status == "gone":
        when = f" {listing.gone_at:%d.%m}" if listing.gone_at else ""
        parts.append(f"снято с ленты{when}")
    if match.cluster_size and match.cluster_size > 1:
        # `is not None`, а не `if`: разброс $0 — это «три карточки по одной
        # цене», самый частый кластер на боевой базе, а не «разброс неизвестен».
        spread = (f", разброс {money(match.cluster_spread_usd)}"
                  if match.cluster_spread_usd is not None else "")
        parts.append(
            f"{match.cluster_size} "
            + _plural(match.cluster_size, "объявление", "объявления", "объявлений")
            + spread
        )
    if match.status and match.status != "new":
        said = MATCH_STATUSES.get(match.status, match.status)
        parts.append(f"{said}: {match.reject_reason}" if match.reject_reason else said)
    return " · ".join(parts)


def render_matches(rows: list[MatchRow], limit: int,
                   min_score: float | None = None) -> str:
    """Витрина: по разделу на заявку, по строке на кластер.

    `min_score` называется в шапке, чтобы пустой раздел читался как «выше
    порога ничего нет», а не как «матчинг не работает». Мерка приходит
    аргументом, а не вычитывается из конфига: печать конфига не читает.
    """
    if not rows:
        return "Подобранных вариантов нет."

    by_request: dict[int, list[MatchRow]] = {}
    requests: dict[int, Request] = {}
    for request, match, listing in rows:
        by_request.setdefault(request.id, []).append((request, match, listing))
        requests[request.id] = request

    lines: list[str] = []
    for request_id, group in by_request.items():
        request = requests[request_id]
        who = f" ({request.client_name})" if request.client_name else ""
        head = f"Заявка {request.external_id or request_id}{who}"
        if min_score is not None:
            head += f", порог дайджеста {min_score:g}"
        head += f" — подобрано {len(group)}"
        if lines:
            lines.append("")
        lines.append(head)
        for _, match, listing in group[:limit]:
            # Снятое видно с первого взгляда, а не из второй строки: минус
            # в начале строки — та же пометка, что в разделе «Снято» у `changes`.
            mark = MINUS if listing.status == "gone" else "•"
            lines.append(
                f"  {mark} {_score(match.score):>10}  {money(listing.price_usd):>10}  "
                f"{per_sqm(listing):>12}  {_place(listing):<26}  {_what(listing):<40}  "
                f"{listing.url}"
            )
            note = _note(match, listing)
            if note:
                lines.append(f"      {note}")
        left = len(group) - len(group[:limit])
        if left:
            lines.append(f"  …и ещё {left}")
    return "\n".join(lines)
