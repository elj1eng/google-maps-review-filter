"""BeautifulSoup parsing of a scraped Google Maps page.

The analyzer is a thin, offline layer over the same markup the scraper
navigates: it turns HTML into immutable :mod:`models` records. No browser
and no network, so it is fully testable against golden fixtures.
"""

import re

from bs4 import BeautifulSoup, Tag

from gmaps_review_filter.config import THRESHOLDS
from gmaps_review_filter.models import PlaceSummary, ReviewReport
from gmaps_review_filter.parsing import (
    parse_rating_text,
    parse_review_count,
    parse_star_rating,
)
from gmaps_review_filter.selectors import CSS_SELECTORS


class ReviewAnalyzer:
    """Parse one page's place header and review cards."""

    def __init__(self, html: str, trust_limit: int | None = None):
        self.soup = BeautifulSoup(html, "html.parser")
        self.limit: int = (
            trust_limit
            if trust_limit is not None
            else THRESHOLDS["MIN_REVIEWS_FOR_TRUST"]
        )
        self._place: PlaceSummary | None = None

    def place(self) -> PlaceSummary:
        """Header facts; cached because both the CLI and the stub guard need them."""
        if self._place is None:
            self._place = self._parse_place()
        return self._place

    def report(self) -> ReviewReport | None:
        """Trusted/filtered split, or None when the page has no review cards."""
        cards = self.soup.find_all(
            "div", class_=re.compile(CSS_SELECTORS["REVIEW_CARD"])
        )
        if not cards:
            return None

        rated: list[tuple[int, float]] = []
        for card in cards:
            rating = self._star_rating(card)
            if rating is not None:
                rated.append((self._reviewer_count(card), rating))

        trusted = [rating for count, rating in rated if count > self.limit]
        filtered = [rating for count, rating in rated if count <= self.limit]
        return ReviewReport(
            total_scanned=len(cards),
            filtered_count=len(filtered),
            trusted_count=len(trusted),
            trusted_avg=_average(trusted),
            all_avg=_average([rating for _, rating in rated]),
            no_rating_count=len(cards) - len(rated),
        )

    def _parse_place(self) -> PlaceSummary:
        name = self._place_name()
        header = self.soup.find("div", class_=re.compile(CSS_SELECTORS["SPOT_HEADER"]))
        if header is None:
            return PlaceSummary(name=name)

        rating_el = header.find("div", class_=re.compile(CSS_SELECTORS["RATING_VALUE"]))
        total_el = header.find("div", class_=re.compile(CSS_SELECTORS["TOTAL_REVIEWS"]))
        return PlaceSummary(
            name=name,
            rating=(
                parse_star_rating(rating_el.get_text(strip=True)) if rating_el else None
            ),
            total_reviews=(
                parse_review_count(total_el.get_text(" ", strip=True))
                if total_el
                else None
            ),
        )

    def _place_name(self) -> str:
        """Current place name, never a previously viewed one.

        The signed-in navigation rail ("Recents") renders buttons with the
        same data-bundle-id as the place header and precedes it in the DOM;
        a plain first-match lookup therefore returned the last spot
        scraped. The document title names the current place, so use it
        first, and skip nav-rail buttons on fallback.
        """
        title = self.soup.title.get_text(strip=True) if self.soup.title else ""
        if title and title != "Google Maps":
            suffix = " - Google Maps"
            if title.endswith(suffix):
                title = title[: -len(suffix)].strip()
            if title:
                return title

        for button in self.soup.find_all("button", attrs={"data-bundle-id": True}):
            # Recents entries live in a <ul>/<li> rail; the place header
            # does not. (Both carry the same navigationrail jsaction.)
            if button.find_parent(["ul", "li"]) is not None:
                continue
            name_el = button.find("div", class_=re.compile(CSS_SELECTORS["PLACE_NAME"]))
            if name_el:
                text = name_el.get_text().strip()
                if text:
                    return text
        return "Unknown Place"

    def _reviewer_count(self, card: Tag) -> int:
        meta = card.find("div", class_=re.compile(CSS_SELECTORS["METADATA"]))
        if meta is None:
            return 0
        return parse_review_count(meta.get_text(" ", strip=True)) or 0

    def _star_rating(self, card: Tag) -> float | None:
        for span in card.find_all("span", attrs={"role": "img"}):
            label = span.get("aria-label")
            if isinstance(label, str):
                rating = parse_star_rating(label)
                if rating is not None:
                    return rating
        # Hotel cards render the rating as "N/5" text instead of star icons.
        for span in card.find_all(
            "span", class_=re.compile(CSS_SELECTORS["RATING_TEXT"])
        ):
            rating = parse_rating_text(span.get_text())
            if rating is not None:
                return rating
        return None


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
