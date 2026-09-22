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

from listam.config import Config
from listam.domain.money import parse_price
from listam.domain.validate import detect_anomalies, rules_from
from listam.runner import SessionRefused, publish, working_session


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
    notes: list[str] = []
    try:
        # Схема здесь не проверяется: `recheck` — та самая команда, которой
        # остальные советуют накатить миграции. Отказ по старой схеме запер
        # бы базу насовсем.
        with working_session(config) as session:
            notes.extend(session.notes)
            session.notes = notes     # заливка пишет в тот же список
            database = session.database

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
                        database.set_computed(listing.id, anomaly=anomaly,
                                              price_amount=amount)

            notes.append(
                f"схема {database.schema_version()}, строк {report.listings}, "
                f"помечено {report.marked}, сумма в валюте оригинала у {report.amounts}, "
                f"изменено строк {report.changed}"
            )

            # Снимок и заливка — по правилам прогона: в хранилище уезжает копия,
            # в которой пересчёт уже сделан, и только если он прошёл без ошибок.
            if report.errors:
                notes.append("база не залита: в пересчёте были ошибки")
            else:
                publish(session, config, "пересчитанная база")
                report.errors += session.failures
    except SessionRefused as exc:
        report.errors = 1
        notes.append(str(exc))
    finally:
        report.notes = "; ".join(note for note in notes if note)
        report.finished_at = datetime.now(timezone.utc)
    return report
