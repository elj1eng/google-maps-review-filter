import os
import re
import shutil
import time

from playwright.sync_api import sync_playwright

from constants import CSS_SELECTORS, BROWSER_CONFIG


class MapsScraper:
    def __init__(self, url):
        self.url = url
        self.profile_path = os.path.join(os.getcwd(), "temp_profile")

    def fetch_html(self):
        if os.path.exists(self.profile_path):
            shutil.rmtree(self.profile_path)

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
            page.wait_for_selector(
                CSS_SELECTORS["BUSINESS_TITLE"], timeout=BROWSER_CONFIG["TIMEOUT"]
            )
            time.sleep(1)
            page.reload(wait_until="domcontentloaded")
            time.sleep(3)

            self._open_reviews_tab(page)
            loaded = self._scroll_reviews(page)

            # Safety net: if we ended suspiciously low (e.g. 5 cards), the
            # page likely loaded slowly or the pane handle went stale.
            # One extra scroll pass often recovers the full ~200+ list.
            min_expected = BROWSER_CONFIG.get("MIN_EXPECTED_REVIEWS", 10)
            if 0 < loaded < min_expected:
                print(f"Only {loaded} cards loaded, retrying scroll once...")
                time.sleep(3)
                loaded = self._scroll_reviews(page)

            html = page.content()
            context.close()
            shutil.rmtree(self.profile_path, ignore_errors=True)
            return html

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

        The old code always used the first `div[tabindex="-1"]`, which is
        sometimes a non-scrollable wrapper. Scrolling it is a no-op, so the
        loop saw "no new cards" 3 times and quit with only ~5 reviews.
        Here we pick the candidate with the largest scrollHeight.
        """
        candidates = [
            'div.m6QErb.DxyBCb.kA9KIf.dS8AEf',
            'div[role="main"] div.m6QErb',
            'div[role="main"] div[tabindex="-1"]',
            'div[tabindex="-1"]',
        ]
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
                    if scroll_h and scroll_h > 100:
                        return el
                except Exception:
                    continue
        try:
            return page.locator('div[role="main"] div[tabindex="-1"]').first
        except Exception:
            return None

    def _scroll_reviews(self, page):
        print("Scrolling review list dynamically...")
        card_sel = f".{CSS_SELECTORS['REVIEW_CARD']}"
        stall_limit = BROWSER_CONFIG.get("SCROLL_STALL_LIMIT", 5)
        sleep_s = BROWSER_CONFIG.get("SCROLL_SLEEP_S", 1.5)
        wait_ms = BROWSER_CONFIG.get("DYNAMIC_WAIT_MS", 4000)

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

            # Re-resolve the pane periodically / after failures: the DOM
            # handle can go stale as Maps re-renders the list.
            if i % 20 == 0 and i > 0:
                try:
                    pane = self._find_scrollable_pane(page) or pane
                except Exception:
                    pass

            # 1) JS scroll on the real container (most reliable).
            try:
                pane.evaluate("el => el.scrollTop = el.scrollHeight")
            except Exception:
                try:
                    pane = self._find_scrollable_pane(page) or pane
                    pane.evaluate("el => el.scrollTop = el.scrollHeight")
                except Exception:
                    try:
                        page.keyboard.press("End")
                    except Exception:
                        pass

            # 2) Also bring the last card into view to trigger lazy-load.
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