"""CSS selectors for Google Maps markup + volatility registry.

Maps obfuscates its classes and rotates them without notice. Each
SELECTOR_META entry records when the primary was last observed plus
fallback selectors tried in order by the scraper's locator. "observed"
must be bumped whenever a primary is updated after a markup change.
"""

from typing import TypedDict


class SelectorMeta(TypedDict):
    observed: str
    fallbacks: list[str]


CSS_SELECTORS: dict[str, str] = {
    "BUSINESS_TITLE": "h1",
    "SPOT_HEADER": "jANrlb",
    "REVIEWS_TAB": "div.Gpq6kf.NlVald",
    "REVIEW_CARD": "jftiEf",
    "METADATA": "RfnDt",
    "RATING_VALUE": "fontDisplayLarge",
    "RATING_TEXT": "fzvQIb",
    "TOTAL_REVIEWS": "fontBodySmall",
    "PLACE_NAME": "cGRe9e",
    # Hotel review filter: the "All reviews" chip (button.HQzyZ) opens a
    # per-platform menu ("Google", "Tripadvisor", ...). Absent on
    # restaurant pages. Option label class rotates; match by text.
    "REVIEW_FILTER_CHIP": "button.HQzyZ",
    "REVIEW_SOURCE_OPTION": "div.twHv4e div.mLuXec",
}

SCROLLABLE_PANE_CANDIDATES: list[str] = [
    "div.m6QErb.DxyBCb.kA9KIf.dS8AEf",
    'div[role="main"] div.m6QErb',
    'div[role="main"] div[tabindex="-1"]',
    'div[tabindex="-1"]',
]

# Volatility registry for the obfuscated Maps classes above. Each entry
# records when the primary was last observed plus fallback selectors tried
# in order by MapsScraper._locate. "observed" must be bumped whenever a
# primary is updated after a Maps markup change.
SELECTOR_META: dict[str, SelectorMeta] = {
    "BUSINESS_TITLE": {"observed": "2026-10", "fallbacks": []},
    "SPOT_HEADER": {"observed": "2026-10", "fallbacks": []},
    "REVIEWS_TAB": {
        "observed": "2026-10",
        "fallbacks": ["button.hh2c6"],
    },
    "REVIEW_CARD": {"observed": "2026-10", "fallbacks": []},
    "METADATA": {"observed": "2026-10", "fallbacks": []},
    "RATING_VALUE": {"observed": "2026-10", "fallbacks": []},
    "RATING_TEXT": {"observed": "2026-10", "fallbacks": []},
    "TOTAL_REVIEWS": {"observed": "2026-10", "fallbacks": []},
    "PLACE_NAME": {"observed": "2026-10", "fallbacks": []},
    "REVIEW_FILTER_CHIP": {"observed": "2026-10", "fallbacks": []},
    "REVIEW_SOURCE_OPTION": {
        "observed": "2026-10",
        "fallbacks": [
            "[role='menu'] [role='menuitem']",
            "[role='listbox'] [role='option']",
        ],
    },
}
