"""Tests for the pure report renderers (JSON schema v1 + human text)."""

from gmaps_review_filter.models import PlaceSummary, ReviewReport
from gmaps_review_filter.report import SCHEMA_VERSION, format_human, to_dict

PLACE = PlaceSummary(name="Test Spot", rating=4.5, total_reviews=1250)
FULL_REPORT = ReviewReport(
    total_scanned=10,
    filtered_count=3,
    trusted_count=7,
    trusted_avg=4.5,
    all_avg=4.3,
    no_rating_count=0,
)
SOURCE = {"source_url": "https://example.test/place", "requested_target": 50}


def test_to_dict_schema_v1_shape():
    data = to_dict(PLACE, FULL_REPORT, trust_threshold=10, **SOURCE)
    assert data["schema_version"] == SCHEMA_VERSION == 1
    assert data["source"] == {
        "url": "https://example.test/place",
        "requested_target": 50,
    }
    assert data["place"] == {"name": "Test Spot", "rating": 4.5, "total_reviews": 1250}
    assert data["sample"] == {"size": 10, "classified": 10, "unrated": 0}
    assert data["trust"] == {
        "threshold": 10,
        "trusted": 7,
        "filtered": 3,
        "ratio": 0.7,
    }
    assert data["ratings"] == {"all": 4.3, "trusted": 4.5}


def test_to_dict_unknown_place_fields_are_null():
    data = to_dict(
        PlaceSummary(name="Unknown Place"), FULL_REPORT, trust_threshold=10, **SOURCE
    )
    assert data["place"] == {
        "name": "Unknown Place",
        "rating": None,
        "total_reviews": None,
    }


def test_to_dict_ratio_none_when_nothing_classified():
    empty = ReviewReport(
        total_scanned=4,
        filtered_count=0,
        trusted_count=0,
        trusted_avg=0.0,
        all_avg=0.0,
        no_rating_count=4,
    )
    data = to_dict(PLACE, empty, trust_threshold=10, **SOURCE)
    assert data["trust"]["ratio"] is None
    assert data["sample"] == {"size": 4, "classified": 0, "unrated": 4}


def test_format_human_shows_na_ratio_and_unparseable_warning():
    report = ReviewReport(
        total_scanned=4,
        filtered_count=0,
        trusted_count=0,
        trusted_avg=0.0,
        all_avg=0.0,
        no_rating_count=4,
    )
    out = format_human(PLACE, report, 10)
    assert "Trusted review ratio: N/A" in out
    assert "4 cards had no parseable rating" in out


def test_format_human_clean_report_has_no_warning():
    out = format_human(PLACE, FULL_REPORT, 10)
    assert "70.0%" in out
    assert "parseable" not in out


def test_format_human_unknown_fields_render_na():
    out = format_human(PlaceSummary(name="Unknown Place"), FULL_REPORT, 10)
    assert "Rating: N/A (N/A)" in out


def test_format_human_color_is_opt_in():
    assert "\033[" not in format_human(PLACE, FULL_REPORT, 10)
    colored = format_human(PLACE, FULL_REPORT, 10, color=True)
    assert "\033[32m" in colored
    assert "\033[31m" in colored
