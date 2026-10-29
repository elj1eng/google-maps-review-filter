"""One throwaway headless browser session: profile dir, driver, page."""

import logging
import shutil
import tempfile
from types import TracebackType
from typing import Literal, cast

from playwright.sync_api import Page, Playwright, ViewportSize, sync_playwright

from gmaps_review_filter.auth import load_storage_cookies
from gmaps_review_filter.config import BROWSER_CONFIG

logger = logging.getLogger(__name__)

WEBDRIVER_MASK_JS = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
)


class ScrapeSession:
    """One throwaway browser session: profile dir, driver, and page.

    Context manager so teardown (driver stop + profile removal) always
    runs: attempts never leak state through scraper attributes, and
    concurrent runs never share a profile.
    """

    def __init__(self, storage_state: str | None = None) -> None:
        self.profile_path = tempfile.mkdtemp(prefix="gmaps_")
        self._playwright: Playwright | None = None
        self.storage_state = storage_state

    def __enter__(self) -> Page:
        self._playwright = sync_playwright().start()
        context = self._playwright.chromium.launch_persistent_context(
            self.profile_path,
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=BROWSER_CONFIG["USER_AGENT"],
            viewport=cast(ViewportSize, BROWSER_CONFIG["VIEWPORT"]),
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

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        try:
            if self._playwright is not None:
                self._playwright.stop()
        finally:
            shutil.rmtree(self.profile_path, ignore_errors=True)
        return False
