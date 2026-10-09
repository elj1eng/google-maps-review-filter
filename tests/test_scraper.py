"""Offline tests for scraper policy: URL gate, retry taxonomy, registry.

No browser is launched: the gate is pure URL parsing, retry is driven by
stubbing _fetch_once, and _locate is exercised with a fake page.
"""

import time

import pytest

from constants import BROWSER_CONFIG, CSS_SELECTORS, SELECTOR_META
from scraper import (
    MapsScraper,
    PermanentScrapeError,
    TransientScrapeError,
    validate_place_url,
)

PLACE = "https://www.google.com/maps/place/Peter+Luger+Steak+House/@40.7,-73.9,17z"


# --- URL gate ---


def test_gate_accepts_place_urls():
    assert validate_place_url(PLACE) == PLACE
    assert validate_place_url("https://www.google.co.uk/maps/place/X/@1,2,17z")
    assert validate_place_url("https://google.com.vn/maps/place/X/@1,2,17z")
    assert validate_place_url("https://maps.app.goo.gl/abc123")


def test_gate_rejects_search_list_without_browser():
    with pytest.raises(PermanentScrapeError):
        MapsScraper("https://www.google.com/maps/search/pizza+near+me")


def test_gate_rejects_non_maps_urls():
    with pytest.raises(PermanentScrapeError):
        validate_place_url("https://example.com/maps/place/X")
    with pytest.raises(PermanentScrapeError):
        validate_place_url("not a url")
    with pytest.raises(PermanentScrapeError):
        validate_place_url("")


# --- retry taxonomy ---


def _run_fetch(monkeypatch, effects):
    calls = []

    def fake_fetch(self):
        calls.append(1)
        effect = effects[len(calls) - 1]
        if isinstance(effect, Exception):
            raise effect
        return effect

    monkeypatch.setattr(MapsScraper, "_fetch_once", fake_fetch)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return calls


def test_retry_transient_then_succeeds(monkeypatch):
    calls = _run_fetch(
        monkeypatch,
        [TransientScrapeError("a"), TransientScrapeError("b"), "<html>"],
    )
    assert MapsScraper(PLACE).fetch_html() == "<html>"
    assert len(calls) == 3


def test_permanent_never_retried(monkeypatch):
    calls = _run_fetch(monkeypatch, [PermanentScrapeError("bad url")])
    with pytest.raises(PermanentScrapeError):
        MapsScraper(PLACE).fetch_html()
    assert len(calls) == 1


def test_transient_exhaustion_raises_runtime_error(monkeypatch):
    n = BROWSER_CONFIG["FETCH_ATTEMPTS"]
    calls = _run_fetch(monkeypatch, [TransientScrapeError("x")] * n)
    with pytest.raises(RuntimeError, match=f"after {n} attempts"):
        MapsScraper(PLACE).fetch_html()
    assert len(calls) == n


# --- selector registry ---


class _FakeLocator:
    def __init__(self, sel, matches):
        self.sel = sel
        self._matches = matches

    def count(self):
        return self._matches


class _FakePage:
    def __init__(self, present):
        self._present = set(present)

    def locator(self, sel):
        return _FakeLocator(sel, 1 if sel in self._present else 0)


def test_locate_prefers_primary():
    loc = MapsScraper._locate(_FakePage([CSS_SELECTORS["REVIEWS_TAB"]]), "REVIEWS_TAB")
    assert loc.sel == CSS_SELECTORS["REVIEWS_TAB"]
    assert loc.count() == 1


def test_locate_uses_fallback():
    fallback = SELECTOR_META["REVIEWS_TAB"]["fallbacks"][0]
    loc = MapsScraper._locate(_FakePage([fallback]), "REVIEWS_TAB")
    assert loc.sel == fallback
    assert loc.count() == 1


def test_locate_empty_returns_primary():
    loc = MapsScraper._locate(_FakePage([]), "REVIEWS_TAB")
    assert loc.sel == CSS_SELECTORS["REVIEWS_TAB"]
    assert loc.count() == 0


def test_refresh_fixtures_import_has_no_side_effects(monkeypatch):
    """Importing the refresh tool must not scrape (network on import)."""
    import importlib

    def no_network(self):
        raise AssertionError("must not scrape on import")

    monkeypatch.setattr(MapsScraper, "fetch_html", no_network)
    importlib.import_module("tests.refresh_fixtures")


def test_every_string_selector_has_registry_entry():
    string_keys = {k for k, v in CSS_SELECTORS.items() if isinstance(v, str)}
    assert string_keys <= set(SELECTOR_META)
    for key, meta in SELECTOR_META.items():
        assert meta["observed"], key
        assert isinstance(meta["fallbacks"], list), key


def test_spot_header_selector_is_registered():
    """The analyzer's header class must live in the registry, not inline.

    jANrlb was hardcoded in analyzer.get_spot_summary: the one selector
    that rotates silently was the one the registry couldn't see.
    """
    assert CSS_SELECTORS["SPOT_HEADER"] == "jANrlb"
    assert "SPOT_HEADER" in SELECTOR_META
