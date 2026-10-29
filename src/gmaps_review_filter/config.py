"""Tuning knobs: trust threshold, UI-matching locales, browser behavior.

Settings is a frozen dataclass so a component can never mutate global
state: tests (and future env/CLI overrides) build a variant with
``dataclasses.replace(DEFAULT_SETTINGS, ...)`` and inject it.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

# Human-language strings for UI matching. The review-count pattern in
# parsing.REVIEW_COUNT_RE already covers en+vi; this table covers the
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


class Viewport(TypedDict):
    """Playwright's viewport shape (same keys as ``ViewportSize``)."""

    width: int
    height: int


@dataclass(frozen=True, slots=True)
class Settings:
    """All tunable behavior, injectable and immutable."""

    # --- browser -------------------------------------------------------
    viewport_width: int = 1280
    viewport_height: int = 900
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
    timeout_ms: int = 15000

    # --- scrape shape --------------------------------------------------
    target_reviews: int = 210
    fetch_attempts: int = 5
    post_goto_settle_s: float = 2.0
    pre_reload_delay_s: float = 2.0
    pre_reload_jitter_s: float = 2.0
    retry_base_s: float = 5.0
    retry_cap_s: float = 30.0
    retry_jitter_s: float = 2.0
    min_reviews_for_trust: int = 10

    # --- scrolling -----------------------------------------------------
    scroll_limit: int = 40
    dynamic_wait_ms: int = 6000
    scroll_stall_limit: int = 5
    scroll_sleep_s: float = 1.5
    scroll_wheel_px: int = 2000
    min_expected_reviews: int = 10

    # --- settle-waits --------------------------------------------------
    stable_polls: int = 3
    stable_interval_s: float = 0.5
    stable_timeout_s: int = 8

    # --- saved session / login ----------------------------------------
    # Anonymous sessions get ~5 reviews behind a sign-in wall; --login
    # writes this per-user file (never committed, see .gitignore).
    auth_state_path: str = str(Path.home() / ".config" / "gmaps-filter" / "auth.json")
    login_url: str = "https://accounts.google.com/ServiceLogin?hl=en"
    login_timeout_s: int = 300
    login_poll_s: float = 2.0

    @property
    def viewport(self) -> Viewport:
        """Fresh dict so callers cannot mutate shared settings."""
        return {"width": self.viewport_width, "height": self.viewport_height}


DEFAULT_SETTINGS = Settings()
