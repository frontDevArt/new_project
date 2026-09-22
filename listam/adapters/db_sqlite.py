"""Реализация Database поверх SQLite. Один файл — вся база, его удобно возить."""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import fields as dataclass_fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from listam.domain.models import Listing, Match, PricePoint, Request, Run
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


# Поля заявки, изменение которых считаем содержательным: всё, что влияет
# на матчинг и на разговор с клиентом. `updated_at` и `source_row` сюда
# не входят — иначе перечитывание одной и той же таблицы каждый час
# выглядело бы как правка всех пятидесяти заявок разом.
REQUEST_FIELDS = (
    "client_name", "client_phone", "status", "budget_max", "budget_stretch",
    "districts", "districts_priority", "rooms", "area_min", "area_max",
    "floor_min", "floor_max", "no_first_floor", "no_last_floor",
    "must_have", "nice_to_have", "floor_rules", "notes",
)

# Поля матча, которые считает пересчёт. `status` и `reject_reason` сюда
# не входят и входить не могут: это след звонка, а не вычисленное значение
# (решение 7 спеки). Их пишет только `set_match_status`.
MATCH_FIELDS = (
    "score", "breakdown", "cluster_id", "cluster_size", "cluster_spread_usd", "run_id",
)


# Списки в TEXT-колонках: базе они нужны цельными, а не отдельной таблицей —
# по ним не ищут, их читают вместе с заявкой.
LIST_FIELDS = ("districts", "districts_priority", "rooms")

FLAG_FIELDS = ("no_first_floor", "no_last_floor")


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
        updates["gone_at"] = None          # встретили на ленте — значит, вернулось
        updates["id"] = listing.id
        # Возврат с того света — это про строку, которая лежала снятой, а не про
        # любую встречу: дату возврата ставим только ей, иначе `returned_at`
        # двигался бы всей ленте разом и не отличал бы возврат от встречи.
        came_back = (existing.status or "active") == "gone"
        if came_back:
            updates["returned_at"] = seen_iso
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
        # Вернулось — это крупнее, чем «подвинуло цену» или «поправило заголовок»:
        # точка истории цен ставится как обычно, но прогон читает исход как
        # возврат и в обновления его не записывает.
        return "returned" if came_back else outcome

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

    def active_ids(self) -> set[str]:
        rows = self.conn.execute("SELECT id FROM listings WHERE status = 'active'")
        return {row["id"] for row in rows}

    def mark_gone(self, listing_ids, gone_at: datetime) -> int:
        """Переводит объявления в 'gone' и ставит дату снятия — один раз.

        `last_seen` не двигается: снятие — это не встреча, объявление никто
        не видел. COALESCE бережёт дату первого снятия: объявление, которое
        уже неделю как ушло, не должно молодеть с каждым обходом.
        """
        marked = 0
        stamp = to_iso(gone_at)
        with self.transaction():
            for listing_id in listing_ids:
                cursor = self.conn.execute(
                    "UPDATE listings SET status = 'gone', gone_at = COALESCE(gone_at, ?), "
                    "returned_at = NULL WHERE id = ? AND status <> 'gone'",
                    (stamp, listing_id),
                )
                marked += cursor.rowcount
        return marked

    def listings_first_seen_since(self, since: datetime) -> list[Listing]:
        """Новое с отметки. Мерка — `first_seen`: встреченное заново новым не стало."""
        rows = self.conn.execute(
            "SELECT * FROM listings WHERE first_seen >= ? ORDER BY first_seen DESC, id DESC",
            (to_iso(since),),
        )
        return [_row_to_listing(row) for row in rows]

    def listings_gone_since(self, since: datetime) -> list[Listing]:
        """Снятое с отметки. Дата снятия ставится один раз — по ней и спрашиваем."""
        rows = self.conn.execute(
            "SELECT * FROM listings WHERE gone_at >= ? ORDER BY gone_at DESC, id DESC",
            (to_iso(since),),
        )
        return [_row_to_listing(row) for row in rows]

    def listings_returned_since(self, since: datetime) -> list[Listing]:
        """Вернувшееся с отметки. Спрашивается у даты возврата — своей колонки.

        По `last_seen` возврат не выбрать: его двигает каждый прогон всей ленте.
        `returned_at` ставится только строке, которая лежала снятой, и гаснет
        при следующем снятии — поэтому раздел показывает тех, кто вернулся
        и остался.
        """
        rows = self.conn.execute(
            "SELECT * FROM listings WHERE returned_at >= ? "
            "ORDER BY returned_at DESC, id DESC",
            (to_iso(since),),
        )
        return [_row_to_listing(row) for row in rows]

    def price_changes_since(
        self, since: datetime
    ) -> list[tuple[Listing, float | None, float | None]]:
        """Одна карточка — одна строка: «было на начало окна → стало на конец».

        Запрос идёт по объявлениям, а не по точкам истории: карточка, дважды
        сменившая цену за окно, — это одна сменившая цену квартира, а не две.
        «Было» — последняя точка ДО окна, «стало» — последняя точка В окне.

        Точки до окна нет — объявление внутри окна и появилось: это «Новое»,
        а не смена цены, и такая строка отбрасывается.

        `seen_at` — текст, сравнение строковое; это работает, потому что все
        отметки пишет `to_iso` в одном формате.
        """
        rows = self.conn.execute(
            "WITH moved AS ("
            "  SELECT listing_id, MAX(id) AS last_id FROM price_history "
            "   WHERE seen_at >= :since GROUP BY listing_id"
            ") "
            "SELECT m.listing_id AS listing_id, "
            "       (SELECT price_usd FROM price_history WHERE id = m.last_id) AS new_price, "
            "       (SELECT p.price_usd FROM price_history p "
            "         WHERE p.listing_id = m.listing_id AND p.seen_at < :since "
            "         ORDER BY p.id DESC LIMIT 1) AS old_price, "
            "       EXISTS (SELECT 1 FROM price_history p "
            "                WHERE p.listing_id = m.listing_id AND p.seen_at < :since"
            "              ) AS has_previous "
            "  FROM moved m ORDER BY m.last_id DESC",
            {"since": to_iso(since)},
        ).fetchall()
        changes: list[tuple[Listing, float | None, float | None]] = []
        for row in rows:
            if not row["has_previous"]:
                continue          # появление объявления, а не смена цены
            item = self.get_listing(row["listing_id"])
            if item is not None:
                changes.append((item, row["old_price"], row["new_price"]))
        return changes

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

    # --- заявки -----------------------------------------------------------
    def set_cluster_ids(self, mapping: dict[str, str]) -> int:
        """Кластеры одной транзакцией; считаются строки, которые правда сменились.

        Условие `cluster_id IS NULL OR cluster_id <> ?` — не экономия записи,
        а мерка: по ней видно, что повторный пересчёт ничего не двигает.
        """
        changed = 0
        with self.transaction():
            for listing_id, cluster_id in mapping.items():
                cursor = self.conn.execute(
                    "UPDATE listings SET cluster_id = ? WHERE id = ? "
                    "AND (cluster_id IS NULL OR cluster_id <> ?)",
                    (cluster_id, listing_id, cluster_id),
                )
                changed += cursor.rowcount
        return changed

    def listings_for_matching(self, since: datetime | None = None) -> list[Listing]:
        """Активные и без аномалии; `since` мерится по `first_seen`."""
        query = ("SELECT * FROM listings WHERE status = 'active' "
                 "AND (anomaly IS NULL OR anomaly = '')")
        params: tuple = ()
        if since is not None:
            query += " AND first_seen >= ?"
            params = (to_iso(since),)
        query += " ORDER BY first_seen DESC, id DESC"
        return [_row_to_listing(row) for row in self.conn.execute(query, params)]

    def upsert_request(self, request: Request, now: datetime) -> str:
        values = {name: _request_value(request, name) for name in REQUEST_FIELDS}
        existing = self.get_request(request.external_id)
        if existing is None:
            values["external_id"] = request.external_id
            values["source_row"] = request.source_row
            values["created_at"] = to_iso(now)
            values["updated_at"] = to_iso(now)
            columns = ", ".join(values)
            placeholders = ", ".join(f":{name}" for name in values)
            with self.transaction():
                self.conn.execute(
                    f"INSERT INTO requests ({columns}) VALUES ({placeholders})", values
                )
            return "new"

        same = all(
            _normalize(values[name]) == _normalize(_request_value(existing, name))
            for name in REQUEST_FIELDS
        )
        if same:
            # Ничего не поменялось — отметку правки не двигаем. `source_row`
            # в сравнение не входит: перестановка колонок в таблице источника
            # не делает заявку другой.
            return "unchanged"

        updates = dict(values)
        updates["source_row"] = request.source_row
        updates["updated_at"] = to_iso(now)
        updates["external_id"] = request.external_id
        assignments = ", ".join(
            f"{name} = :{name}" for name in updates if name != "external_id"
        )
        with self.transaction():
            self.conn.execute(
                f"UPDATE requests SET {assignments} WHERE external_id = :external_id",
                updates,
            )
        return "updated"

    def iter_requests(self, status: str | None = "active") -> Iterator[Request]:
        query = "SELECT * FROM requests"
        params: tuple = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY external_id"
        for row in self.conn.execute(query, params):
            yield _row_to_request(row)

    def get_request(self, external_id: str) -> Request | None:
        row = self.conn.execute(
            "SELECT * FROM requests WHERE external_id = ?", (external_id,)
        ).fetchone()
        return _row_to_request(row) if row else None

    # --- матчи ------------------------------------------------------------
    def upsert_match(self, match: Match, now: datetime) -> str:
        """Пишет вычисленное и не трогает след звонка (решение 7).

        `status` и `reject_reason` в списке присвоений отсутствуют физически,
        а не «сохраняются по условию»: колонку, которой нет в UPDATE, нельзя
        затереть случайной правкой этого метода.
        """
        values = {name: _match_value(match, name) for name in MATCH_FIELDS}
        existing = self.conn.execute(
            "SELECT * FROM matches WHERE request_id = ? AND listing_id = ?",
            (match.request_id, match.listing_id),
        ).fetchone()

        if existing is None:
            values["request_id"] = match.request_id
            values["listing_id"] = match.listing_id
            values["status"] = match.status or "new"
            values["reject_reason"] = match.reject_reason
            values["first_matched_at"] = to_iso(now)
            values["matched_at"] = to_iso(now)
            columns = ", ".join(values)
            placeholders = ", ".join(f":{name}" for name in values)
            with self.transaction():
                self.conn.execute(
                    f"INSERT INTO matches ({columns}) VALUES ({placeholders})", values
                )
            return "new"

        same = all(
            _normalize(values[name]) == _normalize(existing[name])
            for name in MATCH_FIELDS
        )
        if same:
            # Балл и кластер те же — отметку пересчёта не двигаем: иначе
            # ночной пересчёт выглядел бы как обновление всех матчей разом.
            return "unchanged"

        updates = dict(values)
        updates["matched_at"] = to_iso(now)
        updates["id"] = existing["id"]
        assignments = ", ".join(f"{name} = :{name}" for name in updates if name != "id")
        with self.transaction():
            self.conn.execute(
                f"UPDATE matches SET {assignments} WHERE id = :id", updates
            )
        return "updated"

    def matches_for_request(self, request_id: int, min_score: float | None = None,
                            limit: int | None = None) -> list[Match]:
        """От лучшего к худшему; при равных баллах — по объявлению.

        Второй ключ сортировки не украшение: без него порядок выдачи зависит
        от того, в каком порядке sqlite прочитал страницы, и «первые пять»
        из двадцати одинаковых баллов каждый раз разные.
        """
        query = "SELECT * FROM matches WHERE request_id = ?"
        params: list = [request_id]
        if min_score is not None:
            query += " AND score >= ?"
            params.append(float(min_score))
        query += " ORDER BY score DESC, listing_id"
        if limit is not None:
            query += " LIMIT ?"
            params.append(int(limit))
        return [_row_to_match(row) for row in self.conn.execute(query, tuple(params))]

    def set_match_status(self, match_id: int, status: str,
                         reject_reason: str | None = None) -> None:
        with self.transaction():
            self.conn.execute(
                "UPDATE matches SET status = ?, reject_reason = ? WHERE id = ?",
                (status, reject_reason, int(match_id)),
            )

    def count_matches(self, request_id: int | None = None) -> int:
        query = "SELECT COUNT(*) AS n FROM matches"
        params: tuple = ()
        if request_id is not None:
            query += " WHERE request_id = ?"
            params = (request_id,)
        return int(self.conn.execute(query, params).fetchone()["n"])

    # --- журнал прогонов -------------------------------------------------
    def start_run(self, started_at: datetime, rate_amd_per_usd: float | None,
                  mode: str = "full") -> int:
        cursor = self.conn.execute(
            "INSERT INTO runs (started_at, rate_amd_per_usd, mode) VALUES (?, ?, ?)",
            (to_iso(started_at), rate_amd_per_usd, mode),
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
            "price_changed", "gone_marked", "returned", "errors", "notes", "last_page",
            "stop_reason",
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

    def last_run(self, mode: str | None = None) -> Run | None:
        if mode is None:
            row = self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM runs WHERE IFNULL(mode, 'full') = ? ORDER BY id DESC LIMIT 1",
                (mode,),
            ).fetchone()
        return _row_to_run(row) if row else None

    def crawl_to_resume(self) -> Run | None:
        """Прерванный обход по полной ленте: последний `full` плюс его `resume`.

        Брать одну строку журнала нельзя: `last_run(mode="full")` всегда отдаёт
        один и тот же оборванный полный прогон, и второе продолжение
        выбрасывает всё, что прошло первое.

        Поэтому отдаётся последняя строка обхода — по ней видно, закрыт он или
        оборвался, — с `last_page`, догнанным максимумом по всему обходу.
        `fresh` и `partial` сюда не входят: они шли не по полной ленте.
        """
        full = self.conn.execute(
            "SELECT * FROM runs WHERE IFNULL(mode, 'full') = 'full' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if full is None:
            return None
        last = self.conn.execute(
            "SELECT * FROM runs WHERE mode = 'resume' AND id > ? ORDER BY id DESC LIMIT 1",
            (full["id"],),
        ).fetchone()
        crawl = _row_to_run(last if last is not None else full)
        farthest = self.conn.execute(
            "SELECT MAX(IFNULL(last_page, 0)) AS page FROM runs "
            "WHERE id = :full OR (mode = 'resume' AND id > :full)",
            {"full": full["id"]},
        ).fetchone()
        crawl.last_page = int(farthest["page"] or 0)
        return crawl

    def last_successful_run(self) -> Run | None:
        """Последний полный обход, который дошёл до конца и не насчитал ошибок.

        Именно полный: укороченный `--max-pages`, продолженный `--resume` и
        инкрементальный `--fresh` видели не всю ленту, и мерить их числом
        страниц полноту следующего обхода — значит выключить проверку.
        """
        row = self.conn.execute(
            "SELECT * FROM runs WHERE finished_at IS NOT NULL AND IFNULL(errors, 0) = 0 "
            "AND IFNULL(mode, 'full') = 'full' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return _row_to_run(row) if row else None

    def last_run_that_could_mark_gone(self) -> Run | None:
        """Последний завершённый полный обход — мерка для раздела «Снято».

        Ошибки здесь не фильтруются намеренно: пометка снятых случается в конце
        обхода, а ошибка — чаще после неё, при заливке. Выкинув такой прогон,
        мы отдали бы окно предыдущему полному и показали бы снятых дважды.
        """
        row = self.conn.execute(
            "SELECT * FROM runs WHERE finished_at IS NOT NULL "
            "AND IFNULL(mode, 'full') = 'full' ORDER BY id DESC LIMIT 1"
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
        mode=row["mode"],
        price_changed=row["price_changed"] or 0,
        gone_marked=row["gone_marked"] or 0,
        returned=row["returned"] or 0,
        stop_reason=row["stop_reason"],
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
    data["gone_at"] = from_iso(data.get("gone_at"))
    data["returned_at"] = from_iso(data.get("returned_at"))
    return Listing(**{k: v for k, v in data.items() if k in Listing.field_names()})


def _join(values) -> str | None:
    """Список в TEXT-колонку. Пустой список и None — одно и то же: «не задано»."""
    if not values:
        return None
    return ",".join(str(value) for value in values)


def _split(raw: str | None, cast=str) -> list:
    if not raw:
        return []
    return [cast(part.strip()) for part in raw.split(",") if part.strip()]


def _request_value(request: Request, name: str):
    """Поле заявки в том виде, в каком оно лежит в базе: списки строкой, флаги числом."""
    value = getattr(request, name)
    if name in LIST_FIELDS:
        return _join(value)
    if name in FLAG_FIELDS:
        return _to_int(value)
    return value


def _row_to_request(row: sqlite3.Row) -> Request:
    data = dict(row)
    data["districts"] = _split(data.get("districts"))
    data["districts_priority"] = _split(data.get("districts_priority"))
    data["rooms"] = _split(data.get("rooms"), int)
    data["no_first_floor"] = bool(data.get("no_first_floor"))
    data["no_last_floor"] = bool(data.get("no_last_floor"))
    data["created_at"] = from_iso(data.get("created_at"))
    data["updated_at"] = from_iso(data.get("updated_at"))
    known = {f.name for f in dataclass_fields(Request)}
    return Request(**{k: v for k, v in data.items() if k in known})


def _match_value(match: Match, name: str):
    """Поле матча в том виде, в каком оно лежит в базе: разбор балла — JSON."""
    value = getattr(match, name)
    if name == "breakdown":
        # `sort_keys` — чтобы одинаковый разбор давал одинаковую строку:
        # иначе перестановка ключей в словаре выглядела бы как правка балла.
        return json.dumps(value, ensure_ascii=False, sort_keys=True) if value else None
    if name == "cluster_size":
        return int(value or 1)
    return value


def _row_to_match(row: sqlite3.Row) -> Match:
    """Строка базы в матч. Кортежи `(набрано, вес)` возвращаются списками.

    Приводить их обратно к кортежам не надо: в базе разбор балла — данные
    для человека, а не структура, по которой считают.
    """
    return Match(
        id=row["id"],
        request_id=row["request_id"],
        listing_id=row["listing_id"],
        score=row["score"],
        matched_at=from_iso(row["matched_at"]),
        first_matched_at=from_iso(row["first_matched_at"]),
        status=row["status"] or "new",
        reject_reason=row["reject_reason"],
        run_id=row["run_id"],
        breakdown=json.loads(row["breakdown"]) if row["breakdown"] else None,
        cluster_id=row["cluster_id"],
        cluster_size=row["cluster_size"] or 1,
        cluster_spread_usd=row["cluster_spread_usd"],
    )
