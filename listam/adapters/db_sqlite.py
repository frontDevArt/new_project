"""Реализация Database поверх SQLite. Один файл — вся база, его удобно возить."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from listam.domain.models import Listing, PricePoint, Run
from listam.ports.database import Database

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# Поля, изменение которых считаем содержательным обновлением карточки
TRACKED_FIELDS = (
    "title", "district", "street", "price_raw", "currency", "price_usd", "price_amd",
    "area", "rooms", "floor", "floors_total", "seller_type", "verified", "new_build",
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
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

    # --- жизненный цикл -------------------------------------------------
    def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        if self._conn:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("База не открыта: сначала connect()")
        return self._conn

    # --- схема ----------------------------------------------------------
    def migrate(self) -> None:
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            " version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, name TEXT)"
        )
        applied = {
            row["version"] for row in self.conn.execute("SELECT version FROM schema_version")
        }
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = int(path.name.split("_", 1)[0])
            if version in applied:
                continue
            self.conn.executescript(path.read_text(encoding="utf-8"))
            self.conn.execute(
                "INSERT INTO schema_version(version, applied_at, name) VALUES (?, ?, ?)",
                (version, to_iso(datetime.now(timezone.utc)), path.name),
            )
        self.conn.commit()

    def schema_version(self) -> int:
        row = self.conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        return int(row["v"] or 0)

    def table_names(self) -> set[str]:
        rows = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {row["name"] for row in rows}

    # --- объявления -----------------------------------------------------
    def upsert_listing(self, listing: Listing, seen_at: datetime) -> str:
        existing = self.get_listing(listing.id)
        seen_iso = to_iso(seen_at)
        if existing is None:
            values = {name: getattr(listing, name) for name in Listing.field_names()}
            values["first_seen"] = seen_iso
            values["last_seen"] = seen_iso
            values["status"] = listing.status or "active"
            values["verified"] = _to_int(listing.verified)
            values["new_build"] = _to_int(listing.new_build)
            columns = ", ".join(values)
            placeholders = ", ".join(f":{name}" for name in values)
            self.conn.execute(
                f"INSERT INTO listings ({columns}) VALUES ({placeholders})", values
            )
            self._add_price_point(listing.id, seen_at, listing.price_usd)
            self.conn.commit()
            return "new"

        changed = [
            name for name in TRACKED_FIELDS
            if _normalize(getattr(listing, name)) != _normalize(getattr(existing, name))
        ]
        updates = {name: getattr(listing, name) for name in TRACKED_FIELDS}
        updates["verified"] = _to_int(listing.verified)
        updates["new_build"] = _to_int(listing.new_build)
        updates["price_per_sqm"] = listing.price_per_sqm
        updates["last_seen"] = seen_iso
        updates["status"] = "active"
        updates["id"] = listing.id
        assignments = ", ".join(f"{name} = :{name}" for name in updates if name != "id")
        self.conn.execute(f"UPDATE listings SET {assignments} WHERE id = :id", updates)

        outcome = "unchanged"
        if "price_usd" in changed:
            self._add_price_point(listing.id, seen_at, listing.price_usd)
            outcome = "price_changed"
        elif changed:
            outcome = "updated"
        self.conn.commit()
        return outcome

    def _add_price_point(
        self, listing_id: str, seen_at: datetime, price_usd: float | None
    ) -> None:
        self.conn.execute(
            "INSERT INTO price_history (listing_id, seen_at, price_usd) VALUES (?, ?, ?)",
            (listing_id, to_iso(seen_at), price_usd),
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

    def finish_run(self, run_id: int, finished_at: datetime, **counters) -> None:
        allowed = {
            "pages_fetched", "listings_seen", "new_listings", "updated_listings",
            "errors", "notes",
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
        if not row:
            return None
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
        )


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
