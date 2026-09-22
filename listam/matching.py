"""Подбор объявлений под заявки: три выборки, один проход.

Оркестрация по образцу `listam/changes.py` и `listam/clustering_run.py`:
решают домены (`clustering`, `stats`, `scoring`), здесь — кто с кем
сравнивается и что из этого записывается.

Три вещи, которые здесь важнее скорости:

- **Кластеры считаются по всей базе, а не по выборке.** Иначе `--new` не
  узнает, что у свежего объявления уже есть тридцать двойников, и клиент
  получит тридцать первый звонок про ту же квартиру.
- **Отказы не пишутся.** Пара «заявка × объявление» в боевой базе даёт
  миллион строк, и девятьсот девяносто тысяч из них — «район не тот».
  В базе нужны те, по которым можно звонить.
- **Пересчёт не трогает след звонка.** `status` и `reject_reason` пишет
  только человек (решение 7); `upsert_match` их не перечисляет вовсе.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from listam.adapters.db_sqlite import latest_schema_version
from listam.adapters.run_lock import LockBusy
from listam.changes import since_point
from listam.clustering_run import area_tolerance, cluster_database
from listam.config import Config, threshold
from listam.crawler import rotate_backups, take_the_fresher_copy
from listam.domain.clustering import clusters
from listam.domain.models import Match, Request
from listam.domain.scoring import DEFAULT_STRETCH_PERCENT, DEFAULT_WEIGHTS, score
from listam.domain.stats import median_price_per_sqm_by_district
from listam.ports.database import Database
from listam.wiring import build_database, build_run_lock, build_storage, database_path

DEFAULT_KEEP_BACKUPS = 5
DEFAULT_HOT = 70.0
DEFAULT_DIGEST = 40.0


@dataclass
class MatchReport:
    """Что сделал подбор. Печатает это CLI, а не сам подбор."""

    scope: str = ""             # «заявка R-1», «новые объявления…», «вся база»
    requests: int = 0
    listings: int = 0           # объявлений в выборке
    considered: int = 0         # из них представителей кластеров
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    hot: int = 0                # score >= match.thresholds.hot
    digest: int = 0             # hot > score >= digest
    errors: int = 0
    notes: str | None = None
    finished_at: datetime | None = None

    def render(self) -> str:
        lines = [
            f"Подбор: {self.scope}",
            f"Заявок: {self.requests}, объявлений в выборке: {self.listings}, "
            f"из них представителей кластеров: {self.considered}",
            f"Матчи: новых {self.new}, обновлённых {self.updated}, "
            f"без изменений {self.unchanged}",
            f"Из них горячих: {self.hot}, в дайджест: {self.digest}",
        ]
        if self.notes:
            lines.append(self.notes)
        return "\n".join(lines)


@dataclass
class Settings:
    """Веса и пороги подбора — из конфига, а не из кода."""

    weights: dict[str, float]
    stretch_percent: float
    hot: float | None
    digest: float | None


def settings(config: Config) -> Settings:
    """Читает секцию `match`. Ноль значит ноль, `null` — выключено."""
    weights = config.get("match.weights", None)
    stretch = threshold(config, "match.budget_stretch_percent", DEFAULT_STRETCH_PERCENT)
    hot = threshold(config, "match.thresholds.hot", DEFAULT_HOT)
    digest = threshold(config, "match.thresholds.digest", DEFAULT_DIGEST)
    return Settings(
        weights=dict(weights) if weights else dict(DEFAULT_WEIGHTS),
        # Растяжка выключена — значит не растягиваем вовсе, а не «берём
        # десять процентов по умолчанию»: человек сказал «нет», а не промолчал.
        stretch_percent=0.0 if stretch is None else float(stretch),
        hot=None if hot is None else float(hot),
        digest=None if digest is None else float(digest),
    )


def _requests_to_match(database: Database, external_id: str | None
                       ) -> tuple[list[Request], str | None]:
    """Заявки выборки и причина, если выборки нет.

    Заявка, названная руками, обязана быть активной: «подобрал ноль» на
    приостановленной заявке — это не ответ, а молчание.
    """
    if external_id is None:
        return list(database.iter_requests()), None

    request = database.get_request(external_id)
    if request is None:
        return [], f"заявки {external_id} в базе нет — сначала прочитай источник: " \
                   f"python -m listam requests"
    if request.status != "active":
        return [], f"заявка {external_id} со статусом {request.status}: " \
                   f"матчатся только active"
    return [request], None


def _listings_scope(database: Database, only_new: bool, config: Config
                    ) -> tuple[list, str]:
    """Выборка объявлений и как она называется по-человечески."""
    if not only_new:
        return database.listings_for_matching(), "вся база"
    # Мерка та же, что у `changes`: начало последнего прогона. Второй мерки
    # для «нового» заводить незачем — это тот же вопрос, что и там.
    mark, note = since_point(
        database, None, fallback_hours=config.get("changes.fallback_hours", 24)
    )
    return database.listings_for_matching(since=mark), f"новые объявления: {note}"


def run_match(config: Config, *, external_id: str | None = None,
              only_new: bool = False, recount_all: bool = False) -> MatchReport:
    """Один проход подбора. Сводку печатает вызывающий.

    `recount_all` выборку не меняет и не должен: «все активные заявки по
    всей базе» — это и есть подбор без сужений. Флаг существует, чтобы
    полный пересчёт нельзя было запустить молчанием: команда без флагов
    отклоняется кодом 2, а не истолковывается как «пересчитай всё».
    """
    report = MatchReport(scope="заявка " + external_id if external_id else "вся база")

    # Тот же замок, что у прогона и пересчёта: подбор переписывает общую базу
    # и может по дороге проставить кластеры.
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

        required = latest_schema_version()
        version = database.schema_version()
        if version < required:
            report.errors = 1
            notes.append(
                f"Подбор не сделан: схема базы {version}, а код ждёт {required}. "
                f"Команда ничего не мигрирует — накати миграции: "
                f"python -m listam recheck"
            )
            return report

        requests, refusal = _requests_to_match(database, external_id)
        if refusal is not None:
            report.errors = 1
            notes.append(refusal)
            return report
        report.requests = len(requests)
        if not requests:
            # Не ошибка: заявок может не быть ещё или уже. Но и не тишина —
            # пустой подбор обязан сказать, почему он пустой.
            notes.append("подбирать не под что: нет активных заявок")
            return report

        _count_clusters_if_needed(database, config, notes)

        # Кластеры считаются по всей базе, а не по выборке: у свежего
        # объявления двойники могли появиться задолго до него.
        everything = database.listings_for_matching()
        found = clusters(everything, area_tolerance(config))
        representatives = {cluster.cheapest_id: cluster for cluster in found}
        medians = median_price_per_sqm_by_district(everything)

        selection, scope = _listings_scope(database, only_new, config)
        report.scope = f"заявка {external_id}" if external_id else scope
        report.listings = len(selection)
        candidates = [item for item in selection if item.id in representatives]
        report.considered = len(candidates)

        last_run = database.last_run()
        run_id = last_run.id if last_run else None
        _write_matches(database, report, requests, candidates, representatives,
                       medians, run_id, settings(config))

        if report.new or report.updated:
            if _upload(config, database, storage, local_db, remote_name,
                       report, notes):
                database = None    # заливка закрыла базу: снимок делается с живой
        return report
    finally:
        if database is not None:
            database.close()
        lock.release()
        report.notes = "; ".join(note for note in notes if note) or None
        report.finished_at = datetime.now(timezone.utc)


def _count_clusters_if_needed(database: Database, config: Config,
                              notes: list[str]) -> None:
    """Кластеры перед подбором, если в базе есть непроставленные.

    Спека: пересчёт идёт автоматически перед матчингом. Команду `cluster`
    никто не обязан помнить, а без кластеров клиент получает одну квартиру
    тридцать раз. Замка здесь второго нет: `cluster_database` работает по
    уже открытой базе — ровно для этого он и отделён от команды.
    """
    if not any(item.cluster_id is None for item in database.listings_for_matching()):
        return
    counted = cluster_database(database, area_tolerance(config))
    notes.append(
        f"кластеры пересчитаны: объявлений {counted.listings}, "
        f"кластеров {counted.clusters}, изменено строк {counted.changed}"
    )


def _write_matches(database: Database, report: MatchReport, requests, candidates,
                   representatives: dict, medians: dict[str, float],
                   run_id: int | None, tuning: Settings) -> None:
    """Пара «заявка × представитель» → балл → строка в `matches`."""
    now = datetime.now(timezone.utc)
    for request in requests:
        for listing in candidates:
            result = score(request, listing, median_by_district=medians,
                           weights=tuning.weights,
                           stretch_percent=tuning.stretch_percent)
            if result.rejected_by is not None:
                # Отказ в базу не пишется: их миллионы, и звонить по ним некуда.
                continue
            cluster = representatives[listing.id]
            outcome = database.upsert_match(Match(
                request_id=request.id,
                listing_id=listing.id,
                score=float(result.value),
                run_id=run_id,
                breakdown=result.breakdown or None,
                cluster_id=cluster.cluster_id,
                cluster_size=cluster.size,
                cluster_spread_usd=cluster.spread_usd,
            ), now)
            setattr(report, outcome, getattr(report, outcome) + 1)
            if tuning.hot is not None and result.value >= tuning.hot:
                report.hot += 1
            elif tuning.digest is not None and result.value >= tuning.digest:
                report.digest += 1


def _upload(config: Config, database: Database, storage, local_db: Path,
            remote_name: str, report: MatchReport, notes: list[str]) -> bool:
    """Снимок базы в хранилище; отдаёт, закрыта ли база.

    Порядок тот же, что у прогона и пересчёта: снимок с живой базы, ротация
    копий, заливка. Закрывать базу здесь приходится потому, что залить надо
    именно снимок, а не файл, в который ещё пишут.
    """
    snapshot = local_db.with_name(local_db.name + ".snapshot")
    try:
        database.snapshot(snapshot)
    except Exception as exc:       # sqlite3.Error, OSError — заливать нечего
        report.errors += 1
        notes.append(f"снимок базы не сделан: {exc}")
        return False
    database.close()
    rotate_backups(
        storage,
        remote_name,
        int(config.get("storage.keep_backups", DEFAULT_KEEP_BACKUPS) or 0),
        Path(local_db).parent,
    )
    storage.upload(snapshot, remote_name)
    snapshot.unlink(missing_ok=True)
    notes.append("база с матчами залита в хранилище")
    return True
