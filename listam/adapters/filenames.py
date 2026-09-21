"""Имя файла, пришедшее снаружи.

`--name`, имя объекта в хранилище — это имя файла, а не путь. `../../отчёт.xlsx`
должно лечь в свою папку, а не мимо неё. `Path(name).name` одного этого не
делает: у `..` он возвращает `..`, и папка снова уезжает наверх.
"""
from __future__ import annotations

from pathlib import PurePath


def safe_filename(name: str | None) -> str | None:
    """Только имя файла. Ничего пригодного не осталось — None."""
    if not name:
        return None
    last = PurePath(str(name).replace("\\", "/")).name
    if last in ("", ".", ".."):
        return None
    return last


# Спутники файла базы в режиме WAL: свежие записи и общая память читателей.
WAL_SIDECARS = ("-wal", "-shm")


def drop_wal_sidecars(path) -> None:
    """Убирает `-wal` и `-shm` рядом с файлом базы, который только что подменили.

    Файл базы заменён другим (скачан из хранилища, взят более свежий) — старые
    спутники относятся к прежнему файлу. SQLite, увидев их рядом с новой базой,
    считает их своими и либо накатывает чужие записи, либо ругается на
    несовпадение. Подменил файл — убери спутников.
    """
    from pathlib import Path

    base = Path(path)
    for suffix in WAL_SIDECARS:
        base.with_name(base.name + suffix).unlink(missing_ok=True)
