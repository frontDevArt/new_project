"""Пересчёт помеченного по всей базе.

Правила проверки, колонка `anomaly` и сумма в валюте оригинала появились
позже, чем была набрана база: в боевой копии двадцать тысяч строк, у которых
этих полей нет вовсе. Прогон такую базу не чинит — он ходит по ленте и трогает
только те карточки, что встретил сегодня, а карточка, снятая с публикации,
не встретится уже никогда.

Поэтому отдельная команда: взять замок, накатить миграции, пересчитать по всем
строкам то, что считается из уже записанного, и показать сводку. Цены, даты и
историю пересчёт не трогает — он ничего не узнаёт о рынке, он только доводит
базу до того вида, который код ожидает.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from listam.adapters.run_lock import LockBusy
from listam.config import Config
from listam.crawler import rotate_backups, take_the_fresher_copy
from listam.domain.money import parse_price
from listam.domain.validate import detect_anomalies, rules_from
from listam.wiring import build_database, build_run_lock, build_storage, database_path

DEFAULT_KEEP_BACKUPS = 5


@dataclass
class Recheck:
    """Что сделал пересчёт. Печатает это CLI, а не сам пересчёт."""

    listings: int = 0
    marked: int = 0          # строк с непустым anomaly после пересчёта
    changed: int = 0         # строк, у которых что-то изменилось
    amounts: int = 0         # строк с суммой в валюте оригинала
    errors: int = 0
    notes: str = ""
    finished_at: datetime | None = None


def amount_from_raw(price_raw: str | None, current: float | None) -> float | None:
    """Сумма в валюте оригинала из сырой цены. Не разобралась — оставляем как было.

    Сырая строка — это то, что было на странице; число из неё выводится
    однозначно. Пустой разбор означает «не смог прочитать», а не «цены нет»,
    и затирать им уже записанное нельзя.
    """
    parsed = parse_price(price_raw)
    return parsed.amount if parsed.amount is not None else current


def run_recheck(config: Config) -> Recheck:
    """Один проход по всей базе. Возвращает сводку — печатает её вызывающий."""
    report = Recheck()

    # Тот же замок, что у прогона: пересчёт переписывает общую базу, и делать
    # это под идущим обходом — значит соревноваться с ним за один файл.
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
        except OSError as exc:
            report.errors = 1
            notes.append(f"файл базы недоступен: {exc}")
            return report

        rules = rules_from(config)
        with database.transaction():
            for listing in list(database.iter_listings()):
                report.listings += 1
                anomaly = detect_anomalies(listing, rules)
                amount = amount_from_raw(listing.price_raw, listing.price_amount)
                if anomaly:
                    report.marked += 1
                if amount is not None:
                    report.amounts += 1
                if anomaly != listing.anomaly or amount != listing.price_amount:
                    report.changed += 1
                    database.set_computed(listing.id, anomaly=anomaly, price_amount=amount)

        notes.append(
            f"схема {database.schema_version()}, строк {report.listings}, "
            f"помечено {report.marked}, сумма в валюте оригинала у {report.amounts}, "
            f"изменено строк {report.changed}"
        )

        # Снимок и заливка — по правилам прогона: в хранилище уезжает копия,
        # в которой пересчёт уже сделан, и только если он прошёл без ошибок.
        snapshot = local_db.with_name(local_db.name + ".snapshot")
        try:
            database.snapshot(snapshot)
        except Exception as exc:       # sqlite3.Error, OSError — заливать нечего
            report.errors += 1
            notes.append(f"снимок базы не сделан: {exc}")
            snapshot = None
        database.close()
        database = None

        if snapshot is not None:
            if report.errors:
                notes.append("база не залита: в пересчёте были ошибки")
            else:
                rotate_backups(
                    storage,
                    remote_name,
                    int(config.get("storage.keep_backups", DEFAULT_KEEP_BACKUPS) or 0),
                    Path(local_db).parent,
                )
                storage.upload(snapshot, remote_name)
                notes.append("пересчитанная база залита в хранилище")
            snapshot.unlink(missing_ok=True)
        return report
    finally:
        if database is not None:
            database.close()
        lock.release()
        report.notes = "; ".join(note for note in notes if note)
        report.finished_at = datetime.now(timezone.utc)
