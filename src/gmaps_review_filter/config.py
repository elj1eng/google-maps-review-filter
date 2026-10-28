"""Tuning knobs: trust threshold, UI-matching locales, browser behavior."""

from pathlib import Path
from typing import TypedDict


class Thresholds(TypedDict):
    MIN_REVIEWS_FOR_TRUST: int


THRESHOLDS: Thresholds = {
    "MIN_REVIEWS_FOR_TRUST": 10,
}

# Human-language strings for UI matching. The review-count pattern in
# analyzer.REVIEW_COUNT_RE already covers en+vi; this table covers the
# scraper's tab/chip/option matching. Provider names ("Google") are proper
# nouns and match case-insensitively in every locale. "vi" entries are
# best-effort: keywords fall back to English where Maps keeps English
# labels. Detect via <html lang>; anything non-vi falls back to "en".
LOCALE_STRINGS: dict[str, dict[str, tuple[str, ...]]] = {
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


class BrowserConfig(TypedDict):
    VIEWPORT: dict[str, int]
    TIMEOUT: int
    SCROLL_LIMIT: int
    TARGET_REVIEWS: int
    DYNAMIC_WAIT_MS: int
    SCROLL_STALL_LIMIT: int
    SCROLL_SLEEP_S: float
    SCROLL_WHEEL_PX: int
    STABLE_POLLS: int
    STABLE_INTERVAL_S: float
    STABLE_TIMEOUT_S: int
    MIN_EXPECTED_REVIEWS: int
    FETCH_ATTEMPTS: int
    POST_GOTO_SETTLE_S: float
    PRE_RELOAD_DELAY_S: float
    PRE_RELOAD_JITTER_S: float
    RETRY_BASE_S: float
    RETRY_CAP_S: float
    RETRY_JITTER_S: float
    AUTH_STATE_PATH: str
    LOGIN_URL: str
    LOGIN_TIMEOUT_S: int
    LOGIN_POLL_S: float
    USER_AGENT: str


BROWSER_CONFIG: BrowserConfig = {
    "VIEWPORT": {"width": 1280, "height": 900},
    "TIMEOUT": 15000,
    "SCROLL_LIMIT": 40,
    "TARGET_REVIEWS": 210,
    "DYNAMIC_WAIT_MS": 6000,
    "SCROLL_STALL_LIMIT": 5,
    "SCROLL_SLEEP_S": 1.5,
    "SCROLL_WHEEL_PX": 2000,
    "STABLE_POLLS": 3,
    "STABLE_INTERVAL_S": 0.5,
    "STABLE_TIMEOUT_S": 8,
    "MIN_EXPECTED_REVIEWS": 10,
    "FETCH_ATTEMPTS": 5,
    "POST_GOTO_SETTLE_S": 2.0,
    "PRE_RELOAD_DELAY_S": 2.0,
    "PRE_RELOAD_JITTER_S": 2.0,
    "RETRY_BASE_S": 5.0,
    "RETRY_CAP_S": 30.0,
    "RETRY_JITTER_S": 2.0,
    # Saved Google session (Playwright storage_state JSON). Anonymous
    # sessions get ~5 reviews behind a sign-in wall; --login writes this
    # file. Per-user path, never committed (see .gitignore).
    "AUTH_STATE_PATH": str(Path.home() / ".config" / "gmaps-filter" / "auth.json"),
    "LOGIN_URL": "https://accounts.google.com/ServiceLogin?hl=en",
    "LOGIN_TIMEOUT_S": 300,
    "LOGIN_POLL_S": 2.0,
    "USER_AGENT": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
}
