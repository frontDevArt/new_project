"""Заявка покупателя: доменная сущность и разбор строки источника."""
from __future__ import annotations

from listam.domain.models import Request


def test_stretch_defaults_to_a_percent_above_the_budget():
    assert Request(budget_max=100_000).stretch(10) == 110_000


def test_an_explicit_stretch_wins_over_the_percent():
    assert Request(budget_max=100_000, budget_stretch=105_000).stretch(10) == 105_000


def test_a_request_without_a_budget_has_no_stretch():
    assert Request().stretch(10) is None
