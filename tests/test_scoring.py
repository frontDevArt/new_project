"""Движок скоринга: жёсткие критерии, шесть факторов, разбор балла.

Домен и только домен: ни базы, ни сети, ни конфига. Веса приходят
аргументом — кто их читает из конфига, модуль не знает.
"""
from __future__ import annotations

from listam.domain.models import Exclusion, PageFields, Request
from listam.domain.scoring import refusal_words, rejection, score

from tests.contracts.test_database_contract import make_listing


def a_request(**over) -> Request:
    fields = dict(external_id="R-1", budget_max=140_000.0,
                  districts=["Центр"], districts_priority=["Центр"],
                  rooms=[3], area_min=70.0, area_max=100.0)
    fields.update(over)
    return Request(**fields)


# --- жёсткие критерии -------------------------------------------------

def test_a_district_outside_the_list_is_refused():
    assert rejection(a_request(), make_listing(district="Давташен"), 10) == "район"


def test_a_request_without_districts_accepts_any_district():
    assert rejection(a_request(districts=[]), make_listing(district="Давташен"), 10) is None


def test_fewer_rooms_than_asked_is_refused():
    assert rejection(a_request(rooms=[3, 4]), make_listing(rooms=2), 10) == "комнаты"


def test_more_rooms_than_asked_is_not_refused_but_scores_lower():
    # Четыре комнаты вместо трёх — это всё ещё звонок: клиенту может подойти.
    assert rejection(a_request(rooms=[3]), make_listing(rooms=4), 10) is None
    assert score(a_request(rooms=[3]), make_listing(rooms=4)).value < \
           score(a_request(rooms=[3]), make_listing(rooms=3)).value


def test_a_price_above_the_stretched_budget_is_refused():
    assert rejection(a_request(budget_max=100_000.0), make_listing(price_usd=120_000.0), 10) \
        == "бюджет"


def test_a_price_inside_the_stretch_is_not_refused():
    assert rejection(a_request(budget_max=100_000.0), make_listing(price_usd=108_000.0), 10) is None


def test_a_listing_without_a_price_cannot_pass_the_budget_test():
    assert rejection(a_request(), make_listing(price_usd=None), 10) == "цена неизвестна"


def test_an_area_below_the_minimum_is_refused():
    assert rejection(a_request(area_min=70.0), make_listing(area=55.0), 10) == "площадь"


def test_the_stretch_percent_is_the_callers_word_and_not_a_constant():
    """Растяжка — порог из конфига: 0 значит «ни доллара сверх бюджета»."""
    listing = make_listing(price_usd=105_000.0)
    assert rejection(a_request(budget_max=100_000.0), listing, 10) is None
    assert rejection(a_request(budget_max=100_000.0), listing, 0) == "бюджет"


def test_a_request_without_a_budget_does_not_refuse_a_listing_without_a_price():
    """Нечем сравнивать — нечего и отклонять: бюджет просто не проверяется."""
    assert rejection(a_request(budget_max=None), make_listing(price_usd=None), 10) is None


# --- баллы ------------------------------------------------------------

def test_a_perfect_fit_scores_a_hundred():
    listing = make_listing(district="Центр", rooms=3, area=85.0, floor=4, floors_total=9,
                           price_usd=120_000.0, price_per_sqm=1_000.0, seller_type="owner")
    result = score(a_request(), listing, median_by_district={"Центр": 1_400.0})
    assert result.value == 100
    assert result.matched


def test_a_price_inside_the_stretch_scores_less_than_one_inside_the_budget():
    inside = score(a_request(budget_max=140_000.0), make_listing(price_usd=130_000.0))
    stretched = score(a_request(budget_max=140_000.0), make_listing(price_usd=150_000.0))
    assert stretched.value < inside.value


def test_a_priority_district_beats_a_merely_allowed_one():
    request = a_request(districts=["Центр", "Арабкир"], districts_priority=["Центр"])
    top = score(request, make_listing(district="Центр"))
    ok = score(request, make_listing(district="Арабкир"))
    assert top.value > ok.value


def test_below_the_district_median_scores_higher_than_above_it():
    medians = {"Центр": 1_400.0}
    cheap = score(a_request(), make_listing(price_per_sqm=1_000.0), median_by_district=medians)
    dear = score(a_request(), make_listing(price_per_sqm=1_800.0), median_by_district=medians)
    assert cheap.value > dear.value


def test_the_first_floor_is_penalised_only_when_the_client_said_so():
    listing = make_listing(floor=1)
    assert score(a_request(no_first_floor=True), listing).value < \
           score(a_request(no_first_floor=False), listing).value


def test_the_last_floor_is_penalised_only_when_the_client_said_so():
    listing = make_listing(floor=9, floors_total=9)
    assert score(a_request(no_last_floor=True), listing).value < \
           score(a_request(no_last_floor=False), listing).value


def test_a_floor_outside_the_asked_range_scores_lower():
    request = a_request(floor_min=3, floor_max=7)
    assert score(request, make_listing(floor=12)).value < \
           score(request, make_listing(floor=5)).value


def test_an_owner_scores_above_an_agency():
    assert score(a_request(), make_listing(seller_type="owner")).value > \
           score(a_request(), make_listing(seller_type="agency")).value


def test_a_factor_without_data_is_dropped_from_the_denominator_and_not_a_penalty():
    # Нет медианы района — фактор «выгодность» не считается вовсе. Иначе
    # объявление в районе без медианы всегда проигрывало бы двадцать баллов
    # ни за что.
    without = score(a_request(), make_listing(price_per_sqm=1_000.0), median_by_district={})
    assert "price_per_sqm" not in without.breakdown
    assert without.value == 100


def test_a_request_without_an_area_range_is_not_scored_lower_than_one_with_it():
    """Ровно то, ради чего вес исключается из знаменателя, а не обнуляется."""
    listing = make_listing(area=85.0, rooms=3)
    with_range = score(a_request(area_min=70.0, area_max=100.0), listing)
    without_range = score(a_request(area_min=None, area_max=None), listing)
    assert without_range.value == with_range.value == 100


def test_the_breakdown_names_every_factor_that_counted():
    result = score(a_request(), make_listing(), median_by_district={"Центр": 1_400.0})
    assert set(result.breakdown) <= {"budget", "district", "price_per_sqm",
                                     "area_rooms", "floor", "seller_type"}
    for got, weight in result.breakdown.values():
        assert 0 <= got <= weight


def test_the_breakdown_adds_up_to_the_value():
    """«Почему 68, а не 71» должно сходиться из разбора, иначе разбор — украшение."""
    result = score(a_request(no_first_floor=True), make_listing(floor=1, seller_type="agency"),
                   median_by_district={"Центр": 1_400.0})
    got = sum(pair[0] for pair in result.breakdown.values())
    weights = sum(pair[1] for pair in result.breakdown.values())
    assert result.value == round(100 * got / weights)


def test_a_refused_listing_scores_zero_and_says_why():
    result = score(a_request(), make_listing(district="Давташен"))
    assert result.value == 0
    assert result.rejected_by == "район"
    assert not result.matched
    assert result.breakdown == {}


def test_weights_come_from_the_caller_and_are_not_wired_into_the_code():
    listing = make_listing(seller_type="agency")
    only_seller = score(a_request(), listing, weights={"seller_type": 5})
    assert set(only_seller.breakdown) == {"seller_type"}
    assert only_seller.value == 0


def test_a_zero_weight_keeps_the_factor_out_of_the_score_without_dividing_by_zero():
    """Все веса по нулю — балл ноль, а не падение: ноль значит ноль."""
    result = score(a_request(), make_listing(), weights={"budget": 0, "district": 0})
    assert result.value == 0
    assert result.matched


# --- пожелания со страницы (фаза 3 M3.5, решения 9 и 11) ------------------

from listam.domain.models import PageFields  # noqa: E402
from listam.domain.scoring import DEFAULT_WEIGHTS  # noqa: E402
from listam.domain.wishes import Wish  # noqa: E402

RENOVATION = Wish(word="ремонт", field="renovation", any_of=["косметический", "дизайнерский"])
NOT_PANEL = Wish(word="не панель", field="building_type", none_of=["панельное"])
BALCONY = Wish(word="балкон", field="balcony", is_=True)
ELEVATOR = Wish(word="лифт", field="elevator", is_=True)


def test_must_have_without_an_opened_page_is_refused():
    """Жёсткое пожелание не проверить без страницы: объявление — кандидат,
    а не матч (решение 9)."""
    assert rejection(a_request(), make_listing(), 10, page=None,
                     must=[RENOVATION]) == "страница не открыта"


def test_must_have_that_is_known_and_wrong_is_refused_by_the_field():
    page = PageFields(values={"building_type": "панельное", "renovation": "косметический"})
    assert rejection(a_request(), make_listing(), 10, page=page,
                     must=[RENOVATION, NOT_PANEL]) == "тип дома"


def test_must_have_whose_field_is_not_on_the_page_is_not_refused():
    """Страница открыта, поля нет — узнать больше нечем, брокер уточнит."""
    page = PageFields(values={"renovation": "косметический"})
    assert rejection(a_request(), make_listing(), 10, page=page,
                     must=[RENOVATION, NOT_PANEL]) is None


def test_a_listing_refused_by_the_feed_is_refused_before_the_page():
    """Грубое сито первым: «район» честнее, чем «страница не открыта»."""
    assert rejection(a_request(), make_listing(district="Давташен"), 10, page=None,
                     must=[RENOVATION]) == "район"


def test_no_must_have_needs_no_page():
    assert rejection(a_request(), make_listing(), 10, page=None, must=[]) is None


def test_wishes_is_the_seventh_factor():
    assert set(DEFAULT_WEIGHTS) == {"budget", "district", "price_per_sqm", "area_rooms",
                                    "floor", "seller_type", "wishes"}
    assert DEFAULT_WEIGHTS["wishes"] == 15


def test_wishes_factor_counts_only_known_fields():
    """Доля выполненных среди тех, чьё поле известно: лифт есть, балкона
    нет, про ремонт страница молчит — половина."""
    page = PageFields(values={"elevator": True, "balcony": False})

    result = score(a_request(), make_listing(), page=page,
                   nice=[ELEVATOR, BALCONY, RENOVATION])

    assert result.breakdown["wishes"] == (7.5, 15.0)


def test_no_wishes_no_factor():
    """Пожеланий нет — фактор уходит из знаменателя, как остальные шесть."""
    result = score(a_request(), make_listing(), page=PageFields(values={"elevator": True}))

    assert "wishes" not in result.breakdown


def test_wishes_with_no_known_field_leave_the_denominator():
    without = score(a_request(), make_listing())
    unknown = score(a_request(), make_listing(), page=PageFields(values={}),
                    nice=[ELEVATOR])
    unopened = score(a_request(), make_listing(), page=None, nice=[ELEVATOR])

    assert "wishes" not in unknown.breakdown
    assert unknown.value == without.value == unopened.value


def test_met_wishes_raise_the_score_and_missed_ones_lower_it():
    request = a_request(budget_max=130_000.0)      # не идеал: баллу есть куда расти
    met = score(request, make_listing(), page=PageFields(values={"elevator": True}),
                nice=[ELEVATOR])
    missed = score(request, make_listing(), page=PageFields(values={"elevator": False}),
                   nice=[ELEVATOR])
    without = score(request, make_listing())

    assert met.value > without.value > missed.value


def test_score_refuses_on_must_have_like_rejection():
    result = score(a_request(), make_listing(), page=None, must=[RENOVATION])
    assert result.rejected_by == "страница не открыта"


# --- рычаги фазы 4 M3.5 ---------------------------------------------------

def test_a_secondary_district_scores_what_it_is_told():
    """Непервоочередной район — доля фактора `district` из конфига. Фаза 4:
    0,5 давала широкой заявке 1 974 горячих из 2 849 только за «район назван»."""
    request = a_request(districts=["Центр", "Арабкир"], districts_priority=["Центр"])
    listing = make_listing(district="Арабкир")

    assert score(request, listing, secondary_district=0.0).breakdown["district"] == (0.0, 20.0)
    assert score(request, listing, secondary_district=0.5).breakdown["district"] == (10.0, 20.0)


def test_without_a_priority_every_named_district_is_full():
    """Приоритета нет — районы клиенту равны, и «непервоочередного» среди них нет.
    Иначе доля 0 обнулила бы район у всей заявки и утопила её балл целиком."""
    request = a_request(districts=["Центр", "Арабкир"], districts_priority=[])

    result = score(request, make_listing(district="Арабкир"), secondary_district=0.0)

    assert result.breakdown["district"] == (20.0, 20.0)


# --- исключения из отказов клиента (фаза 6 M3.5, решение 15) ---------------

def refused(kind, value=None, reason=None) -> Exclusion:
    return Exclusion(request_id=1, kind=kind, value=value, reason=reason, match_id=1)


def test_a_refused_cluster_is_rejected_with_the_clients_words():
    """Отвергнутая квартира не возвращается ни этой карточкой, ни двойником."""
    listing = make_listing(cluster_id="c-1")
    no = [refused("cluster", "c-1", reason="дорого за такой ремонт")]

    assert rejection(a_request(), listing, 10, refused=no) == \
        "клиент отказал: дорого за такой ремонт"
    assert score(a_request(), listing, refused=no).rejected_by == \
        "клиент отказал: дорого за такой ремонт"


def test_the_cluster_of_the_pass_beats_the_stale_one_on_the_listing():
    """Кластер карточки считает подбор: колонка `listings.cluster_id` бывает
    вчерашней, и мерка — то, что передано аргументом."""
    listing = make_listing(cluster_id="вчерашний")

    assert rejection(a_request(), listing, 10, refused=[refused("cluster", "c-1")],
                     cluster_id="c-1") == "клиент отказал"


def test_another_cluster_is_not_touched_by_the_refusal():
    listing = make_listing(cluster_id="c-2")

    assert rejection(a_request(), listing, 10, refused=[refused("cluster", "c-1")],
                     cluster_id="c-2") is None


def test_a_first_floor_refusal_narrows_the_request():
    no = [refused("first_floor", reason="первый этаж")]

    assert rejection(a_request(), make_listing(floor=1), 10, refused=no) == \
        "клиент отказал: первый этаж"
    assert rejection(a_request(), make_listing(floor=2), 10, refused=no) is None


def test_a_last_floor_refusal_needs_the_number_of_floors():
    no = [refused("last_floor", reason="последний этаж")]

    assert rejection(a_request(), make_listing(floor=9, floors_total=9), 10,
                     refused=no) == "клиент отказал: последний этаж"
    assert rejection(a_request(), make_listing(floor=9, floors_total=None), 10,
                     refused=no) is None


def test_a_district_refusal_takes_out_that_district_only():
    request = a_request(districts=["Центр", "Арабкир"], districts_priority=[])
    no = [refused("district", "Арабкир", reason="район")]

    assert rejection(request, make_listing(district="Арабкир"), 10, refused=no) == \
        "клиент отказал: район"
    assert rejection(request, make_listing(district="Центр"), 10, refused=no) is None


def test_a_page_field_refusal_needs_the_field_known():
    """Поле страницы неизвестно — не отказ (как у пожеланий): узнать больше
    нечем, брокер уточнит звонком."""
    no = [refused("building_type", "панельное", reason="тип дома")]
    panel = PageFields(values={"building_type": "Панельное"})
    stone = PageFields(values={"building_type": "каменное"})

    assert rejection(a_request(), make_listing(), 10, page=panel, refused=no) == \
        "клиент отказал: тип дома"
    assert rejection(a_request(), make_listing(), 10, page=stone, refused=no) is None
    assert rejection(a_request(), make_listing(), 10, page=None, refused=no) is None


def test_the_coarse_sieve_speaks_before_the_refusal():
    """Бюджет честнее «клиент отказал»: вариант не подошёл бы и без отказа."""
    listing = make_listing(cluster_id="c-1", price_usd=500_000.0)

    assert rejection(a_request(), listing, 10,
                     refused=[refused("cluster", "c-1")]) == "бюджет"


def test_refusals_in_words_skip_the_clusters():
    """Шапка заявки: исключения словами; отвергнутые квартиры не перечисляются."""
    words = refusal_words([
        refused("cluster", "c-1"),
        refused("first_floor"),
        refused("first_floor"),
        refused("last_floor"),
        refused("district", "Арабкир"),
        refused("building_type", "панельное"),
        refused("renovation", "косметический"),
    ])

    assert words == ["без 1-го этажа", "без последнего этажа", "не Арабкир",
                     "без панели", "ремонт не «косметический»"]
