"""Fetcher, который читает страницы с диска, а не из сети.

Нужен в двух случаях. Первый: страницу сохранил человек из своего браузера —
скрипт разбирает её тем же кодом, что и живую ленту. Второй: переразобрать
старый снимок после правки парсера, не трогая сайт.

Имя файла получается из пути URL: /category/60/2 → category-60-2.html
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from listam.ports.fetcher import FetchError, Fetcher


class FilesFetcher(Fetcher):
    def __init__(self, directory: str | Path, suffix: str = ".html"):
        self.directory = Path(directory)
        self.suffix = suffix
        self._requests_made = 0

    @staticmethod
    def filename_for(url: str, suffix: str = ".html") -> str:
        path = urlparse(url).path if "://" in url else url
        slug = path.strip("/").replace("/", "-") or "index"
        return f"{slug}{suffix}"

    @property
    def requests_made(self) -> int:
        return self._requests_made

    def get(self, url: str) -> str:
        name = self.filename_for(url, self.suffix)
        path = self.directory / name
        self._requests_made += 1
        if not path.exists():
            raise FetchError(
                f"Нет сохранённой страницы {path}. "
                f"Сохрани {url} из браузера и положи файл под именем {name}."
            )
        return path.read_text(encoding="utf-8", errors="replace")
