"""Контрактный тест порта Notifier: одинаков для любой реализации.

Канал один — брокерский (решение 4 спеки), но адресата порт принимает с
первого дня: маршруты появятся позже, а менять подпись у трёх реализаций
разом — это тот самый разъезд, который план ловит контрактом.
"""
from __future__ import annotations

import os

import pytest

from listam.ports.notifier import NullNotifier, Notifier, StdoutNotifier

# Живой канал проверяется только ключами, выставленными в самой команде запуска:
# pytest `.env` не читает, поэтому обычная батарея в личку брокера не шлёт.
# С ключами контракт `telegram` **отправляет два сообщения в чат** — осознанно:
# канал, который нельзя проверить, не канал.
NO_TELEGRAM = pytest.mark.skipif(
    not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"),
    reason="нет TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID: живой канал не проверить",
)


@pytest.fixture(params=["none", "stdout", pytest.param("telegram", marks=NO_TELEGRAM)])
def notifier(request) -> Notifier:
    if request.param == "none":
        return NullNotifier()
    if request.param == "stdout":
        return StdoutNotifier()
    from listam.adapters.notify_telegram import TelegramNotifier

    return TelegramNotifier(token=os.environ["TELEGRAM_BOT_TOKEN"],
                            chat_id=os.environ["TELEGRAM_CHAT_ID"])


def test_is_a_notifier(notifier):
    assert isinstance(notifier, Notifier)


# Живьём этот текст приходит брокеру в личку — пусть говорит, откуда он.
TEXT = "listam: проверка канала (контрактный тест), это не заявка"


def test_sends_without_raising(notifier):
    notifier.send(TEXT)


def test_accepts_an_addressee(notifier):
    """`to` не обязателен, но принимается всеми: иначе первая же попытка
    послать не туда, куда обычно, потребует править три класса.

    Живому каналу адресат нужен настоящий: выдуманный чат Telegram отвергнет
    («chat not found»), и контракт проверял бы не порт, а выдумку."""
    notifier.send(TEXT, to=getattr(notifier, "chat_id", None) or "-1001234567890")


def test_describes_itself(notifier):
    """`doctor` показывает канал словами: «уведомления никуда не идут» —
    это ответ, а пустая строка — нет."""
    assert notifier.describe()
