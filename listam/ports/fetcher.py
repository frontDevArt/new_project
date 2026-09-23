"""Порт Fetcher: откуда берутся HTML-страницы."""
from __future__ import annotations

from abc import ABC, abstractmethod


class FetchError(Exception):
    """Страницу получить не удалось.

    `status` — код ответа сайта, если сайт ответил (404 — страницы нет).
    Сеть отказала раньше ответа — `None`: шаг `pages` отличает «объявление
    снято» от «сеть моргнула» по нему.
    """

    def __init__(self, message: str = "", status: int | None = None):
        super().__init__(message)
        self.status = status


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
