"""Parsing primitives shared by the DOM scraper and the HTML analyzer.

Both paths see the same human-formatted strings ("1,234 reviews",
"7 đánh giá", "4.5 stars", "5/5"). Keeping the regexes in one module stops
the two parsers from drifting apart.
"""

import re

# Anchored to the "reviews"/"đánh giá" label so an unrelated leading
# number can never be mistaken for a review count.
REVIEW_COUNT_RE = re.compile(r"(\d[\d,]*)\s*(?:reviews?|đánh giá)", re.IGNORECASE)

# Star labels are aria strings such as "4.5 stars" or "Rated 4 out of 5".
_STAR_RATING_RE = re.compile(r"(\d+(?:[.,]\d+)?)")
# Hotel cards render the rating as "N/5" text instead of star icons.
_RATING_TEXT_RE = re.compile(r"\s*(\d(?:[.,]\d)?)\s*/\s*5\s*")


def parse_review_count(text: str | None) -> int | None:
    """First ``<N> reviews`` / ``<N> đánh giá`` count, or None."""
    if not text:
        return None
    match = REVIEW_COUNT_RE.search(text)
    if match is None:
        return None
    return int(match.group(1).replace(",", ""))


def parse_star_rating(label: str | None) -> float | None:
    """Rating from an aria label, bounded to the valid 1..5 range."""
    if not label:
        return None
    match = _STAR_RATING_RE.search(label)
    if match is None:
        return None
    rating = float(match.group(1).replace(",", "."))
    return rating if 1 <= rating <= 5 else None


def parse_rating_text(text: str | None) -> float | None:
    """Rating from hotel-style ``N/5`` text, bounded to 1..5."""
    if not text:
        return None
    match = _RATING_TEXT_RE.fullmatch(text)
    if match is None:
        return None
    rating = float(match.group(1).replace(",", "."))
    return rating if 1 <= rating <= 5 else None
