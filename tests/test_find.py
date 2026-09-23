"""`find` — быстрый поиск (фаза 5 M3.5, решение 16).

Флаги собирают строку заявки и идут через тот же `parse_row` и тот же
`score()`, что подбор. Заявку `find` не пишет; страницы открывает только
`--open N` под потолком `funnel.find_max_opens`.

Сайт здесь — папка с настоящей страницей (`FilesFetcher`), как в `test_pages`.
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from listam.cli import main
from listam.config import load_config
from listam.domain.models import ListingPage, PageFields
from listam.domain.scoring import score
from listam.matching import settings
from listam.wiring import build_database, build_run_lock

from tests.contracts.test_database_contract import make_listing

FIXTURE = Path(__file__).parent / "fixtures" / "item-24041732.html"   # косметический ремонт

WISHES = {
    "ремонт": {"field": "renovation", "any_of": ["косметический", "евроремонт", "дизайнерский"]},
    "лифт": {"field": "elevator", "is": True},
}


def write_config(tmp_path: Path, **over) -> Path:
    """Конфиг на диске: `main` читает его так же, как боевой."""
    data = {
        "env": "test",
        "storage": {"kind": "local", "directory": (tmp_path / "remote").as_posix(),
                    "work_dir": (tmp_path / "work").as_posix(),
                    "db_filename": "listam.sqlite"},
        "scrape": {"kind": "files", "pages_dir": (tmp_path / "site").as_posix()},
        "notify": {"kind": "none"},
        "match": {"thresholds": {"hot": 80, "digest": 40}, "budget_stretch_percent": 10,
                  "limit": 50},
        "funnel": {"delay_seconds": 0, "max_opens_per_run": 30, "find_max_opens": 2,
                   "max_attempts": 2, "wishes": WISHES},
    }
    for section, values in over.items():
        data.setdefault(section, {}).update(values)
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "test.yaml").write_text(yaml.safe_dump(data, allow_unicode=True),
                                          encoding="utf-8")
    return config_dir


def config_of(config_dir: Path):
    return load_config(env="test", config_dir=str(config_dir))


def run(config_dir: Path, *args) -> int:
    return main(["--env", "test", "--config-dir", str(config_dir), "find", *args])


def flat(listing_id, **over):
    """Арабкир, 3 комнаты, 85 м², $110 000 — подходит «Арабкир, 3, до $120k»."""
    fields = dict(district="Арабкир", street=f"улица {listing_id}", rooms=3,
                  area=85.0, floor=4, floors_total=9, price_usd=110_000.0,
                  price_raw="$110,000", price_per_sqm=1294.0, seller_type="owner")
    fields.update(over)
    return make_listing(listing_id, **fields)


def fill(config_dir: Path, *listings, pages=()) -> None:
    database = build_database(config_of(config_dir))
    database.connect()
    database.migrate()
    try:
        for listing in listings:
            database.upsert_listing(listing, seen_at=datetime.now(timezone.utc))
        for page in pages:
            database.save_page(page)
    finally:
        database.close()


def counts(config_dir: Path) -> dict[str, int]:
    database = build_database(config_of(config_dir))
    database.connect()
    try:
        connection = database._conn
        return {table: connection.execute(f"select count(*) from {table}").fetchone()[0]
                for table in ("requests", "matches", "listing_pages")}
    finally:
        database.close()


def page_of(config_dir: Path, listing_id: str) -> ListingPage | None:
    database = build_database(config_of(config_dir))
    database.connect()
    try:
        return database.get_page(listing_id)
    finally:
        database.close()


def publish_pages(config_dir: Path, *listing_ids: str) -> None:
    site = Path(config_of(config_dir).get("scrape.pages_dir"))
    site.mkdir(parents=True, exist_ok=True)
    for listing_id in listing_ids:
        shutil.copyfile(FIXTURE, site / f"ru-item-{listing_id}.html")


def ok_page(listing_id: str, **values) -> ListingPage:
    return ListingPage(listing_id=listing_id, status="ok", attempts=1,
                       price_raw="$110,000", fields=PageFields(values=values))


ASK = ("--district", "Арабкир", "--rooms", "3", "--max-price", "120000")


@pytest.fixture
def config_dir(tmp_path):
    return write_config(tmp_path)


# --- разбор флагов ------------------------------------------------------

@pytest.mark.parametrize("flags, named", [
    (("--max-price", "120k"), "budget_max"),
    (("--rooms", "много"), "rooms"),
    (("--rooms", "3-2"), "rooms"),
    (("--area", "90-60"), "area_max"),
    (("--area", "шестьдесят"), "--area"),
    (("--wish", "ремонт, джакузи"), "джакузи"),
    (("--limit", "0"), "--limit"),
    (("--open", "0"), "--open"),
    (("--open", "3", "--wish", "ремонт"), "funnel.find_max_opens"),
    (("--open", "1"), "--wish"),
])
def test_find_rejects_nonsense(config_dir, capsys, flags, named):
    """Бессмысленный флаг — код 2 и названная колонка, а не пустая выдача."""
    fill(config_dir, flat("1"))

    assert run(config_dir, "--district", "Арабкир", *flags) == 2
    assert named in capsys.readouterr().err


def test_find_needs_at_least_one_condition(config_dir, capsys):
    """Без условий `find` — это вся база, а не поиск."""
    fill(config_dir, flat("1"))

    assert run(config_dir) == 2
    assert "условие" in capsys.readouterr().err


def test_the_plan_spelling_of_the_floor_flags_works_too(config_dir, capsys):
    fill(config_dir, flat("1", floor=1), flat("2", floor=4))

    assert run(config_dir, *ASK, "--floor-not-first") == 0
    assert run(config_dir, *ASK, "--not-first-floor") == 0


# --- поиск --------------------------------------------------------------

def test_find_shows_what_fits_best_first(config_dir, capsys):
    """Не тот район и дороже растянутого бюджета — не показываются; лучший
    по баллу — первым."""
    fill(config_dir,
         flat("cheap", price_usd=90_000.0, price_per_sqm=1058.0),
         flat("fair"),
         flat("far", district="Давташен"),
         flat("dear", price_usd=200_000.0, price_per_sqm=2353.0))

    assert run(config_dir, *ASK) == 0
    out = capsys.readouterr().out

    assert "найдено 2" in out
    assert "item/cheap" in out and "item/fair" in out
    assert "item/far" not in out and "item/dear" not in out
    assert out.index("item/cheap") < out.index("item/fair")


def test_find_scores_with_the_weights_and_share_from_the_config(tmp_path, capsys):
    """Решение 16 и находка фазы 4: балл `find` — это `score()` с весами,
    растяжкой и `secondary_district` из `settings(config)`, а не с умолчаниями."""
    config_dir = write_config(tmp_path, match={
        "weights": {"budget": 0, "district": 0, "price_per_sqm": 0, "area_rooms": 0,
                    "floor": 0, "seller_type": 5, "wishes": 0},
        "secondary_district": 0.5,
    })
    fill(config_dir, flat("owner"), flat("agency", seller_type="agency"))

    assert run(config_dir, *ASK) == 0
    out = capsys.readouterr().out

    owner_line = next(line for line in out.splitlines() if "item/owner" in line)
    agency_line = next(line for line in out.splitlines() if "item/agency" in line)
    assert "100 баллов" in owner_line
    assert re.search(r"(?<!\d)0 баллов", agency_line)


def test_find_scores_exactly_like_matching(config_dir):
    """Один и тот же балл на одном объявлении: `run_find` и `score()`."""
    from listam.domain.requests import parse_row
    from listam.find import Query, run_find

    fill(config_dir, flat("1", price_usd=125_000.0, price_per_sqm=1470.0, floor=1))
    config = config_of(config_dir)

    report = run_find(config, Query(districts=["Арабкир"], rooms="3",
                                    max_price="120000", not_first=True))

    tuning = settings(config)
    expected = score(
        parse_row({"id": "find", "districts": "Арабкир", "rooms": "3",
                   "budget_max": "120000", "no_first_floor": "да"}),
        flat("1", price_usd=125_000.0, price_per_sqm=1470.0, floor=1),
        median_by_district={"Арабкир": 1470.0}, weights=tuning.weights,
        stretch_percent=tuning.stretch_percent,
        secondary_district=tuning.secondary_district)
    assert [row.score.value for row in report.rows] == [expected.value]


@pytest.mark.parametrize("hot, said", [(100, "горячих 0"), (50, "горячих 1")])
def test_find_counts_hot_by_the_config_threshold(tmp_path, capsys, hot, said):
    """Цена за метр равна медиане — балл ниже 100, но выше 50."""
    config_dir = write_config(tmp_path, match={"thresholds": {"hot": hot, "digest": 40}})
    fill(config_dir, flat("1"))

    assert run(config_dir, *ASK) == 0
    assert said in capsys.readouterr().out


def test_find_shows_a_cluster_once(config_dir, capsys):
    """Двойник дороже — тот же кластер: строка одна, по дешёвому."""
    fill(config_dir, flat("a"), flat("b", street="улица a", price_usd=111_000.0,
                                     price_raw="$111,000"))

    assert run(config_dir, *ASK) == 0
    out = capsys.readouterr().out

    assert "найдено 1" in out
    assert "item/a" in out and "item/b" not in out
    assert "2 объявления" in out


def test_find_owner_only(config_dir, capsys):
    fill(config_dir, flat("owner"), flat("agency", seller_type="agency", street="другая"))

    assert run(config_dir, *ASK, "--owner") == 0
    out = capsys.readouterr().out

    assert "item/owner" in out and "item/agency" not in out


def test_find_limit_cuts_the_list_and_says_how_many_are_left(config_dir, capsys):
    fill(config_dir, *[flat(str(i), street=f"улица {i}", area=80.0 + i * 5)
                       for i in range(1, 4)])

    assert run(config_dir, *ASK, "--limit", "1") == 0
    out = capsys.readouterr().out

    assert sum("list.am/ru/item/" in line for line in out.splitlines()) == 1
    assert "…и ещё 2" in out


def test_find_reads_the_base_without_the_lock(config_dir, capsys):
    """Идёт прогон — `find` всё равно отвечает: он базу не пишет."""
    fill(config_dir, flat("1"))
    lock = build_run_lock(config_of(config_dir))
    lock.acquire()
    try:
        assert run(config_dir, *ASK) == 0
    finally:
        lock.release()
    assert "найдено 1" in capsys.readouterr().out


def test_find_counts_known_and_unknown(config_dir, capsys):
    """Строка на пожелание: у скольких поле известно, у скольких подходит,
    и что `--open` откроет лучшие из неизвестных."""
    fill(config_dir, flat("fits", street="а"), flat("not", street="б"),
         flat("unknown", street="в"),
         pages=[ok_page("fits", renovation="косметический"),
                ok_page("not", renovation="частичный")])

    assert run(config_dir, *ASK, "--wish", "ремонт") == 0
    out = capsys.readouterr().out

    assert "ремонт: известно у 2 из 3, подходит 1; неизвестно у 1 — --open 1 " \
           "откроет лучшие" in out


def test_find_wish_is_a_score_factor_not_a_filter(config_dir, capsys):
    """`--wish` — пожелание (`nice_to_have`): неизвестное поле не выкидывает
    вариант, известное и подходящее поднимает балл."""
    fill(config_dir, flat("fits", street="а"), flat("unknown", street="в"),
         pages=[ok_page("fits", renovation="косметический")])

    assert run(config_dir, *ASK, "--wish", "ремонт") == 0
    out = capsys.readouterr().out

    assert "найдено 2" in out
    assert out.index("item/fits") < out.index("item/unknown")


def test_find_open_respects_its_ceiling(config_dir, capsys):
    """`--open N` открывает страницы только найденных и не больше N; потом
    перескоринг, и строка пожелания уже считает открытые."""
    listings = [flat(str(i), street=f"улица {i}", area=80.0 + i * 5) for i in range(1, 5)]
    fill(config_dir, *listings, flat("far", district="Давташен"))
    publish_pages(config_dir, "1", "2", "3", "4", "far")

    assert run(config_dir, *ASK, "--wish", "ремонт", "--open", "2") == 0
    out = capsys.readouterr().out

    opened = [lid for lid in ("1", "2", "3", "4", "far") if page_of(config_dir, lid)]
    assert len(opened) == 2
    assert "far" not in opened
    assert "Открыто страниц: 2" in out
    assert "ремонт: известно у 2 из 4, подходит 2" in out


def test_find_open_skips_pages_already_known(config_dir, capsys):
    """Свежая страница в кэше не открывается заново: потолок тратится
    на неизвестные."""
    fill(config_dir, flat("known", street="а"), flat("new", street="б"),
         pages=[ok_page("known", renovation="косметический")])
    publish_pages(config_dir, "known", "new")

    assert run(config_dir, *ASK, "--wish", "ремонт", "--open", "2") == 0

    assert page_of(config_dir, "new").status == "ok"
    assert page_of(config_dir, "known").fetched_at is None      # не переоткрывалась


def test_find_writes_no_request(config_dir, capsys):
    """Заявку `find` не пишет — ни поиском, ни с `--open`; матчей тоже."""
    fill(config_dir, flat("1"))
    publish_pages(config_dir, "1")

    assert run(config_dir, *ASK) == 0
    assert run(config_dir, *ASK, "--wish", "ремонт", "--open", "1") == 0

    assert counts(config_dir) == {"requests": 0, "matches": 0, "listing_pages": 1}


def test_find_ceiling_null_is_refused_by_the_config(tmp_path, capsys):
    """`null` у потолка — не «выключено», а открытие без потолка."""
    config_dir = write_config(tmp_path, funnel={"find_max_opens": None})
    fill(config_dir, flat("1"))

    assert run(config_dir, *ASK) == 2
    assert "funnel.find_max_opens" in capsys.readouterr().err
