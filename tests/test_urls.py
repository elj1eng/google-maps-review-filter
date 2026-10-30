"""Tests for the place-URL gate: exact Google host allowlist + path shape."""

import pytest

import gmaps_review_filter.urls as urls_mod
from gmaps_review_filter.errors import PermanentScrapeError
from gmaps_review_filter.urls import is_google_host, validate_place_url

PLACE_PATH = "/maps/place/Peter+Luger+Steak+House/@40.7,-73.9,17z"


@pytest.mark.parametrize(
    "url",
    [
        f"https://google.com{PLACE_PATH}",
        f"https://www.google.com{PLACE_PATH}",
        f"https://maps.google.com{PLACE_PATH}",
        f"https://google.com.vn{PLACE_PATH}",
        f"https://www.google.com.vn{PLACE_PATH}",
        f"https://maps.google.com.vn{PLACE_PATH}",
        f"https://www.google.com:443{PLACE_PATH}",
        f"https://www.google.com.{PLACE_PATH}",
        "https://maps.app.goo.gl/abc123",
        "https://goo.gl/maps/abc123",
    ],
)
def test_gate_accepts_google_hosts_and_short_links(url):
    assert validate_place_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        f"https://google.evil.com{PLACE_PATH}",
        f"https://google.com.evil.com{PLACE_PATH}",
        f"https://www.google.co.uk{PLACE_PATH}",
        f"https://notgoogle.com{PLACE_PATH}",
        "https://maps.app.goo.gl.evil.com/abc123",
        "https://example.com/maps/place/X",
        "https://google.com/maps/search/pizza+near+me",
        "https://google.com/",
        "not a url",
        "",
    ],
)
def test_gate_rejects_lookalikes_and_non_place_pages(url):
    with pytest.raises(PermanentScrapeError):
        validate_place_url(url)


def test_is_google_host_accepts_domains_and_subdomains():
    assert is_google_host("google.com")
    assert is_google_host("maps.google.com")
    assert is_google_host("google.com.vn")
    assert is_google_host("www.google.com.vn")


def test_is_google_host_rejects_lookalikes():
    assert not is_google_host("google.evil.com")
    assert not is_google_host("google.com.evil.com")
    assert not is_google_host("google.co.uk")
    assert not is_google_host("notgoogle.com")
    assert not is_google_host("")


def test_malformed_url_is_permanent_and_names_the_parse_error(monkeypatch):
    def boom(url):
        raise ValueError("bad brackets")

    monkeypatch.setattr(urls_mod.urllib.parse, "urlparse", boom)
    with pytest.raises(PermanentScrapeError, match="bad brackets"):
        validate_place_url("https://google.com/maps/place/X")
