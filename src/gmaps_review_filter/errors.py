"""Scrape-failure taxonomy shared by the scraper and the CLI."""


class ScrapeError(Exception):
    """Base class for every scrape failure the CLI reports to the user."""


class TransientScrapeError(ScrapeError):
    """Temporary failure (limited view, render flakes); retry fresh."""


class PermanentScrapeError(ScrapeError):
    """Deterministic failure (bad URL, no Google source); do not retry."""


class RetriesExhaustedError(ScrapeError):
    """Every transient attempt failed; the run is over."""

    def __init__(self, attempts: int, last_error: Exception | None) -> None:
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"Scrape failed after {attempts} attempts ({last_error}). Try again later."
        )
