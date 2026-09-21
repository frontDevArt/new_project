"""Что принёс последний прогон: новое, сменившее цену, снятое.

Команда ничего не узнаёт о рынке и никуда не ходит: она читает базу и
складывает из неё список для человека. Базу она не чинит — схема младше той,
которую ждёт код, это ошибка, а не повод молча накатить миграции.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from listam.adapters.db_sqlite import latest_schema_version
from listam.domain.models import Listing
from listam.wiring import build_database, build_storage, database_path

# Прочерк на месте отсутствующего значения: в списке для человека `None`
# читается как поломка, а не как «сайт этого не сказал».
DASH = "—"

# Настоящий минус U+2212, а не дефис: в колонке чисел дефис теряется.
MINUS = "−"

# Стрелка возврата: объявление, которое уже снимали, снова на ленте.
RETURN = "↩"


@dataclass
class PriceMove:
    listing: Listing
    was: float | None
    now: float | None

    @property
    def percent(self) -> float | None:
        """На сколько процентов сдвинулась цена. Прежней цены нет — и процента нет."""
        if not self.was or self.now is None:
            return None
        return (self.now - self.was) / self.was * 100


@dataclass
class Changes:
    # Отметок две, и это решение, а не недосмотр: новое и цены приносит любой
    # прогон, а снятых ставит только полный. Одна мерка на всё стирала бы
    # раздел «Снято» первым же часовым `--fresh`.
    since: datetime | None = None
    since_note: str = ""
    gone_since: datetime | None = None
    gone_note: str = ""
    new: list[Listing] = field(default_factory=list)
    moved: list[PriceMove] = field(default_factory=list)
    gone: list[Listing] = field(default_factory=list)
    # Вернуться может только помеченное снятым, поэтому мерка у возврата та же,
    # что у снятых: полный обход, который пометку и ставил.
    returned: list[Listing] = field(default_factory=list)
    errors: int = 0
    notes: str | None = None

    @property
    def empty(self) -> bool:
        return not (self.new or self.moved or self.gone or self.returned)


def since_point(
    database, hours: float | None, fallback_hours: float = 24.0
) -> tuple[datetime, str]:
    """С какого момента считаем изменения и как это объяснить человеку.

    По умолчанию — начало последнего завершённого прогона: «что принёс
    последний обход» и есть тот вопрос, ради которого команду зовут.
    Журнал пуст — считаем за `fallback_hours` и говорим об этом вслух,
    чтобы пустой список не выглядел как «на рынке тишина».
    """
    if hours is not None:
        mark = datetime.now(timezone.utc) - timedelta(hours=float(hours))
        return mark, f"за последние {hours:g} ч (с {mark:%Y-%m-%d %H:%M} UTC)"
    last = database.last_run()
    if last is None or last.started_at is None:
        mark = datetime.now(timezone.utc) - timedelta(hours=float(fallback_hours))
        return mark, f"прогонов в журнале нет — показываю за последние {fallback_hours:g} ч"
    return last.started_at, (
        f"с начала прогона {last.id} ({last.started_at:%Y-%m-%d %H:%M} UTC, "
        f"режим {last.mode or 'full'})"
    )


def gone_since_point(
    database, since: datetime, since_note: str, hours: float | None
) -> tuple[datetime, str]:
    """С какого момента считаем снятых и как это объяснить человеку.

    Снятых ставит только полный обход, а `--fresh` ходит каждый час: мерить
    их началом последнего прогона — значит гасить раздел до следующей ночи.
    Мерка — начало последнего завершённого полного обхода.

    `--hours N` задаёт обе отметки: человек попросил окно в часах, и делить
    его надвое незачем.
    """
    if hours is not None:
        return since, since_note
    last_full = database.last_run_that_could_mark_gone()
    if last_full is None or last_full.started_at is None:
        return since, "полных прогонов в журнале нет — снятые считаны по общей мерке"
    return last_full.started_at, (
        f"снятые — с полного обхода {last_full.id} "
        f"({last_full.started_at:%Y-%m-%d %H:%M} UTC)"
    )


def run_changes(config, *, hours: float | None = None) -> Changes:
    """Собирает дельту из базы. Замок не берётся: команда только читает."""
    storage = build_storage(config)
    local_db = database_path(config)
    remote_name = config.get("storage.db_filename", "listam.sqlite")
    if not local_db.exists():
        storage.download(remote_name, local_db)

    database = build_database(config)
    database.connect()
    try:
        required = latest_schema_version()
        version = database.schema_version()
        if version < required:
            return Changes(
                errors=1,
                notes=(
                    f"Изменения не показаны: схема базы {version}, а код ждёт {required}. "
                    f"Команда ничего не мигрирует — накати миграции: python -m listam recheck"
                ),
            )
        since, since_note = since_point(
            database, hours, fallback_hours=config.get("changes.fallback_hours", 24)
        )
        gone_since, gone_note = gone_since_point(database, since, since_note, hours)
        return Changes(
            since=since,
            since_note=since_note,
            gone_since=gone_since,
            gone_note=gone_note,
            new=database.listings_first_seen_since(since),
            moved=[
                PriceMove(listing=item, was=was, now=now)
                for item, was, now in database.price_changes_since(since)
            ],
            gone=database.listings_gone_since(gone_since),
            returned=database.listings_returned_since(gone_since),
        )
    finally:
        database.close()


# --- печать ------------------------------------------------------------


def money(value: float | None) -> str:
    return DASH if value is None else f"${value:,.0f}"


def percent(value: float | None) -> str:
    """Со знаком и с настоящим минусом: `+3.4%`, `−5.2%`."""
    return DASH if value is None else f"{value:+.1f}%".replace("-", MINUS)


def _size(item: Listing) -> str:
    rooms = f"{item.rooms} ком." if item.rooms is not None else DASH
    area = f"{item.area:g} м²" if item.area is not None else DASH
    return f"{rooms}, {area}"


def _per_sqm(item: Listing) -> str:
    return DASH if item.price_per_sqm is None else f"{item.price_per_sqm:,.0f} $/м²"


def _days_on_feed(item: Listing) -> str:
    """Сколько объявление провисело на ленте. Дат не хватает — не выдумываем."""
    if item.gone_at is None or item.first_seen is None:
        return "срок неизвестен"
    return f"висело {(item.gone_at - item.first_seen).days} дней"


def _head(item: Listing) -> str:
    """Общее начало строки: номер и район, выровненные по ширине."""
    return f"{item.id:<10} {(item.district or DASH):<12}"


def _section(title: str, lines: list[str], limit: int, note: str = "") -> list[str]:
    """Раздел, обрезанный до `limit` строк. Обрезано — сказано, сколько осталось."""
    if not lines:
        return []
    shown = lines[:limit]
    out = ["", title, *([f"  {note}"] if note else []), *shown]
    left = len(lines) - len(shown)
    if left:
        out.append(f"  …и ещё {left}")
    return out


def render(changes: Changes, limit: int) -> str:
    """Шапка и четыре раздела: новое, цены, снятое и вернувшееся на ленту."""
    lines = [
        f"Изменения {changes.since_note}".rstrip(),
        f"Новых: {len(changes.new)}   Сменили цену: {len(changes.moved)}   "
        f"Снято: {len(changes.gone)}   Вернулось: {len(changes.returned)}",
    ]
    if changes.empty:
        lines.append("изменений нет")
        return "\n".join(lines)

    lines += _section(
        "Новые",
        [
            f"+ {_head(item)} {_size(item):<16} {money(item.price_usd):>10} "
            f"{_per_sqm(item):>12}  {item.url}"
            for item in changes.new
        ],
        limit,
    )
    lines += _section(
        "Цены",
        [
            f"$ {_head(move.listing)} было {money(move.was)} → стало {money(move.now)}  "
            f"({percent(move.percent)})  {move.listing.url}"
            for move in changes.moved
        ],
        limit,
    )
    # Про вторую мерку говорим, только когда она разошлась с общей: в остальных
    # случаях это шум, повторяющий шапку.
    gone_note = changes.gone_note if changes.gone_since != changes.since else ""
    lines += _section(
        "Снято",
        [
            f"{MINUS} {_head(item)} {_days_on_feed(item)}, "
            f"последняя цена {money(item.price_usd)}  {item.url}"
            for item in changes.gone
        ],
        limit,
        gone_note,
    )
    # Вернувшееся считано той же меркой, что и снятое: пометку ставил полный
    # обход, и возврат отменяет именно её. Но говорится об этом своими словами:
    # под разделом «Вернулись» нота про снятых читается как чужая.
    returned_note = (
        "" if changes.gone_since == changes.since
        else changes.gone_note.replace("снятые —", "вернувшиеся —", 1)
    )
    lines += _section(
        "Вернулись",
        [
            f"{RETURN} {_head(item)} {_size(item):<16} {money(item.price_usd):>10}  "
            f"{item.url}"
            for item in changes.returned
        ],
        limit,
        returned_note,
    )
    return "\n".join(lines)
