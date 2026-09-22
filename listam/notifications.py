"""Уведомление: окно, текст сообщения, отправка и журнал отправок.

Окно живёт здесь с фазы 3, потому что срез витрины `matches --new` мерится
ровно тем же: одна выборка и две подачи (решение 10 спеки). Текст сообщения
нельзя проверить иначе, чем отправкой, а отправленное не отзывается — значит,
человек обязан уметь посмотреть то же самое в терминале до отправки.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from listam.config import Config, ConfigError, hours, positive, switch
from listam.domain.events import RETIRED
from listam.domain.models import Notification
from listam.matches_view import collect_events, render_events
from listam.matching import settings
from listam.ports.notifier import NotifyError
from listam.runner import SessionRefused, publish, working_session
from listam.wiring import build_notifier, notify_channel

KINDS = ("hot", "digest", "feed")

DEFAULT_FALLBACK_HOURS = {"hot": 2.0, "digest": 24.0, "feed": 24.0}


def window_for(config: Config, kind: str, hours: float | None = None,
               database=None, channel: str | None = None
               ) -> tuple[datetime, datetime, str]:
    """Окно `(since, until]` и человеческое объяснение, откуда оно взялось.

    `hours` — окно назад от «сейчас», как у `changes --hours`. Иначе мерка —
    `window_to` последней успешной отправки этого вида: повторный запуск
    не шлёт то же самое второй раз, а сбой сети не теряет событие. Отправок
    ещё не было — берём запасное окно из конфига и **говорим об этом вслух**,
    чтобы пустой список не читался как «на рынке тишина».

    `channel` — окно своего канала: текст, напечатанный в консоль, не двигает
    окно Telegram.
    """
    until = datetime.now(timezone.utc)
    if hours is not None:
        since = until - timedelta(hours=float(hours))
        return since, until, f"за последние {hours:g} ч (с {since:%d.%m %H:%M} UTC)"

    last = (database.last_notification(kind, channel=channel)
            if database is not None else None)
    if last is not None and last.window_to is not None:
        return last.window_to, until, (
            f"с прошлой отправки ({last.window_to:%d.%m %H:%M} UTC)"
        )

    fallback = tuning_for(config, kind).fallback_hours
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
    retired: int = 0            # закрытий в тексте: они не события и не звонки
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
        closed = f" (и отпало {self.retired})" if self.retired else ""
        lines.append(
            f"Событий: {self.events}{closed}, заявок: {self.requests}, "
            + ("отправлено" if self.sent else
               "не отправлено (пробный прогон)" if self.dry_run else "не отправлено")
        )
        if self.notes:
            lines.append(self.notes)
        return "\n".join(lines)


@dataclass
class NotifyTuning:
    """Ручки одного вида уведомления, прочитанные и проверенные разом."""

    enabled: bool
    per_request: int | None      # строк на заявку; у ленты — notify.feed.limit
    fallback_hours: float        # окно, когда отправок этого вида ещё не было
    wide_request: int | None = None
    include_retired: bool = False


def tuning_for(config: Config, kind: str) -> NotifyTuning:
    """Секция `notify.<kind>` целиком — или `ConfigError` с именем ключа.

    Читается до замка и до базы. `enabled: "false"` в кавычках — непустая
    строка, и `bool()` читал её как «да»; `fallback_hours: -48` давал окно
    из будущего. И то и другое отклоняется на входе, как `--limit 0`.

    Закрытия идут только в дайджест: в «горячее» — никогда (решение 1 спеки
    M3), и тумблера для него нет нарочно.
    """
    base = f"notify.{kind}"
    limit_key = f"{base}.limit" if kind == "feed" else f"{base}.per_request"
    limit = positive(config, limit_key, 10)
    tuning = NotifyTuning(
        enabled=switch(config, f"{base}.enabled", True),
        per_request=None if limit is None else int(limit),
        fallback_hours=hours(config, f"{base}.fallback_hours",
                             DEFAULT_FALLBACK_HOURS[kind]),
    )
    if kind == "digest":
        wide = positive(config, f"{base}.wide_request", 50)
        tuning.wide_request = None if wide is None else int(wide)
        tuning.include_retired = switch(config, f"{base}.include_retired", True)
    return tuning


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

    # Секция `notify` проверяется до замка и до базы: бессмысленное значение
    # отклоняется на входе, а не посреди выборки под замком рабочей копии.
    knobs = tuning_for(config, kind)
    report = NotifyReport(kind=kind, dry_run=dry_run)
    if not knobs.enabled:
        report.text = f"уведомления вида {kind} выключены в конфиге (notify.{kind}.enabled)"
        report.scope = "тумблер выключен"
        return report

    tuning = settings(config)
    min_score = tuning.hot if kind == "hot" else tuning.digest
    if kind != "feed" and min_score is None:
        # Порог выключен (`null`) — подбор таких вариантов не считает вовсе.
        # Подставить чужой порог значило бы слать под именем «горячего»
        # то, что человек горячим не назвал.
        report.scope = "порог выключен"
        report.text = (f"порог match.thresholds.{kind} выключен (null): подбор "
                       f"таких вариантов не считает, слать нечего")
        return report

    channel = notify_channel(config)
    if channel == "none" and not dry_run:
        # Канал не настроен — значит, ничего и не ушло. Писать «отправлено»
        # значило бы съесть события: когда Telegram появится, он получил бы
        # только то, что случилось после.
        report.scope = "канал не настроен"
        report.text = ("notify.kind: none — уведомления никуда не идут. Окно не "
                       "сдвинуто: канал, когда появится, получит всё накопленное")
        return report

    # Канал собирается до работы: пустой секрет — отказ на входе, а не после
    # выборки под замком рабочей копии. Пробному прогону канал не нужен.
    notifier = None if dry_run else build_notifier(config)
    notes: list[str] = []
    try:
        with working_session(config) as session:
            notes.extend(session.notes)
            session.notes = notes
            database = session.database

            since, until, scope = window_for(config, kind, database=database,
                                             channel=channel)
            report.scope = scope

            if kind == "feed":
                report.text, report.events = _feed_text(database, knobs, since, until)
            else:
                page = collect_events(config, since=since, until=until,
                                      min_score=min_score, note=scope,
                                      include_retired=knobs.include_retired)
                calls = page.calls()
                report.events = len(calls)
                report.retired = len(page.events) - len(calls)
                # Заявка, у которой только закрытия, звонка не требует и
                # в счёт заявок не входит.
                report.requests = len({event.match.request_id for event in calls})
                report.text = _match_text(page, knobs, kind)

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

            try:
                database.record_notification(Notification(
                    kind=kind, sent_at=datetime.now(timezone.utc),
                    window_from=since, window_to=until,
                    events=report.events, requests=report.requests, text=report.text,
                    channel=channel,
                ))
            except Exception as exc:       # sqlite3.Error, OSError — база не приняла строку
                report.errors = 1
                notes.append(
                    f"сообщение ушло, но журнал не записан: {exc}. Окно осталось, "
                    f"где было, — следующий запуск пошлёт то же самое ещё раз"
                )
                return report
            publish(session, config, "база с журналом уведомлений")
            report.errors += session.failures
    except SessionRefused as exc:
        report.errors = 1
        notes.append(str(exc))
    finally:
        report.notes = "; ".join(note for note in notes if note) or None
    return report


def _match_text(page, knobs: NotifyTuning, kind: str) -> str:
    """Текст уведомления по заявкам плюс пометка слишком широких заявок.

    Широкая заявка — это разговор с брокером, а не с рынком: 10 250 матчей
    и 7 515 горячих на одной заявке боевая приёмка уже видела.
    """
    head = "Звони сейчас" if kind == "hot" else "Что нового со вчера"
    text = render_events(page, per_request=knobs.per_request, head=head)
    if kind != "digest":
        return text

    wide = knobs.wide_request
    if wide is None:
        return text
    # Широту мерят события, а закрытие событием не является (решение 1 спеки):
    # сотни «отпало» — ответ на сужение заявки, и совет «сузь» был бы неправдой.
    key_of = {request.id: key for key, request in page.requests.items()}
    alive: dict[str, int] = {}
    for event in page.events:
        if event.kind != RETIRED:
            key = key_of.get(event.match.request_id)
            alive[key] = alive.get(key, 0) + 1
    # Пробел после ключа обязателен: `R-1` — начало `R-11`, и пометка без него
    # садилась на узкого соседа с чужим счётчиком. На боевой базе таких пометок
    # выходило 77 на 50 заявок.
    marked = []
    for line in text.splitlines():
        marked.append(line)
        for key, total in alive.items():
            if line.startswith(f"Заявка {key} ") and total > wide:
                marked.append(
                    f"  ⚠ заявка слишком широкая: {total} событий за окно. "
                    f"Сузь районы или бюджет, иначе разговор не состоится"
                )
    return "\n".join(marked)


def _feed_text(database, knobs: NotifyTuning, since, until) -> tuple[str, int]:
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

    best = sorted(fresh, key=cheapness, reverse=True)[:knobs.per_request or 0]
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
