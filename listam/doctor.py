"""Самопроверка окружения.

Отвечает на один вопрос: если сейчас запустить прогон — он отработает?
Проверяет конфиг, хранилище, схему базы, курс и право писать выгрузку.
Ничего в рабочих файлах не меняет.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from listam.config import Config
from listam.wiring import build_exporter, build_fetcher, build_rate_provider, build_storage, \
    database_path


@dataclass
class Check:
    name: str
    ok: bool
    details: str


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def add(self, name: str, ok: bool, details: str) -> None:
        self.checks.append(Check(name=name, ok=ok, details=details))

    def render(self) -> str:
        width = max((len(c.name) for c in self.checks), default=0)
        lines = [
            f"{'OK  ' if c.ok else 'СБОЙ'}  {c.name.ljust(width)}  {c.details}"
            for c in self.checks
        ]
        verdict = "Всё на месте — можно запускать прогон." if self.ok else \
            "Есть сбои: прогон запускать рано."
        return "\n".join(lines + ["", verdict])


def run_doctor(config: Config, check_network: bool = True) -> DoctorReport:
    report = DoctorReport()
    report.add("Конфиг", True, f"{config.path} (APP_ENV={config.env})")

    # --- хранилище ----------------------------------------------------
    try:
        storage = build_storage(config)
        result = storage.check()
        report.add("Хранилище", result.ok, result.details)
    except Exception as exc:
        report.add("Хранилище", False, str(exc))

    # --- схема базы ---------------------------------------------------
    try:
        from listam.adapters.db_sqlite import SqliteDatabase

        with tempfile.TemporaryDirectory() as tmp:
            probe = SqliteDatabase(Path(tmp) / "probe.sqlite")
            probe.connect()
            probe.migrate()
            version = probe.schema_version()
            tables = sorted(probe.table_names() - {"schema_version", "sqlite_sequence"})
            probe.close()
        report.add(
            "Схема базы", True,
            f"версия {version}, таблицы: {', '.join(tables)}; рабочий файл — {database_path(config)}",
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
