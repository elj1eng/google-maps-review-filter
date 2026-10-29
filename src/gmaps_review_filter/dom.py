"""Thin, testable wrappers over Playwright DOM probing.

Every helper swallows the DOM errors Maps throws while it re-renders:
"element went away" is normal here, not an exceptional state. The policy
functions (settle-waits, URL/total parsing) stay pure and injectable so
they can be tested without a browser.
"""

import time
from collections.abc import Callable

from playwright.sync_api import Locator, Page

from gmaps_review_filter.config import DEFAULT_SETTINGS, Settings
from gmaps_review_filter.errors import PermanentScrapeError
from gmaps_review_filter.locale import detect_lang
from gmaps_review_filter.parsing import parse_review_count
from gmaps_review_filter.selectors import (
    CSS_SELECTORS,
    SCROLLABLE_PANE_CANDIDATES,
    SELECTOR_META,
)

LIMITED_VIEW_TEXT = "limited view of Google Maps"


def locate(page: Page, key: str) -> Locator:
    """Locator for a registry key: primary, then fallbacks in order.

    Returns the first candidate matching anything; if none match, the
    primary (so callers keep their absent-element behavior).
    """
    meta = SELECTOR_META.get(key)
    fallbacks = meta["fallbacks"] if meta else []
    candidates = [CSS_SELECTORS[key], *fallbacks]
    for sel in candidates:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return page.locator(candidates[0])


def card_counter(page: Page, card_sel: str) -> Callable[[], int | None]:
    """Zero-arg card count for settle-waits; unreadable DOM reads as None."""

    def count() -> int | None:
        try:
            return page.locator(card_sel).count()
        except Exception:
            return None

    return count


def wait_for_stable_count(
    get_count: Callable[[], int | None],
    *,
    polls: int | None = None,
    interval_s: float | None = None,
    timeout_s: float | None = None,
    settings: Settings = DEFAULT_SETTINGS,
) -> int | None:
    """Poll get_count() until it repeats the same value `polls` times in a row.

    Event-driven alternative to fixed sleeps after a list renders: returns
    as soon as the count settles, or the last observed value at timeout.
    get_count is injected so the policy stays testable without a browser.
    """
    needed = polls if polls is not None else settings.stable_polls
    interval = interval_s if interval_s is not None else settings.stable_interval_s
    timeout = timeout_s if timeout_s is not None else settings.stable_timeout_s
    deadline = time.monotonic() + timeout
    last: int | None = None
    run = 0
    while True:
        try:
            value = get_count()
        except Exception:
            value = None
        if value == last:
            run += 1
        else:
            last, run = value, 1
        if run >= needed:
            return last
        if time.monotonic() >= deadline:
            return last
        time.sleep(interval)


def wait_for_title(page: Page, *, settings: Settings = DEFAULT_SETTINGS) -> None:
    """Wait for the place title; fail loudly when it never renders.

    Raises PermanentScrapeError: a page with no title after load +
    reload is a deterministic URL problem (linguistic/unresolvable
    URL), not a scrape flake — exit 2, never retried.
    """
    try:
        page.wait_for_selector(
            CSS_SELECTORS["BUSINESS_TITLE"],
            timeout=settings.timeout_ms,
        )
    except Exception as exc:
        raise PermanentScrapeError(
            "Google Maps never showed a place title — the URL is "
            "likely invalid or the page failed to load."
        ) from exc


def is_limited_view(page: Page) -> bool:
    """True when Google shows the anti-bot banner with only ~5 reviews."""
    try:
        return page.get_by_text(LIMITED_VIEW_TEXT, exact=False).count() > 0
    except Exception:
        return False


def page_lang(page: Page) -> str:
    """Detect the page language for locale-aware tab/chip matching."""
    try:
        return detect_lang(page.evaluate("document.documentElement.lang"))
    except Exception:
        return "en"


def find_scrollable_pane(page: Page) -> Locator | None:
    """Return a handle to the actual scrollable reviews container.

    Picks the candidate div with the largest scrollable area.
    Falls back to the first `div[role="main"] div[tabindex="-1"]`.
    """
    best: Locator | None = None
    best_h: float = 0
    for sel in SCROLLABLE_PANE_CANDIDATES:
        try:
            loc = page.locator(sel)
            n = loc.count()
        except Exception:
            continue
        for i in range(n):
            try:
                el = loc.nth(i)
                scroll_h = el.evaluate("el => el.scrollHeight - el.clientHeight")
                if isinstance(scroll_h, (int, float)) and scroll_h > best_h:
                    best_h = scroll_h
                    best = el
            except Exception:
                continue
    if best is not None:
        return best
    try:
        return page.locator('div[role="main"] div[tabindex="-1"]').first
    except Exception:
        return None


def wheel_scroll(
    page: Page,
    pane: Locator,
    wheel_px: int,
    *,
    settings: Settings = DEFAULT_SETTINGS,
) -> bool:
    """Scroll the reviews pane with real wheel input.

    Returns True only when trusted wheel input was delivered.
    Maps only lazy-loads new review batches in response to trusted
    input events; assigning `scrollTop` via JS scrolls the DOM but
    never triggers the next batch (stalls at the first 5 cards), so
    the JS assignment below runs as a last resort but reports False.
    """
    try:
        box = pane.bounding_box()
        if box and box["width"] > 0 and box["height"] > 0:
            vw = settings.viewport_width
            vh = settings.viewport_height
            cx = min(max(box["x"] + box["width"] / 2, 1), vw - 1)
            cy = min(max(box["y"] + box["height"] / 2, 1), vh - 1)
            page.mouse.move(cx, cy)
            page.mouse.wheel(0, wheel_px)
            return True
    except Exception:
        pass
    try:
        pane.evaluate("el => el.scrollTop = el.scrollHeight")
    except Exception:
        pass
    return False


def spot_review_total(page: Page) -> int | None:
    """Header review count, or None when it cannot be parsed.

    Small spots finish in one or two batches; waiting out the stall
    limit after every review is already in the DOM is pure delay.
    """
    try:
        header = page.locator(f"div.{CSS_SELECTORS['SPOT_HEADER']}")
        if header.count() == 0:
            return None
        total_loc = header.locator(f"div.{CSS_SELECTORS['TOTAL_REVIEWS']}")
        if total_loc.count() == 0:
            return None
        text = total_loc.first.inner_text(timeout=2000)
    except Exception:
        return None
    return parse_review_count(text)
