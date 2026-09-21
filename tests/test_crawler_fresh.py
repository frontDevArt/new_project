"""Инкрементальный обход `--fresh`: где он останавливается и почему.

Сайт здесь не при чём: лента — те же две сохранённые страницы, что и в
`tests/test_crawler.py`.
"""
from __future__ import annotations

from listam.crawler import incremental_stop


def test_incremental_walks_on_while_new_listings_keep_coming():
    assert incremental_stop(pages_without_new=1, threshold=2,
                            pages_fetched=1, ceiling=20) == (None, False)


def test_incremental_stops_after_the_configured_number_of_known_pages():
    reason, is_error = incremental_stop(pages_without_new=2, threshold=2,
                                        pages_fetched=2, ceiling=20)
    assert "2 страниц подряд без новых" in reason
    assert is_error is False


def test_hitting_the_ceiling_is_a_failure_not_a_finish():
    """Потолок значит, что до известных объявлений обход не дошёл: часть ленты
    он не видел, и молча считать такой прогон удачным нельзя."""
    reason, is_error = incremental_stop(pages_without_new=0, threshold=2,
                                        pages_fetched=20, ceiling=20)
    assert "fresh_max_pages" in reason
    assert is_error is True
