"""Командная строка инструмента.

    python -m listam doctor          проверка окружения
    python -m listam scrape          пройти по ленте и обновить базу
    python -m listam recheck         пересчитать пометки по всей базе
    python -m listam export          выгрузить текущую базу в .xlsx
    python -m listam requests        прочитать заявки покупателей из источника
    python -m listam cluster         пересчитать кластеры-дубли по базе
    python -m listam match           подобрать объявления под заявки
    python -m listam matches         показать подобранное по заявкам
    python -m listam changes         что принёс последний прогон

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

    commands.add_parser("requests",
                        help="прочитать заявки из источника и записать в базу")

    commands.add_parser("cluster",
                        help="пересчитать кластеры-дубли по всей базе")

    match = commands.add_parser("match", help="подобрать объявления под заявки")
    match.add_argument("--request",
                       help="внешний идентификатор заявки: подобрать по всей базе")
    match.add_argument("--new", action="store_true",
                       help="только объявления, которые принёс последний прогон")
    match.add_argument("--all", action="store_true",
                       help="пересчитать все активные заявки по всей базе")

    matches = commands.add_parser(
        "matches", help="ранжированный список подобранных вариантов")
    matches.add_argument("--request", help="внешний идентификатор заявки")
    # Значений по умолчанию нет: и порог, и число строк — это пороги конфига
    # (`match.thresholds.digest`, `match.limit`), а не числа в командной строке.
    matches.add_argument("--limit", type=int,
                         help="сколько строк показать (по умолчанию — из конфига)")
    matches.add_argument("--min-score", type=float,
                         help="показывать от этого балла и выше "
                              "(по умолчанию — порог дайджеста из конфига)")

    changes = commands.add_parser("changes", help="что принёс последний прогон")
    changes.add_argument("--hours", type=float,
                         help="за сколько часов считать "
                              "(по умолчанию — с начала прошлого прогона)")
    # Значения по умолчанию нет: сколько строк показывать — порог из конфига
    # (`changes.limit`), а не число, зашитое в командную строку.
    changes.add_argument("--limit", type=int,
                         help="сколько строк показывать в каждом разделе "
                              "(по умолчанию — из конфига)")
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
        # Бессмысленный ввод отклоняется на входе, а не истолковывается:
        # ноль страниц раньше проваливался в полный обход длиной в 215 страниц.
        if args.max_pages is not None and args.max_pages < 1:
            print(
                f"--max-pages {args.max_pages} не годится: страниц в обходе должна "
                "быть хотя бы одна. Полный обход — это команда без --max-pages.",
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

    if args.command == "requests":
        return _requests(config)

    if args.command == "cluster":
        return _cluster(config)

    if args.command == "match":
        # Три флага — три разные выборки, и «оба сразу» не значит ничего.
        # Бессмысленный ввод отклоняется на входе, а не истолковывается:
        # угадав за человека, мы пересчитали бы не то, что он просил.
        chosen = [
            name for name, on in (("--request", bool(args.request)),
                                  ("--new", args.new), ("--all", args.all)) if on
        ]
        if len(chosen) > 1:
            print(
                f"{' и '.join(chosen)} вместе не работают: это три разных выборки. "
                "Выбери одно.",
                file=sys.stderr,
            )
            return 2
        if not chosen:
            print(
                "Нечего подбирать: укажи --request <id> для одной заявки, "
                "--new для объявлений последнего прогона или --all "
                "для полного пересчёта.",
                file=sys.stderr,
            )
            return 2
        return _match(config, external_id=args.request, only_new=args.new,
                      recount_all=args.all)

    if args.command == "matches":
        # Бессмысленный ввод отклоняется на входе: ноль строк — это пустая
        # витрина вместо списка, а балл вне шкалы 0…100 — либо все матчи,
        # либо ни одного, и человек об этом не узнает.
        if args.limit is not None and args.limit <= 0:
            print(
                f"--limit {args.limit} не годится: это число строк витрины, "
                "и меньше одной строки показывать нечего. Нужно число больше нуля.",
                file=sys.stderr,
            )
            return 2
        if args.min_score is not None and not 0 <= args.min_score <= 100:
            print(
                f"--min-score {args.min_score:g} не годится: балл — это шкала "
                "от 0 до 100. Выше ста нет ничего, ниже нуля — тоже.",
                file=sys.stderr,
            )
            return 2
        return _matches(config, external_id=args.request, limit=args.limit,
                        min_score=args.min_score)

    if args.command == "changes":
        # Разбор аргументов — дело командной строки: `run_changes` про коды
        # возврата ничего не знает. Бессмысленный ввод отклоняется на входе,
        # а не истолковывается: окно в будущем и раздел без строк — это
        # молчаливо пустой ответ там, где человек ждал списка.
        if args.hours is not None and args.hours <= 0:
            print(
                f"--hours {args.hours:g} не годится: окно считается назад от «сейчас», "
                "и отрицательное или нулевое окно всегда пусто. Нужно число больше нуля.",
                file=sys.stderr,
            )
            return 2
        if args.limit is not None and args.limit <= 0:
            print(
                f"--limit {args.limit} не годится: это число строк в разделе, "
                "и меньше одной строки показывать нечего. Нужно число больше нуля.",
                file=sys.stderr,
            )
            return 2
        return _changes(config, hours=args.hours, limit=args.limit)

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
        f"вернулось: {run.returned}, ошибок: {run.errors}"
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


def _requests(config) -> int:
    from listam.requests_sync import run_requests_sync

    report = run_requests_sync(config)
    print(report.render())
    return 1 if report.errors else 0


def _cluster(config) -> int:
    from listam.clustering_run import run_clustering

    report = run_clustering(config)
    print(report.render())
    return 1 if report.errors else 0


def _match(config, external_id: str | None, only_new: bool, recount_all: bool) -> int:
    from listam.matching import run_match

    report = run_match(config, external_id=external_id, only_new=only_new,
                       recount_all=recount_all)
    print(report.render())
    return 1 if report.errors else 0


def _matches(config, external_id: str | None, limit: int | None,
             min_score: float | None) -> int:
    from listam.matching import (MatchesError, collect_matches, display_limit,
                                 render_matches, settings)

    if min_score is None:
        min_score = settings(config).digest
    if limit is None:
        limit = display_limit(config)
    try:
        rows = collect_matches(config, external_id=external_id, min_score=min_score)
    except MatchesError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(render_matches(rows, limit=limit, min_score=min_score))
    return 0


def _changes(config, hours: float | None, limit: int | None) -> int:
    from listam.changes import render, run_changes

    report = run_changes(config, hours=hours)
    if report.errors:
        print(report.notes, file=sys.stderr)
        return 1
    if limit is None:
        limit = config.get("changes.limit", 50)
    print(render(report, limit=limit))
    return 0


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
