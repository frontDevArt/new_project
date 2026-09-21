"""Реализация Database поверх SQLite. Один файл — вся база, его удобно возить."""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from listam.domain.models import Listing, PricePoint, Run
from listam.ports.database import Database

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# Сколько ждать, пока чужая запись отпустит базу. Рабочее значение — из конфига.
DEFAULT_BUSY_TIMEOUT_MS = 10_000

# Поля, изменение которых считаем содержательным обновлением карточки.
# Только то, что реально пришло с сайта: пересчитанных здесь нет и быть не может,
# иначе движение курса выглядело бы как изменение цены у всей базы разом.
# price_amount — та же сырая цена, только числом, поэтому он тут, а не в derived.
TRACKED_FIELDS = (
    "title", "district", "street", "price_raw", "currency", "price_amount",
    "area", "rooms", "floor", "floors_total", "seller_type", "verified", "new_build",
)

# Цена шевельнулась — это про сырую цену со страницы, а не про пересчёт.
PRICE_FIELDS = ("price_raw", "currency")

# Пересчитанные поля: их считает прогон по курсу, а не отдаёт сайт.
# Пустое значение здесь означает «не смог посчитать» — таким не затираем.
DERIVED_FIELDS = ("price_usd", "price_amd", "price_per_sqm")


def latest_schema_version(migrations_dir: str | Path | None = None) -> int:
    """Версия схемы, которую ждёт этот код: старшая миграция на диске.

    Числа в коде для этого нет и быть не должно: схему задают файлы миграций,
    и держать рядом с ними вторую версию, которую надо не забыть поправить, —
    это способ однажды соврать.
    """
    directory = Path(migrations_dir) if migrations_dir else MIGRATIONS_DIR
    return max(
        (int(path.name.split("_", 1)[0]) for path in directory.glob("*.sql")),
        default=0,
    )


def to_iso(value: datetime | None) -> str | None:
    """Все отметки времени храним в UTC в ISO-8601."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class SqliteDatabase(Database):
    def __init__(
        self,
        path: str | Path,
        migrations_dir: str | Path | None = None,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    ):
        self.path = Path(path)
        self.migrations_dir = Path(migrations_dir) if migrations_dir else MIGRATIONS_DIR
        self.busy_timeout_ms = int(busy_timeout_ms)
        self._conn: sqlite3.Connection | None = None

    # --- жизненный цикл -------------------------------------------------
    def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: транзакциями управляем сами. Без этого sqlite3
        # не открывает транзакцию под DDL, и миграция накатывалась бы по
        # оператору — падение посреди неё оставляло бы половину схемы.
        self._conn = sqlite3.connect(self.path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        # WAL: выгрузка читает базу, пока прогон в неё пишет, а не падает с
        # «database is locked». busy_timeout — сколько ждать чужой записи,
        # прежде чем сдаться; берётся из конфига.
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")

    def close(self) -> None:
        if self._conn:
            self._conn.commit()
            # Перенос WAL в основной файл: обычно его делает закрытие последнего
            # соединения, но пока базу держит открытой кто-то ещё, этого не
            # происходит, и на диске остаётся файл без последних записей.
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass          # база занята читателем — перенос сделает он
            self._conn.close()
            self._conn = None

    def snapshot(self, target: str | Path) -> None:
        """Целая копия базы в отдельный файл — её и заливаем в хранилище.

        `VACUUM INTO` пишет новый файл из текущего состояния соединения: в нём
        уже есть то, что лежит ещё в `-wal`, и нет собственных спутников.
        Копировать вместо этого файл базы означает иногда залить копию без
        последнего прогона — ровно тогда, когда базу читает кто-то ещё.
        """
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)   # VACUUM INTO не пишет в существующий файл
        self.conn.execute("VACUUM INTO ?", (str(target),))

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("База не открыта: сначала connect()")
        return self._conn

    # --- схема ----------------------------------------------------------
    def migrate(self) -> None:
        """Накатывает недостающие миграции. Каждая — целиком или никак.

        Скрипт и отметка о версии идут одной транзакцией: иначе падение посреди
        миграции оставило бы половину схемы без строки в `schema_version`,
        и следующий запуск попытался бы накатить её заново.
        """
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            " version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, name TEXT)"
        )
        applied = {
            row["version"] for row in self.conn.execute("SELECT version FROM schema_version")
        }
        for path in sorted(self.migrations_dir.glob("*.sql")):
            version = int(path.name.split("_", 1)[0])
            if version in applied:
                continue
            self._apply(path, version)
        self._check_integrity()

    @contextmanager
    def transaction(self):
        """Всё внутри — одной транзакцией. Границы ставим руками: база в autocommit."""
        self.conn.execute("BEGIN")
        try:
            yield
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def _apply(self, path: Path, version: int) -> None:
        with self.transaction():
            for statement in _statements(path.read_text(encoding="utf-8")):
                self.conn.execute(statement)
            self.conn.execute(
                "INSERT INTO schema_version(version, applied_at, name) VALUES (?, ?, ?)",
                (version, to_iso(datetime.now(timezone.utc)), path.name),
            )

    def _check_integrity(self) -> None:
        row = self.conn.execute("PRAGMA quick_check").fetchone()
        answer = row[0] if row else "нет ответа"
        if answer != "ok":
            raise sqlite3.DatabaseError(f"База повреждена: {answer} ({self.path})")

    def schema_version(self) -> int:
        """Версия схемы базы. Миграции не накатывали ни разу — 0.

        Пустой файл — это версия 0, а не повод падать трейсбеком: спрашивать
        версию у базы имеет право любой, в том числе выгрузка, которая сама
        ничего не мигрирует.
        """
        try:
            row = self.conn.execute(
                "SELECT MAX(version) AS v FROM schema_version"
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row["v"] or 0)

    def _columns(self, table: str) -> set[str]:
        """Имена колонок таблицы — какие они в базе сейчас, а не какие ждёт модель."""
        return {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}

    def table_names(self) -> set[str]:
        rows = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {row["name"] for row in rows}

    # --- объявления -----------------------------------------------------
    def upsert_listing(
        self, listing: Listing, seen_at: datetime, rate_amd_per_usd: float | None = None
    ) -> str:
        existing = self.get_listing(listing.id)
        seen_iso = to_iso(seen_at)
        if existing is None:
            values = {name: getattr(listing, name) for name in Listing.field_names()}
            values["first_seen"] = seen_iso
            values["last_seen"] = seen_iso
            values["status"] = listing.status or "active"
            values["verified"] = _to_int(listing.verified)
            values["new_build"] = _to_int(listing.new_build)
            values["gone_at"] = to_iso(listing.gone_at)
            # Модель бежит впереди базы, пока миграции не накатаны: пишем те поля,
            # которые в таблице есть, а не те, которые знает dataclass.
            known = self._columns("listings")
            values = {name: value for name, value in values.items() if name in known}
            columns = ", ".join(values)
            placeholders = ", ".join(f":{name}" for name in values)
            with self.transaction():
                self.conn.execute(
                    f"INSERT INTO listings ({columns}) VALUES ({placeholders})", values
                )
                self._add_price_point(listing.id, seen_at, listing.price_usd, rate_amd_per_usd)
            return "new"

        changed = [
            name for name in TRACKED_FIELDS
            if _normalize(getattr(listing, name)) != _normalize(getattr(existing, name))
        ]
        updates = {name: getattr(listing, name) for name in TRACKED_FIELDS}
        updates.update({name: getattr(listing, name) for name in DERIVED_FIELDS})
        updates["verified"] = _to_int(listing.verified)
        updates["new_build"] = _to_int(listing.new_build)
        updates["anomaly"] = listing.anomaly
        updates["last_seen"] = seen_iso
        updates["status"] = "active"
        updates["id"] = listing.id
        # Сменились валюта или сырая цена — пересчитанное относится к прошлой цене
        # и должно уйти целиком, включая NULL. COALESCE бережёт пересчёт только
        # тогда, когда цена на сайте та же, а курс в этот раз не дался.
        price_moved = any(name in changed for name in PRICE_FIELDS)
        assignments = ", ".join(
            f"{name} = COALESCE(:{name}, {name})"
            if name in DERIVED_FIELDS and not price_moved
            else f"{name} = :{name}"
            for name in updates if name != "id"
        )
        outcome = "unchanged"
        with self.transaction():
            self.conn.execute(f"UPDATE listings SET {assignments} WHERE id = :id", updates)
            if price_moved:
                self._add_price_point(listing.id, seen_at, listing.price_usd, rate_amd_per_usd)
                outcome = "price_changed"
            elif changed:
                outcome = "updated"
        return outcome

    def _add_price_point(
        self,
        listing_id: str,
        seen_at: datetime,
        price_usd: float | None,
        rate_amd_per_usd: float | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO price_history (listing_id, seen_at, price_usd, rate_amd_per_usd) "
            "VALUES (?, ?, ?, ?)",
            (listing_id, to_iso(seen_at), price_usd, rate_amd_per_usd),
        )

    def set_computed(
        self, listing_id: str, *, anomaly: str | None, price_amount: float | None
    ) -> None:
        """Пометка и сумма в валюте оригинала — и больше ничего.

        `last_seen` нарочно не трогаем: пересчёт не видел карточку на сайте,
        и притворяться, что видел, он не имеет права.
        """
        self.conn.execute(
            "UPDATE listings SET anomaly = ?, price_amount = ? WHERE id = ?",
            (anomaly, price_amount, listing_id),
        )

    def get_listing(self, listing_id: str) -> Listing | None:
        row = self.conn.execute(
            "SELECT * FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()
        return _row_to_listing(row) if row else None

    def iter_listings(self) -> Iterator[Listing]:
        query = "SELECT * FROM listings ORDER BY first_seen DESC, id DESC"
        for row in self.conn.execute(query):
            yield _row_to_listing(row)

    def known_ids(self) -> set[str]:
        return {row["id"] for row in self.conn.execute("SELECT id FROM listings")}

    def count_listings(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS n FROM listings").fetchone()["n"])

    def price_history(self, listing_id: str) -> list[PricePoint]:
        rows = self.conn.execute(
            "SELECT * FROM price_history WHERE listing_id = ? ORDER BY id", (listing_id,)
        )
        return [
            PricePoint(
                listing_id=r["listing_id"],
                seen_at=from_iso(r["seen_at"]),
                price_usd=r["price_usd"],
                rate_amd_per_usd=r["rate_amd_per_usd"],
            )
            for r in rows
        ]

    # --- журнал прогонов -------------------------------------------------
    def start_run(self, started_at: datetime, rate_amd_per_usd: float | None) -> int:
        cursor = self.conn.execute(
            "INSERT INTO runs (started_at, rate_amd_per_usd) VALUES (?, ?)",
            (to_iso(started_at), rate_amd_per_usd),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def mark_page(self, run_id: int, page: int) -> None:
        """Пишется после каждой пройденной страницы, а не в конце прогона.

        Обход 215 страниц идёт часами; убитый на середине прогон не должен
        начинаться сначала.
        """
        self.conn.execute(
            "UPDATE runs SET last_page = ? WHERE id = ?", (int(page), run_id)
        )
        self.conn.commit()

    def finish_run(self, run_id: int, finished_at: datetime, **counters) -> None:
        allowed = {
            "pages_fetched", "listings_seen", "new_listings", "updated_listings",
            "errors", "notes", "last_page",
        }
        unknown = set(counters) - allowed
        if unknown:
            raise ValueError(f"Неизвестные счётчики прогона: {sorted(unknown)}")
        counters["finished_at"] = to_iso(finished_at)
        assignments = ", ".join(f"{name} = :{name}" for name in counters)
        self.conn.execute(
            f"UPDATE runs SET {assignments} WHERE id = :id", {**counters, "id": run_id}
        )
        self.conn.commit()

    def last_run(self) -> Run | None:
        row = self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return _row_to_run(row) if row else None

    def last_successful_run(self) -> Run | None:
        """Последний прогон, который дошёл до конца и не насчитал ошибок."""
        row = self.conn.execute(
            "SELECT * FROM runs WHERE finished_at IS NOT NULL AND IFNULL(errors, 0) = 0 "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return _row_to_run(row) if row else None


def _row_to_run(row: sqlite3.Row) -> Run:
    return Run(
        id=row["id"],
        started_at=from_iso(row["started_at"]),
        finished_at=from_iso(row["finished_at"]),
        rate_amd_per_usd=row["rate_amd_per_usd"],
        pages_fetched=row["pages_fetched"],
        listings_seen=row["listings_seen"],
        new_listings=row["new_listings"],
        updated_listings=row["updated_listings"],
        errors=row["errors"],
        notes=row["notes"],
        last_page=row["last_page"] or 0,
    )


# Слова, после которых `;` перестаёт быть концом оператора: тело триггера
# и ветвление живут внутри своего BEGIN/CASE … END.
_BLOCK_OPENERS = ("BEGIN", "CASE")
_WORD = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


def _statements(script: str) -> list[str]:
    """Режет миграцию на операторы, зная про кавычки и блоки `BEGIN … END`.

    Разрез по каждому `;` резал триггер на огрызки, а `;` внутри строкового
    литерала — на неверный SQL. Поэтому текст читается посимвольно: внутри
    кавычек и комментариев точка с запятой ничего не значит, а внутри
    `BEGIN … END` она разделяет операторы тела, а не саму миграцию.
    """
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    index, length = 0, len(script)
    while index < length:
        char = script[index]
        pair = script[index:index + 2]

        if pair == "--":                       # комментарий до конца строки
            end = script.find("\n", index)
            index = length if end == -1 else end
            continue
        if pair == "/*":
            end = script.find("*/", index + 2)
            index = length if end == -1 else end + 2
            continue
        if char in "'\"`":                      # литерал или имя в кавычках
            closing = script.find(char, index + 1)
            while closing != -1 and script[closing:closing + 2] == char * 2:
                closing = script.find(char, closing + 2)   # удвоенная кавычка — это она сама
            closing = length - 1 if closing == -1 else closing
            current.append(script[index:closing + 1])
            index = closing + 1
            continue
        if char == "[":                        # [имя в скобках] — тоже идентификатор
            closing = script.find("]", index + 1)
            closing = length - 1 if closing == -1 else closing
            current.append(script[index:closing + 1])
            index = closing + 1
            continue

        word = _WORD.match(script, index)
        if word:
            upper = word.group(0).upper()
            if upper in _BLOCK_OPENERS:
                depth += 1
            elif upper == "END" and depth:
                depth -= 1
            current.append(word.group(0))
            index = word.end()
            continue

        if char == ";" and depth == 0:
            parts.append("".join(current))
            current = []
            index += 1
            continue

        current.append(char)
        index += 1

    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _to_int(value: bool | None) -> int | None:
    return None if value is None else int(bool(value))


def _to_bool(value) -> bool | None:
    return None if value is None else bool(value)


def _normalize(value):
    return round(value, 2) if isinstance(value, float) else value


def _row_to_listing(row: sqlite3.Row) -> Listing:
    data = dict(row)
    data["verified"] = _to_bool(data.get("verified"))
    data["new_build"] = _to_bool(data.get("new_build"))
    data["first_seen"] = from_iso(data.get("first_seen"))
    data["last_seen"] = from_iso(data.get("last_seen"))
    return Listing(**{k: v for k, v in data.items() if k in Listing.field_names()})
