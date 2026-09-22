"""Сборка реализаций по конфигу.

Единственное место, где код знает имена адаптеров. Всё остальное работает
с портами. Переезд на другой аккаунт или хранилище — правка конфига.
"""
from __future__ import annotations

from pathlib import Path

from listam.config import Config, ConfigError
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

        _guard_profile_dir(config)
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


# Каталоги, которые прогон считает рабочими: профиль браузера не имеет права
# стоять ни на одном из них. Адаптер сносит профиль целиком, когда тот протух.
WORKING_DIRS = (
    "storage.work_dir",
    "storage.directory",    # при storage.kind: local здесь общая копия базы и бэкапы
    "scrape.pages_dir",
    "export.path",
)


def _guard_profile_dir(config: Config) -> None:
    """Не даёт назначить профилем браузера рабочую папку или её родителя.

    `profile_dir: ./data` — опечатка ценой в боевую базу: протухший профиль
    адаптер сносит целиком.
    """
    profile = config.get("scrape.profile_dir")
    if not profile:
        return
    profile = Path(profile).resolve()
    for key in WORKING_DIRS:
        value = config.get(key)
        if not value:
            continue
        other = Path(value).resolve()
        if other == profile or other.is_relative_to(profile):
            raise ConfigError(
                f"scrape.profile_dir = {profile} накрывает {key} = {other}. "
                f"Протухший профиль сносится целиком — назначь ему отдельную папку."
            )


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
    if kind == "telegram":
        from listam.adapters.notify_telegram import DEFAULT_TIMEOUT, TelegramNotifier

        token = config.get("notify.token")
        chat_id = config.get("notify.chat_id")
        if not token or not chat_id:
            raise ConfigError(
                "notify.kind = telegram, но notify.token или notify.chat_id пуст: "
                "секреты живут в .env (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID), "
                "а конфиг на них ссылается. Отправлять в никуда мы не будем."
            )
        return TelegramNotifier(
            token=str(token), chat_id=str(chat_id),
            timeout=float(config.get("notify.timeout_seconds", DEFAULT_TIMEOUT)),
        )
    raise _unknown("notify", kind, ["none", "stdout", "telegram"])


def notify_channel(config: Config) -> str:
    """Как называется канал в журнале отправок: `none`, `stdout`, `telegram`.

    Имя берётся из конфига, а не из собранного адаптера: пробному прогону
    канал не нужен (секретов может не быть), а окно он обязан показать то же,
    что увидит настоящая отправка.
    """
    kind = _kind(config, "notify", "none")
    return "none" if kind in ("none", "null") else kind


def build_requests_source(config: Config) -> RequestsSource:
    kind = _kind(config, "requests", "none")
    if kind in ("none", "empty"):
        return EmptyRequestsSource()
    if kind == "csv":
        from listam.adapters.requests_csv import CsvRequestsSource

        path = config.get("requests.path")
        if not path:
            raise ConfigError(
                "requests.kind = csv, но requests.path не задан: "
                "откуда читать заявки — решает конфиг, а не код."
            )
        return CsvRequestsSource(path=path)
    if kind == "gsheet":
        from listam.adapters.requests_gsheet import GSheetRequestsSource

        return GSheetRequestsSource(
            sheet_id=config.require("requests.sheet"),
            credentials_file=config.get("requests.credentials_file"),
            range_name=config.get("requests.range", "A1:Z1000"),
        )
    raise _unknown("requests", kind, ["none", "csv", "gsheet"])


def database_path(config: Config) -> Path:
    """Куда кладётся локальная рабочая копия базы на время прогона."""
    work_dir = Path(config.get("storage.work_dir", "./data"))
    return work_dir / config.get("storage.db_filename", "listam.sqlite")


def build_database(config: Config):
    """Рабочая копия базы. Реализация одна, но выбирается всё равно здесь."""
    kind = _kind(config, "database", "sqlite")
    if kind == "sqlite":
        from listam.adapters.db_sqlite import DEFAULT_BUSY_TIMEOUT_MS, SqliteDatabase

        return SqliteDatabase(
            database_path(config),
            busy_timeout_ms=config.get("storage.busy_timeout_ms", DEFAULT_BUSY_TIMEOUT_MS),
        )
    raise _unknown("database", kind, ["sqlite"])


def run_lock_path(config: Config) -> Path:
    """Файл замка лежит рядом с рабочей копией базы — он про неё и есть."""
    return database_path(config).with_suffix(".lock")


def build_run_lock(config: Config):
    from listam.adapters.run_lock import DEFAULT_STALE_AFTER, RunLock

    return RunLock(
        run_lock_path(config),
        stale_after_seconds=config.get("storage.lock_stale_seconds", DEFAULT_STALE_AFTER),
    )
