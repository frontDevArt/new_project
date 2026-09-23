"""Песочница имитации: конфиг из config/prod.yaml, но всё боевое заменено.

Остаётся от боевого всё, что меряется: циклы, пороги подбора, воронка,
вёрстка, отказы. Заменяется всё, что трогает мир: хранилище, лог, заявки,
курс, канал уведомлений, источник страниц. Отказ `refuse` — последняя
линия: конфиг, который сюда дошёл с живым адаптером, не запускается.
"""
from __future__ import annotations

import contextlib
import copy
from pathlib import Path
from typing import Iterator

import yaml

from listam.config import Config
from tests.sim.clock import listam_modules
from tests.sim.market import AMD_PER_USD  # курс конфига — тот же, что у цен рынка

PROJECT = Path(__file__).resolve().parents[2]

# Пример заявок — дословно из плана M3.5, раздел «Пример заявок».
EXAMPLE_REQUESTS = """\
id,client_name,client_phone,status,budget_max,budget_stretch,districts,districts_priority,rooms,area_min,area_max,floor_min,floor_max,no_first_floor,no_last_floor,must_have,nice_to_have,floor_rules,notes
R-1,ПРИМЕР узкая,,active,120 000 $,,Арабкир,Арабкир,3,70,95,2,,да,нет,,,,без пожеланий: страницы не нужны
R-2,ПРИМЕР средняя,,active,175 000 $,190000,"Канакер-Зейтун, Нор Норк",Канакер-Зейтун,2-3,80,110,,,да,да,ремонт,"балкон, лифт",,пожелания со страницы
R-3,ПРИМЕР широкая,,active,250 000 $,,"Кентрон, Арабкир, Давташен",Кентрон,2-4,60,,,,нет,нет,не панель,евроремонт,,нарочно широкая: проверка первичной подборки
"""

PATH_KEYS = ("storage.directory", "storage.work_dir", "schedule.log_dir",
             "requests.path", "scrape.pages_dir", "export.path")


class SimRefused(Exception):
    """Конфиг имитации достаёт до боевого — запускать нельзя."""


def build_config(out: Path, *, config_dir: Path = PROJECT / "config") -> Config:
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    data = copy.deepcopy(yaml.safe_load((config_dir / "prod.yaml").read_text(encoding="utf-8")))
    data["env"] = "sim"
    storage = data["storage"]
    for key in ("folder", "credentials_file"):
        storage.pop(key, None)
    storage.update(kind="local", directory=str(out / "store"), work_dir=str(out / "work"),
                   db_filename="listam-sim.sqlite", keep_backups=2)
    data["schedule"]["log_dir"] = str(out / "logs")
    data["scrape"].update(kind="files", pages_dir=str(out / "no-pages"), delay_seconds=0)
    data["funnel"]["delay_seconds"] = 0
    data["rate"] = {"kind": "fixed", "amd_per_usd": AMD_PER_USD}
    notify = data["notify"]
    notify["kind"] = "stdout"
    notify.pop("token", None)
    notify.pop("chat_id", None)
    data["requests"] = {"kind": "csv", "path": str(out / "requests.csv")}
    data["export"]["path"] = str(out / "export")

    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    if "${" in text:
        raise SimRefused("в конфиге имитации осталась ссылка на .env: " +
                         text[text.index("${"):][:60])
    (out / "sim.yaml").write_text(text, encoding="utf-8")
    (out / "requests.csv").write_text(EXAMPLE_REQUESTS, encoding="utf-8")
    return Config(data, env="sim", path=out / "sim.yaml")


def refuse(config: Config, project: Path = PROJECT) -> None:
    problems = []
    if config.get("notify.kind") not in ("stdout", "none"):
        problems.append(f"notify.kind = {config.get('notify.kind')!r} — брокеру ушло бы")
    if config.get("rate.kind") != "fixed":
        problems.append(f"rate.kind = {config.get('rate.kind')!r} — пошёл бы в сеть")
    if config.get("scrape.kind") != "files":
        problems.append(f"scrape.kind = {config.get('scrape.kind')!r} — пошёл бы на list.am")
    live = (Path(project) / "data").resolve()
    for key in PATH_KEYS:
        value = config.get(key)
        if value is None:
            continue
        path = Path(value).resolve()
        if path == live or live in path.parents:
            problems.append(f"{key} = {value} — внутри {live}")
    if problems:
        raise SimRefused("имитация не запускается: " + "; ".join(problems))


@contextlib.contextmanager
def patched_fetcher(fetcher) -> Iterator[None]:
    """`build_fetcher` каждого модуля listam отдаёт подставной list.am."""
    from listam import wiring

    real = wiring.build_fetcher

    def build(config, delay_seconds=None):
        return fetcher

    patched = [module for module in listam_modules()
               if vars(module).get("build_fetcher") is real]
    for module in patched:
        module.build_fetcher = build
    try:
        yield
    finally:
        for module in patched:
            module.build_fetcher = real
