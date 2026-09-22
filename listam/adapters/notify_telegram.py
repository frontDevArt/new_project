"""Реализация Notifier поверх Telegram Bot API.

Зависимость — `requests`, она уже в проекте (ею ходит `fetcher_http`).
Секреты сюда приходят аргументами: токен и чат живут в `.env`, выбор
адаптера — в конфиге, знание имени — в `listam/wiring.py`.

Telegram режет сообщение на 4096 символах. Резать посреди строки нельзя:
обрезанная ссылка — это несостоявшийся звонок. Режем по разделам (пустая
строка), и **каждый раздел — отдельное сообщение**, даже если два влезли бы
в одно: брокер пересылает раздел клиенту (решение 4 спеки), и чужой клиент
в пересланном — это чужое имя и чужой бюджет. Раздел длиннее лимита режется
по строкам.
"""
from __future__ import annotations

import time

import requests
from urllib3.exceptions import ConnectTimeoutError, MaxRetryError

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


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    """Сообщение, разрезанное по разделам так, чтобы ни одна строка не разорвалась.

    Шапка (первый блок, не начинающийся с «Заявка») едет с первой частью
    первого раздела: одна строка «Что нового со вчера» отдельным сообщением —
    шум. Место под неё первый раздел оставляет сам, поэтому шапка отдельно
    уходит, только если она длиннее четверти лимита. Раздел длиннее лимита
    режется по строкам, и каждое продолжение начинается с заголовка заявки:
    брокер пересылает часть клиенту, и кусок без имени заявки — это чужой
    разговор.
    """
    blocks = [block for block in text.split("\n\n") if block.strip()] or [text]
    head = blocks.pop(0) if len(blocks) > 1 and not blocks[0].startswith("Заявка") else None
    attached = head is not None and len(head) + 2 <= limit // 4

    parts: list[str] = []
    for index, block in enumerate(blocks):
        if index == 0 and attached:
            pieces = _split_section(block, limit - len(head) - 2)
            pieces[0] = f"{head}\n\n{pieces[0]}"
        else:
            pieces = _split_section(block, limit)
        parts.extend(pieces)
    if head is not None and not attached:
        parts.insert(0, head)
    return parts


def _split_section(block: str, limit: int) -> list[str]:
    """Раздел заявки: целиком — или по строкам, с заголовком на каждой части."""
    if len(block) <= limit:
        return [block]
    title = block.splitlines()[0][: limit // 4] + CONTINUED
    pieces = _split_lines(block, limit - len(title) - 1)
    return [pieces[0]] + [f"{title}\n{piece}" for piece in pieces[1:]]


def _split_lines(block: str, limit: int) -> list[str]:
    if len(block) <= limit:
        return [block]
    parts: list[str] = []
    current = ""
    for line in block.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        # Строка длиннее лимита целиком — такое бывает только у нечеловеческого
        # ввода; режем как есть, потому что альтернатива — не отправить вовсе.
        while len(line) > limit:
            parts.append(line[:limit])
            line = line[limit:]
        current = line
    if current:
        parts.append(current)
    return parts


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

    def send(self, text: str, to: str | None = None) -> None:
        chat = to or self.chat_id
        parts = split_message(text)
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
                    json={"chat_id": chat, "text": part,
                          "disable_web_page_preview": True},
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
