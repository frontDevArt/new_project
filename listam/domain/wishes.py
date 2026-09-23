"""Словарь пожеланий: слово брокера → условие на поле страницы объявления.

Решения 10 и 11 спеки. Словарь живёт в конфиге (`funnel.wishes`), здесь —
как его читать и как проверять условие. Сети и базы нет.

Значения полей — слова сайта строчными («косметический», «панельное»), и
словарь пишется теми же словами: так брокер сверяет его со страницей глазами.

Ответ проверки — три значения, и путать их нельзя:
`True` — поле известно и подходит, `False` — известно и не подходит,
`None` — поля нет на странице или страница не открыта. `None` не отказ:
узнать больше нечем, брокер уточнит звонком.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from listam.config import Config, ConfigError
from listam.domain.models import PageFields, Request

CONDITIONS = ("any_of", "none_of", "is", "min")

# Поле страницы словом для человека: причина отказа и строка отчёта.
FIELD_LABELS = {
    "renovation": "ремонт",
    "building_type": "тип дома",
    "balcony": "балкон",
    "elevator": "лифт",
    "ceiling_height": "потолки",
    "new_build": "новостройка",
    "furniture": "мебель",
}


def field_label(field: str) -> str:
    return FIELD_LABELS.get(field, field)


def _word(text) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


@dataclass
class Wish:
    word: str
    field: str
    any_of: list | None = None
    none_of: list | None = None
    is_: bool | None = None
    min: float | None = None

    def check(self, fields: PageFields | None) -> bool | None:
        """True/False — известно; None — поля нет или страница не открыта."""
        if fields is None:
            return None
        value = fields.values.get(self.field)
        if value is None:
            return None
        if self.is_ is not None:
            return value == self.is_ if isinstance(value, bool) else None
        if self.min is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            return float(value) >= self.min
        if isinstance(value, (bool, int, float)):
            return None
        if self.any_of is not None:
            return _word(value) in {_word(item) for item in self.any_of}
        if self.none_of is not None:
            return _word(value) not in {_word(item) for item in self.none_of}
        return None


def _refuse(word, problem: str) -> ConfigError:
    return ConfigError(
        f"funnel.wishes: «{word}» — {problem}. Пожелание — это поле страницы "
        f"(field) и одно условие: any_of, none_of (списки слов сайта), "
        f"is (true/false) или min (число)."
    )


def _wish(word: str, entry) -> Wish:
    if not isinstance(entry, dict):
        raise _refuse(word, "нужна секция {field: …, условие: …}")
    field = entry.get("field")
    if not isinstance(field, str) or not field.strip():
        raise _refuse(word, "не названо поле (field)")
    extra = sorted(set(entry) - {"field", *CONDITIONS})
    if extra:
        raise _refuse(word, f"таких условий нет: {', '.join(extra)}")
    given = [name for name in CONDITIONS if name in entry]
    if len(given) != 1:
        raise _refuse(word, "условие должно быть ровно одно, а их "
                            f"{len(given)}")
    condition = given[0]
    value = entry[condition]
    wish = Wish(word=word, field=field.strip())
    if condition in ("any_of", "none_of"):
        if not isinstance(value, list) or not value \
                or not all(isinstance(item, str) and item.strip() for item in value):
            raise _refuse(word, f"{condition} — непустой список слов")
        setattr(wish, condition, [_word(item) for item in value])
    elif condition == "is":
        if not isinstance(value, bool):
            raise _refuse(word, f"is = {value!r} — нужно true или false без кавычек")
        wish.is_ = value
    else:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _refuse(word, f"min = {value!r} — нужно число без кавычек")
        wish.min = float(value)
    return wish


def vocabulary(config: Config) -> dict[str, Wish]:
    """Словарь из `funnel.wishes`. Кривой — `ConfigError` на входе."""
    raw = config.get("funnel.wishes", None)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError("funnel.wishes должен быть словарём «слово: условие»")
    vocab: dict[str, Wish] = {}
    for word, entry in raw.items():
        key = _word(word)
        vocab[key] = _wish(key, entry)
    return vocab


def parse_wishes(text: str | None, vocab: dict[str, Wish]
                 ) -> tuple[list[Wish], list[str]]:
    """Пожелания и неизвестные слова. Разделитель — запятая; регистр и пробелы не важны."""
    wishes: list[Wish] = []
    unknown: list[str] = []
    for part in (text or "").split(","):
        word = _word(part)
        if not word:
            continue
        if word in vocab:
            wishes.append(vocab[word])
        else:
            unknown.append(word)
    return wishes, unknown


def request_wishes(request: Request, vocab: dict[str, Wish]
                   ) -> tuple[list[Wish], list[Wish]]:
    """Жёсткие (`must_have`) и мягкие (`nice_to_have`) пожелания заявки.

    Неизвестные слова отсеивает синхронизация заявок (решение 10): здесь они
    просто не учитываются — заявка, прочитанная до словаря, не падает.
    """
    must, _ = parse_wishes(request.must_have, vocab)
    nice, _ = parse_wishes(request.nice_to_have, vocab)
    return must, nice
