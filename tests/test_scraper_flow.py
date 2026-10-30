"""Offline tests for the scraper's page flow: tabs, filter chip, retries."""

import time
from dataclasses import replace

import pytest

import gmaps_review_filter.scraper as scraper_mod
from gmaps_review_filter.config import DEFAULT_SETTINGS
from gmaps_review_filter.errors import PermanentScrapeError, TransientScrapeError
from gmaps_review_filter.scraper import LIMITED_VIEW_MSG, MapsScraper
from gmaps_review_filter.selectors import CSS_SELECTORS
from tests.fakes import FakeElement, FakeLocator, FakePage

PLACE = "https://www.google.com/maps/place/Peter+Luger+Steak+House/@40.7,-73.9,17z"
CARD_SEL = f".{CSS_SELECTORS['REVIEW_CARD']}"

# Settle immediately: these tests exercise flow, not polling policy.
SETTINGS = replace(
    DEFAULT_SETTINGS, stable_polls=1, stable_interval_s=0, stable_timeout_s=1
)


def _scraper(**kwargs):
    return MapsScraper(PLACE, settings=SETTINGS, **kwargs)


# --- reviews tab ---


def _tabs(*labels):
    return FakeLocator([FakeElement(label) for label in labels])


def test_open_reviews_tab_clicks_the_matching_tab(monkeypatch):
    tabs = _tabs("Overview", "Reviews")
    page = FakePage({CSS_SELECTORS["REVIEWS_TAB"]: tabs})
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_select_google_source", lambda p, lang="en": None)
    assert scraper._open_reviews_tab(page) is True
    assert [tab.clicks for tab in tabs.elements] == [0, 1]
    assert page.waited_selectors == [CARD_SEL]


def test_open_reviews_tab_skips_unreadable_labels(monkeypatch):
    class _Unreadable(FakeElement):
        def inner_text(self, timeout=None):
            raise RuntimeError("detached")

    tabs = FakeLocator([_Unreadable(), FakeElement("Reviews")])
    page = FakePage({CSS_SELECTORS["REVIEWS_TAB"]: tabs})
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_select_google_source", lambda p, lang="en": None)
    assert scraper._open_reviews_tab(page) is True
    assert tabs.elements[1].clicks == 1


def test_open_reviews_tab_warns_when_no_label_matches(monkeypatch, caplog):
    page = FakePage({CSS_SELECTORS["REVIEWS_TAB"]: _tabs("Overview", "About")})
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_select_google_source", lambda p, lang="en": None)
    assert scraper._open_reviews_tab(page) is True
    assert "Reviews tab not found" in caplog.text


def test_open_reviews_tab_returns_false_without_cards(monkeypatch):
    page = FakePage(
        {CSS_SELECTORS["REVIEWS_TAB"]: _tabs("Reviews")},
        wait_selector_error=TimeoutError("no cards"),
    )
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_select_google_source", lambda p, lang="en": None)
    assert scraper._open_reviews_tab(page) is False


# --- hotel filter chip ---


def _chip_page(*, chip_label="All reviews", options=(), cards=(), chip_error=None):
    return FakePage(
        {
            CSS_SELECTORS["REVIEW_FILTER_CHIP"]: FakeLocator(
                [FakeElement(chip_label, click_error=chip_error)]
            ),
            CSS_SELECTORS["REVIEW_SOURCE_OPTION"]: FakeLocator(list(options)),
            CARD_SEL: FakeLocator(list(cards)),
        }
    )


def test_filter_switches_to_google(monkeypatch):
    chip = FakeElement("All reviews")
    google = FakeElement("Google")
    page = FakePage(
        {
            CSS_SELECTORS["REVIEW_FILTER_CHIP"]: FakeLocator([chip]),
            CSS_SELECTORS["REVIEW_SOURCE_OPTION"]: FakeLocator(
                [FakeElement("All reviews"), google]
            ),
            CARD_SEL: FakeLocator([FakeElement()]),
        }
    )
    _scraper()._select_google_source(page)
    assert chip.clicks == 1
    assert google.clicks == 1
    assert page.locator(CARD_SEL).first.waits == 1


def test_filter_already_google_is_a_noop():
    chip = FakeElement("Google reviews")
    page = FakePage({CSS_SELECTORS["REVIEW_FILTER_CHIP"]: FakeLocator([chip])})
    _scraper()._select_google_source(page)
    assert chip.clicks == 0


def test_filter_ignores_the_sort_chip():
    page = FakePage(
        {
            CSS_SELECTORS["REVIEW_FILTER_CHIP"]: FakeLocator(
                [FakeElement("Most relevant")]
            )
        }
    )
    _scraper()._select_google_source(page)  # no exception, no menu lookup


def test_filter_skips_unreadable_chip_labels():
    class _Unreadable(FakeElement):
        def inner_text(self, timeout=None):
            raise RuntimeError("detached")

    chip = FakeElement("All reviews")
    page = FakePage(
        {CSS_SELECTORS["REVIEW_FILTER_CHIP"]: FakeLocator([_Unreadable(), chip])}
    )
    page.locators[CSS_SELECTORS["REVIEW_SOURCE_OPTION"]] = FakeLocator(
        [FakeElement("Google")]
    )
    _scraper()._select_google_source(page)
    assert chip.clicks == 1


def test_filter_returns_early_when_chips_cannot_be_read():
    page = FakePage(
        {
            CSS_SELECTORS["REVIEW_FILTER_CHIP"]: FakeLocator(
                [], count_error=RuntimeError("detached")
            )
        }
    )
    _scraper()._select_google_source(page)


def test_filter_click_failure_is_transient():
    page = _chip_page(chip_error=RuntimeError("overlay"))
    with pytest.raises(TransientScrapeError, match="could not open"):
        _scraper()._select_google_source(page)


def test_filter_menu_without_options_is_transient():
    page = _chip_page()
    with pytest.raises(TransientScrapeError, match="no options"):
        _scraper()._select_google_source(page)


def test_filter_without_google_option_is_permanent():
    page = _chip_page(options=[FakeElement("All reviews"), FakeElement("Tripadvisor")])
    with pytest.raises(PermanentScrapeError, match="no Google option"):
        _scraper()._select_google_source(page)


def test_filter_skips_unreadable_option_labels():
    class _Unreadable(FakeElement):
        def inner_text(self, timeout=None):
            raise RuntimeError("detached")

    google = FakeElement("Google")
    page = _chip_page(options=[_Unreadable(), google])
    _scraper()._select_google_source(page)
    assert google.clicks == 1


def test_filter_falls_back_to_the_option_container(monkeypatch):
    ancestor = FakeElement()
    google = FakeElement("Google", click_error=RuntimeError("overlay"))
    google.child = FakeLocator([ancestor])
    page = _chip_page(options=[google])
    _scraper()._select_google_source(page)
    assert ancestor.clicks == 1


def test_filter_waits_for_the_list_and_tolerates_in_place_updates(monkeypatch):
    first = FakeElement(wait_error=TimeoutError("list updated in place"))
    page = FakePage(
        {
            CSS_SELECTORS["REVIEW_FILTER_CHIP"]: FakeLocator(
                [FakeElement("All reviews")]
            ),
            CSS_SELECTORS["REVIEW_SOURCE_OPTION"]: FakeLocator([FakeElement("Google")]),
            CARD_SEL: FakeLocator([first]),
        }
    )
    _scraper()._select_google_source(page)
    assert first.waits == 1


def test_filter_switch_failure_is_transient():
    page = _chip_page(options=[FakeElement("Google")])
    page.wait_selector_error = RuntimeError("no cards after switch")
    with pytest.raises(TransientScrapeError, match="switching review filter"):
        _scraper()._select_google_source(page)


def test_filter_reraises_a_transient_wait_error():
    page = _chip_page(options=[FakeElement("Google")])
    page.wait_selector_error = TransientScrapeError("render flake")
    with pytest.raises(TransientScrapeError, match="render flake"):
        _scraper()._select_google_source(page)


# --- _fetch_once branches ---


class _Page:
    def __init__(self):
        self.reloads = 0

    def goto(self, *args, **kwargs):
        pass

    def reload(self, *args, **kwargs):
        self.reloads += 1

    def content(self):
        return "<html>ok</html>"


class _Session:
    def __init__(self, page):
        self._page = page

    def __enter__(self):
        return self._page

    def __exit__(self, *args):
        return False


def _stub_fetch_once(monkeypatch, limited_sequence, *, opened=True, loaded=5):
    page = _Page()
    monkeypatch.setattr(scraper_mod, "ScrapeSession", lambda *a, **k: _Session(page))
    monkeypatch.setattr(scraper_mod, "wait_for_title", lambda p, **kw: None)
    sequence = list(limited_sequence)
    monkeypatch.setattr(scraper_mod, "is_limited_view", lambda p: sequence.pop(0))
    monkeypatch.setattr(MapsScraper, "_open_reviews_tab", lambda self, p: opened)
    monkeypatch.setattr(scraper_mod, "scroll_reviews", lambda *a, **kw: loaded)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return page


def test_fetch_once_fails_fast_when_the_reload_stays_limited(monkeypatch):
    page = _stub_fetch_once(monkeypatch, [True, True])
    with pytest.raises(TransientScrapeError, match=LIMITED_VIEW_MSG):
        _scraper()._fetch_once()
    assert page.reloads == 1


def test_fetch_once_raises_when_limited_after_opening_tabs(monkeypatch):
    _stub_fetch_once(monkeypatch, [False, True])
    with pytest.raises(TransientScrapeError, match=LIMITED_VIEW_MSG):
        _scraper()._fetch_once()


def test_fetch_once_requires_a_reviews_list(monkeypatch):
    _stub_fetch_once(monkeypatch, [False], opened=False)
    with pytest.raises(TransientScrapeError, match="no reviews list"):
        _scraper()._fetch_once()


def test_fetch_once_rejects_an_empty_scroll(monkeypatch):
    _stub_fetch_once(monkeypatch, [False, False], opened=True, loaded=0)
    with pytest.raises(TransientScrapeError, match="no reviews list"):
        _scraper()._fetch_once()


def test_fetch_once_raises_when_limited_mid_scroll(monkeypatch):
    _stub_fetch_once(monkeypatch, [False, False, True])
    with pytest.raises(TransientScrapeError, match="mid-scroll"):
        _scraper()._fetch_once()


def test_fetch_once_returns_the_page_content_on_success(monkeypatch):
    _stub_fetch_once(monkeypatch, [False, False, False])
    assert _scraper()._fetch_once() == ("<html>ok</html>", 5)
