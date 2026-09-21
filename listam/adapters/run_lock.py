"""Замок на прогон: одновременно по ленте ходит ровно один процесс.

Два `scrape` разом — это не два прогона, а гонка: обе копии базы расходятся,
и побеждает та, что позже залилась в хранилище. Замок — обычный файл рядом с
базой, который создаётся исключительным созданием (O_EXCL): атомарно и на
Windows, и на POSIX, в отличие от «проверил, потом создал».

Прогон могут убить по-жёсткому, и тогда файл останется лежать. Поэтому у
замка есть срок: слишком старый перехватывается, а не держит папку вечно.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STALE_AFTER = 3 * 60 * 60     # прогон 215 страниц с паузой 1.5 с — это часы


class LockBusy(Exception):
    """Замок держит другой прогон."""


class RunLock:
    def __init__(self, path: str | Path, stale_after_seconds: float = DEFAULT_STALE_AFTER):
        self.path = Path(path)
        self.stale_after_seconds = float(stale_after_seconds)
        self._held = False

    def acquire(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._create()
        except FileExistsError:
            if not self._is_stale():
                raise LockBusy(
                    f"Прогон уже идёт: замок {self.path} держит "
                    f"{self._describe()}. Дождись его или сними файл руками."
                ) from None
            self.path.unlink(missing_ok=True)
            try:
                self._create()
            except FileExistsError:
                raise LockBusy(f"Замок {self.path} перехватил кто-то ещё") from None
        self._held = True
        return self

    def touch(self) -> None:
        """Сердцебиение: прогон жив, срок замка отсчитывается заново.

        Обход ленты идёт часами, а срок замка — защита от убитого процесса,
        а не от долгого. Без этого замок протухает прямо под живым прогоном
        и второй `scrape` уходит в ту же ленту.
        """
        if not self._held:
            return
        try:
            self._write(self.path)
        except OSError:
            return            # файл увели из-под нас: прогон всё равно идёт дальше

    def release(self) -> None:
        if self._held:
            self.path.unlink(missing_ok=True)
            self._held = False

    def __enter__(self) -> "RunLock":
        return self.acquire()

    def __exit__(self, *exc_info) -> None:
        self.release()

    # --- внутренности ---------------------------------------------------

    def _create(self) -> None:
        handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(self._line())

    def _write(self, path: Path) -> None:
        """Перезаписывает строку замка — заодно обновляется и mtime файла."""
        path.write_text(self._line(), encoding="utf-8")

    def _line(self) -> str:
        return f"pid={os.getpid()} alive_at={datetime.now(timezone.utc).isoformat()}\n"

    def _is_stale(self) -> bool:
        if self.stale_after_seconds <= 0:
            return True        # срок не задан — замок никого не держит
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return True        # файл исчез между проверками — значит, свободен
        return age >= self.stale_after_seconds

    def _describe(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8").strip() or "неизвестный процесс"
        except OSError:
            return "неизвестный процесс"
