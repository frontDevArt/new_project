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

from listam.config import Config, threshold
from listam.domain.clustering import DEFAULT_AREA_TOLERANCE, assign, clusters, \
    normalize_street
from listam.ports.database import Database
from listam.runner import SessionRefused, publish, working_session


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


def cluster_database(database: Database, tolerance: float,
                     listings: list | None = None) -> ClusterReport:
    """Пересчёт по уже открытой базе: им пользуется и команда, и матчинг.

    Замка и заливки здесь нет нарочно: матчинг в фазе 5 зовёт это изнутри
    своего замка, и второй замок на том же файле означал бы затор.

    `listings` — уже прочитанная выборка. Подбор читает таблицу один раз и
    отдаёт её сюда: на 20 826 строках каждое лишнее чтение стоит больше
    секунды, а за прогон их набиралось три.
    """
    report = ClusterReport()
    listings = database.listings_for_matching() if listings is None else listings
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
    """Один проход по всей базе. Сводку печатает вызывающий.

    Замок, свежая копия, миграции и заливка — общий каркас (`listam/runner.py`):
    тот же порядок, что у прогона, пересчёта, заявок и подбора. Здесь остаётся
    только то, что у команды своё: что считать и когда это заливать.
    """
    report = ClusterReport()
    notes: list[str] = []
    try:
        # Схема здесь не проверяется — и не проверялась: команда сама катит
        # миграции, а пересчёт кластеров читает те колонки, что есть с M1.
        with working_session(config, needs_schema=False) as session:
            notes.extend(session.notes)
            session.notes = notes     # заливка пишет в тот же список

            counted = cluster_database(session.database, area_tolerance(config))
            counted.errors, counted.notes = report.errors, report.notes
            report = counted

            # Заливаем только когда что-то изменилось: подменять общую копию
            # ради нулевой правки — значит зря гонять сеть и ротацию.
            if report.changed:
                publish(session, config, "база с кластерами")
                report.errors += session.failures
    except SessionRefused as exc:
        report.errors += 1
        notes.append(str(exc))
    finally:
        report.notes = "; ".join(note for note in notes if note)
        report.finished_at = datetime.now(timezone.utc)
    return report
