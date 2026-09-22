"""Витрина матчей: что человек видит по команде `matches` и на листе «Матчи».

Отделена от подбора нарочно. Подбор пишет базу под замком и стоит секунды;
витрина её только читает и обязана отвечать сразу. В одном файле они держали
496 строк и два разных повода правки — «пересчитать иначе» и «показать иначе».

Показывается кластер, а не объявление: «N объявлений, разброс $X» лежит
в самом матче (`cluster_size`, `cluster_spread_usd`) — снимок, сделанный
подбором. Считать его заново незачем, да и нечестно: витрина обязана
показывать то, по чему звонили, а не то, что стало после.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from listam.adapters.db_sqlite import latest_schema_version
from listam.changes import DASH, MINUS, money, per_sqm
from listam.config import Config, positive
from listam.domain.labels import MATCH_STATUSES, SELLER_TYPES
from listam.domain.models import Listing, Match, Request
from listam.matching import settings
from listam.wiring import build_database, build_storage, database_path

MatchRow = tuple[Request, Match, Listing]

DEFAULT_LIMIT = 50


@dataclass
class MatchesPage:
    """Что витрина прочитала и сколько всего есть.

    Две величины, а не одна. Потолок теперь доходит до выборки: на боевых
    числах `matches` без `--request` читала 50 633 строки за 4,0 с, а с
    потолком в SQL — заметно меньше. Но «…и ещё 704» обязано остаться
    правдой, а прочитанные строки про непрочитанные ничего не знают — их
    считает отдельный запрос.
    """

    rows: list[MatchRow] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)   # ключ группы → всего


def group_key(request: Request) -> str:
    """Ключ раздела витрины: внешний идентификатор, а не `id`.

    Заявка, не записанная в базу, имеет `id = None`, и все такие схлопнулись
    бы в один раздел.
    """
    return request.external_id or f"#{request.id}"


class MatchesError(Exception):
    """Витрину попросили показать то, чего в базе нет или чего она не поймёт."""


def display_limit(config: Config) -> int:
    """Сколько строк показывает витрина. Порог из конфига, а не число в коде."""
    value = positive(config, "match.limit", DEFAULT_LIMIT)
    return DEFAULT_LIMIT if value is None else int(value)


def collect_matches(config: Config, *, external_id: str | None = None,
                    min_score: float | None = None,
                    limit: int | None = None) -> MatchesPage:
    """Матчи для витрины: заявка, матч и объявление одной строкой.

    Объявление приходит вместе с матчем, одним запросом на заявку, и снятое
    из выдачи не выпадает (решение 8): витрина его помечает, а не прячет.
    Матч, у которого объявления в базе нет вовсе, не показывается — `JOIN`
    его не отдаёт; это не «снято», снятое лежит на месте с пометкой.

    `min_score` не задан — берётся порог дайджеста из конфига. `limit` —
    сколько строк **читать**: он доходит до SQL, а сколько их всего, отвечает
    счётчик. Замка здесь нет и записи тоже: витрина читает базу, а не чинит её.
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

        page = MatchesPage()
        for request in requests:
            pairs = database.matches_with_listings(
                request.id, min_score=min_score, limit=limit)
            if not pairs:
                continue
            page.totals[group_key(request)] = database.count_matches_alive(
                request.id, min_score=min_score)
            for match, listing in pairs:
                page.rows.append((request, match, listing))
        return page
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


def render_matches(page: MatchesPage, limit: int,
                   min_score: float | None = None) -> str:
    """Витрина: по разделу на заявку, по строке на кластер.

    `min_score` называется в шапке, чтобы пустой раздел читался как «выше
    порога ничего нет», а не как «матчинг не работает». Мерка приходит
    аргументом, а не вычитывается из конфига: печать конфига не читает.

    Хвост «…и ещё N» считается от `page.totals`, а не от длины прочитанного:
    потолок доходит до выборки, и прочитанные строки про остальные ничего
    не знают.
    """
    if not page.rows:
        return "Подобранных вариантов нет."

    by_request: dict[str, list[MatchRow]] = {}
    requests: dict[str, Request] = {}
    for request, match, listing in page.rows:
        key = group_key(request)
        by_request.setdefault(key, []).append((request, match, listing))
        requests[key] = request

    lines: list[str] = []
    for key, group in by_request.items():
        request = requests[key]
        total = page.totals.get(key, len(group))
        who = f" ({request.client_name})" if request.client_name else ""
        head = f"Заявка {request.external_id or key}{who}"
        if min_score is not None:
            head += f", порог дайджеста {min_score:g}"
        head += f" — подобрано {total}"
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
        left = total - len(group[:limit])
        if left > 0:
            lines.append(f"  …и ещё {left}")
    return "\n".join(lines)
