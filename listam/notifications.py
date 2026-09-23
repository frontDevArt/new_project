"""Уведомление: окно, выборка, сообщение, отправка и журнал отправок.

Окно живёт здесь с фазы 3, потому что срез витрины `matches --new` мерится
ровно тем же: одна выборка и две подачи (решение 10 спеки). Текст сообщения
нельзя проверить иначе, чем отправкой, а отправленное не отзывается — значит,
человек обязан уметь посмотреть то же самое в терминале до отправки.

Сообщение собирает `listam/layout.py` (пункт 8 анализа после M3): канал
получает структуру, `--dry-run` и журнал — её простой текст. Одна выборка,
одна структура, два рендера — расхождения между «посмотрел» и «ушло» нет.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from listam.config import Config, ConfigError, hours, positive, switch
from listam.domain.models import Notification
from listam.layout import (Message, circles, digest_message, feed_message,
                           gem_percent, hot_message, plain)
from listam.matches_view import collect_events
from listam.matching import settings
from listam.ports.notifier import NotifyError
from listam.runner import SessionRefused, publish, working_session
from listam.wiring import build_notifier, notify_channel

KINDS = ("hot", "digest", "feed")

DEFAULT_FALLBACK_HOURS = {"hot": 2.0, "digest": 24.0, "feed": 24.0}
DEFAULT_INITIAL_TOP = 15      # сколько лучших из первичной подборки показывает дайджест


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
    initial_top: int | None = None   # дайджест: лучших из первичной подборки


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
        top = positive(config, f"{base}.initial_top", DEFAULT_INITIAL_TOP)
        tuning.initial_top = None if top is None else int(top)
    # Пороги вёрстки — тоже на входе: 🟢 от балла 170 не загорится никогда.
    if kind == "feed":
        gem_percent(config)
    else:
        circles(config)
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
                message, report.events = _feed_message(config, database, knobs,
                                                       since, until)
            else:
                page = collect_events(config, since=since, until=until,
                                      min_score=min_score, note=scope,
                                      include_retired=knobs.include_retired,
                                      database=database)
                if kind == "hot":
                    # Решение 14: первичная подборка новой заявки — в дайджест,
                    # звонить по ней в этот час незачем.
                    page = page.market()
                calls = page.calls()
                report.events = len(calls)
                report.retired = len(page.events) - len(calls)
                # Заявка, у которой только закрытия, звонка не требует и
                # в счёт заявок не входит.
                report.requests = len({event.match.request_id for event in calls})
                message = _match_message(config, database, page, knobs, kind, until)
            report.text = plain(message)

            if dry_run:
                notes.append("пробный прогон: не отправлено, журнал не тронут")
                return report

            # Пустое «звони сейчас» в чат не идёт (решение 5 спеки M3.5):
            # расписание зовёт его каждый час, и брокер получал бы 24
            # «событий нет» в сутки. Журнал пишется всё равно — окно сдвигается.
            # У дайджеста «событий нет» — законный ответ раз в сутки.
            if kind == "hot" and report.events == 0:
                notes.append("событий нет — не отправлено")
            else:
                try:
                    notifier.send(message)
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


def _match_message(config: Config, database, page, knobs: NotifyTuning, kind: str,
                   until: datetime) -> Message:
    """Сообщение по заявкам: «звони сейчас» или дайджест со сводкой.

    Пометка широкой заявки только в дайджесте: «горячее» отвечает на «кому
    звонить сейчас», и совет «сузь заявку» ему не по размеру. Процент к
    медиане района — по тем же объявлениям, по которым считает подбор.
    """
    from listam.domain.stats import median_price_per_sqm_by_district

    medians = (median_price_per_sqm_by_district(database.listings_for_matching())
               if page.events else {})
    if kind == "hot":
        return hot_message(page, medians, config, per_request=knobs.per_request)
    active = sum(1 for _ in database.iter_requests())
    return digest_message(page, medians, config, per_request=knobs.per_request,
                          wide=knobs.wide_request, active=active, at=until,
                          initial_top=knobs.initial_top)


def _feed_message(config: Config, database, knobs: NotifyTuning,
                  since, until) -> tuple[Message, int]:
    """Что пришло на ленту вне заявок: счётчики и 💎 против медианы района.

    Тумблер отдельный нарочно: брокеру нужно видеть ленту, даже когда
    ни одна заявка этого не взяла.
    """
    from listam.domain.stats import median_price_per_sqm_by_district

    fresh = [item for item in database.listings_first_seen_since(since)
             if item.first_seen is not None and item.first_seen <= until]
    cheaper = [row for row in database.price_changes_since(since)
               if row[1] is not None and row[2] is not None and row[2] < row[1]]
    gone = database.listings_gone_since(since)
    medians = median_price_per_sqm_by_district(database.listings_for_matching())
    message = feed_message(fresh, cheaper=len(cheaper), gone=len(gone),
                           medians=medians, config=config, limit=knobs.per_request)
    return message, len(fresh)
