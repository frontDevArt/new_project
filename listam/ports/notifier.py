"""Порт Notifier: доставка алертов. На M0 — заглушки, Telegram появится на M3."""
from __future__ import annotations

from abc import ABC, abstractmethod


class Notifier(ABC):
    @abstractmethod
    def send(self, text: str) -> None: ...


class NullNotifier(Notifier):
    """Ничего не отправляет. Значение по умолчанию до M3."""

    def send(self, text: str) -> None:
        return None


class StdoutNotifier(Notifier):
    def send(self, text: str) -> None:
        print(f"[уведомление] {text}")
