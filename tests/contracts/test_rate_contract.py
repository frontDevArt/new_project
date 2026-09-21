"""Контрактный тест порта RateProvider.

Курс нужен, чтобы привести драмы к долларам, и он пишется в журнал прогона —
иначе старые цифры перестают быть воспроизводимыми.
"""
from __future__ import annotations

from datetime import timezone
from pathlib import Path

import pytest

from listam.adapters.fetcher_http import HttpFetcher
from listam.adapters.rate_am import RateAmProvider
from listam.adapters.rate_fixed import FixedRateProvider
from listam.ports.rate import RateError, RateProvider

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "rateam_banks_cash.html"


@pytest.fixture(params=["fixed", "rate_am"])
def provider(request, http_site):
    if request.param == "fixed":
        return FixedRateProvider(amd_per_usd=363.25)
    http_site.route("/rates", FIXTURE.read_text(encoding="utf-8"))
    return RateAmProvider(
        fetcher=HttpFetcher(base_url=http_site.base_url, delay_seconds=0),
        url="/rates",
    )


def test_is_a_rate_provider(provider):
    assert isinstance(provider, RateProvider)


def test_returns_plausible_dram_per_dollar_rate(provider):
    rate = provider.amd_per_usd()
    assert 200 < rate.value < 600


def test_rate_carries_source_and_utc_timestamp(provider):
    rate = provider.amd_per_usd()
    assert rate.source
    assert rate.fetched_at.tzinfo == timezone.utc


def test_fixed_provider_returns_configured_value():
    assert FixedRateProvider(amd_per_usd=400.0).amd_per_usd().value == 400.0


def test_rate_am_reads_median_of_banks(http_site):
    http_site.route("/rates", FIXTURE.read_text(encoding="utf-8"))
    provider = RateAmProvider(
        fetcher=HttpFetcher(base_url=http_site.base_url, delay_seconds=0), url="/rates"
    )
    rate = provider.amd_per_usd()
    assert rate.value == pytest.approx(363.25, abs=0.5)
    assert rate.banks_counted >= 10


def test_rate_am_raises_when_page_has_no_rates(http_site):
    http_site.route("/rates", "<html><body>тут курсов нет</body></html>")
    provider = RateAmProvider(
        fetcher=HttpFetcher(base_url=http_site.base_url, delay_seconds=0), url="/rates"
    )
    with pytest.raises(RateError):
        provider.amd_per_usd()
