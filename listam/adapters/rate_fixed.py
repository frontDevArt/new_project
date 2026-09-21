"""Курс, заданный в конфиге. Нужен для отладки и как запасной вариант."""
from __future__ import annotations

from datetime import datetime, timezone

from listam.ports.rate import Rate, RateError, RateProvider


class FixedRateProvider(RateProvider):
    def __init__(self, amd_per_usd: float):
        self.value = float(amd_per_usd)

    def amd_per_usd(self) -> Rate:
        if self.value <= 0:
            raise RateError(
                "В конфиге не задан rate.amd_per_usd — курс 0 это не курс, "
                "а незаполненная настройка"
            )
        return Rate(
            value=self.value,
            source="fixed",
            fetched_at=datetime.now(timezone.utc),
        )
