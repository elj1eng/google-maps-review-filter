"""Place-URL validation, before any browser work."""

import re
import urllib.parse

from gmaps_review_filter.errors import PermanentScrapeError

# Hosts whose links resolve (via redirect) to a place page.
SHORT_HOSTS = ("maps.app.goo.gl", "goo.gl")

_GOOGLE_HOST_RE = re.compile(r"(www\.)?google\.[a-z.]+")


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
    if host in SHORT_HOSTS or _GOOGLE_HOST_RE.fullmatch(host or ""):
        if "/maps/place/" in parsed.path or host in SHORT_HOSTS:
            return cleaned
        raise PermanentScrapeError(
            "URL is not a place page (expected /maps/place/). "
            "Search-list URLs have no reviews list — open the place first."
        )
    raise PermanentScrapeError("URL is not a Google Maps link.")
