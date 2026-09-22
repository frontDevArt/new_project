"""Порт Notifier: доставка уведомлений брокеру.

Канал один и он брокерский (решение 4 спеки M3): клиентам брокер пересылает
сам. Адресата порт всё равно принимает — маршруты появятся позже, и менять
подпись у трёх реализаций разом дороже, чем принять `to=None` сегодня.

Отказ канала — `NotifyError`, а не голое исключение библиотеки: команда,
которая его ловит, не обязана знать, чем именно ходит адаптер в сеть.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class NotifyError(Exception):
    """Сообщение не ушло: сеть, токен, чат или лимит канала."""


class Notifier(ABC):
    @abstractmethod
    def send(self, text: str, to: str | None = None) -> None:
        """Отправляет сообщение; `to` не задан — адресат по умолчанию из конфига."""

    @abstractmethod
    def describe(self) -> str:
        """Чем является канал — для `doctor` и для отчёта команды."""


class NullNotifier(Notifier):
    """`notify.kind: none` — канал не настроен, и это не ошибка."""

    def send(self, text: str, to: str | None = None) -> None:
        return None

    def describe(self) -> str:
        return "уведомления никуда не идут (notify.kind: none)"


class StdoutNotifier(Notifier):
    """`notify.kind: stdout` — сообщение печатается, а не отправляется.

    Это не заглушка, а рабочий режим: на нём проверяют текст до того, как
    он уйдёт человеку. Отправленное не отзывается.
    """

    def send(self, text: str, to: str | None = None) -> None:
        print(f"[уведомление{' → ' + to if to else ''}]\n{text}")

    def describe(self) -> str:
        return "уведомления печатаются в консоль (notify.kind: stdout)"
