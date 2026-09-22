"""Контрактный тест порта Notifier: одинаков для любой реализации.

Канал один — брокерский (решение 4 спеки), но адресата порт принимает с
первого дня: маршруты появятся позже, а менять подпись у трёх реализаций
разом — это тот самый разъезд, который план ловит контрактом.
"""
from __future__ import annotations

import pytest

from listam.ports.notifier import NullNotifier, Notifier, StdoutNotifier


@pytest.fixture(params=["none", "stdout"])
def notifier(request) -> Notifier:
    return NullNotifier() if request.param == "none" else StdoutNotifier()


def test_is_a_notifier(notifier):
    assert isinstance(notifier, Notifier)


def test_sends_without_raising(notifier):
    notifier.send("Заявка R-1 — 3 новых")


def test_accepts_an_addressee(notifier):
    """`to` не обязателен, но принимается всеми: иначе первая же попытка
    послать не туда, куда обычно, потребует править три класса."""
    notifier.send("Заявка R-1 — 3 новых", to="-1001234567890")


def test_describes_itself(notifier):
    """`doctor` показывает канал словами: «уведомления никуда не идут» —
    это ответ, а пустая строка — нет."""
    assert notifier.describe()
