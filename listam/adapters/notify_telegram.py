"""Реализация Notifier поверх Telegram Bot API.

Зависимость — `requests`, она уже в проекте (ею ходит `fetcher_http`).
Секреты сюда приходят аргументами: токен и чат живут в `.env`, выбор
адаптера — в конфиге, знание имени — в `listam/wiring.py`.

Сообщение приходит структурой (`layout.Message`) и уходит HTML
(`parse_mode: HTML`, превью ссылок выключено — пункт 8 анализа после M3).
**Каждый раздел — отдельное сообщение**, даже если два влезли бы в одно:
брокер пересылает раздел клиенту (решение 4 спеки M3), и чужой клиент
в пересланном — это чужое имя и чужой бюджет.

Telegram режет сообщение на 4096 символах, а HTML с оборванным тегом
отклоняет целиком. Поэтому раздел длиннее лимита режется **только между
карточками**, и каждое продолжение начинается с шапки заявки
« (продолжение)». Карточку длиннее лимита (нечеловеческий ввод) режем по её
строкам: строка — законченный кусок HTML, теги в ней закрыты.
"""
from __future__ import annotations

import html
import time

import requests
from urllib3.exceptions import ConnectTimeoutError, MaxRetryError

from listam.layout import Line, Message, Section, Span, to_html
from listam.ports.notifier import NotifyError, Notifier

DEFAULT_API = "https://api.telegram.org"
LIMIT = 4096
DEFAULT_TIMEOUT = 20.0
# Bot API просит не больше сообщения в секунду в один чат; на пачке частей
# дайджеста 429 ловится и так, поэтому пауза стоит между частями всегда.
DEFAULT_PAUSE = 1.0
MAX_RETRIES = 3          # сколько раз повторить часть, которую Telegram точно не принял
MAX_WAIT = 60.0          # дольше минуты не ждём: повторит следующий запуск


CONTINUED = " (продолжение)"


def split_section(section: Section, limit: int = LIMIT) -> list[str]:
    """Раздел заявки HTML-частями не длиннее `limit`, разрезанный между карточками.

    Первая часть — с шапкой, каждое продолжение — с шапкой « (продолжение)»:
    брокер пересылает часть клиенту, и кусок без имени заявки — чужой
    разговор. Хвост («➕ ещё …») едет с последней частью.
    """
    whole = to_html(section)
    if len(whole) <= limit:
        return [whole]

    again = _continued(section.head)
    budget = limit - len(to_html(Section(head=again, cards=[]))) - 2
    parts: list[str] = []
    head, current = section.head, []
    for unit in _units(section.cards, budget):
        if current and len(to_html(Section(head=head, cards=current + [unit]))) > limit:
            parts.append(to_html(Section(head=head, cards=current)))
            head, current = again, []
        current.append(unit)
    last = Section(head=head, cards=current, tail=section.tail)
    if current and len(to_html(last)) > limit:
        parts.append(to_html(Section(head=head, cards=current)))
        last = Section(head=again, cards=[], tail=section.tail)
    parts.append(to_html(last))
    return parts


def _continued(head: list[Line]) -> list[Line]:
    """Шапка продолжения: к первой строке приписано « (продолжение)»."""
    if not head or not head[0]:
        return [[Span(CONTINUED.strip())]] + head[1:]
    first = list(head[0])
    last = first[-1]
    first[-1] = Span(last.text + CONTINUED, bold=last.bold, href=last.href)
    return [first] + head[1:]


def _units(cards: list[list[Line]], budget: int) -> list[list[Line]]:
    """Карточки как есть; карточка длиннее бюджета — по строке на часть,
    строка длиннее бюджета — кусками простого текста (без тегов)."""
    units: list[list[Line]] = []
    for card in cards:
        if len(to_html(Section(head=[], cards=[card]))) <= budget:
            units.append(card)
            continue
        for line in card:
            if len(to_html(Section(head=[], cards=[[line]]))) <= budget:
                units.append([line])
            else:
                units.extend([[Span(piece)]] for piece in _pieces(line, budget))
    return units


def _pieces(line: Line, budget: int) -> list[str]:
    text = "".join(f"{span.text} {span.href}" if span.href else span.text
                   for span in line)
    pieces, current = [], ""
    for char in text:
        if len(html.escape(current + char, quote=False)) > budget:
            pieces.append(current)
            current = ""
        current += char
    if current:
        pieces.append(current)
    return pieces


def _retry_after(answer) -> float | None:
    """Сколько секунд Telegram просит подождать. Не 429 — не просит."""
    if getattr(answer, "status_code", None) != 429:
        return None
    try:
        return float(answer.json()["parameters"]["retry_after"])
    except (ValueError, KeyError, TypeError):
        return DEFAULT_PAUSE


def _never_connected(exc: requests.ConnectionError) -> bool:
    """Соединение не установилось — значит, часть точно не ушла.

    `requests.ConnectionError` зовёт так и обрыв уже после отправки
    («Connection aborted»): там часть могла дойти, и повтор дал бы дубль.
    Отказ соединиться `requests` приносит как `MaxRetryError` с причиной
    `ConnectTimeoutError` (её подкласс — `NewConnectionError`).
    """
    if isinstance(exc, requests.ConnectTimeout):
        return True
    reason = exc.args[0] if exc.args else None
    return (isinstance(reason, MaxRetryError)
            and isinstance(reason.reason, ConnectTimeoutError))


class TelegramNotifier(Notifier):
    def __init__(self, token: str, chat_id: str, timeout: float = DEFAULT_TIMEOUT,
                 api_url: str = DEFAULT_API, pause: float = DEFAULT_PAUSE):
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout
        self.api_url = api_url.rstrip("/")
        self.pause = pause

    def send(self, message: Message, to: str | None = None) -> None:
        chat = to or self.chat_id
        parts = [part for section in message.sections for part in split_section(section)]
        for number, part in enumerate(parts):
            if number:
                time.sleep(self.pause)
            self._deliver(chat, part, number, parts)

    def _deliver(self, chat: str, part: str, number: int, parts: list[str]) -> None:
        """Одна часть. Повтор — только там, где Telegram её точно не принял.

        429 — «подожди N секунд», и сообщение не доставлено; соединение,
        которое не установилось, тоже ничего не доставило. А обрыв после
        отправки (таймаут чтения, «Connection aborted») не повторяется: часть
        могла уйти, и брокер получил бы её дважды.
        """
        for attempt in range(MAX_RETRIES + 1):
            last = attempt == MAX_RETRIES
            try:
                answer = requests.post(
                    f"{self.api_url}/bot{self.token}/sendMessage",
                    json={"chat_id": chat, "text": part, "parse_mode": "HTML",
                          "link_preview_options": {"is_disabled": True}},
                    timeout=self.timeout,
                )
            except requests.ConnectionError as exc:
                if _never_connected(exc) and not last:
                    time.sleep(self.pause)
                    continue
                raise NotifyError(self._hide(
                    f"Telegram не ответил: {exc}{self._progress(number, parts)}"
                )) from None
            except requests.RequestException as exc:
                raise NotifyError(self._hide(
                    f"Telegram не ответил: {exc}{self._progress(number, parts)}"
                )) from None
            if getattr(answer, "ok", False):
                return
            wait = _retry_after(answer)
            if wait is not None and not last:
                # Час ожидания в команде по расписанию — зависшая команда.
                # Ждём не дольше минуты; если Telegram всё ещё занят, он
                # ответит 429 снова, и на последней попытке это отказ словами.
                time.sleep(min(wait, MAX_WAIT))
                continue
            raise NotifyError(self._hide(
                f"Telegram отказал (код {answer.status_code}): {answer.text}"
                f"{self._progress(number, parts)}"
            ))

    @staticmethod
    def _progress(delivered: int, parts: list[str]) -> str:
        if len(parts) == 1:
            return ""
        return (f"; доставлено {delivered} из {len(parts)} сообщений"
                + (" — при повторе они придут ещё раз" if delivered else ""))

    def _hide(self, message: str) -> str:
        """`requests` кладёт адрес запроса в текст исключения, а в адресе — токен."""
        return message.replace(self.token, "<token>") if self.token else message

    def describe(self) -> str:
        tail = str(self.chat_id)[-4:] if self.chat_id else "?"
        return f"Telegram, чат …{tail} (notify.kind: telegram)"
