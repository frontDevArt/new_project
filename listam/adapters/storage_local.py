"""Хранилище в локальной папке. Быстрое, для отладки и для работы без облака."""
from __future__ import annotations

import shutil
from pathlib import Path

from listam.adapters.filenames import drop_wal_sidecars, safe_filename
from listam.ports.storage import CheckReport, Storage, StorageError


class LocalStorage(Storage):
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def _path(self, name: str) -> Path:
        # имя файла — только имя, никаких путей наружу
        safe = safe_filename(name)
        if not safe:
            raise StorageError(f"Недопустимое имя файла: {name!r}")
        return self.directory / safe

    def exists(self, name: str) -> bool:
        return self._path(name).exists()

    def download(self, name: str, target: str | Path) -> bool:
        source = self._path(name)
        if not source.exists():
            return False
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        drop_wal_sidecars(target)   # спутники относились к прежнему файлу
        return True

    def upload(self, source: str | Path, name: str) -> None:
        source = Path(source)
        if not source.exists():
            raise StorageError(f"Нечего заливать: {source} не существует")
        self.directory.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, self._path(name))

    def names(self, prefix: str = "") -> list[str]:
        if not self.directory.exists():
            return []
        return sorted(
            item.name for item in self.directory.iterdir()
            if item.is_file() and item.name.startswith(prefix)
            and not item.name.startswith(".")
        )

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)

    def check(self) -> CheckReport:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            probe = self.directory / ".listam-write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            return CheckReport(ok=False, details=f"{self.directory}: {exc}")
        return CheckReport(ok=True, details=f"локальная папка {self.directory}, запись доступна")
