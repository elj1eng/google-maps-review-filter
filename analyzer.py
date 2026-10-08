import re

from bs4 import BeautifulSoup, Tag

from constants import CSS_SELECTORS, THRESHOLDS

REVIEW_COUNT_RE = re.compile(r"(\d[\d,]*)\s*(?:reviews?|đánh giá)", re.IGNORECASE)


class ReviewAnalyzer:
    def __init__(self, html: str, trust_limit: int | None = None):
        self.soup = BeautifulSoup(html, "html.parser")
        self.limit: int = (
            trust_limit
            if trust_limit is not None
            else THRESHOLDS["MIN_REVIEWS_FOR_TRUST"]
        )

    def get_spot_summary(self) -> dict[str, str]:
        summary = {"rating": "N/A", "total": "N/A", "name": "Unknown Place"}

        button = self.soup.find("button", attrs={"data-bundle-id": True})
        if button:
            name_el = button.find("div", class_=re.compile(CSS_SELECTORS["PLACE_NAME"]))
            if name_el:
                summary["name"] = name_el.get_text().strip()

        header = self.soup.find("div", class_=re.compile("jANrlb"))
        if header:
            rating_el = header.find(
                "div", class_=re.compile(CSS_SELECTORS["RATING_VALUE"])
            )
            if rating_el:
                summary["rating"] = rating_el.get_text().strip()
            total_el = header.find(
                "div", class_=re.compile(CSS_SELECTORS["TOTAL_REVIEWS"])
            )
            if total_el:
                # Anchor to the "reviews" label so an unrelated leading
                # number in the header can never be mistaken for the total.
                match = REVIEW_COUNT_RE.search(total_el.get_text(" ", strip=True))
                if match:
                    summary["total"] = match.group(1).replace(",", "")

        return summary

    def analyze_reviews(self) -> dict[str, int | float]:
        cards = self.soup.find_all(
            "div", class_=re.compile(CSS_SELECTORS["REVIEW_CARD"])
        )

        all_ratings: list[dict[str, int | float]] = []
        for card in cards:
            count = self._reviewer_count(card)
            rating = self._star_rating(card)
            if rating is not None:
                all_ratings.append({"count": count, "rating": rating})

        total = len(cards)
        if total == 0:
            return {}

        trusted = [r for r in all_ratings if r["count"] > self.limit]
        filtered_out = [r for r in all_ratings if r["count"] <= self.limit]

        return {
            "total_scanned": total,
            "filtered_count": len(filtered_out),
            "trusted_count": len(trusted),
            "trusted_avg": _average([r["rating"] for r in trusted]),
            "all_avg": _average([r["rating"] for r in all_ratings]),
            "no_rating_count": total - len(all_ratings),
        }

    def _reviewer_count(self, card: Tag) -> int:
        meta = card.find("div", class_=re.compile(CSS_SELECTORS["METADATA"]))
        if not meta:
            return 0
        match = REVIEW_COUNT_RE.search(meta.get_text(" ", strip=True))
        if not match:
            return 0
        # Safe without try/except: the regex admits only digits and commas.
        return int(match.group(1).replace(",", ""))

    def _star_rating(self, card: Tag) -> float | None:
        for span in card.find_all("span", attrs={"role": "img"}):
            label = span.get("aria-label")
            if not isinstance(label, str):
                continue
            match = re.search(r"(\d+[,.]?\d*)", label)
            if match:
                # Safe: the regex admits only digits with one optional
                # decimal separator.
                rating = float(match.group(1).replace(",", "."))
                if 1 <= rating <= 5:
                    return rating
        # Hotel cards render the rating as "N/5" text instead of star icons.
        for span in card.find_all(
            "span", class_=re.compile(CSS_SELECTORS["RATING_TEXT"])
        ):
            match = re.fullmatch(r"\s*(\d(?:[,.]\d)?)\s*/\s*5\s*", span.get_text())
            if match:
                # Safe: single digit with one optional decimal always parses.
                rating = float(match.group(1).replace(",", "."))
                if 1 <= rating <= 5:
                    return rating
        return None


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
