"""Google session handling: cookie detection, storage state, login flow."""

import json
import logging
import shutil
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from playwright._impl._api_structures import SetCookieParam
from playwright.sync_api import (
    BrowserContext,
    Cookie,
    ViewportSize,
    sync_playwright,
)

from gmaps_review_filter.config import BROWSER_CONFIG
from gmaps_review_filter.errors import PermanentScrapeError

logger = logging.getLogger(__name__)

# Cookies Google sets once a browser is signed in. Any one of them means
# the session can see the full review feed instead of the ~5-card wall.
GOOGLE_SESSION_COOKIES = ("SID", "SAPISID", "SSID")


def has_google_session(cookies: Sequence[Cookie]) -> bool:
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


def save_storage_state(context: BrowserContext, path: str | Path) -> str:
    """Persist the browser session as JSON, readable by the owner only."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(target))
    target.chmod(0o600)
    return str(target)


def load_storage_cookies(path: str | Path) -> list[SetCookieParam]:
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
    return cast("list[SetCookieParam]", cookies) if isinstance(cookies, list) else []


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
                viewport=cast(ViewportSize, BROWSER_CONFIG["VIEWPORT"]),
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
