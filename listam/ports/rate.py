"""Порт RateProvider: курс AMD→USD."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


class RateError(Exception):
    """Курс получить не удалось."""


@dataclass(frozen=True)
class Rate:
    value: float          # сколько драмов за один доллар
    source: str           # чем взят курс — уходит в журнал прогона
    fetched_at: datetime  # UTC
    banks_counted: int = 0


class RateProvider(ABC):
    @abstractmethod
    def amd_per_usd(self) -> Rate:
        """Возвращает курс. Бросает RateError, если источник не отдал цифру."""
