"""Place-URL validation, before any browser work."""

import urllib.parse

from gmaps_review_filter.errors import PermanentScrapeError

# Registrable domains Google Maps serves. Locale-scoped: google.com and
# google.com.vn (and any subdomain, e.g. maps.google.com), nothing else.
# Suffix matching on "." + domain rejects lookalikes such as
# google.evil.com and google.com.evil.com.
GOOGLE_DOMAINS = ("google.com", "google.com.vn")

# Short links that redirect to a place page.
SHORT_HOSTS = ("maps.app.goo.gl", "goo.gl")


def is_google_host(host: str) -> bool:
    """True for google.com / google.com.vn and their subdomains."""
    host = host.lower().rstrip(".")
    return any(
        host == domain or host.endswith("." + domain) for domain in GOOGLE_DOMAINS
    )


def validate_place_url(url: str) -> str:
    """Reject non-place URLs before launching a browser.

    Search-list and homepage URLs can never yield a reviews list;
    failing here saves minutes of doomed retries. Raises
    PermanentScrapeError (never retried).
    """
    cleaned = (url or "").strip()
    try:
        parsed = urllib.parse.urlparse(cleaned)
        host = (parsed.hostname or "").lower()
    except Exception as exc:
        raise PermanentScrapeError(f"not a valid URL ({exc})") from exc
    if host in SHORT_HOSTS:
        return cleaned
    if is_google_host(host):
        if "/maps/place/" in parsed.path:
            return cleaned
        raise PermanentScrapeError(
            "URL is not a place page (expected /maps/place/). "
            "Search-list URLs have no reviews list — open the place first."
        )
    raise PermanentScrapeError("URL is not a Google Maps link.")
