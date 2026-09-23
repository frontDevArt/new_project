"""Имитация недели: настоящие циклы расписания на подставных часах и list.am.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe tmp/simulate_week.py --out <папка вне репозитория>

Снимок рынка — копия боевой базы (только чтение, резервной копией SQLite);
без неё — синтетический рынок. Спека — docs/superpowers/specs/2026-09-23-week-sim-design.md.
Код 0 — все пороги выполнены, 1 — есть проваленный, 2 — песочница отказала.
"""
from __future__ import annotations

import argparse
import contextlib
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.sim.market import MarketParams  # noqa: E402
from tests.sim.report import compute, render_markdown, to_json  # noqa: E402
from tests.sim.sandbox import SimRefused  # noqa: E402
from tests.sim.week import simulate  # noqa: E402

LIVE_DB = ROOT / "data" / "listam-prod.sqlite"


def _snapshot(source: Path, out: Path) -> Path:
    target = out / "snapshot.sqlite"
    uri = f"file:{source.resolve().as_posix()}?mode=ro"
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as src, \
            contextlib.closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="имитация недели расписания")
    parser.add_argument("--out", required=True, type=Path, help="рабочая папка вне репозитория")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--new-per-hour", type=float, default=20.0)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--snapshot", type=Path, help=f"база-источник рынка (по умолчанию {LIVE_DB})")
    source.add_argument("--synthetic", type=int, help="синтетический рынок из N объявлений")
    args = parser.parse_args(argv)

    out = args.out.resolve()
    if out == ROOT or ROOT in out.parents:
        print(f"--out {out} внутри репозитория — нужна папка вне его", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    snapshot = None
    if args.synthetic is None:
        source_db = args.snapshot or LIVE_DB
        if not source_db.exists():
            print(f"нет базы {source_db}: --snapshot или --synthetic N", file=sys.stderr)
            return 2
        snapshot = _snapshot(source_db, out)
    try:
        result = simulate(out, days=args.days, seed=args.seed,
                          params=MarketParams(new_per_hour=args.new_per_hour),
                          snapshot=snapshot, synthetic=args.synthetic or 300)
    except SimRefused as exc:
        print(exc, file=sys.stderr)
        return 2
    metrics = compute(result)
    report = render_markdown(result, metrics)
    (out / "report.md").write_text(report, encoding="utf-8")
    (out / "report.json").write_text(to_json(result, metrics), encoding="utf-8")
    print(report)
    print(f"команда: {' '.join(sys.argv)}")
    return 0 if all(m.ok is not False for m in metrics) else 1


if __name__ == "__main__":
    raise SystemExit(main())
