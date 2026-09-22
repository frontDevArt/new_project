"""Уведомление: окно, текст сообщения, отправка и журнал отправок.

Окно живёт здесь с фазы 3, потому что срез витрины `matches --new` мерится
ровно тем же: одна выборка и две подачи (решение 10 спеки). Текст сообщения
нельзя проверить иначе, чем отправкой, а отправленное не отзывается — значит,
человек обязан уметь посмотреть то же самое в терминале до отправки.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from listam.config import Config, ConfigError, positive, threshold
from listam.domain.models import Notification
from listam.matches_view import collect_events, render_events
from listam.matching import settings
from listam.ports.notifier import NotifyError
from listam.runner import SessionRefused, publish, working_session
from listam.wiring import build_notifier

KINDS = ("hot", "digest", "feed")

DEFAULT_FALLBACK_HOURS = {"hot": 2.0, "digest": 24.0, "feed": 24.0}


def window_for(config: Config, kind: str, hours: float | None = None,
               database=None) -> tuple[datetime, datetime, str]:
    """Окно `(since, until]` и человеческое объяснение, откуда оно взялось.

    `hours` — окно назад от «сейчас», как у `changes --hours`. Иначе мерка —
    `window_to` последней успешной отправки этого вида: повторный запуск
    не шлёт то же самое второй раз, а сбой сети не теряет событие. Отправок
    ещё не было — берём запасное окно из конфига и **говорим об этом вслух**,
    чтобы пустой список не читался как «на рынке тишина».
    """
    until = datetime.now(timezone.utc)
    if hours is not None:
        since = until - timedelta(hours=float(hours))
        return since, until, f"за последние {hours:g} ч (с {since:%d.%m %H:%M} UTC)"

    last = database.last_notification(kind) if database is not None else None
    if last is not None and last.window_to is not None:
        return last.window_to, until, (
            f"с прошлой отправки ({last.window_to:%d.%m %H:%M} UTC)"
        )

    fallback = threshold(config, f"notify.{kind}.fallback_hours",
                         DEFAULT_FALLBACK_HOURS[kind])
    fallback = DEFAULT_FALLBACK_HOURS[kind] if fallback is None else float(fallback)
    since = until - timedelta(hours=fallback)
    return since, until, (
        f"отправок ещё не было — беру последние {fallback:g} ч "
        f"(с {since:%d.%m %H:%M} UTC)"
    )


@dataclass
class NotifyReport:
    """Что сделало уведомление. Печатает это CLI, а не сам отправитель."""

    kind: str = ""
    scope: str = ""             # человеческое объяснение окна
    events: int = 0
    requests: int = 0
    sent: bool = False
    dry_run: bool = False
    text: str = ""
    errors: int = 0
    notes: str | None = None

    def render(self) -> str:
        lines = [f"Уведомление ({self.kind}): {self.scope}"]
        if self.text:
            lines.append(self.text)
        lines.append(
            f"Событий: {self.events}, заявок: {self.requests}, "
            + ("отправлено" if self.sent else
               "не отправлено (пробный прогон)" if self.dry_run else "не отправлено")
        )
        if self.notes:
            lines.append(self.notes)
        return "\n".join(lines)


def enabled(config: Config, kind: str) -> bool:
    """Тумблер вида уведомления. Выключено — это решение человека, не ошибка."""
    return bool(threshold(config, f"notify.{kind}.enabled", True))


def per_request(config: Config, kind: str) -> int | None:
    """Сколько строк на заявку. Ноль бессмыслен — это счётчик строк."""
    key = f"notify.{kind}.limit" if kind == "feed" else f"notify.{kind}.per_request"
    value = positive(config, key, 10)
    return None if value is None else int(value)


def run_notify(config: Config, *, kind: str, dry_run: bool = False) -> NotifyReport:
    """Одна отправка одного вида. Сводку печатает вызывающий.

    Порядок здесь и есть решение: сообщение уходит **до** записи в журнал,
    и запись делается только после успеха. Наоборот было бы «отправлено»
    в базе при неотправленном сообщении — и потерянное событие навсегда.

    Замок, свежая копия, миграции и заливка — общий каркас
    (`listam/runner.py`): тот же порядок, что у прогона, пересчёта, кластеров,
    заявок и подбора. Пятой копии этого блока не будет.
    """
    if kind not in KINDS:
        raise ConfigError(f"вид уведомления {kind!r} не из списка: {', '.join(KINDS)}")

    report = NotifyReport(kind=kind, dry_run=dry_run)
    if not enabled(config, kind):
        report.text = f"уведомления вида {kind} выключены в конфиге (notify.{kind}.enabled)"
        report.scope = "тумблер выключен"
        return report

    # Канал собирается до работы: пустой секрет — отказ на входе, а не после
    # выборки под замком рабочей копии. Пробному прогону канал не нужен.
    notifier = None if dry_run else build_notifier(config)

    tuning = settings(config)
    min_score = tuning.hot if kind == "hot" else tuning.digest
    notes: list[str] = []
    try:
        with working_session(config) as session:
            notes.extend(session.notes)
            session.notes = notes
            database = session.database

            since, until, scope = window_for(config, kind, database=database)
            report.scope = scope

            if kind == "feed":
                report.text, report.events = _feed_text(database, config, since, until)
            else:
                page = collect_events(config, since=since, until=until,
                                      min_score=min_score, note=scope)
                report.events = len(page.events)
                report.requests = len(page.totals)
                report.text = _match_text(page, config, kind)

            if dry_run:
                notes.append("пробный прогон: не отправлено, журнал не тронут")
                return report

            try:
                notifier.send(report.text)
            except NotifyError as exc:
                report.errors = 1
                notes.append(f"канал отказал: {exc}. Окно не сдвинуто — "
                             f"следующий запуск пошлёт то же самое")
                return report
            report.sent = True

            database.record_notification(Notification(
                kind=kind, sent_at=datetime.now(timezone.utc),
                window_from=since, window_to=until,
                events=report.events, requests=report.requests, text=report.text,
            ))
            publish(session, config, "база с журналом уведомлений")
            report.errors += session.failures
    except SessionRefused as exc:
        report.errors = 1
        notes.append(str(exc))
    finally:
        report.notes = "; ".join(note for note in notes if note) or None
    return report


def _match_text(page, config: Config, kind: str) -> str:
    """Текст уведомления по заявкам плюс пометка слишком широких заявок.

    Широкая заявка — это разговор с брокером, а не с рынком: 10 250 матчей
    и 7 515 горячих на одной заявке боевая приёмка уже видела.
    """
    head = "Звони сейчас" if kind == "hot" else "Что нового со вчера"
    text = render_events(page, per_request=per_request(config, kind), head=head)
    if kind != "digest":
        return text

    wide = positive(config, "notify.digest.wide_request", 50)
    if wide is None:
        return text
    # Пробел после ключа обязателен: `R-1` — начало `R-11`, и пометка без него
    # садилась на узкого соседа с чужим счётчиком. На боевой базе таких пометок
    # выходило 77 на 50 заявок.
    marked = []
    for line in text.splitlines():
        marked.append(line)
        for key, total in page.totals.items():
            if line.startswith(f"Заявка {key} ") and total > int(wide):
                marked.append(
                    f"  ⚠ заявка слишком широкая: {total} событий за окно. "
                    f"Сузь районы или бюджет, иначе разговор не состоится"
                )
    return "\n".join(marked)


def _feed_text(database, config: Config, since, until) -> tuple[str, int]:
    """Что пришло на ленту вне заявок: счётчики и лучшие по выгодности.

    Тумблер отдельный нарочно: брокеру нужно видеть ленту, даже когда
    ни одна заявка этого не взяла.
    """
    from listam.changes import money, per_sqm
    from listam.domain.stats import median_price_per_sqm_by_district

    fresh = [item for item in database.listings_first_seen_since(since)
             if item.first_seen is not None and item.first_seen <= until]
    cheaper = [row for row in database.price_changes_since(since)
               if row[1] is not None and row[2] is not None and row[2] < row[1]]
    gone = database.listings_gone_since(since)

    medians = median_price_per_sqm_by_district(database.listings_for_matching())

    def cheapness(item) -> float:
        """Насколько объявление дешевле медианы своего района. Нет медианы —
        считаем ноль: не наказываем и не награждаем, как и скоринг."""
        median = medians.get(item.district or "")
        if not median or item.price_per_sqm is None:
            return 0.0
        return (median - item.price_per_sqm) / median

    best = sorted(fresh, key=cheapness, reverse=True)[:per_request(config, "feed") or 0]
    lines = [
        f"На ленте: новых {len(fresh)}, подешевели {len(cheaper)}, снято {len(gone)}",
    ]
    for item in best:
        lines.append(
            f"  • {money(item.price_usd):>10}  {per_sqm(item):>12}  "
            f"{item.district or '—'}, {item.street or '—'}  {item.url}"
        )
    left = len(fresh) - len(best)
    if left > 0:
        lines.append(f"  …и ещё {left} — python -m listam changes")
    return "\n".join(lines), len(fresh)
