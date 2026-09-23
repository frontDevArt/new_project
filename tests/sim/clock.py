"""Часы имитации: подмена `datetime` во всех модулях listam.

Все модули listam пишут `from datetime import datetime` и берут время
`datetime.now(...)` (сторож — tests/test_sim_clock.py). Подмена меняет этот
атрибут модуля на наследника с `now()` от часов имитации. Метакласс держит
`isinstance(настоящий_datetime, подменённый)` истинным: объекты из
`fromisoformat` и арифметики остаются настоящими `datetime`.
"""
from __future__ import annotations

import contextlib
import importlib
import pkgutil
import sys
from datetime import datetime as _real
from datetime import timedelta, timezone
from types import ModuleType
from typing import Iterator

import listam


class SimClock:
    def __init__(self, start: _real):
        if start.tzinfo is None:
            raise ValueError("часы имитации: нужна отметка с часовым поясом")
        self._now = start.astimezone(timezone.utc)

    def now(self) -> _real:
        return self._now

    def set(self, moment: _real) -> None:
        moment = moment.astimezone(timezone.utc)
        if moment < self._now:
            raise ValueError(f"часы имитации назад не идут: {moment} < {self._now}")
        self._now = moment

    def advance(self, **delta) -> None:
        self.set(self._now + timedelta(**delta))


_ACTIVE: list[SimClock] = []


class _SimDatetimeMeta(type):
    def __instancecheck__(cls, obj) -> bool:
        return isinstance(obj, _real)

    def __subclasscheck__(cls, sub) -> bool:
        return issubclass(sub, _real)


class SimDatetime(_real, metaclass=_SimDatetimeMeta):
    @classmethod
    def now(cls, tz=None):
        if not _ACTIVE:
            return _real.now(tz)
        moment = _ACTIVE[-1].now()
        if tz is None:
            return moment.astimezone().replace(tzinfo=None)   # как datetime.now(): местное, наивное
        return moment.astimezone(tz)

    @classmethod
    def utcnow(cls):
        raise AssertionError("datetime.utcnow() в listam запрещён — сторож test_sim_clock")

    @classmethod
    def today(cls):
        return cls.now()


def listam_modules() -> list[ModuleType]:
    """Все модули listam, импортированные: ленивый импорт после подмены
    получил бы настоящее время."""
    for info in pkgutil.walk_packages(listam.__path__, "listam."):
        if info.name.endswith("__main__"):
            continue                      # его импорт запускает CLI
        importlib.import_module(info.name)
    return [module for name, module in sorted(sys.modules.items())
            if (name == "listam" or name.startswith("listam.")) and module is not None]


@contextlib.contextmanager
def sim_clock(start: _real) -> Iterator[SimClock]:
    clock = SimClock(start)
    patched = [module for module in listam_modules()
               if vars(module).get("datetime") is _real]
    for module in patched:
        module.datetime = SimDatetime
    _ACTIVE.append(clock)
    try:
        yield clock
    finally:
        _ACTIVE.pop()
        for module in patched:
            module.datetime = _real
