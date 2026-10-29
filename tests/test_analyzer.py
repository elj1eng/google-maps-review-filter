"""Golden-fixture + unit tests for ReviewAnalyzer.

Fixtures are trimmed excerpts of real Google Maps pages (see provenance
comments inside each file). They run fully offline in seconds.
"""

from pathlib import Path

import pytest

from gmaps_review_filter.analyzer import ReviewAnalyzer
from gmaps_review_filter.models import PlaceSummary

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
    assert ReviewAnalyzer(load("hotel_mixed.html")).place() == PlaceSummary(
        name="The Ritz London", rating=4.6, total_reviews=7904
    )


def test_summary_restaurant():
    assert ReviewAnalyzer(load("restaurant.html")).place() == PlaceSummary(
        name="Peter Luger Steak House", rating=4.4, total_reviews=16244
    )


def test_summary_name_ignores_navigation_rail_recents():
    """A signed-in session adds a Recents rail listing prior spots.

    Its buttons share data-bundle-id with the place header, and it comes
    first in the DOM, so the first-match lookup returned the previously
    scraped spot on every later scrape. The document title is authoritative.
    """
    html = """
    <html><head><title>Brisnie Matcha - Google Maps</title></head><body>
    <div class="rail"><ul>
      <li><button data-bundle-id="Ho Chi Minh City"
                  jsaction="click:navigationrail.bundle">
        <div class="cGRe9e">Ho Chi Minh City</div></button></li>
      <li><button data-bundle-id="Marina Bay Sands Singapore"
                  jsaction="click:navigationrail.bundle">
        <div class="cGRe9e">Marina Bay Sands Singapore</div></button></li>
    </ul></div>
    </body></html>
    """
    assert ReviewAnalyzer(html).place().name == "Brisnie Matcha"


def test_summary_name_skips_nav_buttons_without_title():
    html = """
    <html><body>
    <div class="rail"><ul><li>
      <button data-bundle-id="Old Spot" jsaction="click:navigationrail.bundle">
        <div class="cGRe9e">Old Spot</div></button>
    </li></ul></div>
    <button data-bundle-id="Real Spot"><div class="cGRe9e">Real Spot</div></button>
    </body></html>
    """
    assert ReviewAnalyzer(html).place().name == "Real Spot"


def test_summary_generic_title_falls_back_to_header():
    html = """
    <html><head><title>Google Maps</title></head><body>
    <button data-bundle-id="Real Spot"><div class="cGRe9e">Real Spot</div></button>
    </body></html>
    """
    assert ReviewAnalyzer(html).place().name == "Real Spot"


def test_summary_missing_header_defaults():
    assert ReviewAnalyzer("<html><body></body></html>").place() == PlaceSummary(
        name="Unknown Place"
    )


# --- golden fixtures: frozen end-to-end shapes ---


def test_hotel_mixed_shape():
    """Mixed feed: Google cards + third-party excerpts without counts."""
    rep = ReviewAnalyzer(load("hotel_mixed.html")).report()
    assert rep.total_scanned == 5
    assert rep.trusted_count == 1
    assert rep.filtered_count == 4
    assert rep.no_rating_count == 0
    # Regression net for the shipped bug: hotel N/5 ratings parsed as 0.0
    # trusted_avg because no rating was extracted at all.
    assert rep.trusted_avg == 5.0


def test_hotel_google_only_comma_count():
    """Comma-separated reviewer counts ('1,234 reviews') parse as ints."""
    rep = ReviewAnalyzer(load("hotel_google_only.html")).report()
    assert rep.total_scanned == 3
    assert rep.trusted_count == 2
    assert rep.trusted_avg == 4.5
    assert rep.all_avg == pytest.approx(14 / 3)


def test_restaurant_star_icons():
    rep = ReviewAnalyzer(load("restaurant.html")).report()
    assert rep.total_scanned == 4
    assert rep.trusted_count == 2
    assert rep.filtered_count == 2
    assert rep.trusted_avg == 5.0


def test_empty_html_returns_none_report():
    assert ReviewAnalyzer("<html><body></body></html>").report() is None


# --- rating parsing ---


def test_star_aria_label_rating():
    rep = ReviewAnalyzer(
        page(card(count_text="50 reviews", rating_stars="4 stars"))
    ).report()
    assert rep.trusted_count == 1
    assert rep.trusted_avg == 4.0


def test_n_slash_5_text_rating():
    rep = ReviewAnalyzer(
        page(card(count_text="50 reviews", rating_text="5/5"))
    ).report()
    assert rep.trusted_count == 1
    assert rep.trusted_avg == 5.0


def test_decimal_n_slash_5_rating():
    rep = ReviewAnalyzer(
        page(card(count_text="50 reviews", rating_text="4.5/5"))
    ).report()
    assert rep.trusted_avg == 4.5


def test_out_of_range_text_rating_ignored():
    rep = ReviewAnalyzer(
        page(card(count_text="50 reviews", rating_text="6/5"))
    ).report()
    assert rep.no_rating_count == 1
    assert rep.trusted_count == 0


def test_unrated_card_excluded_from_averages():
    rep = ReviewAnalyzer(
        page(
            card(count_text="50 reviews", rating_text="5/5"),
            card(count_text="60 reviews"),
        )
    ).report()
    assert rep.total_scanned == 2
    assert rep.no_rating_count == 1
    assert rep.trusted_count == 1
    assert rep.all_avg == 5.0


# --- reviewer-count parsing + trust boundary ---


def test_trust_boundary_ten_is_filtered_eleven_is_trusted():
    rep = ReviewAnalyzer(
        page(
            card(count_text="10 reviews", rating_text="5/5"),
            card(count_text="11 reviews", rating_text="1/5"),
        )
    ).report()
    assert rep.filtered_count == 1
    assert rep.trusted_count == 1
    assert rep.trusted_avg == 1.0


def test_missing_count_is_filtered_not_trusted():
    rep = ReviewAnalyzer(page(card(rating_text="5/5"))).report()
    assert rep.filtered_count == 1
    assert rep.trusted_count == 0


def test_metadata_without_count_is_filtered():
    html = (
        '<div class="jftiEf"><div class="RfnDt">Local Guide</div>'
        '<span class="fontBodyLarge fzvQIb">5/5</span></div>'
    )
    rep = ReviewAnalyzer(page(html)).report()
    assert rep.filtered_count == 1
    assert rep.trusted_count == 0


def test_vietnamese_count():
    rep = ReviewAnalyzer(
        page(card(count_text="7 đánh giá", rating_text="5/5"))
    ).report()
    assert rep.filtered_count == 1
    assert rep.trusted_count == 0


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
