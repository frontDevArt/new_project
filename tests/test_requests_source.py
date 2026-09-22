"""Решение 1 спеки: csv и gsheet дают одну и ту же заявку из одной и той же строки."""
from __future__ import annotations

from listam.adapters.requests_gsheet import GSheetRequestsSource
from listam.domain.requests import parse_rows


class FakeSheet(GSheetRequestsSource):
    """Тот же разбор значений, но без сети: ответ Sheets кладётся руками."""

    def __init__(self, values):
        super().__init__(sheet_id="fake")
        self.values = values

    def _service(self):                      # pragma: no cover — сеть не нужна
        raise AssertionError("тест не ходит в сеть")

    def rows(self):
        return self._rows_from(self.values)


HEADER = ["id", "budget_max", "notes"]


def test_a_short_row_is_padded_and_read():
    source = FakeSheet([HEADER, ["R-1", "100000"]])
    assert source.rows() == [{"id": "R-1", "budget_max": "100000", "notes": ""}]


def test_a_row_longer_than_the_header_is_not_silently_trimmed():
    source = FakeSheet([HEADER, ["R-1", "100000", "заметка", "хвост"]])
    parsed, errors = parse_rows(source.rows())
    assert parsed == []
    assert len(errors) == 1
    assert "больше, чем колонок" in errors[0].message, (
        "csv такую строку отклоняет; gsheet обязан вести себя так же, "
        "иначе контрактный тест источника ничего не гарантирует"
    )


def test_an_empty_row_is_skipped_and_not_a_refusal():
    source = FakeSheet([HEADER, ["", "", ""], ["R-1", "100000", ""]])
    assert [row["id"] for row in source.rows()] == ["R-1"]


def test_describe_names_the_range_so_a_full_sheet_is_visible():
    assert "A1:Z1000" in GSheetRequestsSource(sheet_id="fake").describe()
