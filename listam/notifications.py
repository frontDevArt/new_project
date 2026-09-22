"""Окно уведомлений: с какого момента считаем события и как это назвать.

Сборка сообщения и отправка появятся в фазе 4; окно живёт здесь с фазы 3,
потому что срез витрины `matches --new` мерится ровно тем же.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from listam.config import Config, threshold

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
