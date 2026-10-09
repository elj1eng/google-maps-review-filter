"""Scrape-failure taxonomy shared by the scraper and the CLI."""


class TransientScrapeError(Exception):
    """Temporary failure (limited view, render flakes); retry fresh."""


class PermanentScrapeError(Exception):
    """Deterministic failure (bad URL, no Google source); do not retry."""
