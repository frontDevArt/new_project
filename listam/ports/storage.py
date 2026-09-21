"""Порт Storage: где лежит файл базы между прогонами."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


class StorageError(Exception):
    """Хранилище недоступно или отказало."""


@dataclass(frozen=True)
class CheckReport:
    """Результат самопроверки — из этого собирается отчёт `doctor`."""

    ok: bool
    details: str


class Storage(ABC):
    @abstractmethod
    def exists(self, name: str) -> bool: ...

    @abstractmethod
    def download(self, name: str, target: str | Path) -> bool:
        """Кладёт файл в target. False — если такого файла в хранилище нет."""

    @abstractmethod
    def upload(self, source: str | Path, name: str) -> None:
        """Заливает файл, перезаписывая прежний."""

    @abstractmethod
    def check(self) -> CheckReport:
        """Проверяет доступ и право на запись, не трогая рабочие файлы."""
