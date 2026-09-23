"""Порт Notifier: доставка уведомлений брокеру.

Канал один и он брокерский (решение 4 спеки M3): клиентам брокер пересылает
сам. Адресата порт всё равно принимает — маршруты появятся позже, и менять
подпись у трёх реализаций разом дороже, чем принять `to=None` сегодня.

Сообщение — структура `layout.Message`, а не строка (решение 7 спеки
M3.5): резать между карточками может только тот, кто знает, где карточки.
Рендер выбирает реализация: Telegram — HTML, консоль — простой текст.

Отказ канала — `NotifyError`, а не голое исключение библиотеки: команда,
которая его ловит, не обязана знать, чем именно ходит адаптер в сеть.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from listam.layout import Message, plain


class NotifyError(Exception):
    """Сообщение не ушло: сеть, токен, чат или лимит канала."""


class Notifier(ABC):
    @abstractmethod
    def send(self, message: Message, to: str | None = None) -> None:
        """Отправляет сообщение целиком; `to` не задан — адресат по умолчанию из конфига."""

    @abstractmethod
    def describe(self) -> str:
        """Чем является канал — для `doctor` и для отчёта команды."""


class NullNotifier(Notifier):
    """`notify.kind: none` — канал не настроен, и это не ошибка."""

    def send(self, message: Message, to: str | None = None) -> None:
        return None

    def describe(self) -> str:
        return "уведомления никуда не идут (notify.kind: none)"


class StdoutNotifier(Notifier):
    """`notify.kind: stdout` — сообщение печатается, а не отправляется.

    Это не заглушка, а рабочий режим: на нём проверяют текст до того, как
    он уйдёт человеку. Отправленное не отзывается.
    """

    def send(self, message: Message, to: str | None = None) -> None:
        print(f"[уведомление{' → ' + to if to else ''}]\n{plain(message)}")

    def describe(self) -> str:
        return "уведомления печатаются в консоль (notify.kind: stdout)"
