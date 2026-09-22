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

from listam.ports.notifier import NotifyError, Notifier

DEFAULT_API = "https://api.telegram.org"
LIMIT = 4096
DEFAULT_TIMEOUT = 20.0
# Bot API просит не больше сообщения в секунду в один чат; на пачке частей
# дайджеста 429 ловится и так, поэтому пауза стоит между частями всегда.
DEFAULT_PAUSE = 1.0


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    """Сообщение, разрезанное по разделам так, чтобы ни одна строка не разорвалась.

    Шапка (первый блок, не начинающийся с «Заявка») едет вместе с первым
    разделом: одна строка «Что нового со вчера» отдельным сообщением — шум.
    """
    blocks = [block for block in text.split("\n\n") if block.strip()] or [text]
    if len(blocks) > 1 and not blocks[0].startswith("Заявка") \
            and len(blocks[0]) + 2 + len(blocks[1]) <= limit:
        blocks[:2] = [f"{blocks[0]}\n\n{blocks[1]}"]

    parts: list[str] = []
    for block in blocks:
        parts.extend(_split_lines(block, limit))
    return parts


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
            try:
                answer = requests.post(
                    f"{self.api_url}/bot{self.token}/sendMessage",
                    json={"chat_id": chat, "text": part,
                          "disable_web_page_preview": True},
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                raise NotifyError(self._hide(
                    f"Telegram не ответил: {exc}{self._progress(number, parts)}"
                )) from None
            if not getattr(answer, "ok", False):
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
