import os
import shutil
import time

from playwright.sync_api import sync_playwright

from constants import CSS_SELECTORS, BROWSER_CONFIG

LIMITED_VIEW_TEXT = "limited view of Google Maps"


class IncompleteScrape(Exception):
    """Maps served a truncated "limited view" review list; retry with a fresh session."""


class MapsScraper:
    def __init__(self, url):
        self.url = url
        self.profile_path = os.path.join(os.getcwd(), "temp_profile")

    def fetch_html(self):
        """Scrape the reviews page, retrying with a fresh session if Google
        serves its anti-bot "limited view" (~5 reviews, banner shown)."""
        attempts = BROWSER_CONFIG.get("FETCH_ATTEMPTS", 3)
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                return self._fetch_once()
            except IncompleteScrape as exc:
                last_error = exc
                print(
                    f"Attempt {attempt}/{attempts}: {exc} — "
                    "retrying with a fresh session..."
                )
                # Exponential backoff (3s, 6s, 12s, 24s, 30s) to ride out
                # limited-view bursts instead of hammering Google.
                time.sleep(min(3 * 2 ** (attempt - 1), 30))
        raise RuntimeError(
            f"Google kept serving a limited review list after {attempts} "
            f"attempts ({last_error}). Try again later."
        )

    def _fetch_once(self):
        if os.path.exists(self.profile_path):
            shutil.rmtree(self.profile_path)

        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    self.profile_path,
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                    user_agent=BROWSER_CONFIG["USER_AGENT"],
                    viewport=BROWSER_CONFIG["VIEWPORT"],
                )
                page = context.pages[0]
                page.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                )

                page.goto(self.url, wait_until="domcontentloaded")
                try:
                    page.wait_for_selector(
                        CSS_SELECTORS["BUSINESS_TITLE"],
                        timeout=BROWSER_CONFIG["TIMEOUT"],
                    )
                except Exception as exc:
                    raise RuntimeError(
                        "Google Maps never showed a place title — the URL is "
                        "likely invalid or the page failed to load."
                    ) from exc
                time.sleep(1)
                page.reload(wait_until="domcontentloaded")
                time.sleep(3)

                # Fail fast: the anti-bot banner is already on the DOM when
                # the limited view is active, so don't bother opening tabs.
                if self._is_limited_view(page):
                    raise IncompleteScrape("Google served a 'limited view' of Maps.")

                opened = self._open_reviews_tab(page)
                if opened and self._is_limited_view(page):
                    raise IncompleteScrape("Google served a 'limited view' of Maps.")

                loaded = self._scroll_reviews(page) if opened else 0

                if not opened or loaded == 0:
                    # Search-list page or a place without a rendered reviews
                    # list — never return empty HTML silently.
                    raise IncompleteScrape(
                        "the page had no reviews list (wrong page or load failure)"
                    )

                if self._is_limited_view(page):
                    raise IncompleteScrape(
                        "Google served a 'limited view' of Maps mid-scroll."
                    )

                return page.content()
        finally:
            shutil.rmtree(self.profile_path, ignore_errors=True)

    def _is_limited_view(self, page):
        """True when Google shows the anti-bot banner with only ~5 reviews."""
        try:
            return page.get_by_text(LIMITED_VIEW_TEXT, exact=False).count() > 0
        except Exception:
            return False

    def _open_reviews_tab(self, page):
        tabs = page.locator(CSS_SELECTORS["REVIEWS_TAB"])
        for i in range(tabs.count()):
            try:
                label = tabs.nth(i).inner_text(timeout=2000).lower()
            except Exception:
                continue
            if "review" in label:
                tabs.nth(i).click(force=True)
                break
        else:
            print("Warning: Reviews tab not found; using whatever is visible.")

        # Wait for the review list to actually render before scrolling.
        # Without this, _scroll_reviews can start while only the first
        # batch (or zero cards) is present and exit early with ~5 reviews.
        try:
            page.wait_for_selector(
                f".{CSS_SELECTORS['REVIEW_CARD']}",
                timeout=BROWSER_CONFIG["TIMEOUT"],
            )
        except Exception:
            print("Warning: No review cards appeared after opening reviews tab.")
            return False
        time.sleep(2)
        return True

    def _find_scrollable_pane(self, page):
        """Return a handle to the actual scrollable reviews container.

        Picks the candidate div with the largest scrollable area.
        Falls back to the first `div[role="main"] div[tabindex="-1"]`.
        """
        candidates = CSS_SELECTORS["SCROLLABLE_PANE_CANDIDATES"]
        best = None
        best_h = 0
        for sel in candidates:
            try:
                loc = page.locator(sel)
                n = loc.count()
            except Exception:
                continue
            for i in range(n):
                try:
                    el = loc.nth(i)
                    scroll_h = el.evaluate("el => el.scrollHeight - el.clientHeight")
                    if scroll_h and scroll_h > best_h:
                        best_h = scroll_h
                        best = el
                except Exception:
                    continue
        if best:
            return best
        try:
            return page.locator('div[role="main"] div[tabindex="-1"]').first
        except Exception:
            return None

    def _wheel_scroll(self, page, pane, wheel_px):
        """Scroll the reviews pane with real wheel input.

        Maps only lazy-loads new review batches in response to trusted
        input events; assigning `scrollTop` via JS scrolls the DOM but
        never triggers the next batch (stalls at the first 5 cards).
        """
        try:
            box = pane.bounding_box()
            if box and box["width"] > 0 and box["height"] > 0:
                vw = BROWSER_CONFIG["VIEWPORT"]["width"]
                vh = BROWSER_CONFIG["VIEWPORT"]["height"]
                cx = min(max(box["x"] + box["width"] / 2, 1), vw - 1)
                cy = min(max(box["y"] + box["height"] / 2, 1), vh - 1)
                page.mouse.move(cx, cy)
                page.mouse.wheel(0, wheel_px)
                return True
        except Exception:
            pass
        try:
            pane.evaluate("el => el.scrollTop = el.scrollHeight")
            return True
        except Exception:
            return False

    def _scroll_reviews(self, page):
        print("Scrolling review list dynamically...")
        card_sel = f".{CSS_SELECTORS['REVIEW_CARD']}"
        stall_limit = BROWSER_CONFIG.get("SCROLL_STALL_LIMIT", 5)
        sleep_s = BROWSER_CONFIG.get("SCROLL_SLEEP_S", 1.5)
        wait_ms = BROWSER_CONFIG.get("DYNAMIC_WAIT_MS", 4000)
        wheel_px = BROWSER_CONFIG.get("SCROLL_WHEEL_PX", 2000)
        target = BROWSER_CONFIG.get("TARGET_REVIEWS", 210)

        pane = self._find_scrollable_pane(page)
        if pane is None:
            print("Warning: scrollable reviews pane not found.")
            return 0

        no_change_count = 0
        for i in range(BROWSER_CONFIG["SCROLL_LIMIT"]):
            try:
                prev_count = page.locator(card_sel).count()
            except Exception:
                prev_count = 0

            # Reached the desired sample size: done.
            if prev_count >= target:
                print(f"Reached target: {prev_count} reviews (target {target}).")
                break

            # Re-resolve the pane periodically, after stalls, or after
            # failures: the DOM handle goes stale as Maps re-renders the
            # list, and scrolling a stale pane silently does nothing.
            if (i % 5 == 0 and i > 0) or no_change_count > 0:
                try:
                    pane = self._find_scrollable_pane(page) or pane
                except Exception:
                    pass

            # 1) Trusted wheel input over the pane (primary trigger).
            if not self._wheel_scroll(page, pane, wheel_px):
                try:
                    pane = self._find_scrollable_pane(page) or pane
                    self._wheel_scroll(page, pane, wheel_px)
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
                    cards.nth(cards.count() - 1).scroll_into_view_if_needed(timeout=2000)
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
                    print(f"Scrolling done: {new_count} reviews (stable for {stall_limit} checks).")
                    break
            else:
                no_change_count = 0
                if (i + 1) % 10 == 0:
                    print(f"  ... loaded {new_count} reviews so far")

        try:
            final = page.locator(card_sel).count()
        except Exception:
            final = 0
        print(f"Finished scrolling: {final} review cards in DOM.")
        min_expected = BROWSER_CONFIG.get("MIN_EXPECTED_REVIEWS", 10)
        if 0 < final < min_expected:
            print(
                f"Warning: only {final} reviews loaded (expected more). "
                "Page may have loaded slowly or Maps rate-limited this run; "
                "try again."
            )
        return final
