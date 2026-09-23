"""Замер фазы 4 M3.5: «единицы, а не сотни». Только на копии боевой базы.

    .venv/Scripts/python.exe tmp/measure_m35.py --label before --out <папка>

Что делает:

1. Копирует `data/listam-prod.sqlite` в `<out>/<label>/{work,store}` и пишет
   рядом `config/prod.yaml` — боевой конфиг с подменёнными путями,
   `notify.kind: none` и запасным окном 96 ч (журнал отправок пуст — окно
   тогда захватывает рождение всех матчей). Оригинал не трогается.
   `--set ключ=значение` (yaml) — рычаг поверх боевого конфига.
2. Гоняет CLI на копии (`PYTHONIOENCODING=utf-8`): `match --all`,
   `pages --dry-run`, `notify --hot --dry-run`, `notify --digest --dry-run`.
   С `--new-request` первым шагом идёт `requests`: новая заявка на копии.
3. Печатает по заявкам: живых матчей, `origin` (request / market / NULL),
   горячих, децили балла; кандидатов воронки и их грубый балл; сколько
   событий на заявку в «горячем» и в дайджесте (по тексту пробного прогона).
4. Модель потока: объявления по возрастанию номера (номер list.am растёт со
   временем подачи) режутся на окна по `--window` штук; в каждом окне —
   сколько горячих матчей на заявку. Медиана и 95-й перцентиль — оценка
   «событий на заявку в часовом „звони сейчас“», если за час приходит
   `--window` объявлений. Настоящий темп — из логов часового цикла (Д-6).

Журнал `notifications` пуст (расписание не стоит, Д-6) — поэтому «горячее»
меряется пробным прогоном и моделью окон, а не журналом отправок.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
SOURCE_DB = ROOT / "data" / "listam-prod.sqlite"


def _set(tree: dict, dotted: str, value) -> None:
    keys = dotted.split(".")
    node = tree
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value


def prepare(out: Path, overrides: list[str], new_request: str | None) -> Path:
    if out.exists():
        shutil.rmtree(out)
    work, store, config_dir = out / "work", out / "store", out / "config"
    for folder in (work, store, config_dir):
        folder.mkdir(parents=True)
    shutil.copy2(SOURCE_DB, work / "listam-prod.sqlite")
    shutil.copy2(SOURCE_DB, store / "listam-prod.sqlite")

    config = yaml.safe_load((ROOT / "config" / "prod.yaml").read_text(encoding="utf-8"))
    _set(config, "storage.work_dir", str(work))
    _set(config, "storage.directory", str(store))
    requests_csv = out / "requests.csv"
    text = (ROOT / "data" / "requests.csv").read_text(encoding="utf-8")
    if new_request:
        text = text.rstrip("\n") + "\n" + new_request + "\n"
    requests_csv.write_text(text, encoding="utf-8")
    _set(config, "requests.path", str(requests_csv))
    _set(config, "notify.kind", "none")
    _set(config, "notify.hot.fallback_hours", 96)
    _set(config, "notify.digest.fallback_hours", 96)
    for item in overrides:
        key, _, raw = item.partition("=")
        _set(config, key, yaml.safe_load(raw))
    (config_dir / "prod.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return config_dir


def cli(config_dir: Path, *args: str) -> str:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    done = subprocess.run(
        [str(PYTHON), "-m", "listam", "--env", "prod", "--config-dir", str(config_dir),
         *args], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    return f"$ listam {' '.join(args)}  → код {done.returncode}\n{done.stdout}{done.stderr}"


def per_request_from_text(text: str, head: str) -> dict[str, int]:
    """Событий на заявку по тексту пробного прогона: карточки плюс «➕ ещё N»."""
    counts: dict[str, int] = {}
    current = None
    for line in text.splitlines():
        found = re.match(rf"{re.escape(head)} · (\S+)", line)
        if found:
            current = found.group(1)
            counts.setdefault(current, 0)
            continue
        if current is None:
            continue
        if line[:1] in ("🆕", "📉", "♻") or line.startswith("♻️"):
            counts[current] += 1
        more = re.match(r"➕ ещё (\d+)", line)
        if more:
            counts[current] += int(more.group(1))
    return counts


def percentile(values: list[int], share: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--window", type=int, default=100)
    parser.add_argument("--set", action="append", default=[], dest="overrides")
    parser.add_argument("--new-request", help="строка CSV новой заявки: дописывается "
                        "к копии заявок, первым шагом идёт `requests`")
    args = parser.parse_args()

    out = Path(args.out) / args.label
    config_dir = prepare(out, args.overrides, args.new_request)
    print(f"# Замер {args.label}: копия {SOURCE_DB.name} → {out}")
    if args.overrides:
        print("# рычаги:", ", ".join(args.overrides))

    outputs = {}
    steps = [["requests"]] if args.new_request else []
    for step in (*steps, ["match", "--all"], ["pages", "--dry-run"],
                 ["notify", "--hot", "--dry-run"], ["notify", "--digest", "--dry-run"]):
        text = cli(config_dir, *step)
        outputs[" ".join(step)] = text
        head = [line for line in text.splitlines()
                if re.search(r"код|Событий|Кандидатов|заявок с|новых|Закрыто|Из них|"
                             r"первичн|открылись", line)]
        print("\n".join(head[:8]))

    from listam.config import load_config
    from listam.clustering_run import area_tolerance
    from listam.domain.clustering import clusters
    from listam.domain.scoring import rejection, score
    from listam.domain.stats import median_price_per_sqm_by_district
    from listam.domain.wishes import request_wishes, vocabulary
    from listam.matching import settings
    from listam.wiring import build_database

    config = load_config("prod", config_dir)
    tuning = settings(config)
    hot, digest = tuning.hot, tuning.digest
    print(f"\n# пороги: hot {hot}, digest {digest}; веса {tuning.weights}; "
          f"secondary_district {tuning.secondary_district}")

    db = sqlite3.connect(out / "work" / "listam-prod.sqlite")
    rows = db.execute(
        "select r.external_id, m.score, m.origin, m.listing_id from matches m "
        "join requests r on r.id = m.request_id where m.retired_at is null").fetchall()
    by_request: dict[str, list] = {}
    for rid, value, origin, listing_id in rows:
        by_request.setdefault(rid, []).append((value, origin, listing_id))

    hot_text = per_request_from_text(outputs["notify --hot --dry-run"], "🔥 ЗВОНИ СЕЙЧАС")
    digest_text = per_request_from_text(outputs["notify --digest --dry-run"], "📋 ДАЙДЖЕСТ")

    print("\n| Заявка | живых | request / market / NULL | горячих (≥hot) | "
          "децили балла (10…90 %) | в «горячем» | в дайджесте |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for rid in sorted(by_request):
        items = by_request[rid]
        scores = sorted(value for value, _, _ in items)
        origins = [origin for _, origin, _ in items]
        deciles = ([round(percentile(scores, q / 10)) for q in range(1, 10)]
                   if scores else [])
        hot_count = sum(1 for value in scores if hot is not None and value >= hot)
        print(f"| {rid} | {len(items)} | {origins.count('request')} / "
              f"{origins.count('market')} / {origins.count(None)} | {hot_count} | "
              f"{' '.join(map(str, deciles))} | {hot_text.get(rid, 0)} | "
              f"{digest_text.get(rid, 0)} |")

    # Балл «сейчас» по заявкам: подбор без страницы (поля страницы неизвестны,
    # фактор `wishes` уходит из знаменателя). У R-1 без пожеланий это и есть
    # балл матча; у R-2/R-3 матчи в базе заморожены до открытия страницы
    # (решение 12), и балл, с которым матч родится, — этот же плюс `wishes`.
    database = build_database(config)
    database.connect()
    try:
        everything = database.listings_for_matching()
        representatives = {c.cheapest_id for c in clusters(everything, area_tolerance(config))}
        medians = median_price_per_sqm_by_district(everything)
        words = vocabulary(config)
        requests = list(database.iter_requests())
        ids = sorted((listing.id for listing in everything if listing.id.isdigit()),
                     key=int)
        window_of = {listing_id: index // args.window for index, listing_id in enumerate(ids)}
        windows = len(ids) // args.window
        cap = config.get("notify.hot.per_request")
        print("\n# балл «сейчас» (без страницы) по представителям кластеров")
        print("| Заявка | пожелания | прошли сито (≥ digest) | из них ≥ hot | "
              "децили балла (10…90 %) |")
        print("| --- | --- | --- | --- | --- |")
        streams = {}
        for request in requests:
            must, nice = request_wishes(request, words)
            values, hot_ids = [], []
            for listing in everything:
                if listing.id not in representatives:
                    continue
                if rejection(request, listing, tuning.stretch_percent) is not None:
                    continue
                value = score(request, listing, median_by_district=medians,
                              weights=tuning.weights,
                              stretch_percent=tuning.stretch_percent,
                              secondary_district=tuning.secondary_district).value
                if digest is not None and value < digest:
                    continue
                values.append(value)
                if hot is not None and value >= hot:
                    hot_ids.append(listing.id)
            values.sort()
            deciles = ([round(percentile(values, q / 10)) for q in range(1, 10)]
                       if values else [])
            said = (f"must {len(must)}, nice {len(nice)}") if (must or nice) else "нет"
            print(f"| {request.external_id} | {said} | {len(values)} | {len(hot_ids)} | "
                  f"{' '.join(map(str, deciles))} |")
            streams[request.external_id] = hot_ids

        # Модель потока: окна по номеру объявления.
        print(f"\n# модель потока: {len(ids)} объявлений по номеру, окно "
              f"{args.window} → {windows} полных окон; горячих (балл «сейчас» ≥ hot) "
              f"на заявку в окне; потолок сообщения {cap}")
        print("| Заявка | медиана | 95-й перцентиль | максимум | окон > потолка |")
        print("| --- | --- | --- | --- | --- |")
        for rid, hot_ids in streams.items():
            counts = [0] * windows
            for listing_id in hot_ids:
                index = window_of.get(listing_id)
                if index is not None and index < windows:
                    counts[index] += 1
            over = sum(1 for count in counts if cap and count > cap)
            print(f"| {rid} | {statistics.median(counts):g} | "
                  f"{percentile(counts, 0.95):g} | {max(counts)} | {over} из {windows} |")
    finally:
        database.close()

    journal = db.execute("select count(*) from notifications").fetchone()[0]
    print(f"\n# журнал notifications на копии: {journal} строк")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
