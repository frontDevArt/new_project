"""Заявка покупателя: доменная сущность и разбор строки источника."""
from __future__ import annotations

from listam.domain.models import Request


def test_stretch_defaults_to_a_percent_above_the_budget():
    assert Request(budget_max=100_000).stretch(10) == 110_000


def test_an_explicit_stretch_wins_over_the_percent():
    assert Request(budget_max=100_000, budget_stretch=105_000).stretch(10) == 105_000


def test_a_request_without_a_budget_has_no_stretch():
    assert Request().stretch(10) is None


import pytest

from listam.domain.requests import RequestParseError, parse_row, parse_rows


def row(**over) -> dict:
    fields = {
        "id": "R-1", "client_name": "Ани", "client_phone": "+374 00 000000",
        "status": "active", "budget_max": "120 000 $", "budget_stretch": "",
        "districts": "Кентрон, Арабкир", "districts_priority": "Кентрон",
        "rooms": "2-3", "area_min": "60", "area_max": "95",
        "floor_min": "2", "floor_max": "", "no_first_floor": "да", "no_last_floor": "нет",
        "must_have": "балкон", "nice_to_have": "ремонт", "notes": "", "floor_rules": "",
    }
    fields.update(over)
    return fields


def test_money_is_read_without_spaces_and_currency_signs():
    assert parse_row(row()).budget_max == 120_000


def test_rooms_are_read_both_as_a_list_and_as_a_range():
    assert parse_row(row(rooms="2-3")).rooms == [2, 3]
    assert parse_row(row(rooms="2, 4")).rooms == [2, 4]


def test_districts_are_split_and_trimmed():
    parsed = parse_row(row())
    assert parsed.districts == ["Кентрон", "Арабкир"]
    assert parsed.districts_priority == ["Кентрон"]


def test_yes_and_no_are_read_in_any_of_the_usual_spellings():
    for yes in ("да", "Да", "yes", "1", "+", "true"):
        assert parse_row(row(no_first_floor=yes)).no_first_floor is True
    for no in ("нет", "no", "0", "-", "", "false"):
        assert parse_row(row(no_first_floor=no)).no_first_floor is False


def test_an_empty_optional_column_stays_empty_and_is_not_guessed():
    parsed = parse_row(row(area_max="", floor_max=""))
    assert parsed.area_max is None
    assert parsed.floor_max is None


def test_the_whole_source_row_is_kept_as_written():
    parsed = parse_row(row(notes="звонить после шести"))
    assert "звонить после шести" in parsed.source_row


def test_a_budget_that_is_not_a_number_is_a_refusal_naming_the_column():
    with pytest.raises(RequestParseError) as exc:
        parse_row(row(budget_max="примерно 100к"), row_number=3)
    assert exc.value.column == "budget_max"
    assert "примерно 100к" in exc.value.value


def test_a_stretch_below_the_budget_is_a_refusal():
    with pytest.raises(RequestParseError) as exc:
        parse_row(row(budget_max="100000", budget_stretch="90000"))
    assert exc.value.column == "budget_stretch"


def test_an_area_range_upside_down_is_a_refusal():
    with pytest.raises(RequestParseError) as exc:
        parse_row(row(area_min="95", area_max="60"))
    assert exc.value.column == "area_max"


def test_an_unknown_status_is_a_refusal_and_not_read_as_active():
    with pytest.raises(RequestParseError) as exc:
        parse_row(row(status="в работе"))
    assert exc.value.column == "status"


def test_a_request_without_an_id_is_a_refusal():
    with pytest.raises(RequestParseError) as exc:
        parse_row(row(id=""))
    assert exc.value.column == "id"


def test_a_priority_district_outside_the_allowed_list_is_a_refusal():
    with pytest.raises(RequestParseError) as exc:
        parse_row(row(districts="Кентрон", districts_priority="Давташен"))
    assert exc.value.column == "districts_priority"


def test_one_broken_row_does_not_cost_the_others():
    parsed, errors = parse_rows([row(id="R-1"), row(id="R-2", budget_max="сколько-то"),
                                 row(id="R-3")])
    assert [item.external_id for item in parsed] == ["R-1", "R-3"]
    assert len(errors) == 1
    assert errors[0].external_id == "R-2"
    assert "budget_max" in errors[0].render()


def test_a_dot_with_three_digits_after_it_is_a_thousands_separator():
    """«120.000» человек пишет как разряды, а не как 120 долларов и копейки."""
    assert parse_row(row(budget_max="120.000")).budget_max == 120_000
    assert parse_row(row(budget_max="1,234,567")).budget_max == 1_234_567
    assert parse_row(row(area_min="95,5", area_max="")).area_min == 95.5


def test_a_number_with_a_word_stuck_to_it_is_not_trimmed_to_the_digits():
    """«100к» — это не 100. Обрезать до цифр значит выдумать бюджет вчетверо меньше."""
    with pytest.raises(RequestParseError) as exc:
        parse_row(row(budget_max="100к"))
    assert exc.value.column == "budget_max"


def test_a_row_with_more_values_than_columns_is_a_refusal_and_not_a_crash():
    """Лишняя запятая в заметке — это разъехавшаяся строка, а не падение чтения.

    `csv.DictReader` складывает лишние значения под ключ `None`, и разбор
    такой строки обязан отклонить её по правилам решения 2, а не унести
    с собой всю таблицу.
    """
    broken = row(notes="семья с ребёнком")
    broken[None] = ["смотрит с октября"]
    parsed, errors = parse_rows([row(id="R-1"), broken, row(id="R-3")])
    assert [item.external_id for item in parsed] == ["R-1", "R-3"]
    assert len(errors) == 1
    assert "смотрит с октября" in errors[0].render()
    assert "шапк" in errors[0].render()
