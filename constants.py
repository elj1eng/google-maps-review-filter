CSS_SELECTORS = {
    "BUSINESS_TITLE": "h1",
    "REVIEWS_TAB": "div.Gpq6kf.NlVald",
    "REVIEW_CARD": "jftiEf",
    "METADATA": "RfnDt",
    "RATING_VALUE": "fontDisplayLarge",
    "RATING_TEXT": "fzvQIb",
    "TOTAL_REVIEWS": "fontBodySmall",
    "PLACE_NAME": "cGRe9e",
    "SCROLLABLE_PANE_CANDIDATES": [
        "div.m6QErb.DxyBCb.kA9KIf.dS8AEf",
        'div[role="main"] div.m6QErb',
        'div[role="main"] div[tabindex="-1"]',
        'div[tabindex="-1"]',
    ],
    # Hotel review filter: the "All reviews" chip (button.HQzyZ) opens a
    # per-platform menu ("Google", "Tripadvisor", ...). Absent on
    # restaurant pages. Option label class rotates; match by text.
    "REVIEW_FILTER_CHIP": "button.HQzyZ",
    "REVIEW_SOURCE_OPTION": "div.twHv4e div.mLuXec",
}

# Volatility registry for the obfuscated Maps classes above. Each entry
# records when the primary was last observed plus fallback selectors tried
# in order by MapsScraper._locate. "observed" must be bumped whenever a
# primary is updated after a Maps markup change.
SELECTOR_META = {
    "BUSINESS_TITLE": {"observed": "2026-10", "fallbacks": []},
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

THRESHOLDS = {
    "MIN_REVIEWS_FOR_TRUST": 10,
}

# Human-language strings for UI matching. The review-count pattern in
# analyzer.REVIEW_COUNT_RE already covers en+vi; this table covers the
# scraper's tab/chip/option matching. Provider names ("Google") are proper
# nouns and match case-insensitively in every locale. "vi" entries are
# best-effort: keywords fall back to English where Maps keeps English
# labels. Detect via <html lang>; anything non-vi falls back to "en".
LOCALE_STRINGS = {
    "en": {
        "reviews_tab": ("review",),
        "filter_chip": ("review",),
        "sort_chip_exclude": ("relevant",),
        "google_source": ("google",),
    },
    "vi": {
        "reviews_tab": ("đánh giá", "review"),
        "filter_chip": ("đánh giá", "review"),
        "sort_chip_exclude": ("relevant", "liên quan"),
        "google_source": ("google",),
    },
}

BROWSER_CONFIG = {
    "VIEWPORT": {"width": 1280, "height": 900},
    "TIMEOUT": 15000,
    "SCROLL_LIMIT": 40,
    "TARGET_REVIEWS": 210,
    "DYNAMIC_WAIT_MS": 6000,
    "SCROLL_STALL_LIMIT": 5,
    "SCROLL_SLEEP_S": 1.5,
    "SCROLL_WHEEL_PX": 2000,
    "MIN_EXPECTED_REVIEWS": 10,
    "FETCH_ATTEMPTS": 5,
    "USER_AGENT": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
}
