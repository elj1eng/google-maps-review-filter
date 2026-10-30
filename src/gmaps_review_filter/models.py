"""Immutable domain records produced by the analyzer.

Numbers are native (``int``/``float``) and missing facts are ``None``;
the "N/A" sentinel lives only in the human renderer, never in the data.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlaceSummary:
    """Header facts about the scraped place."""

    name: str
    rating: float | None = None
    total_reviews: int | None = None


@dataclass(frozen=True, slots=True)
class ReviewReport:
    """Trusted/filtered split over one loaded sample."""

    total_scanned: int
    filtered_count: int
    trusted_count: int
    trusted_avg: float
    all_avg: float
    no_rating_count: int

    @property
    def classified(self) -> int:
        """Cards with a parseable rating (missing reviewer count reads as 0)."""
        return self.filtered_count + self.trusted_count

    @property
    def trusted_ratio(self) -> float | None:
        """Trusted share of classified cards; None when nothing classified."""
        return self.trusted_count / self.classified if self.classified else None
