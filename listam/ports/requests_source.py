"""Порт RequestsSource: откуда берутся заявки покупателей.

Ядро системы — заявка, но наполняется она на M2. Здесь объявлен интерфейс
и заглушка, чтобы остальной код уже сейчас писался против порта, а не
против Google Sheet.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class RequestsSource(ABC):
    @abstractmethod
    def active_requests(self) -> list:
        """Список активных заявок покупателей."""


class EmptyRequestsSource(RequestsSource):
    """Заглушка на M0–M1: заявок ещё нет."""

    def active_requests(self) -> list:
        return []
