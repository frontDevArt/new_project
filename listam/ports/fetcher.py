"""Порт Fetcher: откуда берутся HTML-страницы."""
from __future__ import annotations

from abc import ABC, abstractmethod


class FetchError(Exception):
    """Страницу получить не удалось."""


class Fetcher(ABC):
    @abstractmethod
    def get(self, url: str) -> str:
        """Возвращает текст страницы. Бросает FetchError, если не вышло."""

    @property
    @abstractmethod
    def requests_made(self) -> int:
        """Сколько сетевых запросов сделано — идёт в журнал прогона."""

    def close(self) -> None:
        """Освободить ресурсы. По умолчанию — нечего освобождать."""
