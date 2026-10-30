"""Refresh golden fixtures from live Google Maps pages.

Re-scrapes the fixture spots, re-trims to header + representative cards,
and prints the new frozen shapes so test assertions can be updated.
Network-dependent; run sparingly (rate limits apply).

Usage (from the repo root): uv run python -m scripts.refresh_fixtures
"""

import time
from pathlib import Path

from bs4 import BeautifulSoup

from gmaps_review_filter.analyzer import ReviewAnalyzer
from gmaps_review_filter.scraper import MapsScraper
from gmaps_review_filter.selectors import CSS_SELECTORS
from tests.spots import LUGER_URL, MBS_URL

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

SPOTS = {
    # NOTE: hotel_mixed.html is archival (pre-filter mixed feed, kept so the
    # analyzer stays tested against third-party excerpts) and is NOT
    # refreshed: live scrapes now return Google-only feeds by design.
    "hotel_google_only.html": (MBS_URL, [0, 1, 159]),
    "restaurant.html": (LUGER_URL, [0, 1, 3, 4]),
}


def trim(html: str, url: str, indices: list[int]) -> str:
    soup = BeautifulSoup(html, "html.parser")
    parts = [f"<!-- provenance: trimmed from {url} (refreshed) -->"]
    header = soup.find("div", class_=CSS_SELECTORS["SPOT_HEADER"])
    if header:
        parts.append(str(header))
    name_btn = soup.find("button", attrs={"data-bundle-id": True})
    if name_btn:
        parts.append(str(name_btn))
    cards = soup.find_all("div", class_="jftiEf")
    for i in indices:
        parts.append(str(cards[i]))
    return "<html><body>\n" + "\n".join(parts) + "\n</body></html>"


def main() -> None:
    for name, (url, indices) in SPOTS.items():
        print(f"scraping {name} ...", flush=True)
        html = MapsScraper(url).fetch_html()
        doc = trim(html, url, indices)
        (OUT / name).write_text(doc, encoding="utf-8")
        rep = ReviewAnalyzer(doc).report()
        print(f"wrote {name} ({len(doc)} bytes): {rep}", flush=True)
        time.sleep(15)
    print("done: update frozen assertions in tests/test_analyzer.py to match")


if __name__ == "__main__":
    main()
