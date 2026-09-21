"""Командная строка инструмента.

    python -m listam doctor          проверка окружения
    python -m listam scrape          пройти по ленте и обновить базу
    python -m listam export          выгрузить текущую базу в .xlsx

Окружение выбирается переменной APP_ENV или флагом --env.
"""
from __future__ import annotations

import argparse
import sys

from listam.config import ConfigError, load_config
from listam.doctor import run_doctor
from listam.wiring import build_exporter, build_storage, database_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="listam", description="Мониторинг list.am для брокера")
    parser.add_argument("--env", help="окружение: dev, prod (по умолчанию APP_ENV или dev)")
    parser.add_argument("--config-dir", default="config", help="папка с файлами конфигурации")
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor", help="проверить конфиг, хранилище, курс и права")
    doctor.add_argument("--no-network", action="store_true",
                        help="не ходить в сеть (быстрая проверка)")

    scrape = commands.add_parser("scrape", help="пройти по ленте категории и обновить базу")
    scrape.add_argument("--max-pages", type=int,
                        help="сколько страниц пройти (по умолчанию — из конфига)")
    scrape.add_argument("--dry-run", action="store_true",
                        help="разобрать страницы, но ничего не записывать")

    export = commands.add_parser("export", help="выгрузить базу в .xlsx")
    export.add_argument("--name", help="имя файла выгрузки")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(env=args.env, config_dir=args.config_dir)
    except ConfigError as exc:
        print(f"Конфигурация не загрузилась: {exc}", file=sys.stderr)
        return 2

    if args.command == "doctor":
        report = run_doctor(config, check_network=not args.no_network)
        print(report.render())
        return 0 if report.ok else 1

    if args.command == "scrape":
        return _scrape(config, max_pages=args.max_pages, dry_run=args.dry_run)

    if args.command == "export":
        return _export(config, name=args.name)

    return 2


def _scrape(config, max_pages: int | None, dry_run: bool) -> int:
    from listam.crawler import run_scrape

    run = run_scrape(config, max_pages=max_pages, dry_run=dry_run)
    print(
        f"Прогон: страниц: {run.pages_fetched}, карточек: {run.listings_seen}, "
        f"новых: {run.new_listings}, обновлённых: {run.updated_listings}, "
        f"ошибок: {run.errors}"
    )
    if run.notes:
        print(run.notes)
    if dry_run:
        print("Пробный прогон: ничего не записано.")
    return 1 if run.errors else 0


def _export(config, name: str | None) -> int:
    from listam.adapters.db_sqlite import SqliteDatabase

    storage = build_storage(config)
    local_db = database_path(config)
    remote_name = config.get("storage.db_filename", "listam.sqlite")
    if not local_db.exists():
        storage.download(remote_name, local_db)

    database = SqliteDatabase(local_db)
    database.connect()
    database.migrate()
    listings = list(database.iter_listings())
    database.close()

    path = build_exporter(config).export(listings, name=name)
    print(f"Выгружено объявлений: {len(listings)} → {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
