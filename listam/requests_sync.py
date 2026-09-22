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

from listam.config import Config
from listam.domain.requests import RequestError
from listam.runner import SessionRefused, publish, working_session
from listam.wiring import build_requests_source


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

    # Замок, свежая копия, миграции и заливка — общий каркас
    # (`listam/runner.py`): тот же порядок, что у прогона, пересчёта,
    # кластеров и подбора.
    notes: list[str] = []
    try:
        with working_session(config) as session:
            notes.extend(session.notes)
            session.notes = notes     # заливка пишет в тот же список
            database = session.database

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
                publish(session, config, "база с заявками")
                report.errors += session.failures
    except SessionRefused as exc:
        report.errors += 1
        notes.append(str(exc))
    finally:
        report.notes = "; ".join(notes) or None
        report.finished_at = datetime.now(timezone.utc)
    return report
