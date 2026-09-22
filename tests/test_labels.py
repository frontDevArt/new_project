"""Подписи статусов живут в одном месте: два словаря расходятся через месяц."""
from __future__ import annotations

from listam.domain import labels


def test_every_match_status_of_the_schema_has_a_russian_word():
    from listam.adapters.db_sqlite import MATCH_STATUSES

    assert set(MATCH_STATUSES) == set(labels.MATCH_STATUSES)


def test_the_window_and_the_export_read_the_same_dictionary():
    from listam import matches_view
    from listam.adapters import exporter_xlsx

    assert exporter_xlsx.MATCH_STATUSES is labels.MATCH_STATUSES
    assert matches_view.MATCH_STATUSES is labels.MATCH_STATUSES
    assert exporter_xlsx.SELLER_TYPES is labels.SELLER_TYPES
    assert matches_view.SELLER_TYPES is labels.SELLER_TYPES


def test_every_listing_status_of_the_database_has_a_russian_word():
    """Витрина и выгрузка переводят `status` объявления одним словарём."""
    from listam.adapters import exporter_xlsx

    assert exporter_xlsx.STATUSES is labels.LISTING_STATUSES
    assert set(labels.LISTING_STATUSES) == {"active", "gone"}
