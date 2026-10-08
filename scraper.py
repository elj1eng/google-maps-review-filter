import re
import shutil
import tempfile
import time
import urllib.parse

from playwright.sync_api import sync_playwright

from constants import BROWSER_CONFIG, CSS_SELECTORS, SELECTOR_META

LIMITED_VIEW_TEXT = "limited view of Google Maps"
LIMITED_VIEW_MSG = "Google served a 'limited view' of Maps."
WEBDRIVER_MASK_JS = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
)

# Hosts whose links resolve (via redirect) to a place page.
_SHORT_HOSTS = ("maps.app.goo.gl", "goo.gl")


def validate_place_url(url: str) -> str:
    """Reject non-place URLs before launching a browser.

    Search-list and homepage URLs can never yield a reviews list;
    failing here saves minutes of doomed retries. Raises
    PermanentScrapeError (never retried).
    """
    cleaned = (url or "").strip()
    try:
        parsed = urllib.parse.urlparse(cleaned)
    except Exception as exc:
        raise PermanentScrapeError(f"not a valid URL ({exc})") from exc
    host = parsed.netloc.lower()
    if host in _SHORT_HOSTS or re.fullmatch(r"(www\.)?google\.[a-z.]+", host or ""):
        if "/maps/place/" in parsed.path or host in _SHORT_HOSTS:
            return cleaned
        raise PermanentScrapeError(
            "URL is not a place page (expected /maps/place/). "
            "Search-list URLs have no reviews list — open the place first."
        )
    raise PermanentScrapeError("URL is not a Google Maps link.")


class TransientScrapeError(Exception):
    """Temporary failure (limited view, render flakes); retry fresh."""


class PermanentScrapeError(Exception):
    """Deterministic failure (bad URL, no Google source); do not retry."""


class MapsScraper:
    def __init__(self, url):
        # Fail fast on non-place URLs: no browser is launched for them.
        self.url = validate_place_url(url)
        self.profile_path = None

    def fetch_html(self):
        """Scrape the reviews page.

        Transient failures (limited view, render flakes) are retried with a
        fresh session and backoff. Permanent failures (bad URL, no Google
        source) raise immediately without burning retries.
        """
        attempts = BROWSER_CONFIG.get("FETCH_ATTEMPTS", 3)
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                return self._fetch_once()
            except TransientScrapeError as exc:
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
        # Fresh throwaway profile per attempt: concurrent runs never share
        # state, and nothing depends on the working directory.
        self.profile_path = tempfile.mkdtemp(prefix="gmaps_")

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
                page.add_init_script(WEBDRIVER_MASK_JS)

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
                    raise TransientScrapeError(LIMITED_VIEW_MSG)

                opened = self._open_reviews_tab(page)
                if opened and self._is_limited_view(page):
                    raise TransientScrapeError(LIMITED_VIEW_MSG)

                loaded = self._scroll_reviews(page) if opened else 0

                if not opened or loaded == 0:
                    # Search-list page or a place without a rendered reviews
                    # list — never return empty HTML silently.
                    raise TransientScrapeError(
                        "the page had no reviews list (wrong page or load failure)"
                    )

                if self._is_limited_view(page):
                    raise TransientScrapeError(
                        "Google served a 'limited view' of Maps mid-scroll."
                    )

                return page.content()
        finally:
            shutil.rmtree(self.profile_path, ignore_errors=True)
            self.profile_path = None

    def _is_limited_view(self, page):
        """True when Google shows the anti-bot banner with only ~5 reviews."""
        try:
            return page.get_by_text(LIMITED_VIEW_TEXT, exact=False).count() > 0
        except Exception:
            return False

    def _open_reviews_tab(self, page):
        tabs = self._locate(page, "REVIEWS_TAB")
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

        # Hotel tabs: filter to Google-only reviews via the filter chip.
        # Restaurant pages have no filter chips: no-op there.
        self._select_google_source(page)

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

    @staticmethod
    def _locate(page, key):
        """Locator for a registry key: primary, then fallbacks in order.

        Returns the first candidate matching anything; if none match,
        the primary (so callers keep their absent-element behavior).
        """
        candidates = [CSS_SELECTORS[key]] + SELECTOR_META.get(key, {}).get(
            "fallbacks", []
        )
        for sel in candidates:
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    return loc
            except Exception:
                continue
        return page.locator(candidates[0])

    def _select_google_source(self, page):
        """Filter a hotel Reviews tab to Google-only via the filter chip.

        Hotel tabs have filter chips (button.HQzyZ: "All reviews",
        "Most relevant"). The "All reviews" chip opens a per-platform menu;
        choosing "Google" drops third-party excerpts from the feed.
        Restaurant pages have no filter chips: return immediately.
        Raises PermanentScrapeError when Google is missing (deterministic
        page fact: fail fast) or TransientScrapeError when the menu
        interaction itself fails (render flake: worth a fresh session).
        """
        chip = None
        chip_label = ""
        try:
            chips = self._locate(page, "REVIEW_FILTER_CHIP")
            for i in range(chips.count()):
                try:
                    label = chips.nth(i).inner_text(timeout=2000).strip()
                except Exception:
                    continue
                if "review" in label.lower() and "relevant" not in label.lower():
                    chip = chips.nth(i)
                    chip_label = label
                    break
        except Exception:
            return
        if chip is None:
            return  # no filter chip: restaurant page or changed markup
        if chip_label.lower() == "google":
            print("Review filter already 'Google'.")
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
                timeout=BROWSER_CONFIG["TIMEOUT"],
            )
        except Exception:
            pass
        options = self._locate(page, "REVIEW_SOURCE_OPTION")
        if options.count() == 0:
            raise TransientScrapeError("review filter menu opened with no options")

        names, google_idx = [], -1
        for i in range(options.count()):
            try:
                text = options.nth(i).inner_text(timeout=2000).strip()
            except Exception:
                continue
            names.append(text)
            if text.lower() == "google" and google_idx < 0:
                google_idx = i
        print(f"Review filter options: {names or ['(unreadable)']}")
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
                first.wait_for(state="detached", timeout=BROWSER_CONFIG["TIMEOUT"])
            except Exception:
                pass  # list updated in place; verify presence below
            page.wait_for_selector(card_sel, timeout=BROWSER_CONFIG["TIMEOUT"])
        except TransientScrapeError:
            raise
        except Exception as exc:
            raise TransientScrapeError(
                f"switching review filter to Google failed ({exc})"
            )
        time.sleep(2)
        print("Review filter switched to 'Google'.")

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
                    print(
                        f"Scrolling done: {new_count} reviews "
                        f"(stable for {stall_limit} checks)."
                    )
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
