"""Порт RequestsSource: откуда берутся заявки покупателей.

Адаптер отвечает ровно за одно — достать сырые строки. Превращение строки
в заявку живёт в домене и одно на всех (решение 1): `csv` и `gsheet`
обязаны дать одну и ту же заявку из одной и той же строки, иначе
контрактный тест ничего не гарантирует.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from listam.domain.models import Request
from listam.domain.requests import RequestError, parse_rows


class RequestsSource(ABC):
    @abstractmethod
    def rows(self) -> list[dict]:
        """Сырые строки источника: колонка → значение, всё текстом."""

    @abstractmethod
    def describe(self) -> str:
        """Чем является источник — для `doctor` и для отчёта команды."""

    def read(self) -> tuple[list[Request], list[RequestError]]:
        """Разобранные заявки и отклонённые строки. Разбор один на все адаптеры."""
        return parse_rows(self.rows())

    def active_requests(self) -> list[Request]:
        parsed, _ = self.read()
        return [item for item in parsed if item.status == "active"]


class EmptyRequestsSource(RequestsSource):
    """`requests.kind: none` — источник не настроен, и это не ошибка."""

    def rows(self) -> list[dict]:
        return []

    def describe(self) -> str:
        return "источник заявок не настроен (requests.kind: none)"
