"""Самопроверка окружения.

Отвечает на один вопрос: если сейчас запустить прогон — он отработает?
Проверяет конфиг, хранилище, схему базы, курс и право писать выгрузку.
Ничего в рабочих файлах не меняет.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from listam.config import Config, threshold
from listam.crawler import DEFAULT_FRESH_MAX_PAGES, DEFAULT_FRESH_STOP_PAGES, \
    DEFAULT_MAX_GONE, DEFAULT_MAX_PAGES_DROP
from listam.domain.clustering import DEFAULT_AREA_TOLERANCE
from listam.domain.scoring import DEFAULT_STRETCH_PERCENT, DEFAULT_WEIGHTS
from listam.ports.requests_source import EmptyRequestsSource
from listam.wiring import build_exporter, build_fetcher, build_rate_provider, \
    build_requests_source, build_storage, database_path


@dataclass
class Check:
    name: str
    ok: bool
    details: str
    warn: bool = False      # работать будет, но человеку стоит про это знать


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def add(self, name: str, ok: bool, details: str, warn: bool = False) -> None:
        self.checks.append(Check(name=name, ok=ok, details=details, warn=warn))

    def render(self) -> str:
        width = max((len(c.name) for c in self.checks), default=0)
        lines = [
            f"{'СБОЙ' if not c.ok else ' ⚠  ' if c.warn else 'OK  '}  "
            f"{c.name.ljust(width)}  {c.details}"
            for c in self.checks
        ]
        if not self.ok:
            verdict = "Есть сбои: прогон запускать рано."
        elif any(check.warn for check in self.checks):
            verdict = "Запускать можно, но есть предупреждения — посмотри строки с ⚠."
        else:
            verdict = "Всё на месте — можно запускать прогон."
        return "\n".join(lines + ["", verdict])


def _printed(value) -> str:
    return "не задан" if value is None else f"{value}"


def thresholds_check(config: Config) -> Check:
    """Пороги прогона глазами человека: с чем пойдёт обход и чем это грозит.

    `doctor` предупреждает, но не чинит (решение 7 плана QA M1): конфиг — территория
    человека, и опасное значение здесь становится строкой отчёта, а не правкой файла.
    Сбоем считается только явное вредительство — порог, при котором прогон работает,
    но обещанного не делает вовсе.
    """
    values = {
        "max_pages": threshold(config, "scrape.max_pages", None),
        "fresh_stop_after_known_pages": threshold(
            config, "scrape.fresh_stop_after_known_pages", DEFAULT_FRESH_STOP_PAGES),
        "fresh_max_pages": threshold(config, "scrape.fresh_max_pages",
                                     DEFAULT_FRESH_MAX_PAGES),
        "max_gone_percent": threshold(config, "scrape.max_gone_percent", DEFAULT_MAX_GONE),
        "expected_pages_min": threshold(config, "scrape.expected_pages_min", None),
        "max_pages_drop_percent": threshold(config, "scrape.max_pages_drop_percent",
                                            DEFAULT_MAX_PAGES_DROP),
    }
    listed = ", ".join(f"{key} = {_printed(value)}" for key, value in values.items())

    harm: list[str] = []
    warn: list[str] = []

    gone = values["max_gone_percent"]
    if gone == 0:
        harm.append(
            "max_gone_percent = 0 значит «пропало хоть что-то — сбой»: снятыми не будет "
            "помечено ничего, и раздел «Снято» останется пустым навсегда. "
            "Чтобы выключить проверку, ставят null, но тогда оборванный обход пометит "
            "снятой всю базу"
        )
    elif gone is None:
        warn.append(
            "max_gone_percent: null — предохранителя нет: оборванный обход пометит "
            "снятыми все объявления, до которых не дошёл"
        )

    if values["max_pages"] is not None:
        warn.append(
            f"обход укорочен потолком окружения scrape.max_pages = {values['max_pages']}: "
            f"полным он считается, но всю ленту не видит, и снятых помечает по неполной картине"
        )

    stop = values["fresh_stop_after_known_pages"]
    if stop is None:
        warn.append(
            "fresh_stop_after_known_pages: null — останавливаться `--fresh` нечему: "
            "он дойдёт до потолка, и это будет записано ошибкой прогона"
        )
    elif stop == 0:
        warn.append(
            "fresh_stop_after_known_pages = 0 — `--fresh` встанет на первой же странице "
            "без новых объявлений"
        )

    if values["fresh_max_pages"] == 0:
        warn.append(
            "fresh_max_pages = 0 — инкрементальный обход упрётся в потолок сразу, "
            "не пройдя ни страницы"
        )

    if values["max_pages_drop_percent"] is None and values["expected_pages_min"] is None:
        warn.append(
            "недобор страниц не проверяется ничем: max_pages_drop_percent и "
            "expected_pages_min оба null"
        )

    details = "; ".join([listed] + harm + warn)
    return Check(name="Пороги прогона", ok=not harm, details=details, warn=bool(warn))


def match_check(config: Config) -> Check:
    """С чем поедет матчинг: веса факторов, пороги и допуск кластеров.

    Балл 0–100 сам по себе не говорит ничего: «68» складывается из весов,
    и если вес опечатан или обнулён, человек узнает об этом только по
    странной витрине. Конфиг `doctor` не правит (решение 7 плана QA M1) —
    он его показывает.
    """
    section = config.section("match")
    weights = config.section("match.weights") or dict(DEFAULT_WEIGHTS)
    hot = threshold(config, "match.thresholds.hot", None)
    digest = threshold(config, "match.thresholds.digest", None)
    stretch = threshold(config, "match.budget_stretch_percent", DEFAULT_STRETCH_PERCENT)
    tolerance = threshold(config, "match.cluster.area_tolerance", DEFAULT_AREA_TOLERANCE)

    listed = ", ".join(f"{name} = {_printed(value)}" for name, value in weights.items())
    details = (f"веса: {listed}; пороги: hot = {_printed(hot)}, "
               f"digest = {_printed(digest)}; budget_stretch_percent = {_printed(stretch)}; "
               f"area_tolerance = {_printed(tolerance)}")

    harm: list[str] = []
    warn: list[str] = []

    if sum(float(value or 0) for value in weights.values()) == 0:
        harm.append(
            "все веса по нулю: это не «выключено», а матчинг, который каждому "
            "объявлению ставит 0 баллов — витрина будет ранжирована ничем"
        )

    # Сбой, а не предупреждение: `settings` на таком конфиге отказывается
    # стартовать, и `doctor` обязан отвечать то же самое, что ответит команда.
    unknown = sorted(set(weights) - set(DEFAULT_WEIGHTS))
    missing = sorted(set(DEFAULT_WEIGHTS) - set(weights))
    if unknown:
        harm.append(
            f"таких факторов нет, и их вес в балл не войдёт: {', '.join(unknown)}"
        )
    if missing:
        harm.append(
            f"фактор не назван и молча выпадет из балла: {', '.join(missing)}. "
            f"Чтобы выключить — ставь вес 0"
        )

    if hot is not None and digest is not None and hot < digest:
        warn.append(
            f"hot = {hot} ниже digest = {digest}: «горячим» окажется всё, что попало "
            f"в дайджест, и немедленным уведомлением станет обычный поток"
        )

    if not section:
        warn.append(
            "секции match в конфиге нет — матчинг поедет на значениях по умолчанию "
            "из спеки; поправить их будет негде"
        )

    details = "; ".join([details] + harm + warn)
    return Check(name="Матчинг", ok=not harm, details=details, warn=bool(warn))


def requests_check(config: Config) -> Check:
    """Заработает ли матчинг: есть ли источник заявок и читается ли он.

    Ненастроенный источник — предупреждение, а не сбой: окружение исправно,
    но матчить нечего, и человек должен узнать об этом здесь, а не по пустой
    витрине матчей. Неразобранные строки — тоже предупреждение: решение 2
    спеки говорит, что опечатка не роняет чтение, но молчать о ней нельзя.
    """
    try:
        source = build_requests_source(config)
    except Exception as exc:
        return Check(name="Источник заявок", ok=False, details=str(exc))

    described = source.describe()
    if isinstance(source, EmptyRequestsSource):
        return Check(
            name="Источник заявок", ok=True, warn=True,
            details=f"{described} — матчинг работать не будет: заявок неоткуда взять",
        )

    try:
        parsed, rejected = source.read()
    except Exception as exc:
        return Check(name="Источник заявок", ok=False, details=f"{described}: {exc}")

    active = sum(1 for item in parsed if item.status == "active")
    details = f"{described}: заявок {len(parsed)}, из них активных {active}"
    if rejected:
        details += f"; не разобрано строк {len(rejected)} — {rejected[0].render()}"
    if not active:
        details += "; активных заявок нет — матчить нечего"
    return Check(name="Источник заявок", ok=True,
                 warn=bool(rejected) or not active, details=details)


def run_doctor(config: Config, check_network: bool = True) -> DoctorReport:
    report = DoctorReport()
    report.add("Конфиг", True, f"{config.path} (APP_ENV={config.env})")
    report.checks.append(thresholds_check(config))
    report.checks.append(requests_check(config))
    report.checks.append(match_check(config))

    # --- хранилище ----------------------------------------------------
    try:
        storage = build_storage(config)
        result = storage.check()
        report.add("Хранилище", result.ok, result.details)
    except Exception as exc:
        report.add("Хранилище", False, str(exc))

    # --- схема базы ---------------------------------------------------
    # Пробник во временной папке отвечает на вопрос «накатываются ли миграции
    # этим кодом», и это нужно. Но команда работает не с ним: рабочая база
    # может стоять на версии 4, и тогда `match`, `export` и `matches` откажутся
    # работать, а doctor до этой правки отвечал «OK, версия 9».
    try:
        from listam.adapters.db_sqlite import SqliteDatabase, latest_schema_version

        required = latest_schema_version()
        with tempfile.TemporaryDirectory() as tmp:
            probe = SqliteDatabase(Path(tmp) / "probe.sqlite")
            probe.connect()
            probe.migrate()
            tables = sorted(probe.table_names() - {"schema_version", "sqlite_sequence"})
            probe.close()

        working = database_path(config)
        if not working.exists():
            report.add(
                "Схема базы", True,
                f"код ждёт версию {required}, таблицы: {', '.join(tables)}; "
                f"рабочего файла {working} ещё нет — он появится первым прогоном",
            )
        else:
            live = SqliteDatabase(working)
            live.connect()
            version = live.schema_version()
            live.close()
            report.add(
                "Схема базы", version >= required,
                f"рабочий файл {working}: версия {version}, код ждёт {required}"
                + ("" if version >= required
                   else " — накати миграции: python -m listam recheck")
                + f"; таблицы: {', '.join(tables)}",
            )
    except Exception as exc:
        report.add("Схема базы", False, str(exc))

    # --- курс ---------------------------------------------------------
    fetcher = None
    try:
        kind = config.get("rate.kind", "rate_am")
        if kind != "fixed" and not check_network:
            report.add("Курс AMD→USD", True, f"источник {kind}, сеть не проверялась")
        else:
            fetcher = build_fetcher(config)
            provider = build_rate_provider(config, fetcher)
            rate = provider.amd_per_usd()
            suffix = f", банков: {rate.banks_counted}" if rate.banks_counted else ""
            report.add("Курс AMD→USD", True, f"{rate.value} ({rate.source}{suffix})")
    except Exception as exc:
        report.add("Курс AMD→USD", False, str(exc))
    finally:
        if fetcher is not None:
            fetcher.close()

    # --- выгрузка -----------------------------------------------------
    try:
        exporter = build_exporter(config)
        path = exporter.export([], name=".listam-doctor-probe.xlsx")
        path.unlink(missing_ok=True)
        report.add("Выгрузка", True, f"{config.get('export.path', './out')}, запись доступна")
    except Exception as exc:
        report.add("Выгрузка", False, str(exc))

    # --- сеть до list.am ----------------------------------------------
    if check_network:
        base_url = config.get("scrape.base_url", "")
        category = config.get("scrape.category", 60)
        fetcher = None
        try:
            fetcher = build_fetcher(config)
            html = fetcher.get(f"/category/{category}")
            report.add(
                "Доступ к list.am", True,
                f"{base_url}/category/{category}: {len(html)} байт "
                f"({config.get('scrape.kind', 'http')})",
            )
        except Exception as exc:
            report.add("Доступ к list.am", False, str(exc))
        finally:
            if fetcher is not None:
                fetcher.close()

    return report
