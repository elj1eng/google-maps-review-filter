"""Place-URL validation, before any browser work."""

import time
import urllib.error
import urllib.parse
import urllib.request

from gmaps_review_filter.errors import PermanentScrapeError

# Registrable domains Google Maps serves. Locale-scoped: google.com and
# google.com.vn (and any subdomain, e.g. maps.google.com), nothing else.
# Suffix matching on "." + domain rejects lookalikes such as
# google.evil.com and google.com.evil.com.
GOOGLE_DOMAINS = ("google.com", "google.com.vn")

# Short links that redirect to a place page.
SHORT_HOSTS = ("maps.app.goo.gl", "goo.gl")

# One redirect chain for display labels; short enough to never stall queueing.
SHORT_LINK_TIMEOUT_S = 5.0
# Short-link resolution is best effort: a throttled or flaky hop must not
# leave a URL in the queue, so transient failures get a couple more tries.
SHORT_LINK_ATTEMPTS = 3
SHORT_LINK_BACKOFF_S = 0.4
# A plain browser UA; Google serves share links more reliably to these than
# to obviously non-browser clients.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class _TransientLookupError(Exception):
    """A short-link hop failed in a way worth retrying (timeout, 429, 5xx)."""


def is_google_host(host: str) -> bool:
    """True for google.com / google.com.vn and their subdomains."""
    host = host.lower().rstrip(".")
    return any(
        host == domain or host.endswith("." + domain) for domain in GOOGLE_DOMAINS
    )


def place_label(url: str) -> str:
    """Best human label for a place URL, without opening the page.

    Place URLs carry the name in the ``/maps/place/<name>/`` slug, so it can
    be decoded for queue display. Short links have no name until Google
    resolves them, so they fall back to host + path.
    """
    cleaned = (url or "").strip()
    try:
        parsed = urllib.parse.urlparse(cleaned)
    except ValueError:
        return cleaned
    marker = "/maps/place/"
    if marker in parsed.path:
        slug = parsed.path.split(marker, 1)[1].split("/", 1)[0]
        name = urllib.parse.unquote_plus(slug).strip()
        if name:
            return name
    fallback = f"{parsed.netloc}{parsed.path}".strip("/")
    return fallback or cleaned


def is_short_link(url: str) -> bool:
    """True for an ``http(s)`` link on the Maps short-host allowlist."""
    cleaned = (url or "").strip()
    try:
        parsed = urllib.parse.urlparse(cleaned)
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and host in SHORT_HOSTS


def resolve_short_link(
    url: str,
    *,
    timeout: float = SHORT_LINK_TIMEOUT_S,
    attempts: int = SHORT_LINK_ATTEMPTS,
) -> str | None:
    """Follow a Maps short link to the place URL it points at, if possible.

    Only the short-host allowlist is resolved, and the final URL must be a
    Google place page. Transient failures (timeout, throttling, 5xx) are
    retried a couple of times so a busy network does not strand a URL in
    the queue. Any remaining failure returns ``None`` so queueing falls
    back to the short link's host + path instead of blocking further.
    """
    if not is_short_link(url):
        return None
    cleaned = url.strip()
    attempt = 0
    while True:
        attempt += 1
        try:
            return _resolve_once(cleaned, timeout)
        except _TransientLookupError:
            if attempt >= max(attempts, 1):
                return None
            time.sleep(SHORT_LINK_BACKOFF_S * attempt)


def _resolve_once(url: str, timeout: float) -> str | None:
    """One redirect chain; raises only when retrying could help."""
    request = urllib.request.Request(  # noqa: S310 - http(s) + allowlist above
        url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}
    )
    try:
        # Only the short-link allowlist is opened above; only the redirect
        # target is read, never the response body.
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            final = str(response.geturl())
    except urllib.error.HTTPError as exc:
        if exc.code == 429 or 500 <= exc.code < 600:
            raise _TransientLookupError(str(exc)) from exc
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise _TransientLookupError(str(exc)) from exc
    except Exception:
        return None
    try:
        final_parsed = urllib.parse.urlparse(final)
        final_host = (final_parsed.hostname or "").lower()
    except ValueError:
        return None
    if is_google_host(final_host) and "/maps/place/" in final_parsed.path:
        return final
    return None


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
