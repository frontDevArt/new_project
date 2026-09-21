"""Порт Exporter: витрина для человека. База — SQLite, Excel — выгрузка."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable

from listam.domain.models import Listing


class Exporter(ABC):
    @abstractmethod
    def export(self, listings: Iterable[Listing], name: str | None = None) -> Path:
        """Пишет выгрузку и возвращает путь к файлу."""
