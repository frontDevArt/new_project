"""Сборка реализаций по конфигу.

Единственное место, где код знает имена адаптеров. Всё остальное работает
с портами. Переезд на другой аккаунт или хранилище — правка конфига.
"""
from __future__ import annotations

from pathlib import Path

from listam.config import Config
from listam.ports.exporter import Exporter
from listam.ports.fetcher import Fetcher
from listam.ports.notifier import Notifier, NullNotifier, StdoutNotifier
from listam.ports.rate import RateProvider
from listam.ports.requests_source import EmptyRequestsSource, RequestsSource
from listam.ports.storage import Storage


class UnknownAdapter(Exception):
    """В конфиге указан адаптер, которого нет."""


def _kind(config: Config, section: str, default: str) -> str:
    return str(config.get(f"{section}.kind", default))


def _unknown(section: str, kind: str, options) -> UnknownAdapter:
    return UnknownAdapter(
        f"{section}.kind = {kind!r} — такой реализации нет. Доступны: {', '.join(options)}"
    )


def build_storage(config: Config) -> Storage:
    kind = _kind(config, "storage", "local")
    if kind == "local":
        from listam.adapters.storage_local import LocalStorage

        return LocalStorage(directory=config.get("storage.directory", "./data"))
    if kind == "gdrive":
        from listam.adapters.storage_gdrive import GDriveStorage

        return GDriveStorage(
            folder_id=config.get("storage.folder"),
            credentials_file=config.get("storage.credentials_file"),
        )
    raise _unknown("storage", kind, ["local", "gdrive"])


def build_fetcher(config: Config) -> Fetcher:
    kind = _kind(config, "scrape", "http")
    if kind in ("http", "fetcher", ""):
        from listam.adapters.fetcher_http import DEFAULT_USER_AGENT, HttpFetcher

        return HttpFetcher(
            base_url=config.get("scrape.base_url"),
            delay_seconds=config.get("scrape.delay_seconds", 1.5),
            user_agent=config.get("scrape.user_agent", DEFAULT_USER_AGENT),
            timeout=config.get("scrape.timeout_seconds", 20.0),
            retries=config.get("scrape.retries", 3),
        )
    if kind == "files":
        from listam.adapters.fetcher_files import FilesFetcher

        return FilesFetcher(directory=config.get("scrape.pages_dir", "./data/pages"))
    if kind in ("playwright", "browser"):
        from listam.adapters.fetcher_playwright import PlaywrightFetcher

        return PlaywrightFetcher(
            base_url=config.get("scrape.base_url"),
            delay_seconds=config.get("scrape.delay_seconds", 1.5),
            timeout=config.get("scrape.timeout_seconds", 45.0),
            retries=config.get("scrape.retries", 3),
            headless=config.get("scrape.headless", False),
            channel=config.get("scrape.browser_channel"),
            user_data_dir=config.get("scrape.profile_dir"),
            challenge_wait_seconds=config.get("scrape.challenge_wait_seconds", 25.0),
        )
    raise _unknown("scrape", kind, ["http", "files", "playwright"])


def build_rate_provider(config: Config, fetcher: Fetcher | None) -> RateProvider:
    kind = _kind(config, "rate", "rate_am")
    if kind == "fixed":
        from listam.adapters.rate_fixed import FixedRateProvider

        return FixedRateProvider(amd_per_usd=config.get("rate.amd_per_usd", 0) or 0)
    if kind == "rate_am":
        from listam.adapters.rate_am import DEFAULT_URL, RateAmProvider

        return RateAmProvider(fetcher=fetcher, url=config.get("rate.url", DEFAULT_URL))
    raise _unknown("rate", kind, ["rate_am", "fixed"])


def build_exporter(config: Config) -> Exporter:
    kind = _kind(config, "export", "xlsx_local")
    if kind == "xlsx_local":
        from listam.adapters.exporter_xlsx import XlsxExporter

        return XlsxExporter(directory=config.get("export.path", "./out"))
    raise _unknown("export", kind, ["xlsx_local"])


def build_notifier(config: Config) -> Notifier:
    kind = _kind(config, "notify", "none")
    if kind in ("none", "null"):
        return NullNotifier()
    if kind == "stdout":
        return StdoutNotifier()
    raise _unknown("notify", kind, ["none", "stdout"])  # telegram появится на M3


def build_requests_source(config: Config) -> RequestsSource:
    kind = _kind(config, "requests", "none")
    if kind in ("none", "empty"):
        return EmptyRequestsSource()
    raise _unknown("requests", kind, ["none"])  # gsheet, csv появятся на M2


def database_path(config: Config) -> Path:
    """Куда кладётся локальная рабочая копия базы на время прогона."""
    work_dir = Path(config.get("storage.work_dir", "./data"))
    return work_dir / config.get("storage.db_filename", "listam.sqlite")
