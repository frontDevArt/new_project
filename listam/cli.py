"""Командная строка инструмента.

    python -m listam doctor          проверка окружения
    python -m listam scrape          пройти по ленте и обновить базу
    python -m listam recheck         пересчитать пометки по всей базе
    python -m listam export          выгрузить текущую базу в .xlsx

Окружение выбирается переменной APP_ENV или флагом --env.
"""
from __future__ import annotations

import argparse
import sys

from listam.adapters.db_sqlite import latest_schema_version
from listam.config import ConfigError, load_config
from listam.doctor import run_doctor
from listam.wiring import build_database, build_exporter, build_storage, database_path


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
    scrape.add_argument("--allow-shrink", action="store_true",
                        help="разрешить заливку, если база заметно усохла")
    scrape.add_argument("--resume", action="store_true",
                        help="продолжить прерванный обход с последней пройденной страницы")
    scrape.add_argument("--fresh", action="store_true",
                        help="инкрементальный обход: только свежая часть ленты "
                             "до уже известных объявлений")
    scrape.add_argument("--allow-upload-with-errors", action="store_true",
                        help="залить базу в хранилище, даже если в прогоне были ошибки")

    commands.add_parser(
        "recheck",
        help="пересчитать пометки и суммы в валюте по всей базе (разовая операция)",
    )

    export = commands.add_parser("export", help="выгрузить базу в .xlsx")
    export.add_argument("--name", help="имя файла выгрузки")
    return parser


def _force_utf8_output() -> None:
    """Сообщения инструмента на русском, а консоль Windows по умолчанию cp1252.

    Перенаправленный вывод (пайп, `> файл`, запуск из-под другой программы) получает
    именно её и падает на первой кириллической букве. Печать итога не должна ронять
    прогон, который уже сходил в сеть и записал базу, поэтому оба потока переводим
    в UTF-8, а непредставимый символ заменяем, а не бросаем исключение.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # подменённый поток в тестах — трогать нечего
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # поток уже закрыт или не перенастраивается
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
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
        if args.fresh and args.resume:
            print(
                "--fresh и --resume вместе не работают: первый идёт с головы ленты, "
                "второй продолжает прерванный полный обход. Выбери одно.",
                file=sys.stderr,
            )
            return 2
        return _scrape(config, max_pages=args.max_pages, dry_run=args.dry_run,
                       allow_shrink=args.allow_shrink, resume=args.resume,
                       allow_upload_with_errors=args.allow_upload_with_errors,
                       fresh=args.fresh)

    if args.command == "recheck":
        return _recheck(config)

    if args.command == "export":
        return _export(config, name=args.name)

    return 2


def _scrape(config, max_pages: int | None, dry_run: bool, allow_shrink: bool = False,
            resume: bool = False, allow_upload_with_errors: bool = False,
            fresh: bool = False) -> int:
    import listam.crawler

    run = listam.crawler.run_scrape(
        config, max_pages=max_pages, dry_run=dry_run, allow_shrink=allow_shrink,
        resume=resume, allow_upload_with_errors=allow_upload_with_errors,
        fresh=fresh,
    )
    print(
        f"Прогон ({run.mode}): страниц: {run.pages_fetched}, карточек: {run.listings_seen}, "
        f"новых: {run.new_listings}, обновлённых: {run.updated_listings}, "
        f"сменили цену: {run.price_changed}, снято: {run.gone_marked}, "
        f"ошибок: {run.errors}"
    )
    if run.stop_reason:
        print(f"Обход кончился: {run.stop_reason}")
    if run.notes:
        print(run.notes)
    if dry_run:
        print("Пробный прогон: ничего не записано.")
    return 1 if run.errors else 0


def _recheck(config) -> int:
    from listam.recheck import run_recheck

    report = run_recheck(config)
    print(
        f"Пересчёт: строк: {report.listings}, помечено: {report.marked}, "
        f"сумма в валюте оригинала: {report.amounts}, изменено: {report.changed}, "
        f"ошибок: {report.errors}"
    )
    if report.notes:
        print(report.notes)
    return 1 if report.errors else 0


def _export(config, name: str | None) -> int:
    storage = build_storage(config)
    local_db = database_path(config)
    remote_name = config.get("storage.db_filename", "listam.sqlite")
    if not local_db.exists():
        storage.download(remote_name, local_db)

    database = build_database(config)
    database.connect()
    # Выгрузка читает базу, а не чинит её. Молчаливая миграция по дороге к .xlsx
    # правит общую копию за спиной у человека — и делает это тогда, когда он
    # просил всего лишь таблицу.
    required = latest_schema_version()
    version = database.schema_version()
    if version < required:
        database.close()
        print(
            f"Выгрузка не сделана: схема базы {version}, а код ждёт {required}. "
            f"Выгрузка ничего не мигрирует — накати миграции и пересчитай базу: "
            f"python -m listam recheck",
            file=sys.stderr,
        )
        return 1
    listings = list(database.iter_listings())
    database.close()

    path = build_exporter(config).export(listings, name=name)
    print(f"Выгружено объявлений: {len(listings)} → {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
