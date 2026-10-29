"""The review-list scroll loop, isolated from browser/session setup."""

import logging
import time

from playwright.sync_api import Page

from gmaps_review_filter.config import BROWSER_CONFIG
from gmaps_review_filter.dom import (
    find_scrollable_pane,
    spot_review_total,
    wheel_scroll,
)
from gmaps_review_filter.progress import ReviewProgress
from gmaps_review_filter.selectors import CSS_SELECTORS

logger = logging.getLogger(__name__)


def scroll_reviews(page: Page, target: int, progress: ReviewProgress) -> int:
    """Scroll until the target sample or the spot's last review is loaded.

    Returns the number of review cards in the final DOM. The progress bar
    is finished on every path, including failures.
    """
    card_sel = f".{CSS_SELECTORS['REVIEW_CARD']}"
    stall_limit = BROWSER_CONFIG["SCROLL_STALL_LIMIT"]
    sleep_s = BROWSER_CONFIG["SCROLL_SLEEP_S"]
    wait_ms = BROWSER_CONFIG["DYNAMIC_WAIT_MS"]
    wheel_px = BROWSER_CONFIG["SCROLL_WHEEL_PX"]
    if not progress.enabled:
        logger.info("Scrolling review list dynamically...")

    try:
        pane = find_scrollable_pane(page)
        if pane is None:
            logger.warning("Scrollable reviews pane not found.")
            return 0

        # Stop at whichever comes first: the requested sample or the
        # spot's own review count. Without this, a 36-review spot keeps
        # scrolling through the full stall window after card 36.
        total = spot_review_total(page)
        goal = target if not total else min(target, total)

        # Draw the first frame before scrolling begins, so the bar
        # (and the attempt label) is visible as soon as the crawl
        # starts instead of after the first batch lands.
        try:
            progress.update(page.locator(card_sel).count())
        except Exception:
            progress.update(0)

        no_change_count = 0
        completion: tuple[str, tuple[int, ...]] | None = None
        for i in range(BROWSER_CONFIG["SCROLL_LIMIT"]):
            try:
                prev_count = page.locator(card_sel).count()
            except Exception:
                prev_count = 0

            # Reached the sample size, or the spot has no more reviews.
            if prev_count >= goal:
                completion = (
                    (
                        "Reached the spot's total: %d reviews.",
                        (prev_count,),
                    )
                    if goal < target
                    else (
                        "Reached target: %d reviews (target %d).",
                        (prev_count, target),
                    )
                )
                break

            # Re-resolve the pane periodically, after stalls, or after
            # failures: the DOM handle goes stale as Maps re-renders the
            # list, and scrolling a stale pane silently does nothing.
            if (i % 5 == 0 and i > 0) or no_change_count > 0:
                try:
                    pane = find_scrollable_pane(page) or pane
                except Exception:
                    pass

            # 1) Trusted wheel input over the pane (primary trigger).
            if not wheel_scroll(page, pane, wheel_px):
                try:
                    pane = find_scrollable_pane(page) or pane
                    wheel_scroll(page, pane, wheel_px)
                except Exception:
                    pass

            # 2) Fallbacks: keyboard End + bring the last card into view
            # to wake lazy-load observers.
            try:
                page.keyboard.press("End")
            except Exception:
                pass
            try:
                cards = page.locator(card_sel)
                if cards.count():
                    cards.nth(cards.count() - 1).scroll_into_view_if_needed(
                        timeout=2000
                    )
            except Exception:
                pass

            # 3) Wait for growth instead of a blind fixed sleep.
            try:
                page.wait_for_function(
                    f"document.querySelectorAll('{card_sel}').length > {prev_count}",
                    timeout=wait_ms,
                )
            except Exception:
                time.sleep(sleep_s)

            try:
                new_count = page.locator(card_sel).count()
            except Exception:
                new_count = prev_count

            if new_count == prev_count:
                no_change_count += 1
                if no_change_count >= stall_limit:
                    completion = (
                        "Scrolling done: %d reviews (stable for %d checks).",
                        (new_count, stall_limit),
                    )
                    break
            else:
                no_change_count = 0
                progress.update(new_count)
                # When the bar owns the line, periodic logs would tear
                # it apart; they stay for piped/CI output.
                if (i + 1) % 10 == 0 and not progress.enabled:
                    logger.info("  ... loaded %d reviews so far", new_count)

        try:
            final = page.locator(card_sel).count()
        except Exception:
            final = 0
        # Close the bar line before any log: logging would otherwise
        # append to the frame ("72/210 (34%)INFO: Scrolling done...").
        progress.finish()
        if completion is not None and not progress.enabled:
            logger.info(completion[0], *completion[1])
        min_expected = BROWSER_CONFIG["MIN_EXPECTED_REVIEWS"]
        # A spot with fewer reviews than min_expected is complete, not
        # a slow load; only warn when the DOM is short of what exists.
        expected = min_expected if not total else min(min_expected, total)
        if 0 < final < expected:
            logger.warning(
                "Only %d reviews loaded (expected more). Page may have loaded "
                "slowly or Maps rate-limited this run; try again.",
                final,
            )
        return final
    finally:
        progress.finish()
