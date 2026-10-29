"""Rendering of an analysis: machine-readable JSON and the human report.

Pure functions: no streams, no logging, no color detection. The caller
decides whether color is appropriate (TTY + NO_COLOR), which keeps piped
stdout plain and makes both renderers trivial to test.
"""

from typing import Any

from gmaps_review_filter.models import PlaceSummary, ReviewReport

SCHEMA_VERSION = 1

GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"


def to_dict(
    place: PlaceSummary,
    report: ReviewReport,
    *,
    source_url: str,
    requested_target: int,
    trust_threshold: int,
) -> dict[str, Any]:
    """JSON payload (schema v1): grouped facts plus provenance."""
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "url": source_url,
            "requested_target": requested_target,
        },
        "place": {
            "name": place.name,
            "rating": place.rating,
            "total_reviews": place.total_reviews,
        },
        "sample": {
            "size": report.total_scanned,
            "classified": report.classified,
            "unrated": report.no_rating_count,
        },
        "trust": {
            "threshold": trust_threshold,
            "trusted": report.trusted_count,
            "filtered": report.filtered_count,
            "ratio": report.trusted_ratio,
        },
        "ratings": {
            "all": report.all_avg,
            "trusted": report.trusted_avg,
        },
    }


def format_human(
    place: PlaceSummary,
    report: ReviewReport,
    trust_threshold: int,
    *,
    color: bool = False,
) -> str:
    """The report text. Layout is stable; only ANSI codes are optional."""
    rating = _unknown(place.rating)
    total = _unknown(place.total_reviews)
    ratio = (
        f"{report.trusted_ratio * 100:.1f}%"
        if report.trusted_ratio is not None
        else "N/A"
    )
    parts = [
        "",
        place.name,
        f"Rating: {rating} ({total})",
        "",
        f"--- ANALYSIS (Sample: {report.total_scanned} reviews) ---",
        _paint(
            f"Filtered out (<= {trust_threshold} reviews): {report.filtered_count}",
            RED,
            color,
        ),
        f"Trusted reviewers (> {trust_threshold} reviews): {report.trusted_count}",
        _paint(f"Trusted review ratio: {ratio}", GREEN, color),
    ]
    if report.no_rating_count:
        parts += [
            "",
            _paint(
                f"Warning: {report.no_rating_count} cards had no parseable "
                "rating (markup may have changed).",
                RED,
                color,
            ),
        ]
    parts += [
        "",
        f"Average rating (all): {report.all_avg:.2f} / 5",
        _paint(
            f"Average rating (trusted only): {report.trusted_avg:.2f} / 5", GREEN, color
        ),
        "",
    ]
    return "\n".join(parts)


def _unknown(value: float | int | None) -> str:
    return "N/A" if value is None else str(value)


def _paint(text: str, code: str, color: bool) -> str:
    return f"{code}{text}{RESET}" if color else text
