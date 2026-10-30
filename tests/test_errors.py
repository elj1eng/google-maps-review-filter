"""Tests for the scrape error taxonomy and its CLI exit-code mapping."""

import pytest

from gmaps_review_filter.cli import EXIT_BAD_URL, EXIT_SCRAPE_FAILED, exit_code_for
from gmaps_review_filter.errors import (
    PermanentScrapeError,
    RetriesExhaustedError,
    ScrapeError,
    TransientScrapeError,
)


def test_every_scrape_error_shares_the_base():
    assert issubclass(TransientScrapeError, ScrapeError)
    assert issubclass(PermanentScrapeError, ScrapeError)
    assert issubclass(RetriesExhaustedError, ScrapeError)


def test_retries_exhausted_names_attempts_and_last_error():
    last = TransientScrapeError("limited view")
    exc = RetriesExhaustedError(5, last)
    assert exc.attempts == 5
    assert exc.last_error is last
    assert "after 5 attempts" in str(exc)
    assert "limited view" in str(exc)


def test_retries_exhausted_allows_a_missing_last_error():
    exc = RetriesExhaustedError(1, None)
    assert exc.last_error is None
    assert "None" in str(exc)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (PermanentScrapeError("bad url"), EXIT_BAD_URL),
        (TransientScrapeError("flake"), EXIT_SCRAPE_FAILED),
        (RetriesExhaustedError(5, None), EXIT_SCRAPE_FAILED),
    ],
)
def test_exit_code_for_maps_the_taxonomy(exc, expected):
    assert exit_code_for(exc) == expected
