import json
import logging
import random
import re
import shutil
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright

from gmaps_review_filter.analyzer import ReviewAnalyzer
from gmaps_review_filter.config import BROWSER_CONFIG, LOCALE_STRINGS
from gmaps_review_filter.errors import PermanentScrapeError, TransientScrapeError
from gmaps_review_filter.progress import ReviewProgress
from gmaps_review_filter.selectors import (
    CSS_SELECTORS,
    SCROLLABLE_PANE_CANDIDATES,
    SELECTOR_META,
)

logger = logging.getLogger(__name__)

LIMITED_VIEW_TEXT = "limited view of Google Maps"
LIMITED_VIEW_MSG = "Google served a 'limited view' of Maps."
WEBDRIVER_MASK_JS = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
)

# Hosts whose links resolve (via redirect) to a place page.
_SHORT_HOSTS = ("maps.app.goo.gl", "goo.gl")

# Cookies Google sets once a browser is signed in. Any one of them means
# the session can see the full review feed instead of the ~5-card wall.
GOOGLE_SESSION_COOKIES = ("SID", "SAPISID", "SSID")


def _retry_delay_s(attempt: int) -> float:
    """Jittered exponential backoff for inter-attempt waits (1-based)."""
    base = BROWSER_CONFIG["RETRY_BASE_S"]
    cap = BROWSER_CONFIG["RETRY_CAP_S"]
    jitter = BROWSER_CONFIG["RETRY_JITTER_S"]
    return min(base * 2 ** (attempt - 1), cap) + random.uniform(0, jitter)  # noqa: S311


def _pre_reload_delay_s() -> float:
    """Settle delay before a reload so rapid double-loads don't flag bots."""
    base = BROWSER_CONFIG["PRE_RELOAD_DELAY_S"]
    jitter = BROWSER_CONFIG["PRE_RELOAD_JITTER_S"]
    return base + random.uniform(0, jitter)  # noqa: S311


def has_google_session(cookies) -> bool:
    """True when a cookie jar carries at least one signed-in Google cookie."""
    return any(
        c.get("name") in GOOGLE_SESSION_COOKIES and c.get("value") for c in cookies
    )


def resolve_auth_state(auth_state: str | None = None) -> str | None:
    """Return the saved session path, or None when it doesn't exist yet.

    Anonymous scraping still works: it just hits the ~5-review wall, so a
    missing file is a fallback, not an error.
    """
    path = Path(auth_state or BROWSER_CONFIG["AUTH_STATE_PATH"]).expanduser()
    return str(path) if path.is_file() else None


def save_storage_state(context, path) -> str:
    """Persist the browser session as JSON, readable by the owner only."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(target))
    target.chmod(0o600)
    return str(target)


def load_storage_cookies(path) -> list[dict]:
    """Read cookies from a saved storage_state file; [] when unusable.

    launch_persistent_context does not accept storage_state on this
    Playwright build, so sessions are restored with add_cookies instead.
    """
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read saved session %s: %s", path, exc)
        return []
    cookies = data.get("cookies") if isinstance(data, dict) else None
    return cookies if isinstance(cookies, list) else []


def login_and_save_state(
    path: str | None = None, *, timeout_s: float | None = None
) -> str:
    """Open a headed browser, wait for a Google sign-in, save the session.

    Polls the cookie jar instead of prompting the terminal, so the flow
    also works when stdin is not a TTY. Raises PermanentScrapeError on
    timeout: this is a setup step, retrying cannot fix a missing login.
    """
    target = str(Path(path or BROWSER_CONFIG["AUTH_STATE_PATH"]).expanduser())
    timeout = timeout_s if timeout_s is not None else BROWSER_CONFIG["LOGIN_TIMEOUT_S"]
    poll_s = BROWSER_CONFIG["LOGIN_POLL_S"]
    profile_path = tempfile.mkdtemp(prefix="gmaps_login_")
    saved = None
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                profile_path,
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                user_agent=BROWSER_CONFIG["USER_AGENT"],
                viewport=BROWSER_CONFIG["VIEWPORT"],
            )
            page = context.pages[0]
            page.goto(BROWSER_CONFIG["LOGIN_URL"], wait_until="domcontentloaded")
            logger.info(
                "Sign in to Google in the opened window (waiting up to %ss).",
                timeout,
            )
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if has_google_session(context.cookies()):
                    saved = save_storage_state(context, target)
                    break
                time.sleep(poll_s)
            context.close()
    finally:
        shutil.rmtree(profile_path, ignore_errors=True)
    if saved is None:
        raise PermanentScrapeError(
            f"no Google sign-in detected within {timeout}s — run --login again."
        )
    return saved


def _matches_any(text, keywords):
    """True when any keyword occurs in text (case-insensitive)."""
    lowered = (text or "").lower()
    return any(k in lowered for k in keywords)


def detect_lang(lang_tag):
    """Map an <html lang> value to a LOCALE_STRINGS key ("vi" or "en")."""
    return "vi" if (lang_tag or "").lower().startswith("vi") else "en"


def is_throttled_stub(loaded: int, target: int, total: int | None) -> bool:
    """True when the loaded sample falls short of what the spot offers.

    Strict: any shortfall vs min(target, total) is treated as a throttled
    stub worth a fresh session. total None (header unparseable) never
    retries blind — falls back to warn-and-return.
    """
    if total is None:
        return False
    return loaded < min(target, total)


def _wait_for_stable_count(get_count, *, polls=None, interval_s=None, timeout_s=None):
    """Poll get_count() until it repeats the same value `polls` times in a row.

    Event-driven alternative to fixed sleeps after a list renders: returns
    as soon as the count settles, or the last observed value at timeout.
    get_count is injected so the policy stays testable without a browser.
    """
    needed = polls if polls is not None else BROWSER_CONFIG["STABLE_POLLS"]
    interval = (
        interval_s if interval_s is not None else BROWSER_CONFIG["STABLE_INTERVAL_S"]
    )
    timeout = timeout_s if timeout_s is not None else BROWSER_CONFIG["STABLE_TIMEOUT_S"]
    deadline = time.monotonic() + timeout
    last, run = None, 0
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


class ScrapeSession:
    """One throwaway browser session: profile dir, driver, and page.

    Context manager so teardown (driver stop + profile removal) always
    runs: attempts never leak state through scraper attributes, and
    concurrent runs never share a profile.
    """

    def __init__(self, storage_state=None):
        self.profile_path = tempfile.mkdtemp(prefix="gmaps_")
        self._playwright = None
        self.storage_state = storage_state

    def __enter__(self):
        self._playwright = sync_playwright().start()
        context = self._playwright.chromium.launch_persistent_context(
            self.profile_path,
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=BROWSER_CONFIG["USER_AGENT"],
            viewport=BROWSER_CONFIG["VIEWPORT"],
        )
        if self.storage_state:
            cookies = load_storage_cookies(self.storage_state)
            if cookies:
                try:
                    context.add_cookies(cookies)
                except Exception as exc:
                    logger.warning(
                        "Saved session rejected (%s); running anonymous.", exc
                    )
        page = context.pages[0]
        page.add_init_script(WEBDRIVER_MASK_JS)
        return page

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._playwright is not None:
                self._playwright.stop()
        finally:
            shutil.rmtree(self.profile_path, ignore_errors=True)
        return False


def _card_counter(page, card_sel):
    """Zero-arg card count for settle-waits; unreadable DOM reads as None."""

    def count():
        try:
            return page.locator(card_sel).count()
        except Exception:
            return None

    return count


class MapsScraper:
    def __init__(
        self,
        url,
        target_reviews=None,
        auth_state=None,
        progress_factory=None,
        progress_enabled=True,
    ):
        # Fail fast on non-place URLs: no browser is launched for them.
        self.url = validate_place_url(url)
        self.target = target_reviews or BROWSER_CONFIG["TARGET_REVIEWS"]
        # A saved sign-in lifts the ~5-review wall; absent file = anonymous.
        self.auth_state = resolve_auth_state(auth_state)
        self.progress_factory = progress_factory or ReviewProgress
        self.progress_enabled = progress_enabled

    def _make_progress(self, attempt, attempts):
        """One bar per scrape attempt; short label so it fits on one line."""
        label = "Loading reviews" if attempts <= 1 else f"Attempt {attempt}/{attempts}"
        enabled = None if self.progress_enabled else False
        return self.progress_factory(self.target, enabled=enabled, label=label)

    def fetch_html(self):
        """Scrape the reviews page.

        Returns the reviews HTML. Transient failures (limited view, render
        flakes, throttled stubs) are retried with a fresh session and
        backoff. Permanent failures (bad URL, no Google source) raise
        immediately without burning retries.
        """
        attempts = BROWSER_CONFIG["FETCH_ATTEMPTS"]
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                html, loaded = self._fetch_once(attempt, attempts)
                # Stub guard: a 3-card sample from a 1006-review spot is a
                # throttled stub, not a result — retry with a fresh session
                # instead of reporting it silently.
                total_raw = ReviewAnalyzer(html).get_spot_summary()["total"]
                total = int(total_raw) if total_raw.isdigit() else None
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
                    print("----", file=sys.stderr)
                    # Jittered backoff to ride out limited-view bursts
                    # instead of hammering Google on a fixed cadence.
                    time.sleep(_retry_delay_s(attempt))
                else:
                    logger.warning(
                        "Attempt %d/%d: %s — giving up.", attempt, attempts, exc
                    )
        raise RuntimeError(
            f"Scrape failed after {attempts} attempts ({last_error}). Try again later."
        )

    def _fetch_once(self, attempt=1, attempts=1):
        with ScrapeSession(self.auth_state) as page:
            page.goto(self.url, wait_until="domcontentloaded")
            self._wait_for_title(page)

            # Settle: a transient banner can clear on its own once JS
            # finishes; checking immediately locks in a false positive.
            time.sleep(BROWSER_CONFIG["POST_GOTO_SETTLE_S"])

            # Conditional reload: skip the second navigation on the happy
            # path. Back-to-back goto+reload from a fresh profile reads as
            # automation and triggers the limited view itself.
            if self._is_limited_view(page):
                # Wait longer before the refresh instead of hammering it.
                time.sleep(_pre_reload_delay_s())
                page.reload(wait_until="domcontentloaded")
                self._wait_for_title(page)

                # Fail fast: the anti-bot banner is already on the DOM
                # when the limited view is active, so don't open tabs.
                if self._is_limited_view(page):
                    raise TransientScrapeError(LIMITED_VIEW_MSG)

            opened = self._open_reviews_tab(page)
            if opened and self._is_limited_view(page):
                raise TransientScrapeError(LIMITED_VIEW_MSG)

            loaded = (
                self._scroll_reviews(page, self._make_progress(attempt, attempts))
                if opened
                else 0
            )

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

            return page.content(), loaded

    @staticmethod
    def _wait_for_title(page):
        """Wait for the place title; fail loudly when it never renders.

        Raises PermanentScrapeError: a page with no title after load +
        reload is a deterministic URL problem (linguistic/unresolvable
        URL), not a scrape flake — exit 2, never retried.
        """
        try:
            page.wait_for_selector(
                CSS_SELECTORS["BUSINESS_TITLE"],
                timeout=BROWSER_CONFIG["TIMEOUT"],
            )
        except Exception as exc:
            raise PermanentScrapeError(
                "Google Maps never showed a place title — the URL is "
                "likely invalid or the page failed to load."
            ) from exc

    def _is_limited_view(self, page):
        """True when Google shows the anti-bot banner with only ~5 reviews."""
        try:
            return page.get_by_text(LIMITED_VIEW_TEXT, exact=False).count() > 0
        except Exception:
            return False

    @staticmethod
    def _page_lang(page):
        """Detect the page language for locale-aware tab/chip matching."""
        try:
            return detect_lang(page.evaluate("document.documentElement.lang"))
        except Exception:
            return "en"

    def _open_reviews_tab(self, page):
        lang = self._page_lang(page)
        tab_keywords = LOCALE_STRINGS[lang]["reviews_tab"]
        tabs = self._locate(page, "REVIEWS_TAB")
        for i in range(tabs.count()):
            try:
                label = tabs.nth(i).inner_text(timeout=2000).lower()
            except Exception:
                continue
            if _matches_any(label, tab_keywords):
                tabs.nth(i).click(force=True)
                break
        else:
            logger.warning("Reviews tab not found; using whatever is visible.")

        # Hotel tabs: filter to Google-only reviews via the filter chip.
        # Restaurant pages have no filter chips: no-op there.
        self._select_google_source(page, lang)

        # Wait for the review list to actually render before scrolling.
        # Without this, _scroll_reviews can start while only the first
        # batch (or zero cards) is present and exit early with ~5 reviews.
        # Settle-wait, not a fixed sleep: returns as soon as the batch
        # stops growing.
        card_sel = f".{CSS_SELECTORS['REVIEW_CARD']}"
        try:
            page.wait_for_selector(
                card_sel,
                timeout=BROWSER_CONFIG["TIMEOUT"],
            )
        except Exception:
            logger.warning("No review cards appeared after opening reviews tab.")
            return False

        settled = _wait_for_stable_count(_card_counter(page, card_sel))
        logger.debug("Review list settled at %s cards.", settled)
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

    def _select_google_source(self, page, lang="en"):
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
        strings = LOCALE_STRINGS.get(lang, LOCALE_STRINGS["en"])
        try:
            chips = self._locate(page, "REVIEW_FILTER_CHIP")
            for i in range(chips.count()):
                try:
                    label = chips.nth(i).inner_text(timeout=2000).strip()
                except Exception:
                    continue
                if _matches_any(label, strings["filter_chip"]) and not _matches_any(
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

        # Settle: the list may update in place after the switch; proceed
        # as soon as the count stops changing instead of a fixed sleep.
        settled = _wait_for_stable_count(_card_counter(page, card_sel))
        logger.info("Review filter switched to 'Google' (%s cards).", settled)

    def _find_scrollable_pane(self, page):
        """Return a handle to the actual scrollable reviews container.

        Picks the candidate div with the largest scrollable area.
        Falls back to the first `div[role="main"] div[tabindex="-1"]`.
        """
        candidates = SCROLLABLE_PANE_CANDIDATES
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

        Returns True only when trusted wheel input was delivered.
        Maps only lazy-loads new review batches in response to trusted
        input events; assigning `scrollTop` via JS scrolls the DOM but
        never triggers the next batch (stalls at the first 5 cards), so
        the JS assignment below runs as a last resort but reports False.
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
        except Exception:
            pass
        return False

    def _scroll_reviews(self, page, progress=None):
        logger.info("Scrolling review list dynamically...")
        card_sel = f".{CSS_SELECTORS['REVIEW_CARD']}"
        stall_limit = BROWSER_CONFIG["SCROLL_STALL_LIMIT"]
        sleep_s = BROWSER_CONFIG["SCROLL_SLEEP_S"]
        wait_ms = BROWSER_CONFIG["DYNAMIC_WAIT_MS"]
        wheel_px = BROWSER_CONFIG["SCROLL_WHEEL_PX"]
        target = self.target
        if progress is None:
            progress = self._make_progress(1, 1)

        try:
            pane = self._find_scrollable_pane(page)
            if pane is None:
                logger.warning("Scrollable reviews pane not found.")
                return 0

            # Draw the first frame before scrolling begins, so the bar
            # (and the attempt label) is visible as soon as the crawl
            # starts instead of after the first batch lands.
            try:
                progress.update(page.locator(card_sel).count())
            except Exception:
                progress.update(0)

            no_change_count = 0
            completion = None
            for i in range(BROWSER_CONFIG["SCROLL_LIMIT"]):
                try:
                    prev_count = page.locator(card_sel).count()
                except Exception:
                    prev_count = 0

                # Reached the desired sample size: done.
                if prev_count >= target:
                    completion = (
                        "Reached target: %d reviews (target %d).",
                        prev_count,
                        target,
                    )
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
                        f"document.querySelectorAll('{card_sel}').length "
                        f"> {prev_count}",
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
                            new_count,
                            stall_limit,
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
            if completion is not None:
                logger.info(*completion)
            min_expected = BROWSER_CONFIG["MIN_EXPECTED_REVIEWS"]
            if 0 < final < min_expected:
                logger.warning(
                    "Only %d reviews loaded (expected more). Page may have loaded "
                    "slowly or Maps rate-limited this run; try again.",
                    final,
                )
            return final
        finally:
            progress.finish()
