"""Разбор цен с list.am: цены встречаются и в долларах, и в драмах."""
import pytest

from listam.domain.money import Money, parse_price, to_number


@pytest.mark.parametrize(
    "raw, amount, currency",
    [
        ("$132,000", 132000.0, "USD"),
        ("$ 132 000", 132000.0, "USD"),
        ("132,000 $", 132000.0, "USD"),
        ("48,500,000 ֏", 48500000.0, "AMD"),
        ("48 500 000 драм", 48500000.0, "AMD"),
        ("59.500.000 ֏", 59500000.0, "AMD"),
        ("1 500 000 ₽", 1500000.0, "RUB"),
        ("€95,000", 95000.0, "EUR"),
    ],
)
def test_parses_amount_and_currency(raw, amount, currency):
    money = parse_price(raw)
    assert money.amount == amount
    assert money.currency == currency
    assert money.raw == raw


def test_keeps_raw_string_untouched():
    assert parse_price("  $132,000  ").raw == "  $132,000  "


@pytest.mark.parametrize("raw", ["", None, "Договорная", "—"])
def test_unparseable_price_gives_empty_money(raw):
    money = parse_price(raw)
    assert money.amount is None
    assert money.currency is None


def test_converts_amd_to_usd_with_given_rate():
    money = parse_price("48,500,000 ֏")
    assert money.to_usd(rate_amd_per_usd=385.0) == pytest.approx(125974.03, abs=0.01)


def test_usd_price_converts_to_itself_regardless_of_rate():
    assert parse_price("$132,000").to_usd(rate_amd_per_usd=385.0) == 132000.0


def test_to_amd_uses_rate_for_usd_prices():
    assert parse_price("$100,000").to_amd(rate_amd_per_usd=385.0) == 38500000.0


def test_unknown_currency_does_not_convert():
    assert parse_price("1 500 000 ₽").to_usd(rate_amd_per_usd=385.0) is None


# --- L1: знаков в строке бывает больше одного ---

def test_currency_is_the_sign_that_stands_next_to_the_number():
    """Два знака в одной цене — берём тот, что у числа, а не первый по словарю."""
    assert parse_price("23 800 000 ֏ (около $ 65 000)").currency == "AMD"
    assert parse_price("$ 65 000 (это 23 800 000 ֏)").currency == "USD"


def test_a_single_sign_is_found_wherever_it_stands():
    assert parse_price("23 800 000 ֏").currency == "AMD"
    assert parse_price("$132,000").currency == "USD"
    assert parse_price("132 000 долл.").currency == "USD"


# --- СРЕДНИЙ 11: разряды и дробная часть по позиции последнего разделителя ---

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("290,000", 290000.0),
        ("33 912 000", 33912000.0),
        ("1.200", 1200.0),
        ("56,5", 56.5),
        ("1,250", 1250.0),
        ("123,456,789.00", 123456789.0),
        ("12.345.678", 12345678.0),
    ],
)
def test_to_number_separators(raw, expected):
    assert to_number(raw) == expected
