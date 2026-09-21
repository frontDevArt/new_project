"""Доли заполненности полей за прогон.

Парсер устроен так, что потерянное поле — это `None`, а не падение: уронить
обход на трёхсотой странице хуже, чем недосчитаться улицы. Обратная сторона
в том, что переименованный на сайте класс не виден вообще никак. `ge3` стал
называться иначе — и вся база молча записалась в собственники; пропал
`category-data-list-card__location` — и район исчез у всех сразу.

Поэтому прогон считает, у какой доли карточек поле заполнено, и сравнивает
с порогами из конфига (секция `coverage`). Ушло за порог — это ошибка прогона
с внятным текстом, а не тихая запись.

Порогов в конфиге нет — ничего не проверяем: выдумывать их за человека
нельзя, рынок и вёрстка меняются, а ложная тревога дороже молчания.
"""
from __future__ import annotations

from collections import Counter, defaultdict

DEFAULT_MIN_SAMPLE = 100      # на меньшем числе карточек доля ничего не значит


class Coverage:
    """Накопитель долей: карточки приходят по одной, приговор выносится в конце."""

    def __init__(self, rules: dict | None = None):
        self.rules = rules or {}
        self.min_filled: dict = self.rules.get("min_filled") or {}
        self.min_share: dict = self.rules.get("min_share") or {}
        self.min_sample = int(self.rules.get("min_sample", DEFAULT_MIN_SAMPLE) or 0)
        self.total = 0
        self._filled: Counter = Counter()
        self._values: dict[str, Counter] = defaultdict(Counter)

    def add(self, listing) -> None:
        self.total += 1
        for name in self.min_filled:
            if _filled(getattr(listing, name, None)):
                self._filled[name] += 1
        for name in self.min_share:
            value = getattr(listing, name, None)
            if _filled(value):
                self._values[name][value] += 1

    def skipped_note(self) -> str | None:
        """Почему приговора не будет. Проверка состоялась — None.

        Пустой список `failures()` на малой выборке означает не «вёрстка цела»,
        а «мы не смотрели». Молчать об этом нельзя: прогон на 96 карточках при
        пороге в 100 выглядел ровно как здоровый.
        """
        if not (self.min_filled or self.min_share):
            return None
        if self.total >= self.min_sample:
            return None
        return (
            f"карточек меньше coverage.min_sample = {self.min_sample}, "
            f"проверка вёрстки пропущена: в прогоне их {self.total}"
        )

    def failures(self) -> list[str]:
        """Пороги, за которые прогон вышел. Пусто — всё в порядке."""
        if self.total < self.min_sample:
            return []
        messages = []
        for name, threshold in self.min_filled.items():
            share = self._filled[name] / self.total
            if share < float(threshold):
                messages.append(
                    f"поле {name} заполнено у {_percent(share)} карточек прогона, "
                    f"порог coverage.min_filled.{name} = {_percent(threshold)}. "
                    f"Так выглядит уехавшая вёрстка"
                )
        for name, expected in self.min_share.items():
            for value, threshold in (expected or {}).items():
                share = self._values[name][value] / self.total
                if share < float(threshold):
                    messages.append(
                        f"{name} = {value} у {_percent(share)} карточек прогона, "
                        f"порог coverage.min_share.{name}.{value} = {_percent(threshold)}. "
                        f"Так выглядит уехавшая вёрстка"
                    )
        return messages


def _filled(value) -> bool:
    """Пустая строка — это не «поле есть», это «поля не нашли»."""
    if value is None:
        return False
    return not (isinstance(value, str) and not value.strip())


def _percent(share: float) -> str:
    return f"{float(share) * 100:.0f}%"


def rules_from(config) -> dict:
    """Пороги из секции `coverage`. Секции нет — проверок нет."""
    return config.section("coverage") or {}
