"""Golden-fixture + unit tests for ReviewAnalyzer.

Fixtures are trimmed excerpts of real Google Maps pages (see provenance
comments inside each file). They run fully offline in seconds.
"""

from pathlib import Path

import pytest

from analyzer import ReviewAnalyzer

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def card(*, count_text=None, rating_stars=None, rating_text=None):
    """Minimal review card exercising one parsing path."""
    meta = f'<div class="RfnDt">{count_text}</div>' if count_text else ""
    star = (
        f'<span role="img" aria-label="{rating_stars}"></span>' if rating_stars else ""
    )
    rtext = (
        f'<span class="fontBodyLarge fzvQIb">{rating_text}</span>'
        if rating_text
        else ""
    )
    return f'<div class="jftiEf">{meta}{star}{rtext}</div>'


def page(*cards):
    return "<html><body>" + "".join(cards) + "</body></html>"


# --- spot summary ---


def test_summary_hotel_mixed():
    assert ReviewAnalyzer(load("hotel_mixed.html")).get_spot_summary() == {
        "rating": "4.6",
        "total": "7904",
        "name": "The Ritz London",
    }


def test_summary_restaurant():
    assert ReviewAnalyzer(load("restaurant.html")).get_spot_summary() == {
        "rating": "4.4",
        "total": "16244",
        "name": "Peter Luger Steak House",
    }


def test_summary_missing_header_defaults():
    assert ReviewAnalyzer("<html><body></body></html>").get_spot_summary() == {
        "rating": "N/A",
        "total": "N/A",
        "name": "Unknown Place",
    }


# --- golden fixtures: frozen end-to-end shapes ---


def test_hotel_mixed_shape():
    """Mixed feed: Google cards + third-party excerpts without counts."""
    rep = ReviewAnalyzer(load("hotel_mixed.html")).analyze_reviews()
    assert rep["total_scanned"] == 5
    assert rep["trusted_count"] == 1
    assert rep["filtered_count"] == 4
    assert rep["no_rating_count"] == 0
    # Regression net for the shipped bug: hotel N/5 ratings parsed as 0.0
    # trusted_avg because no rating was extracted at all.
    assert rep["trusted_avg"] == 5.0


def test_hotel_google_only_comma_count():
    """Comma-separated reviewer counts ('1,234 reviews') parse as ints."""
    rep = ReviewAnalyzer(load("hotel_google_only.html")).analyze_reviews()
    assert rep["total_scanned"] == 3
    assert rep["trusted_count"] == 2
    assert rep["trusted_avg"] == 4.5
    assert rep["all_avg"] == pytest.approx(14 / 3)


def test_restaurant_star_icons():
    rep = ReviewAnalyzer(load("restaurant.html")).analyze_reviews()
    assert rep["total_scanned"] == 4
    assert rep["trusted_count"] == 2
    assert rep["filtered_count"] == 2
    assert rep["trusted_avg"] == 5.0


def test_empty_html_returns_empty_report():
    assert ReviewAnalyzer("<html><body></body></html>").analyze_reviews() == {}


# --- rating parsing ---


def test_star_aria_label_rating():
    rep = ReviewAnalyzer(
        page(card(count_text="50 reviews", rating_stars="4 stars"))
    ).analyze_reviews()
    assert rep["trusted_count"] == 1
    assert rep["trusted_avg"] == 4.0


def test_n_slash_5_text_rating():
    rep = ReviewAnalyzer(
        page(card(count_text="50 reviews", rating_text="5/5"))
    ).analyze_reviews()
    assert rep["trusted_count"] == 1
    assert rep["trusted_avg"] == 5.0


def test_decimal_n_slash_5_rating():
    rep = ReviewAnalyzer(
        page(card(count_text="50 reviews", rating_text="4.5/5"))
    ).analyze_reviews()
    assert rep["trusted_avg"] == 4.5


def test_out_of_range_text_rating_ignored():
    rep = ReviewAnalyzer(
        page(card(count_text="50 reviews", rating_text="6/5"))
    ).analyze_reviews()
    assert rep["no_rating_count"] == 1
    assert rep["trusted_count"] == 0


def test_unrated_card_excluded_from_averages():
    rep = ReviewAnalyzer(
        page(
            card(count_text="50 reviews", rating_text="5/5"),
            card(count_text="60 reviews"),
        )
    ).analyze_reviews()
    assert rep["total_scanned"] == 2
    assert rep["no_rating_count"] == 1
    assert rep["trusted_count"] == 1
    assert rep["all_avg"] == 5.0


# --- reviewer-count parsing + trust boundary ---


def test_trust_boundary_ten_is_filtered_eleven_is_trusted():
    rep = ReviewAnalyzer(
        page(
            card(count_text="10 reviews", rating_text="5/5"),
            card(count_text="11 reviews", rating_text="1/5"),
        )
    ).analyze_reviews()
    assert rep["filtered_count"] == 1
    assert rep["trusted_count"] == 1
    assert rep["trusted_avg"] == 1.0


def test_missing_count_is_filtered_not_trusted():
    rep = ReviewAnalyzer(page(card(rating_text="5/5"))).analyze_reviews()
    assert rep["filtered_count"] == 1
    assert rep["trusted_count"] == 0


def test_metadata_without_count_is_filtered():
    html = (
        '<div class="jftiEf"><div class="RfnDt">Local Guide</div>'
        '<span class="fontBodyLarge fzvQIb">5/5</span></div>'
    )
    rep = ReviewAnalyzer(page(html)).analyze_reviews()
    assert rep["filtered_count"] == 1
    assert rep["trusted_count"] == 0


def test_vietnamese_count():
    rep = ReviewAnalyzer(
        page(card(count_text="7 đánh giá", rating_text="5/5"))
    ).analyze_reviews()
    assert rep["filtered_count"] == 1
    assert rep["trusted_count"] == 0


def test_non_string_aria_label_ignored():
    """Defensive: multi-valued attrs (lists) never parse as ratings."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(
        '<div class="jftiEf"><div class="RfnDt">50 reviews</div></div>',
        "html.parser",
    )
    tag = soup.new_tag("span", attrs={"role": "img"})
    tag["aria-label"] = ["5", "stars"]
    soup.div.append(tag)
    assert ReviewAnalyzer("<html></html>")._star_rating(soup.div) is None
