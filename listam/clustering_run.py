"""Пересчёт кластеров-дублей по всей базе.

Кластер — единица показа: клиенту нельзя звонить дважды про одну квартиру.
Считает кластеры домен (`listam/domain/clustering.py`), здесь — оркестрация:
взять замок, забрать базу посвежее, прочитать выборку, записать `cluster_id`
и сказать человеку числа.

Числа не украшение. «Кластеров 18 400» без «объявлений без улицы 4 224»
читается как поломка дедупа, хотя это дырка в данных: улицы нет у пятой
части базы, и такое объявление по решению 4 остаётся само по себе.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from listam.adapters.run_lock import LockBusy
from listam.config import Config, threshold
from listam.crawler import rotate_backups, take_the_fresher_copy
from listam.domain.clustering import DEFAULT_AREA_TOLERANCE, assign, clusters, \
    normalize_street
from listam.ports.database import Database
from listam.wiring import build_database, build_run_lock, build_storage, database_path

DEFAULT_KEEP_BACKUPS = 5


@dataclass
class ClusterReport:
    """Что сделал пересчёт кластеров. Печатает это CLI, а не сам пересчёт."""

    listings: int = 0
    clusters: int = 0
    multi: int = 0              # кластеров больше одного члена
    largest: int = 0            # размер самого большого кластера
    without_street: int = 0     # объявлений без улицы: кластеризованы поодиночке
    changed: int = 0
    errors: int = 0
    notes: str = ""
    finished_at: datetime | None = None

    def render(self) -> str:
        share = f"{self.without_street / self.listings * 100:.1f}%" if self.listings else "0%"
        lines = [
            f"Кластеры: объявлений {self.listings}, кластеров {self.clusters}, "
            f"из них многочленных {self.multi}, крупнейший {self.largest}",
            f"Без улицы: {self.without_street} ({share}) — каждое такое объявление "
            f"кластер из самого себя",
            f"Изменено строк: {self.changed}",
        ]
        if self.notes:
            lines.append(self.notes)
        return "\n".join(lines)


def area_tolerance(config: Config) -> float:
    """Допуск объединения площадей. Порог из конфига, ноль значит ноль."""
    value = threshold(config, "match.cluster.area_tolerance", DEFAULT_AREA_TOLERANCE)
    return DEFAULT_AREA_TOLERANCE if value is None else float(value)


def cluster_database(database: Database, tolerance: float) -> ClusterReport:
    """Пересчёт по уже открытой базе: им пользуется и команда, и матчинг.

    Замка и заливки здесь нет нарочно: матчинг в фазе 5 зовёт это изнутри
    своего замка, и второй замок на том же файле означал бы затор.
    """
    report = ClusterReport()
    listings = database.listings_for_matching()
    report.listings = len(listings)
    report.without_street = sum(
        1 for item in listings if normalize_street(item.street) is None
    )

    found = clusters(listings, tolerance)
    report.clusters = len(found)
    report.multi = sum(1 for cluster in found if cluster.size > 1)
    report.largest = max((cluster.size for cluster in found), default=0)
    report.changed = database.set_cluster_ids(assign(listings, tolerance))
    return report


def run_clustering(config: Config) -> ClusterReport:
    """Один проход по всей базе. Сводку печатает вызывающий."""
    report = ClusterReport()

    # Тот же замок, что у прогона и пересчёта: команда переписывает общую
    # базу, и делать это под идущим обходом — значит соревноваться за файл.
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

        counted = cluster_database(database, area_tolerance(config))
        counted.errors, counted.notes = report.errors, report.notes
        report = counted

        if report.changed:
            snapshot = local_db.with_name(local_db.name + ".snapshot")
            try:
                database.snapshot(snapshot)
            except Exception as exc:   # sqlite3.Error, OSError — заливать нечего
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
                notes.append("база с кластерами залита в хранилище")
        return report
    finally:
        if database is not None:
            database.close()
        lock.release()
        report.notes = "; ".join(note for note in notes if note)
        report.finished_at = datetime.now(timezone.utc)
