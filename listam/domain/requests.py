"""Разбор строки заявки: нестрогий по форме, строгий по смыслу.

Форма человеческая: пробелы, знак валюты, «да»/«yes»/«+» — всё читается.
Смысл — нет: «примерно 100к» в бюджете, «много» в комнатах и перевёрнутый
диапазон площади не истолковываются, а отклоняют строку целиком (решение 2).
Истолкованная наугад заявка ошибётся молча и покажет клиенту не то.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable

from listam.domain.models import Request

COLUMNS = (
    "id", "client_name", "client_phone", "status",
    "budget_max", "budget_stretch", "districts", "districts_priority", "rooms",
    "area_min", "area_max", "floor_min", "floor_max",
    "no_first_floor", "no_last_floor",
    "must_have", "nice_to_have", "floor_rules", "notes",
)

STATUSES = ("active", "paused", "closed")

TRUE_WORDS = {"да", "yes", "y", "1", "+", "true", "истина"}
FALSE_WORDS = {"нет", "no", "n", "0", "-", "false", "ложь", ""}

# Разделители в списках: запятая, точка с запятой, перевод строки.
SPLIT = re.compile(r"[,;\n]+")

# Украшения числа, которые человек пишет и которые ничего не значат:
# пробелы всех сортов и знаки валюты.
DECORATION = re.compile(r"[\s  $€₽֏]+")
# Что остаётся после украшений, обязано быть числом целиком. Обрезать
# «примерно 100к» до 100 нельзя: это не чтение, а выдумывание бюджета.
PLAIN = re.compile(r"-?\d+")
# Разряды тысяч: «120 000», «120.000», «1,234,567». От дробной части точка
# с тремя цифрами после неё неотличима, поэтому разряд читается первым.
GROUPED = re.compile(r"-?\d{1,3}(?:[.,]\d{3})+")
FRACTIONAL = re.compile(r"-?\d+[.,]\d+")


class RequestParseError(Exception):
    def __init__(self, column: str, value: str | None, message: str):
        super().__init__(f"{column} = {value!r} — {message}")
        self.column = column
        self.value = "" if value is None else str(value)
        self.message = message


@dataclass
class RequestError:
    """Отклонённая строка: что, где и почему. Показывается человеку как есть."""

    row_number: int
    external_id: str | None
    column: str | None
    value: str | None
    message: str

    def render(self) -> str:
        who = f" (заявка {self.external_id})" if self.external_id else ""
        return (f"строка {self.row_number}{who}: "
                f"{self.column} = {self.value!r} — {self.message}")


def _text(row: dict, column: str) -> str:
    return str(row.get(column, "") or "").strip()


def _number(row: dict, column: str) -> float | None:
    raw = _text(row, column)
    if not raw:
        return None
    cleaned = DECORATION.sub("", raw)
    if GROUPED.fullmatch(cleaned):
        return float(re.sub(r"[.,]", "", cleaned))
    if FRACTIONAL.fullmatch(cleaned):
        return float(cleaned.replace(",", "."))
    if PLAIN.fullmatch(cleaned):
        return float(cleaned)
    raise RequestParseError(column, raw, "не число")


def _integer(row: dict, column: str) -> int | None:
    value = _number(row, column)
    if value is None:
        return None
    if value != int(value):
        raise RequestParseError(column, _text(row, column), "не целое число")
    return int(value)


def _flag(row: dict, column: str) -> bool:
    raw = _text(row, column).lower()
    if raw in TRUE_WORDS:
        return True
    if raw in FALSE_WORDS:
        return False
    raise RequestParseError(column, raw, "не да и не нет")


def _list(row: dict, column: str) -> list[str]:
    raw = _text(row, column)
    return [part.strip() for part in SPLIT.split(raw) if part.strip()]


def _rooms(row: dict) -> list[int]:
    raw = _text(row, "rooms")
    if not raw:
        return []
    values: set[int] = set()
    for part in SPLIT.split(raw):
        part = part.strip()
        if not part:
            continue
        span = re.fullmatch(r"(\d+)\s*[-–—]\s*(\d+)", part)
        if span:
            low, high = int(span.group(1)), int(span.group(2))
            if low > high:
                raise RequestParseError("rooms", part, "диапазон задом наперёд")
            values.update(range(low, high + 1))
            continue
        if not part.isdigit():
            raise RequestParseError("rooms", part, "не число комнат")
        values.add(int(part))
    return sorted(values)


def parse_row(row: dict, row_number: int = 0) -> Request:
    """Одна строка источника в заявку. Непонятное значение — `RequestParseError`."""
    external_id = _text(row, "id")
    if not external_id:
        raise RequestParseError("id", external_id, "заявка без идентификатора")

    status = _text(row, "status").lower() or "active"
    if status not in STATUSES:
        raise RequestParseError("status", status,
                                f"неизвестный статус; бывают: {', '.join(STATUSES)}")

    budget_max = _number(row, "budget_max")
    budget_stretch = _number(row, "budget_stretch")
    if budget_max is not None and budget_stretch is not None and budget_stretch < budget_max:
        raise RequestParseError("budget_stretch", _text(row, "budget_stretch"),
                                "растянутый бюджет меньше основного")

    area_min = _number(row, "area_min")
    area_max = _number(row, "area_max")
    if area_min is not None and area_max is not None and area_max < area_min:
        raise RequestParseError("area_max", _text(row, "area_max"),
                                "верхняя граница площади ниже нижней")

    floor_min = _integer(row, "floor_min")
    floor_max = _integer(row, "floor_max")
    if floor_min is not None and floor_max is not None and floor_max < floor_min:
        raise RequestParseError("floor_max", _text(row, "floor_max"),
                                "верхний этаж ниже нижнего")

    districts = _list(row, "districts")
    priority = _list(row, "districts_priority")
    outside = [item for item in priority if districts and item not in districts]
    if outside:
        raise RequestParseError("districts_priority", ", ".join(outside),
                                "приоритетный район не входит в список допустимых")

    return Request(
        external_id=external_id,
        client_name=_text(row, "client_name") or None,
        client_phone=_text(row, "client_phone") or None,
        status=status,
        budget_max=budget_max,
        budget_stretch=budget_stretch,
        districts=districts,
        districts_priority=priority,
        rooms=_rooms(row),
        area_min=area_min,
        area_max=area_max,
        floor_min=floor_min,
        floor_max=floor_max,
        no_first_floor=_flag(row, "no_first_floor"),
        no_last_floor=_flag(row, "no_last_floor"),
        must_have=_text(row, "must_have") or None,
        nice_to_have=_text(row, "nice_to_have") or None,
        floor_rules=_text(row, "floor_rules") or None,
        notes=_text(row, "notes") or None,
        source_row=json.dumps(row, ensure_ascii=False, sort_keys=True),
    )


def parse_rows(rows: Iterable[dict]) -> tuple[list[Request], list[RequestError]]:
    """Разобранные заявки и отклонённые строки. Одна опечатка не стоит остальных."""
    parsed: list[Request] = []
    errors: list[RequestError] = []
    for number, row in enumerate(rows, start=1):
        try:
            parsed.append(parse_row(row, row_number=number))
        except RequestParseError as exc:
            errors.append(RequestError(
                row_number=number,
                external_id=str(row.get("id") or "").strip() or None,
                column=exc.column, value=exc.value, message=exc.message,
            ))
    return parsed, errors
