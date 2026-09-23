"""Telegram: длинное сообщение, отказ сети, адресат по умолчанию.

Сети здесь нет: `requests.post` подменяется. Живой чат — задачи 5.3 и 5.4
и приёмка фазы 7, и они делаются явно, потому что отправленное не отзывается.
"""
from __future__ import annotations

import pytest
import requests

from listam.adapters.notify_telegram import LIMIT, TelegramNotifier, split_section
from listam.layout import Message, Section, Span, text_message, to_html
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
    TelegramNotifier(token="123:abc", chat_id="-100500").send(
        text_message("Заявка R-1 — 3 новых"))

    url, payload = sent[0]
    assert url.endswith("/bot123:abc/sendMessage")
    assert payload["chat_id"] == "-100500"
    assert payload["text"] == "Заявка R-1 — 3 новых"


def test_an_explicit_addressee_wins(monkeypatch):
    sent = []
    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post",
                        lambda url, json, timeout: sent.append(json) or Answer())

    TelegramNotifier(token="t", chat_id="-100500").send(text_message("текст"), to="-100777")

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
        TelegramNotifier(token="t", chat_id="-1").send(text_message("текст"))


def test_a_network_failure_does_not_leak_the_token(monkeypatch):
    """`requests` кладёт адрес запроса в текст исключения, а в адресе Bot API
    лежит токен. Отчёт команды печатается в консоль и в журнал планировщика —
    токен туда уйти не должен."""
    def post(url, json, timeout):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post", post)

    with pytest.raises(NotifyError) as caught:
        TelegramNotifier(token="8833:SECRET", chat_id="-1").send(text_message("текст"))

    assert "8833:SECRET" not in str(caught.value)


def sections(*heads: str) -> Message:
    """Сообщение из разделов с одной строкой шапки — по разделу на заявку."""
    return Message([Section(head=[[Span(head)]], cards=[]) for head in heads])


def fat_card(number: int) -> list:
    """Карточка из трёх строк, с тегами в HTML: цена жирным и ссылка."""
    return [[Span("🆕 "), Span(f"${number:06d}", bold=True), Span(" · " + "м² " * 60)],
            [Span(f"📍 карточка {number:03d} · " + "ул. <Раффи> & Co " * 5)],
            [Span("🔗 "), Span("Открыть", href=f"https://www.list.am/ru/item/{number}")]]


def long_section(count: int = 40) -> Section:
    return Section(head=[[Span("🔥 ЗВОНИ СЕЙЧАС · R-7 · Давид", bold=True)],
                         [Span("━━━━━━━━━━━━━━━")]],
                   cards=[fat_card(number) for number in range(count)],
                   tail=[[Span("➕ ещё 3 варианта — в дайджесте вечером")]])


def test_the_message_is_html_without_a_preview(monkeypatch):
    """Пункт 8: `parse_mode: HTML` и выключенное превью — иначе одна
    карточка займёт весь экран."""
    sent = []
    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post",
                        lambda url, json, timeout: sent.append(json) or Answer())

    TelegramNotifier(token="t", chat_id="1").send(Message([long_section(1)]))

    assert sent[0]["parse_mode"] == "HTML"
    assert sent[0]["link_preview_options"] == {"is_disabled": True}
    assert "disable_web_page_preview" not in sent[0]
    assert '<a href="https://www.list.am/ru/item/0">Открыть</a>' in sent[0]["text"]
    assert "&lt;Раффи&gt; &amp; Co" in sent[0]["text"]


def test_a_short_section_stays_one_piece():
    section = long_section(2)

    assert split_section(section) == [to_html(section)]


def test_split_only_between_cards():
    """Резать внутри карточки нельзя: обрезанный тег Telegram отклонит, а
    карточка без ссылки — несостоявшийся звонок."""
    section = long_section()
    cards = [to_html(Section(head=[], cards=[card])) for card in section.cards]

    parts = split_section(section)

    assert len(parts) > 1
    joined = "\n".join(parts)
    assert all(joined.count(card) == 1 for card in cards), "каждая карточка целиком и один раз"
    for part in parts:
        assert part.count("<b>") == part.count("</b>")
        assert part.count("<a ") == part.count("</a>")


def test_continuation_repeats_the_head():
    """L-1 аудита: часть без имени заявки — чужой разговор для клиента,
    которому брокер её переслал."""
    parts = split_section(long_section())

    assert parts[0].startswith("<b>🔥 ЗВОНИ СЕЙЧАС · R-7 · Давид</b>\n━")
    for part in parts[1:]:
        assert part.startswith("<b>🔥 ЗВОНИ СЕЙЧАС · R-7 · Давид (продолжение)</b>\n━")
    assert parts[-1].endswith("➕ ещё 3 варианта — в дайджесте вечером")
    assert sum("➕ ещё 3" in part for part in parts) == 1


def test_no_part_exceeds_the_limit():
    parts = split_section(long_section(120))

    assert all(len(part) <= LIMIT for part in parts)
    small = split_section(long_section(40), limit=1000)
    assert all(len(part) <= 1000 for part in small)
    assert len(small) > len(split_section(long_section(40)))


def test_a_card_longer_than_the_limit_is_cut_between_its_lines():
    """Нечеловеческий ввод: одна карточка длиннее лимита. Режем по строкам —
    строка остаётся целой, теги в ней закрыты."""
    huge = [[Span("строка " + "х" * 1500)] for _ in range(5)]
    section = Section(head=[[Span("шапка")]], cards=[huge])

    parts = split_section(section, limit=2000)

    assert all(len(part) <= 2000 for part in parts)
    assert sum(part.count("строка ") for part in parts) == 5


def test_two_requests_never_share_a_message(monkeypatch):
    """Брокер пересылает раздел клиенту (решение 4 спеки M3). Два клиента в
    одном сообщении — это имя и бюджет одного, пересланные другому."""
    sent = []
    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post",
                        lambda url, json, timeout: sent.append(json["text"]) or Answer())

    TelegramNotifier(token="t", chat_id="-1").send(sections("Заявка R-1", "Заявка R-2"))

    assert sent == ["Заявка R-1", "Заявка R-2"]


def test_every_part_is_sent_in_order(monkeypatch):
    sent = []
    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post",
                        lambda url, json, timeout: sent.append(json["text"]) or Answer())

    TelegramNotifier(token="t", chat_id="-1").send(
        Message([sections("Заявка R-1").sections[0], long_section()]))

    assert sent[0] == "Заявка R-1"
    assert sent[1:] == split_section(long_section())


def test_a_refusal_in_the_middle_says_how_much_already_arrived(monkeypatch):
    """Журнал при отказе не пишется (решение 2 спеки), и следующий запуск пошлёт
    всё заново — включая дошедшее. Брокер должен узнать об этом из отчёта,
    а не из дублей в чате."""
    answers = iter([Answer(), Answer(), Answer(ok=False, status=400, text="Bad Request")])
    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post",
                        lambda url, json, timeout: next(answers))

    with pytest.raises(NotifyError) as caught:
        TelegramNotifier(token="t", chat_id="-1").send(
            sections("Заявка R-1", "Заявка R-2", "Заявка R-3", "Заявка R-4"))

    assert "доставлено 2 из 4" in str(caught.value)


def test_describe_names_the_channel_but_not_the_token():
    text = TelegramNotifier(token="8833:SECRET", chat_id="1930501720").describe()

    assert "Telegram" in text
    assert "8833:SECRET" not in text


class TooMany:
    """Ответ Bot API на 429: сообщение не принято, подожди `retry_after` секунд."""

    ok, status_code = False, 429
    text = '{"ok":false,"error_code":429,"description":"Too Many Requests"}'

    def __init__(self, retry_after=5):
        self.retry_after = retry_after

    def json(self):
        return {"ok": False, "error_code": 429,
                "parameters": {"retry_after": self.retry_after}}


def five_sections() -> Message:
    return sections(*(f"Заявка R-{index} — новый: 1" for index in range(5)))


def test_a_too_many_requests_answer_is_waited_out_and_not_resent(monkeypatch):
    """H-4 аудита: 429 на третьей части из пяти давал `NotifyError`, и повтор
    слал все пять — две из них брокеру во второй раз."""
    answers = iter([Answer(), Answer(), TooMany(), Answer(), Answer(), Answer()])
    sent, waited = [], []

    def post(url, json, timeout):
        answer = next(answers)
        if answer.ok:
            sent.append(json["text"])
        return answer

    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post", post)
    monkeypatch.setattr("listam.adapters.notify_telegram.time.sleep", waited.append)

    TelegramNotifier(token="t", chat_id="1").send(five_sections())

    assert len(sent) == 5
    assert len(set(sent)) == 5, "ни одна часть не пришла дважды"
    assert 5 in waited


def test_a_channel_that_keeps_saying_wait_gives_up_in_words(monkeypatch):
    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post",
                        lambda url, json, timeout: TooMany())

    with pytest.raises(NotifyError, match="429"):
        TelegramNotifier(token="t", chat_id="1").send(text_message("Заявка R-1 — новый: 1"))


def test_a_wait_longer_than_a_minute_is_not_waited(monkeypatch):
    """Час ожидания в команде по расписанию — это зависшая команда. Дольше
    минуты не ждём: повтор сделает следующий запуск."""
    answers = iter([TooMany(retry_after=3600), Answer()])
    waited = []
    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post",
                        lambda url, json, timeout: next(answers))
    monkeypatch.setattr("listam.adapters.notify_telegram.time.sleep", waited.append)

    TelegramNotifier(token="t", chat_id="1").send(text_message("Заявка R-1 — новый: 1"))

    assert max(waited) <= 60


def test_a_connection_torn_after_the_send_is_not_repeated(monkeypatch):
    """Обрыв после отправки — часть могла дойти: повтор принёс бы брокеру
    дубль. Повторяется только соединение, которое не установилось."""
    from urllib3.exceptions import ProtocolError

    calls = []

    def post(url, json, timeout):
        calls.append(json["text"])
        raise requests.ConnectionError(ProtocolError("Connection aborted."))

    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post", post)

    with pytest.raises(NotifyError):
        TelegramNotifier(token="t", chat_id="1").send(text_message("Заявка R-1 — новый: 1"))

    assert len(calls) == 1


def test_a_connection_that_never_opened_is_tried_again(monkeypatch):
    """Соединение не установилось — ничего не ушло, и повтор дубля не даст."""
    from urllib3.exceptions import MaxRetryError, NewConnectionError

    def refused(url):
        return requests.ConnectionError(MaxRetryError(
            None, url, NewConnectionError(None, "Connection refused")))

    answers = iter([refused, Answer()])
    sent = []

    def post(url, json, timeout):
        answer = next(answers)
        if callable(answer):
            raise answer(url)
        sent.append(json["text"])
        return answer

    monkeypatch.setattr("listam.adapters.notify_telegram.requests.post", post)

    TelegramNotifier(token="t", chat_id="1").send(text_message("Заявка R-1 — новый: 1"))

    assert sent == ["Заявка R-1 — новый: 1"]

