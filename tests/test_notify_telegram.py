"""Telegram: длинное сообщение, отказ сети, адресат по умолчанию.

Сети здесь нет: `requests.post` подменяется. Живой чат — задачи 5.3 и 5.4
и приёмка фазы 7, и они делаются явно, потому что отправленное не отзывается.
"""
from __future__ import annotations

import pytest
import requests

from listam.adapters.notify_telegram import LIMIT, TelegramNotifier, split_message
from listam.ports.notifier import NotifyError


class Answer:
    def __init__(self, ok=True, status=200, text='{"ok":true}'):
        self.ok, self.status_code, self.text = ok, status, text

    def json(self):
        return {"ok": self.ok, "description": "нет"}


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    """Пауза между частями — забота живого канала, а не батареи."""
    monkeypatch.setattr("listam.adapters.notify_telegram.time.sleep", lambda seconds: None)


def test_a_message_goes_to_the_chat_from_the_config(monkeypatch):
    sent = []

    def post(url, json, timeout):
        sent.append((url, json))
        return Answer()

    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post", post)
    TelegramNotifier(token="123:abc", chat_id="-100500").send("Заявка R-1 — 3 новых")

    url, payload = sent[0]
    assert url.endswith("/bot123:abc/sendMessage")
    assert payload["chat_id"] == "-100500"
    assert payload["text"] == "Заявка R-1 — 3 новых"


def test_an_explicit_addressee_wins(monkeypatch):
    sent = []
    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post",
                        lambda url, json, timeout: sent.append(json) or Answer())

    TelegramNotifier(token="t", chat_id="-100500").send("текст", to="-100777")

    assert sent[0]["chat_id"] == "-100777"


def test_a_refusal_of_the_channel_is_a_notify_error(monkeypatch):
    """Чужое исключение наружу не выпускается: команда ловит `NotifyError`
    и не обязана знать, чем адаптер ходит в сеть."""
    monkeypatch.setattr(
        "listam.adapters.notify_telegram.requests.post",
        lambda url, json, timeout: Answer(ok=False, status=403,
                                          text='{"ok":false,"description":"forbidden"}'),
    )

    with pytest.raises(NotifyError):
        TelegramNotifier(token="t", chat_id="-1").send("текст")


def test_a_network_failure_does_not_leak_the_token(monkeypatch):
    """`requests` кладёт адрес запроса в текст исключения, а в адресе Bot API
    лежит токен. Отчёт команды печатается в консоль и в журнал планировщика —
    токен туда уйти не должен."""
    def post(url, json, timeout):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post", post)

    with pytest.raises(NotifyError) as caught:
        TelegramNotifier(token="8833:SECRET", chat_id="-1").send("текст")

    assert "8833:SECRET" not in str(caught.value)


def test_a_long_message_is_split_by_sections():
    """Резать посреди строки нельзя: обрезанная ссылка — это несостоявшийся
    звонок. Режем по разделам, в крайнем случае — по строкам."""
    section = "Заявка R-1\n" + "\n".join(f"  • строка {i}" for i in range(200))
    text = "\n\n".join([section] * 4)

    parts = split_message(text)

    assert len(parts) > 1
    assert all(len(part) <= LIMIT for part in parts)
    assert "".join(parts).count("https") == text.count("https")


def test_a_short_message_stays_one_piece():
    assert split_message("Заявка R-1 — 3 новых") == ["Заявка R-1 — 3 новых"]


def test_two_requests_never_share_a_message():
    """Брокер пересылает раздел клиенту (решение 4 спеки). Два клиента в одном
    сообщении — это имя и бюджет одного, пересланные другому."""
    text = ("Что нового со вчера: с прошлой отправки\n\n"
            "Заявка R-1 (Ани) — новый: 1\n  • 85 баллов\n\n"
            "Заявка R-2 (Давид) — новый: 1\n  • 70 баллов")

    parts = split_message(text)

    assert parts == [
        "Что нового со вчера: с прошлой отправки\n\nЗаявка R-1 (Ани) — новый: 1\n  • 85 баллов",
        "Заявка R-2 (Давид) — новый: 1\n  • 70 баллов",
    ]


def test_a_section_longer_than_the_limit_is_cut_between_lines():
    lines = [f"  • строка {i:04d} https://www.list.am/ru/item/{i}" for i in range(300)]
    text = "Заявка R-1 — новый: 300\n" + "\n".join(lines)

    parts = split_message(text)

    assert len(parts) > 1
    assert all(len(part) <= LIMIT for part in parts)
    assert "\n".join(parts) == text


def test_every_part_is_sent_in_order(monkeypatch):
    sent = []
    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post",
                        lambda url, json, timeout: sent.append(json["text"]) or Answer())

    TelegramNotifier(token="t", chat_id="-1").send("Заявка R-1\n\nЗаявка R-2\n\nЗаявка R-3")

    assert sent == ["Заявка R-1", "Заявка R-2", "Заявка R-3"]


def test_a_refusal_in_the_middle_says_how_much_already_arrived(monkeypatch):
    """Журнал при отказе не пишется (решение 2 спеки), и следующий запуск пошлёт
    всё заново — включая дошедшее. Брокер должен узнать об этом из отчёта,
    а не из дублей в чате."""
    answers = iter([Answer(), Answer(), Answer(ok=False, status=429, text="Too Many Requests")])
    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post",
                        lambda url, json, timeout: next(answers))

    with pytest.raises(NotifyError) as caught:
        TelegramNotifier(token="t", chat_id="-1").send(
            "Заявка R-1\n\nЗаявка R-2\n\nЗаявка R-3\n\nЗаявка R-4")

    assert "доставлено 2 из 4" in str(caught.value)


def test_describe_names_the_channel_but_not_the_token():
    text = TelegramNotifier(token="8833:SECRET", chat_id="1930501720").describe()

    assert "Telegram" in text
    assert "8833:SECRET" not in text
