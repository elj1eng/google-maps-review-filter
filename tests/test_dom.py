"""Offline tests for the DOM probing helpers (fake pages, no browser)."""

import time

import pytest

from gmaps_review_filter.config import BROWSER_CONFIG
from gmaps_review_filter.dom import (
    locate,
    spot_review_total,
    wait_for_stable_count,
    wait_for_title,
)
from gmaps_review_filter.errors import PermanentScrapeError
from gmaps_review_filter.selectors import CSS_SELECTORS, SELECTOR_META

# --- settle-wait policy (scripted counters, no browser) ---


def test_settle_returns_when_count_stabilizes():
    counts = iter([0, 0, 3, 3, 3])
    started = time.monotonic()
    result = wait_for_stable_count(
        lambda: next(counts), polls=3, interval_s=0, timeout_s=5
    )
    assert result == 3
    assert time.monotonic() - started < 5  # returned early, not at the cap


def test_settle_flat_from_start_polls_exactly_n_times():
    calls = []

    def flat():
        calls.append(1)
        return 5

    assert wait_for_stable_count(flat, polls=3, interval_s=0, timeout_s=5) == 5
    assert len(calls) == 3


def test_settle_caps_on_ever_growing_counts():
    state = {"n": 0}

    def growing():
        state["n"] += 1
        return state["n"]

    started = time.monotonic()
    result = wait_for_stable_count(growing, polls=3, interval_s=0.01, timeout_s=0.2)
    assert result > 3  # never stabilized: kept polling, not an early return
    assert result == state["n"]  # last observed value, no raise
    assert time.monotonic() - started >= 0.15  # actually waited for the cap


def test_settle_defaults_come_from_config():
    assert BROWSER_CONFIG["STABLE_POLLS"] == 3
    assert BROWSER_CONFIG["STABLE_INTERVAL_S"] == 0.5
    assert BROWSER_CONFIG["STABLE_TIMEOUT_S"] == 8


def test_settle_treats_unreadable_counts_as_none():
    calls = []

    def flaky():
        calls.append(1)
        raise RuntimeError("DOM went away")

    assert wait_for_stable_count(flaky, polls=2, interval_s=0, timeout_s=1) is None
    assert len(calls) == 2


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
        wait_for_title(_NoTitlePage())


# --- header review total ---


class _HeaderTextPage:
    """Fake page/summary header exposing a review-count string."""

    def __init__(self, text):
        self.text = text

    def locator(self, sel):
        return self

    def count(self):
        return 1

    @property
    def first(self):
        return self

    def inner_text(self, timeout=None):
        return self.text


def test_spot_total_parses_the_header_count():
    assert spot_review_total(_HeaderTextPage("4.6 (7,904 reviews)")) == 7904
    assert spot_review_total(_HeaderTextPage("5.0 (12 đánh giá)")) == 12
    assert spot_review_total(_HeaderTextPage("no count here")) is None


def test_spot_total_uses_the_class_selector():
    """The registry value is a bare class; a tag selector would miss it."""
    seen = []

    class _Recording(_HeaderTextPage):
        def locator(self, sel):
            seen.append(sel)
            return self

    assert spot_review_total(_Recording("6 reviews")) == 6
    assert "div.jANrlb" in seen


# --- selector registry lookup ---


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
    loc = locate(_FakePage([CSS_SELECTORS["REVIEWS_TAB"]]), "REVIEWS_TAB")
    assert loc.sel == CSS_SELECTORS["REVIEWS_TAB"]
    assert loc.count() == 1


def test_locate_uses_fallback():
    fallback = SELECTOR_META["REVIEWS_TAB"]["fallbacks"][0]
    loc = locate(_FakePage([fallback]), "REVIEWS_TAB")
    assert loc.sel == fallback
    assert loc.count() == 1


def test_locate_empty_returns_primary():
    loc = locate(_FakePage([]), "REVIEWS_TAB")
    assert loc.sel == CSS_SELECTORS["REVIEWS_TAB"]
    assert loc.count() == 0
