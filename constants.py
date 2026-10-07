CSS_SELECTORS = {
    "BUSINESS_TITLE": "h1",
    "REVIEWS_TAB": "div.Gpq6kf.NlVald",
    "REVIEW_CARD": "jftiEf",
    "METADATA": "RfnDt",
    "RATING_VALUE": "fontDisplayLarge",
    "TOTAL_REVIEWS": "fontBodySmall",
    "PLACE_NAME": "cGRe9e",
    "SCROLLABLE_PANE_CANDIDATES": [
        "div.m6QErb.DxyBCb.kA9KIf.dS8AEf",
        'div[role="main"] div.m6QErb',
        'div[role="main"] div[tabindex="-1"]',
        'div[tabindex="-1"]',
    ],
}

THRESHOLDS = {
    "MIN_REVIEWS_FOR_TRUST": 10,
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
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
}
