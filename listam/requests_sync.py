"""Чтение заявок из источника в базу.

Команда ничего не решает про рынок: она забирает строки у источника, отдаёт
их общему доменному разбору и кладёт разобранное в базу. Отклонённые строки
перечисляются человеку и в базу не попадают (решение 2 спеки): одна опечатка
в бюджете не имеет права оставить брокера без остальных заявок и не имеет
права быть истолкованной.

Граница между «опечатка» и «сбой» проходит здесь: часть строк не разобрана —
это предупреждение и код 0; не разобрана ни одна при непустой таблице —
это уехавший формат, и это код 1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from listam.adapters.db_sqlite import latest_schema_version
from listam.adapters.run_lock import LockBusy
from listam.config import Config
from listam.crawler import rotate_backups, take_the_fresher_copy
from listam.domain.requests import RequestError
from listam.wiring import build_database, build_requests_source, build_run_lock, \
    build_storage, database_path

DEFAULT_KEEP_BACKUPS = 5


@dataclass
class SyncReport:
    """Что сделало чтение заявок. Печатает это CLI, а не сам синхронизатор."""

    source: str = ""
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    closed: int = 0             # заявок закрыто: строки в источнике больше нет
    closed_ids: list[str] = field(default_factory=list)
    rejected: list[RequestError] = field(default_factory=list)
    errors: int = 0
    notes: str | None = None
    finished_at: datetime | None = None

    def render(self) -> str:
        lines = [f"Источник: {self.source}"]
        if self.notes:
            lines.append(self.notes)
        lines.append(
            f"Заявки: новых {self.new}, обновлённых {self.updated}, "
            f"без изменений {self.unchanged}, закрытых {self.closed}"
        )
        if self.closed_ids:
            lines.append(f"  закрыты (нет в источнике): {', '.join(self.closed_ids)}")
        if self.rejected:
            lines.append(f"⚠ Не разобрано: {len(self.rejected)}")
            lines.extend(f"  {item.render()}" for item in self.rejected)
        return "\n".join(lines)


def run_requests_sync(config: Config) -> SyncReport:
    """Один проход: источник → домен → база. Сводку печатает вызывающий."""
    report = SyncReport()

    # Источник читается до базы, и это не порядок ради порядка: недоступный
    # источник обязан оставить базу нетронутой, а не уронить команду посреди
    # записи.
    try:
        source = build_requests_source(config)
        report.source = source.describe()
        parsed, rejected = source.read()
    except Exception as exc:
        report.source = report.source or "не собран"
        report.errors = 1
        report.notes = f"Заявки не прочитаны: {exc}"
        report.finished_at = datetime.now(timezone.utc)
        return report

    report.rejected = list(rejected)
    if rejected and not parsed:
        # Ни одна строка не разобралась при непустой таблице: это не полсотни
        # опечаток подряд, это уехавший формат таблицы.
        report.errors = 1
        report.notes = (
            f"Не разобрана ни одна строка из {len(rejected)}: похоже, формат таблицы "
            f"не тот — колонки переименованы или переставлены. Ожидаются колонки "
            f"из образца; заявки не записаны."
        )
        report.finished_at = datetime.now(timezone.utc)
        return report

    lock = build_run_lock(config)
    try:
        lock.acquire()
    except LockBusy as exc:
        report.errors = 1
        report.notes = str(exc)
        report.finished_at = datetime.now(timezone.utc)
        return report

    database = None
    notes: list[str] = []
    local_db = database_path(config)
    remote_name = config.get("storage.db_filename", "listam.sqlite")
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
                f"Заявки не записаны: схема базы {version}, а код ждёт {required}. "
                f"Накати миграции: python -m listam recheck"
            )
            return report

        now = datetime.now(timezone.utc)
        for request in parsed:
            # Транзакцию на строку открывает сам `upsert_request`: обёртка
            # снаружи означала бы вложенный BEGIN, а его sqlite не допускает.
            outcome = database.upsert_request(request, now)
            setattr(report, outcome, getattr(report, outcome) + 1)

        # Пустой источник — почти всегда сбой доступа, а не «все клиенты ушли».
        # Закрыть по нему всю базу заявок означало бы потерять работу месяца
        # из-за одной недоступной таблицы.
        if parsed:
            present = {request.external_id for request in parsed}
            before = {item.external_id for item in database.iter_requests()}
            report.closed = database.close_requests_missing_from(present, now)
            report.closed_ids = sorted(before - present)
        else:
            notes.append("источник не отдал ни одной заявки — "
                         "ничего не закрываем, это похоже на сбой доступа")

        if report.new or report.updated or report.closed:
            snapshot = local_db.with_name(local_db.name + ".snapshot")
            try:
                database.snapshot(snapshot)
            except Exception as exc:    # sqlite3.Error, OSError — заливать нечего
                report.errors += 1
                notes.append(f"снимок базы не сделан: {exc}")
                snapshot = None
            database.close()
            database = None
            if snapshot is not None:
                rotate_backups(
                    storage,
                    remote_name,
                    int(config.get("storage.keep_backups", DEFAULT_KEEP_BACKUPS) or 0),
                    Path(local_db).parent,
                )
                storage.upload(snapshot, remote_name)
                snapshot.unlink(missing_ok=True)
                notes.append("база с заявками залита в хранилище")
        return report
    finally:
        if database is not None:
            database.close()
        lock.release()
        report.notes = "; ".join(notes) or None
        report.finished_at = datetime.now(timezone.utc)
