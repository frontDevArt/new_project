"""Загрузка конфигурации.

Правило проекта: ни один путь, идентификатор и ключ не зашит в код.
Всё внешнее приходит отсюда: config/<env>.yaml + переменные окружения.
Секреты — только в переменных окружения, в yaml лежат ссылки ${VAR}.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# `${VAR}` — обязательный секрет: без него конфиг не грузится. `${VAR:-}` —
# необязательный: пусто — значит пусто, и отказать вправе только тот, кому
# он нужен. Канал уведомлений — не ключ обхода.
PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-)?\}")


class ConfigError(Exception):
    """Конфигурация не найдена, неполна или ссылается на пустую переменную."""


class Config:
    def __init__(self, data: dict, env: str, path: Path):
        self.data = data
        self.env = env
        self.path = path

    def get(self, key: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, key: str) -> Any:
        sentinel = object()
        value = self.get(key, sentinel)
        if value is sentinel or value is None:
            raise ConfigError(f"В конфиге {self.path} не задан обязательный ключ: {key}")
        return value

    def section(self, key: str) -> dict:
        value = self.get(key, {}) or {}
        if not isinstance(value, dict):
            raise ConfigError(f"Ключ {key} в {self.path} должен быть секцией")
        return value

    def __repr__(self) -> str:  # pragma: no cover - диагностика
        return f"Config(env={self.env!r}, path={self.path!r})"


def _substitute(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _substitute(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(v) for v in node]
    if isinstance(node, str):
        def replace(m: re.Match) -> str:
            name, optional = m.group(1), m.group(2)
            value = os.environ.get(name)
            if value is None or value == "":
                if optional:
                    return ""
                raise ConfigError(
                    f"Переменная окружения {name} не задана, а конфиг на неё ссылается. "
                    f"Добавь её в .env (образец — .env.example)."
                )
            return value
        return PLACEHOLDER.sub(replace, node)
    return node


def load_config(
    env: str | None = None,
    config_dir: str | Path = "config",
    dotenv_path: str | Path = ".env",
) -> Config:
    """Читает .env (не перетирая уже заданное окружение), затем config/<env>.yaml."""
    dotenv_path = Path(dotenv_path)
    if dotenv_path.exists():
        load_dotenv(dotenv_path, override=False)
    env = env or os.environ.get("APP_ENV", "dev")
    path = Path(config_dir) / f"{env}.yaml"
    if not path.exists():
        raise ConfigError(f"Нет файла конфигурации {path} (APP_ENV={env})")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Конфиг {path} должен быть отображением ключ→значение")
    return Config(_substitute(raw), env=env, path=path)

_MISSING = object()


def threshold(config: Config, key: str, default: Any) -> Any:
    """Порог из конфига. `null` — выключено (None), число — число, в том числе 0.

    `or default` здесь нельзя: ноль — это заданное значение, и на порогах
    безопасности он означает самый строгий режим, а не отсутствие проверки.
    Ключа в конфиге нет — берётся значение по умолчанию: человек про этот порог
    ничего не сказал, и молча снимать его нельзя.
    """
    value = config.get(key, _MISSING)
    if value is _MISSING:
        return default
    return value


def _number(key: str, value: Any) -> float:
    """Число из yaml. `true` — не число, хотя Python считает его единицей,
    а строка «десять» давала трейсбек `ValueError` вместо ответа человеку."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{key} = {value!r} не годится: здесь нужно число, без кавычек.")
    return float(value)


def switch(config: Config, key: str, default: bool) -> bool:
    """Тумблер: `true` или `false`; `null` — выключено.

    Строка `"false"` в yaml — это не «нет», а непустая строка, и `bool()`
    читал её как «да»: выключенный человеком вид уведомлений продолжал слать.
    """
    value = threshold(config, key, default)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ConfigError(
            f"{key} = {value!r} не годится: тумблер — это true или false без "
            f"кавычек. Строка {value!r} — не ответ «да» или «нет»."
        )
    return value


def hours(config: Config, key: str, default: float) -> float:
    """Окно в часах: число не меньше нуля. `null` — «как по умолчанию».

    Отрицательное окно смотрит в будущее и отвечает «событий нет» — то есть
    выглядит как спокойный рынок. Ноль — это ноль: окно пустое, но честное.
    """
    value = threshold(config, key, default)
    if value is None:
        return float(default)
    number = _number(key, value)
    if number < 0:
        raise ConfigError(
            f"{key} = {value} не годится: окно в часах не бывает отрицательным — "
            f"оно смотрело бы в будущее и отвечало «событий нет»."
        )
    return number


def positive(config: Config, key: str, default: Any) -> Any:
    """Порог, который обязан быть больше нуля. `null` по-прежнему «выключено».

    Правило командной строки («--limit 0 не годится: меньше одной строки
    показывать нечего») ровно так же верно для конфига. Ноль, пришедший
    из yaml, до сих пор давал витрину из одной строки «…и ещё 30» — то есть
    молча прятал весь ответ. Отклонять такое нужно там же, где читают.
    """
    value = threshold(config, key, default)
    if value is None:
        return None
    number = _number(key, value)
    if number <= 0:
        raise ConfigError(
            f"{key} = {value} не годится: это счётчик, и меньше единицы он "
            f"ничего не показывает. Чтобы снять ограничение, ставят null."
        )
    return value


def score_threshold(config: Config, key: str, default: Any) -> float | None:
    """Порог балла. `null` — «порога нет», число — число, но только 0…100.

    Балл по построению лежит в 0…100. Порог 170 не сработает никогда: он
    молча выключает уведомления и отвечает «горячих 0» — то есть выглядит
    как спокойный рынок. Бессмысленное значение в конфиге отклоняется так же,
    как бессмысленный флаг: на входе и кодом 2.
    """
    value = threshold(config, key, default)
    if value is None:
        return None
    number = _number(key, value)
    if not 0 <= number <= 100:
        raise ConfigError(
            f"{key} = {value} не годится: балл — это шкала от 0 до 100, "
            f"и порог за её краем не сработает никогда. Выше ста нет ничего, "
            f"ниже нуля — тоже; чтобы снять порог, ставят null."
        )
    return number
