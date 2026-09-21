"""Курс, заданный в конфиге. Нужен для отладки и как запасной вариант."""
from __future__ import annotations

from datetime import datetime, timezone

from listam.ports.rate import Rate, RateProvider


class FixedRateProvider(RateProvider):
    def __init__(self, amd_per_usd: float):
        self.value = float(amd_per_usd)

    def amd_per_usd(self) -> Rate:
        return Rate(
            value=self.value,
            source="fixed",
            fetched_at=datetime.now(timezone.utc),
        )
