"""Google Maps scraping orchestration: retry policy + reviews-tab handling."""

import logging
import random
import time
from typing import Protocol

from playwright.sync_api import Locator, Page

from gmaps_review_filter.analyzer import ReviewAnalyzer
from gmaps_review_filter.auth import resolve_auth_state
from gmaps_review_filter.config import DEFAULT_SETTINGS, LOCALE_STRINGS, Settings
from gmaps_review_filter.dom import (
    card_counter,
    is_limited_view,
    locate,
    page_lang,
    wait_for_stable_count,
    wait_for_title,
)
from gmaps_review_filter.errors import (
    PermanentScrapeError,
    RetriesExhaustedError,
    TransientScrapeError,
)
from gmaps_review_filter.locale import matches_any
from gmaps_review_filter.progress import ReviewProgress
from gmaps_review_filter.scroll import scroll_reviews
from gmaps_review_filter.selectors import CSS_SELECTORS
from gmaps_review_filter.session import ScrapeSession
from gmaps_review_filter.ui import Terminal
from gmaps_review_filter.urls import validate_place_url

logger = logging.getLogger(__name__)

LIMITED_VIEW_MSG = "Google served a 'limited view' of Maps."


class ProgressFactory(Protocol):
    def __call__(
        self,
        total: int,
        *,
        enabled: bool | None = ...,
        label: str = ...,
    ) -> ReviewProgress: ...


def _retry_delay_s(attempt: int, *, settings: Settings = DEFAULT_SETTINGS) -> float:
    """Jittered exponential backoff for inter-attempt waits (1-based)."""
    base = settings.retry_base_s
    cap = settings.retry_cap_s
    jitter = settings.retry_jitter_s
    return min(base * 2.0 ** (attempt - 1), cap) + random.uniform(0, jitter)  # noqa: S311


def _pre_reload_delay_s(*, settings: Settings = DEFAULT_SETTINGS) -> float:
    """Settle delay before a reload so rapid double-loads don't flag bots."""
    base = settings.pre_reload_delay_s
    jitter = settings.pre_reload_jitter_s
    return base + random.uniform(0, jitter)  # noqa: S311


def is_throttled_stub(loaded: int, target: int, total: int | None) -> bool:
    """True when the loaded sample falls short of what the spot offers.

    Strict: any shortfall vs min(target, total) is treated as a throttled
    stub worth a fresh session. total None (header unparseable) never
    retries blind — falls back to warn-and-return.
    """
    if total is None:
        return False
    return loaded < min(target, total)


class MapsScraper:
    """Scrape one place URL, retrying transient failures with a fresh session."""

    def __init__(
        self,
        url: str,
        target_reviews: int | None = None,
        auth_state: str | None = None,
        progress_factory: ProgressFactory | None = None,
        progress_enabled: bool = True,
        ui: Terminal | None = None,
        settings: Settings = DEFAULT_SETTINGS,
    ) -> None:
        # Fail fast on non-place URLs: no browser is launched for them.
        self.url = validate_place_url(url)
        self.settings = settings
        self.target = target_reviews or settings.target_reviews
        # A saved sign-in lifts the ~5-review wall; absent file = anonymous.
        self.auth_state = resolve_auth_state(auth_state, settings=settings)
        self.progress_factory = progress_factory
        self.progress_enabled = progress_enabled
        self.ui = ui or Terminal()

    def _make_progress(self, attempt: int, attempts: int) -> ReviewProgress:
        """One bar per scrape attempt; short label so it fits on one line."""
        label = "Loading reviews" if attempts <= 1 else f"Attempt {attempt}/{attempts}"
        enabled = None if self.progress_enabled else False
        if self.progress_factory is not None:
            return self.progress_factory(self.target, enabled=enabled, label=label)
        return self.ui.progress(self.target, label=label, enabled=enabled)

    def fetch_html(self) -> str:
        """Scrape the reviews page.

        Returns the reviews HTML. Transient failures (limited view, render
        flakes, throttled stubs) are retried with a fresh session and
        backoff. Permanent failures (bad URL, no Google source) raise
        immediately without burning retries.
        """
        attempts = self.settings.fetch_attempts
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                html, loaded = self._fetch_once(attempt, attempts)
                # Stub guard: a 3-card sample from a 1006-review spot is a
                # throttled stub, not a result — retry with a fresh session
                # instead of reporting it silently.
                total = ReviewAnalyzer(html).place().total_reviews
                if is_throttled_stub(loaded, self.target, total):
                    raise TransientScrapeError(
                        f"loaded only {loaded} of {total} total reviews — "
                        "likely a throttled stub"
                    )
                return html
            except TransientScrapeError as exc:
                last_error = exc
                if attempt < attempts:
                    logger.warning(
                        "Attempt %d/%d: %s — retrying with a fresh session...",
                        attempt,
                        attempts,
                        exc,
                    )
                    # Visual boundary between attempts' progress output.
                    self.ui.separator()
                    # Jittered backoff to ride out limited-view bursts
                    # instead of hammering Google on a fixed cadence.
                    time.sleep(_retry_delay_s(attempt, settings=self.settings))
                else:
                    logger.warning(
                        "Attempt %d/%d: %s — giving up.", attempt, attempts, exc
                    )
        raise RetriesExhaustedError(attempts, last_error)

    def _fetch_once(self, attempt: int = 1, attempts: int = 1) -> tuple[str, int]:
        with ScrapeSession(self.auth_state, settings=self.settings) as page:
            page.goto(self.url, wait_until="domcontentloaded")
            wait_for_title(page, settings=self.settings)

            # Settle: a transient banner can clear on its own once JS
            # finishes; checking immediately locks in a false positive.
            time.sleep(self.settings.post_goto_settle_s)

            # Conditional reload: skip the second navigation on the happy
            # path. Back-to-back goto+reload from a fresh profile reads as
            # automation and triggers the limited view itself.
            if is_limited_view(page):
                # Wait longer before the refresh instead of hammering it.
                time.sleep(_pre_reload_delay_s(settings=self.settings))
                page.reload(wait_until="domcontentloaded")
                wait_for_title(page, settings=self.settings)

                # Fail fast: the anti-bot banner is already on the DOM
                # when the limited view is active, so don't open tabs.
                if is_limited_view(page):
                    raise TransientScrapeError(LIMITED_VIEW_MSG)

            opened = self._open_reviews_tab(page)
            if opened and is_limited_view(page):
                raise TransientScrapeError(LIMITED_VIEW_MSG)

            loaded = (
                scroll_reviews(
                    page,
                    self.target,
                    self._make_progress(attempt, attempts),
                    settings=self.settings,
                )
                if opened
                else 0
            )

            if not opened or loaded == 0:
                # Search-list page or a place without a rendered reviews
                # list — never return empty HTML silently.
                raise TransientScrapeError(
                    "the page had no reviews list (wrong page or load failure)"
                )

            if is_limited_view(page):
                raise TransientScrapeError(
                    "Google served a 'limited view' of Maps mid-scroll."
                )

            return page.content(), loaded

    def _open_reviews_tab(self, page: Page) -> bool:
        lang = page_lang(page)
        tab_keywords = LOCALE_STRINGS[lang]["reviews_tab"]
        tabs = locate(page, "REVIEWS_TAB")
        for i in range(tabs.count()):
            try:
                label = tabs.nth(i).inner_text(timeout=2000).lower()
            except Exception:
                continue
            if matches_any(label, tab_keywords):
                tabs.nth(i).click(force=True)
                break
        else:
            logger.warning("Reviews tab not found; using whatever is visible.")

        # Hotel tabs: filter to Google-only reviews via the filter chip.
        # Restaurant pages have no filter chips: no-op there.
        self._select_google_source(page, lang)

        # Wait for the review list to actually render before scrolling.
        # Without this, scroll_reviews can start while only the first
        # batch (or zero cards) is present and exit early with ~5 reviews.
        # Settle-wait, not a fixed sleep: returns as soon as the batch
        # stops growing.
        card_sel = f".{CSS_SELECTORS['REVIEW_CARD']}"
        try:
            page.wait_for_selector(
                card_sel,
                timeout=self.settings.timeout_ms,
            )
        except Exception:
            logger.warning("No review cards appeared after opening reviews tab.")
            return False

        settled = wait_for_stable_count(
            card_counter(page, card_sel), settings=self.settings
        )
        logger.debug("Review list settled at %s cards.", settled)
        return True

    def _select_google_source(self, page: Page, lang: str = "en") -> None:
        """Filter a hotel Reviews tab to Google-only via the filter chip.

        Hotel tabs have filter chips (button.HQzyZ: "All reviews",
        "Most relevant"). The "All reviews" chip opens a per-platform menu;
        choosing "Google" drops third-party excerpts from the feed.
        Restaurant pages have no filter chips: return immediately.
        Raises PermanentScrapeError when Google is missing (deterministic
        page fact: fail fast) or TransientScrapeError when the menu
        interaction itself fails (render flake: worth a fresh session).
        """
        chip: Locator | None = None
        chip_label = ""
        strings = LOCALE_STRINGS.get(lang, LOCALE_STRINGS["en"])
        try:
            chips = locate(page, "REVIEW_FILTER_CHIP")
            for i in range(chips.count()):
                try:
                    label = chips.nth(i).inner_text(timeout=2000).strip()
                except Exception:
                    continue
                if matches_any(label, strings["filter_chip"]) and not matches_any(
                    label, strings["sort_chip_exclude"]
                ):
                    chip = chips.nth(i)
                    chip_label = label
                    break
        except Exception:
            return
        if chip is None:
            return  # no filter chip: restaurant page or changed markup
        if chip_label.lower() in strings["google_source"]:
            logger.info("Review filter already 'Google'.")
            return

        card_sel = f".{CSS_SELECTORS['REVIEW_CARD']}"
        try:
            chip.click(force=True)
        except Exception as exc:
            raise TransientScrapeError(f"could not open the review filter menu ({exc})")

        # The menu renders on click; the option label class rotates, so
        # match options by visible text, scoped to the open menu.
        try:
            page.wait_for_selector(
                CSS_SELECTORS["REVIEW_SOURCE_OPTION"],
                timeout=self.settings.timeout_ms,
            )
        except Exception:
            pass
        options = locate(page, "REVIEW_SOURCE_OPTION")
        if options.count() == 0:
            raise TransientScrapeError("review filter menu opened with no options")

        names, google_idx = [], -1
        for i in range(options.count()):
            try:
                text = options.nth(i).inner_text(timeout=2000).strip()
            except Exception:
                continue
            names.append(text)
            if text.lower() in strings["google_source"] and google_idx < 0:
                google_idx = i
        logger.info("Review filter options: %s", names or ["(unreadable)"])
        if google_idx < 0:
            raise PermanentScrapeError("review filter offers no Google option")

        try:
            first = page.locator(card_sel).first
            try:
                options.nth(google_idx).click(force=True)
            except Exception:
                # Fall back to the clickable option container.
                options.nth(google_idx).locator(
                    "xpath=ancestor::div[contains(@class,'twHv4e')]"
                ).first.click(force=True)
            try:
                first.wait_for(state="detached", timeout=self.settings.timeout_ms)
            except Exception:
                pass  # list updated in place; verify presence below
            page.wait_for_selector(card_sel, timeout=self.settings.timeout_ms)
        except TransientScrapeError:
            raise
        except Exception as exc:
            raise TransientScrapeError(
                f"switching review filter to Google failed ({exc})"
            )

        # Settle: the list may update in place after the switch; proceed
        # as soon as the count stops changing instead of a fixed sleep.
        settled = wait_for_stable_count(
            card_counter(page, card_sel), settings=self.settings
        )
        logger.info("Review filter switched to 'Google' (%s cards).", settled)
