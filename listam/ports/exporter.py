"""Порт Exporter: витрина для человека. База — SQLite, Excel — выгрузка."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable

from listam.domain.models import Listing, Match, Request


class Exporter(ABC):
    @abstractmethod
    def export(self, listings: Iterable[Listing], name: str | None = None,
               matches: list[tuple[Request, Match, Listing]] | None = None) -> Path:
        """Пишет выгрузку и возвращает путь к файлу.

        `matches` — строки витрины «заявка, матч, объявление»: отдельный лист
        рядом с листом объявлений. Матчей нет — листа нет, и это не ошибка.
        """
