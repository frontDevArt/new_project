"""Курс с rate.am.

Страница — Next.js: котировки лежат не в разметке, а в потоковом payload
(self.__next_f.push) со ссылками вида "$f5" на другие объекты. Разбираем
payload, разворачиваем ссылки и берём медиану по банкам — одна касса может
выставить что угодно, медиана устойчива.
"""
from __future__ import annotations

import json
import re
import statistics
from datetime import datetime, timezone

from listam.ports.fetcher import FetchError, Fetcher
from listam.ports.rate import Rate, RateError, RateProvider

DEFAULT_URL = "https://rate.am/ru/armenian-dram-exchange-rates/banks/cash"

CHUNK = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.S)
OBJECT_LINE = re.compile(r"(?m)^([0-9a-f]+):(\{.*\})$")
PLAUSIBLE = (200.0, 600.0)  # драмов за доллар; всё вне диапазона — мусор


class RateAmProvider(RateProvider):
    def __init__(self, fetcher: Fetcher, url: str = DEFAULT_URL, prefer: str = "CASH"):
        self.fetcher = fetcher
        self.url = url
        self.prefer = prefer

    def amd_per_usd(self) -> Rate:
        try:
            html = self.fetcher.get(self.url)
        except FetchError as exc:
            raise RateError(f"rate.am не отдал страницу: {exc}") from exc

        quotes = _usd_quotes(html, prefer=self.prefer)
        if not quotes:
            raise RateError(
                f"На странице {self.url} не нашлось котировок USD — вероятно, поменялась вёрстка"
            )
        return Rate(
            value=round(statistics.median(quotes), 2),
            source="rate.am",
            fetched_at=datetime.now(timezone.utc),
            banks_counted=len(quotes),
        )


def _payload(html: str) -> str:
    chunks = CHUNK.findall(html)
    if not chunks:
        return ""
    return "".join(chunks).encode("utf-8", "ignore").decode("unicode_escape", "ignore")


def _objects(payload: str) -> dict[str, dict]:
    objects: dict[str, dict] = {}
    for match in OBJECT_LINE.finditer(payload):
        try:
            objects[match.group(1)] = json.loads(match.group(2))
        except json.JSONDecodeError:
            continue
    return objects


def _deref(value, objects: dict[str, dict], depth: int = 0):
    if depth > 6:
        return value
    if isinstance(value, str) and value.startswith("$"):
        return _deref(objects.get(value[1:]), objects, depth + 1)
    if isinstance(value, dict):
        return {k: _deref(v, objects, depth + 1) for k, v in value.items()}
    return value


def _usd_quotes(html: str, prefer: str = "CASH") -> list[float]:
    objects = _objects(_payload(html))
    quotes: list[float] = []
    for obj in objects.values():
        if not isinstance(obj, dict) or "USD" not in obj:
            continue
        usd = _deref(obj["USD"], objects)
        if not isinstance(usd, dict):
            continue
        node = None
        for key in (prefer, "CASH", "CLEARING", "CARD"):
            candidate = usd.get(key)
            if isinstance(candidate, dict):
                node = candidate
                break
        if node is None:
            continue
        mid = _mid(node.get("buy"), node.get("sell"))
        if mid is not None:
            quotes.append(mid)
    return quotes


def _mid(buy, sell) -> float | None:
    values = []
    for raw in (buy, sell):
        try:
            number = float(str(raw).replace(",", "."))
        except (TypeError, ValueError):
            continue
        if PLAUSIBLE[0] < number < PLAUSIBLE[1]:
            values.append(number)
    return sum(values) / len(values) if values else None
