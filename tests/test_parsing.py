"""Unit tests for the shared parsing primitives."""

import pytest

from gmaps_review_filter.parsing import (
    parse_rating_text,
    parse_review_count,
    parse_star_rating,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,234 reviews", 1234),
        ("7 đánh giá", 7),
        ("Rated by 42 people", None),
        ("no numbers here", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_review_count(text, expected):
    assert parse_review_count(text) == expected


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("4 stars", 4.0),
        ("4.5 stars", 4.5),
        ("4,5 stars", 4.5),
        ("6 stars", None),
        ("no rating", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_star_rating(label, expected):
    assert parse_star_rating(label) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5/5", 5.0),
        ("4.5/5", 4.5),
        (" 4,5 / 5 ", 4.5),
        ("6/5", None),
        ("4/10", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_rating_text(text, expected):
    assert parse_rating_text(text) == expected
