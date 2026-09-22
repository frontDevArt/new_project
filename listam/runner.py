"""Каркас оркестрации: то общее, что делают все команды, пишущие в базу.

Замок → свежая копия из хранилища → открыть → накатить миграции → проверить,
что схема не старше кода. На выходе — закрыть и отпустить замок. Пять команд
(`scrape`, `recheck`, `cluster`, `requests`, `match`) писали это пятью
копиями, и копии уже разошлись: одна ловила `OSError` там, где другие ловили
`Exception`, и падала трейсбеком на битом файле; другая звала `storage.upload`
вне `try` и теряла отчёт о проделанной работе из-за отказа сети.

Отчёты команд каркас не трогает: что считать ошибкой и что печатать человеку,
каждая команда решает сама. Каркас только называет причину отказа и считает
свои неудачи (`Session.failures`) — команда прибавляет их к своим ошибкам.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from listam.adapters.db_sqlite import latest_schema_version
from listam.adapters.run_lock import LockBusy
from listam.config import Config
from listam.crawler import rotate_backups, take_the_fresher_copy
from listam.ports.database import Database
from listam.ports.storage import Storage
from listam.wiring import build_database, build_run_lock, build_storage, database_path

DEFAULT_KEEP_BACKUPS = 5


class SessionRefused(Exception):
    """Работать нельзя: замок занят, файла нет, база бита или схема старая."""


@dataclass
class Session:
    """Открытая база под замком и всё, что нужно, чтобы залить её обратно."""

    database: Database
    storage: Storage
    local_db: Path
    remote_name: str
    notes: list[str] = field(default_factory=list)
    closed: bool = False
    failures: int = 0        # неудачи заливки: команда прибавит их к своим ошибкам


@contextmanager
def working_session(config: Config, *, needs_schema: bool = True) -> Iterator[Session]:
    """Открытая база под замком. Отказ — `SessionRefused` с внятной причиной.

    `needs_schema=False` — для команды, которая как раз миграции и катит:
    отказ «накати миграции: python -m listam recheck» самому `recheck`
    запер бы базу насовсем.
    """
    lock = build_run_lock(config)
    try:
        lock.acquire()
    except LockBusy as exc:
        raise SessionRefused(str(exc)) from exc

    local_db = database_path(config)
    remote_name = config.get("storage.db_filename", "listam.sqlite")
    database = None
    try:
        try:
            storage = build_storage(config)
            remote = take_the_fresher_copy(storage, remote_name, local_db)
            database = build_database(config)
            database.connect()
            database.migrate()
        except Exception as exc:       # OSError, sqlite3.Error — базы нет или бита
            raise SessionRefused(f"файл базы недоступен: {exc}") from exc

        if needs_schema:
            required = latest_schema_version()
            version = database.schema_version()
            if version < required:
                raise SessionRefused(
                    f"схема базы {version}, а код ждёт {required}. "
                    f"Команда ничего не мигрирует — накати миграции: "
                    f"python -m listam recheck"
                )

        session = Session(database=database, storage=storage, local_db=local_db,
                          remote_name=remote_name)
        if remote.note:
            session.notes.append(remote.note)
        yield session
    finally:
        if database is not None:
            database.close()
        lock.release()


def publish(session: Session, config: Config, what: str) -> bool:
    """Снимок → ротация копий → заливка. Отдаёт, закрыта ли база.

    Заливать надо именно снимок, а не файл, в который ещё пишут. Отказ сети
    после записи — строка отчёта, а не трейсбек: база уже сохранена, и работа
    не пропала.
    """
    snapshot = session.local_db.with_name(session.local_db.name + ".snapshot")
    try:
        session.database.snapshot(snapshot)
    except Exception as exc:       # sqlite3.Error, OSError — заливать нечего
        session.failures += 1
        session.notes.append(f"снимок базы не сделан: {exc}")
        return False
    session.database.close()
    session.closed = True
    try:
        rotate_backups(
            session.storage, session.remote_name,
            int(config.get("storage.keep_backups", DEFAULT_KEEP_BACKUPS) or 0),
            Path(session.local_db).parent,
        )
        session.storage.upload(snapshot, session.remote_name)
    except Exception as exc:       # OSError, ошибки Google API — сеть отказала
        session.failures += 1
        session.notes.append(f"база не залита в хранилище: {exc}")
        snapshot.unlink(missing_ok=True)
        return True
    snapshot.unlink(missing_ok=True)
    session.notes.append(f"{what} залита в хранилище")
    return True
