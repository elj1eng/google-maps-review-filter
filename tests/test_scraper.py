"""Offline tests for scraper policy: URL gate, retry taxonomy, registry.

No browser is launched: the gate is pure URL parsing, retry is driven by
stubbing _fetch_once, and _locate is exercised with a fake page.
"""

import time
from pathlib import Path

import pytest

from gmaps_review_filter.config import BROWSER_CONFIG
from gmaps_review_filter.scraper import (
    MapsScraper,
    PermanentScrapeError,
    TransientScrapeError,
    _wait_for_stable_count,
    is_throttled_stub,
    validate_place_url,
)
from gmaps_review_filter.selectors import CSS_SELECTORS, SELECTOR_META

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


# --- settle-wait policy (offline: scripted counters, no browser) ---


def test_settle_returns_when_count_stabilizes():
    counts = iter([0, 0, 3, 3, 3])
    started = time.monotonic()
    result = _wait_for_stable_count(
        lambda: next(counts), polls=3, interval_s=0, timeout_s=5
    )
    assert result == 3
    assert time.monotonic() - started < 5  # returned early, not at the cap


def test_settle_flat_from_start_polls_exactly_n_times():
    calls = []

    def flat():
        calls.append(1)
        return 5

    assert _wait_for_stable_count(flat, polls=3, interval_s=0, timeout_s=5) == 5
    assert len(calls) == 3


def test_settle_caps_on_ever_growing_counts():
    state = {"n": 0}

    def growing():
        state["n"] += 1
        return state["n"]

    started = time.monotonic()
    result = _wait_for_stable_count(growing, polls=3, interval_s=0.01, timeout_s=0.2)
    assert result > 3  # never stabilized: kept polling, not an early return
    assert result == state["n"]  # last observed value, no raise
    assert time.monotonic() - started >= 0.15  # actually waited for the cap


def test_settle_defaults_come_from_config():
    assert BROWSER_CONFIG["STABLE_POLLS"] == 3
    assert BROWSER_CONFIG["STABLE_INTERVAL_S"] == 0.5
    assert BROWSER_CONFIG["STABLE_TIMEOUT_S"] == 8


# --- stub guard: loaded sample vs header total (offline literals) ---


def test_stub_guard_flags_shortfall():
    assert is_throttled_stub(3, 210, 1006)
    assert is_throttled_stub(200, 210, 1006)


def test_stub_guard_accepts_full_sample_or_small_spot():
    assert not is_throttled_stub(210, 210, 1006)
    assert not is_throttled_stub(8, 210, 8)
    assert not is_throttled_stub(0, 210, 0)


def test_stub_guard_unknown_total_never_retries_blind():
    assert not is_throttled_stub(3, 210, None)


# --- title gate: unresolvable URLs fail fast as permanent ---


class _NoTitlePage:
    def wait_for_selector(self, *args, **kwargs):
        raise TimeoutError("timeout")


def test_wait_for_title_raises_permanent_not_runtime():
    """Linguistic/unresolvable URLs exit 2 (bad URL), not 1.

    Bare /maps/place/Name URLs resolve to nothing: no h1 ever renders.
    That is a deterministic URL problem, not a scrape flake.
    """
    with pytest.raises(PermanentScrapeError, match="never showed a place title"):
        MapsScraper._wait_for_title(_NoTitlePage())


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
        [TransientScrapeError("a"), TransientScrapeError("b"), ("<html>", 5)],
    )
    assert MapsScraper(PLACE).fetch_html() == "<html>"
    assert len(calls) == 3


def _fixture_html(name):
    return (Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")


def test_stub_sample_retried_then_succeeds(monkeypatch):
    """A 3-card sample from a 16244-total spot retries at the loop level,
    where the scraper no longer needs the analyzer import."""
    html = _fixture_html("restaurant.html")
    calls = _run_fetch(monkeypatch, [(html, 3), (html, 60)])
    assert MapsScraper(PLACE, target_reviews=50).fetch_html() == html
    assert len(calls) == 2


def test_permanent_never_retried(monkeypatch):
    calls = _run_fetch(monkeypatch, [PermanentScrapeError("bad url")])
    with pytest.raises(PermanentScrapeError):
        MapsScraper(PLACE).fetch_html()
    assert len(calls) == 1


def test_transient_exhaustion_message_names_actual_failure(monkeypatch):
    """The exhaustion message must quote the real last error, not always
    claim a limited view (e.g. 'no reviews list' exhaustion said
    'limited review list')."""
    _run_fetch(
        monkeypatch,
        [TransientScrapeError("no reviews list")] * BROWSER_CONFIG["FETCH_ATTEMPTS"],
    )
    with pytest.raises(RuntimeError) as excinfo:
        MapsScraper(PLACE).fetch_html()
    assert "no reviews list" in str(excinfo.value)
    assert "limited review list" not in str(excinfo.value)


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
    importlib.import_module("gmaps_review_filter.refresh_fixtures")


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
