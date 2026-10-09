"""Refresh golden fixtures from live Google Maps pages.

Re-scrapes the fixture spots, re-trims to header + representative cards,
and prints the new frozen shapes so test assertions can be updated.
Network-dependent; run sparingly (rate limits apply).

Usage: uv run python tests/refresh_fixtures.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup  # noqa: E402

from analyzer import ReviewAnalyzer  # noqa: E402
from constants import CSS_SELECTORS  # noqa: E402
from scraper import MapsScraper  # noqa: E402

OUT = Path(__file__).parent / "fixtures"

MBS_URL = (
    "https://www.google.com/maps/place/Marina+Bay+Sands+Singapore/"
    "@1.2837575,103.8591065,17z/data=!3m1!"
    "5s0x31da19042de382df:0x5bbfe003fe5e690!4m9!3m8!"
    "1s0x31da19ee4cc09203:0x26c9afefa555dd7!5m2!4m1!1i2!"
    "8m2!3d1.2837575!4d103.8591065!16zL20vMGRkOTAz?entry=ttu"
)
LUGER_URL = (
    "https://www.google.com/maps/place/Peter+Luger+Steak+House/"
    "@40.7098774,-73.9625052,17z/data=!3m1!4b1!4m6!3m5!"
    "1s0x89c25bdedbcaf647:0x469a1d01423a03f2!8m2!3d40.7098774!"
    "4d-73.9625052!16zL20vMGI2MWt4?entry=ttu"
)

SPOTS = {
    # NOTE: hotel_mixed.html is archival (pre-filter mixed feed, kept so the
    # analyzer stays tested against third-party excerpts) and is NOT
    # refreshed: live scrapes now return Google-only feeds by design.
    "hotel_google_only.html": (MBS_URL, [0, 1, 159]),
    "restaurant.html": (LUGER_URL, [0, 1, 3, 4]),
}


def trim(html, url, indices):
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


def main():
    for name, (url, indices) in SPOTS.items():
        print(f"scraping {name} ...", flush=True)
        html = MapsScraper(url).fetch_html()
        doc = trim(html, url, indices)
        (OUT / name).write_text(doc, encoding="utf-8")
        rep = ReviewAnalyzer(doc).analyze_reviews()
        print(f"wrote {name} ({len(doc)} bytes): {rep}", flush=True)
        time.sleep(15)
    print("done: update frozen assertions in tests/test_analyzer.py to match")


if __name__ == "__main__":
    main()
