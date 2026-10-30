"""Offline tests for the DOM probing helpers (fake pages, no browser)."""

import time

import pytest

from gmaps_review_filter.config import DEFAULT_SETTINGS
from gmaps_review_filter.dom import (
    card_counter,
    find_scrollable_pane,
    is_limited_view,
    locate,
    page_lang,
    spot_review_total,
    wait_for_stable_count,
    wait_for_title,
    wheel_scroll,
)
from gmaps_review_filter.errors import PermanentScrapeError
from gmaps_review_filter.selectors import (
    CSS_SELECTORS,
    SCROLLABLE_PANE_CANDIDATES,
    SELECTOR_META,
)
from tests.fakes import FakeElement, FakeLocator, FakePage

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
    assert DEFAULT_SETTINGS.stable_polls == 3
    assert DEFAULT_SETTINGS.stable_interval_s == 0.5
    assert DEFAULT_SETTINGS.stable_timeout_s == 8


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


# --- DOM probing failures are swallowed, never raised ---


def test_locate_skips_a_selector_that_raises():
    fallback = SELECTOR_META["REVIEWS_TAB"]["fallbacks"][0]
    page = FakePage(
        {
            CSS_SELECTORS["REVIEWS_TAB"]: RuntimeError("detached"),
            fallback: FakeLocator([FakeElement("Reviews")]),
        }
    )
    assert locate(page, "REVIEWS_TAB").count() == 1


def test_card_counter_reads_the_dom_and_returns_none_on_error():
    assert card_counter(FakePage(), ".jftiEf")() == 0
    broken = FakePage({".jftiEf": FakeLocator([], count_error=RuntimeError("gone"))})
    assert card_counter(broken, ".jftiEf")() is None


def test_is_limited_view_detects_the_banner_and_swallows_errors():
    assert is_limited_view(FakePage(limited_count=1))
    assert not is_limited_view(FakePage(limited_count=0))

    class _Broken(FakePage):
        def get_by_text(self, text, exact=False):
            raise RuntimeError("gone")

    assert not is_limited_view(_Broken())


def test_page_lang_uses_html_lang_with_an_english_fallback():
    assert page_lang(FakePage(lang="vi-VN")) == "vi"
    assert page_lang(FakePage(lang="en-GB")) == "en"

    class _Broken(FakePage):
        def evaluate(self, script):
            raise RuntimeError("no document")

    assert page_lang(_Broken()) == "en"


# --- scrollable pane discovery ---


def _pane_page(heights, *, fallback=None, fallback_error=None):
    locators = {}
    for selector, height in zip(SCROLLABLE_PANE_CANDIDATES, heights, strict=False):
        element = FakeElement(evaluate_result=height)
        locators[selector] = FakeLocator([element])
    if fallback is not None:
        locators['div[role="main"] div[tabindex="-1"]'] = fallback
    if fallback_error is not None:
        locators['div[role="main"] div[tabindex="-1"]'] = fallback_error
    return FakePage(locators)


def test_find_scrollable_pane_picks_the_largest_scroll_area():
    tall = FakeElement(evaluate_result=900)
    short = FakeElement(evaluate_result=10)
    page = _pane_page([10, 900])
    # Replace the second candidate's element with the tall one.
    page.locators[SCROLLABLE_PANE_CANDIDATES[1]] = FakeLocator([short, tall])
    assert find_scrollable_pane(page) is tall


def test_find_scrollable_pane_falls_back_when_nothing_scrolls():
    fallback = FakeElement()
    page = _pane_page([0, 0], fallback=FakeLocator([fallback]))
    assert find_scrollable_pane(page) is fallback


def test_find_scrollable_pane_returns_none_when_the_fallback_is_missing():
    page = _pane_page([0, 0], fallback_error=RuntimeError("detached"))
    assert find_scrollable_pane(page) is None


def test_find_scrollable_pane_skips_unreadable_candidates():
    page = _pane_page([RuntimeError("gone"), 50])
    page.locators[SCROLLABLE_PANE_CANDIDATES[0]] = FakeLocator(
        [], count_error=RuntimeError("gone")
    )
    pane = find_scrollable_pane(page)
    assert pane is not None
    assert pane.evaluate_result == 50


def test_find_scrollable_pane_skips_elements_whose_metrics_raise():
    page = _pane_page([0, 0])
    page.locators[SCROLLABLE_PANE_CANDIDATES[0]] = FakeLocator(
        [FakeElement(evaluate_error=RuntimeError("detached"))]
    )
    page.locators[SCROLLABLE_PANE_CANDIDATES[1]] = FakeLocator(
        [FakeElement(evaluate_result=30)]
    )
    pane = find_scrollable_pane(page)
    assert pane is not None
    assert pane.evaluate_result == 30


# --- wheel input ---


def test_wheel_scroll_uses_trusted_mouse_input_inside_the_viewport():
    element = FakeElement(box={"x": 100, "y": 100, "width": 200, "height": 200})
    page = FakePage()
    assert wheel_scroll(page, element, 2000) is True
    assert page.mouse.wheels == [(0, 2000)]
    x, y = page.mouse.moves[0]
    assert 0 < x < 1280 and 0 < y < 900


def test_wheel_scroll_clamps_to_the_viewport_edges():
    element = FakeElement(box={"x": 5000, "y": 5000, "width": 200, "height": 200})
    page = FakePage()
    assert wheel_scroll(page, element, 500) is True
    assert page.mouse.moves[0] == (1279, 899)


def test_wheel_scroll_falls_back_to_js_and_reports_false():
    element = FakeElement(box=None)
    page = FakePage()
    assert wheel_scroll(page, element, 1000) is False
    assert element.evaluated == ["el => el.scrollTop = el.scrollHeight"]


def test_wheel_scroll_swallows_both_failures():
    element = FakeElement(
        box_error=RuntimeError("no box"), evaluate_error=RuntimeError("gone")
    )
    assert wheel_scroll(FakePage(), element, 1000) is False


# --- header total absence ---


def test_spot_total_returns_none_when_header_is_missing():
    assert spot_review_total(FakePage()) is None


def test_spot_total_returns_none_when_the_count_is_missing():
    header = FakeElement(child=FakeLocator([]))
    page = FakePage({f"div.{CSS_SELECTORS['SPOT_HEADER']}": FakeLocator([header])})
    assert spot_review_total(page) is None


def test_spot_total_returns_none_when_reading_raises():
    class _RaisingText(FakeElement):
        def inner_text(self, timeout=None):
            raise RuntimeError("gone")

    header = FakeElement(child=FakeLocator([_RaisingText("6 reviews")]))
    page = FakePage({f"div.{CSS_SELECTORS['SPOT_HEADER']}": FakeLocator([header])})
    assert spot_review_total(page) is None
