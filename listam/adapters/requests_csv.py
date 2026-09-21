"""Заявки из CSV-файла: отладочный источник и он же запасной для боевого.

Путь — из конфига (`requests.path`), в коде его нет и быть не может.
"""
from __future__ import annotations

import csv
from pathlib import Path

from listam.ports.requests_source import RequestsSource


class MissingRequestsFile(Exception):
    """Файла заявок нет по указанному пути."""


class CsvRequestsSource(RequestsSource):
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def rows(self) -> list[dict]:
        if not self.path.exists():
            raise MissingRequestsFile(
                f"Файла заявок нет: {self.path}. Путь задаётся ключом requests.path "
                f"в config/<env>.yaml."
            )
        # utf-8-sig: таблица, сохранённая Excel'ем, начинается с BOM, и без
        # него первая колонка называется '﻿id' и не находится никогда.
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

    def describe(self) -> str:
        return f"CSV: {self.path}"
