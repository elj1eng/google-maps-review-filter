"""Tests for the place-URL gate: exact Google host allowlist + path shape."""

import pytest

import gmaps_review_filter.urls as urls_mod
from gmaps_review_filter.errors import PermanentScrapeError
from gmaps_review_filter.urls import (
    is_google_host,
    place_label,
    resolve_short_link,
    validate_place_url,
)

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


def test_place_label_decodes_the_place_slug():
    url = (
        "https://www.google.com/maps/place/"
        "Tr%C3%B2n+Kitchen/@10.7869621,106.6805991,17z/data=!3m1!4b1"
    )
    assert place_label(url) == "Tròn Kitchen"


def test_place_label_falls_back_to_host_and_path_for_short_links():
    assert place_label("https://maps.app.goo.gl/abc123") == "maps.app.goo.gl/abc123"


def test_place_label_returns_unparseable_input():
    assert place_label("not a url") == "not a url"
    assert place_label("") == ""


def test_place_label_survives_a_parse_error(monkeypatch):
    def boom(url):
        raise ValueError("bad brackets")

    monkeypatch.setattr(urls_mod.urllib.parse, "urlparse", boom)
    assert place_label("https://google.com/maps/place/X") == (
        "https://google.com/maps/place/X"
    )


SHORT_URL = "https://maps.app.goo.gl/abc123"
PLACE_URL = (
    "https://www.google.com/maps/place/Tr%C3%B2n+Kitchen/@10.7869621,106.6805991,17z"
)


def _fake_urlopen(monkeypatch, final_url, recorded=None):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def geturl(self):
            return final_url

    def opener(request, timeout):
        if recorded is not None:
            recorded.append((request.full_url, timeout))
        return Response()

    monkeypatch.setattr(urls_mod.urllib.request, "urlopen", opener)


def test_resolve_short_link_follows_the_redirect(monkeypatch):
    calls = []
    _fake_urlopen(monkeypatch, PLACE_URL, calls)
    assert resolve_short_link(SHORT_URL) == PLACE_URL
    assert calls[0][0] == SHORT_URL


def test_resolve_short_link_skips_regular_place_urls(monkeypatch):
    def refuse(request, timeout):
        raise AssertionError("network touched")

    monkeypatch.setattr(urls_mod.urllib.request, "urlopen", refuse)
    assert resolve_short_link(PLACE_URL) is None


def test_resolve_short_link_falls_back_when_the_redirect_fails(monkeypatch):
    def boom(request, timeout):
        raise OSError("offline")

    monkeypatch.setattr(urls_mod.urllib.request, "urlopen", boom)
    assert resolve_short_link(SHORT_URL) is None


def test_resolve_short_link_rejects_a_non_place_target(monkeypatch):
    _fake_urlopen(monkeypatch, "https://www.google.com/maps/search/coffee")
    assert resolve_short_link(SHORT_URL) is None


def test_resolve_short_link_rejects_a_non_google_target(monkeypatch):
    _fake_urlopen(monkeypatch, "https://example.com/maps/place/X")
    assert resolve_short_link(SHORT_URL) is None


def test_resolve_short_link_rejects_unparseable_input(monkeypatch):
    def refuse(request, timeout):
        raise AssertionError("network touched")

    monkeypatch.setattr(urls_mod.urllib.request, "urlopen", refuse)
    assert resolve_short_link("not a url") is None
    assert resolve_short_link("ftp://maps.app.goo.gl/abc123") is None


def test_resolve_short_link_survives_a_parse_error(monkeypatch):
    def boom(url):
        raise ValueError("bad brackets")

    monkeypatch.setattr(urls_mod.urllib.parse, "urlparse", boom)
    assert resolve_short_link(SHORT_URL) is None


def test_resolve_short_link_ignores_an_unparseable_redirect(monkeypatch):
    _fake_urlopen(monkeypatch, "http://[::1")
    assert resolve_short_link(SHORT_URL) is None


def test_malformed_url_is_permanent_and_names_the_parse_error(monkeypatch):
    def boom(url):
        raise ValueError("bad brackets")

    monkeypatch.setattr(urls_mod.urllib.parse, "urlparse", boom)
    with pytest.raises(PermanentScrapeError, match="bad brackets"):
        validate_place_url("https://google.com/maps/place/X")
