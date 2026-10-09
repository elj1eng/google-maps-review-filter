"""Filter Google Maps reviews to a trusted rating (package root)."""

from gmaps_review_filter.errors import PermanentScrapeError, TransientScrapeError

__all__ = ["PermanentScrapeError", "TransientScrapeError"]
