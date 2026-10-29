"""Offline tests for scraper orchestration: URL gate, retry, selectors.

No browser is launched: the gate is pure URL parsing, retry is driven by
stubbing _fetch_once, and the progress factory is a recording fake.
"""

import logging
import os
import time
from pathlib import Path

import pytest

from gmaps_review_filter.config import BROWSER_CONFIG
from gmaps_review_filter.errors import PermanentScrapeError, TransientScrapeError
from gmaps_review_filter.scraper import (
    MapsScraper,
    _pre_reload_delay_s,
    _retry_delay_s,
    is_throttled_stub,
)
from gmaps_review_filter.selectors import CSS_SELECTORS, SELECTOR_META
from gmaps_review_filter.urls import validate_place_url

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


# --- retry taxonomy ---


def _run_fetch(monkeypatch, effects):
    calls = []

    def fake_fetch(self, *args, **kwargs):
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


def test_no_sleep_after_final_attempt(monkeypatch):
    n = BROWSER_CONFIG["FETCH_ATTEMPTS"]
    _run_fetch(monkeypatch, [TransientScrapeError("x")] * n)
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    with pytest.raises(RuntimeError):
        MapsScraper(PLACE).fetch_html()
    assert len(sleeps) == n - 1


def test_final_attempt_does_not_claim_it_will_retry(monkeypatch, caplog):
    n = BROWSER_CONFIG["FETCH_ATTEMPTS"]
    _run_fetch(monkeypatch, [TransientScrapeError("x")] * n)
    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError):
        MapsScraper(PLACE).fetch_html()
    attempts_logged = [
        r.getMessage() for r in caplog.records if r.getMessage().startswith("Attempt")
    ]
    assert "retrying with a fresh session" not in attempts_logged[-1]
    assert "giving up" in attempts_logged[-1]


def test_retry_delay_is_jittered_exponential(monkeypatch):
    monkeypatch.setattr("gmaps_review_filter.scraper.random.uniform", lambda a, b: 1.5)
    base = BROWSER_CONFIG["RETRY_BASE_S"]
    cap = BROWSER_CONFIG["RETRY_CAP_S"]
    assert _retry_delay_s(1) == base + 1.5
    assert _retry_delay_s(2) == base * 2 + 1.5
    assert _retry_delay_s(99) == cap + 1.5


def test_pre_reload_delay_adds_jitter(monkeypatch):
    monkeypatch.setattr("gmaps_review_filter.scraper.random.uniform", lambda a, b: 1.0)
    assert _pre_reload_delay_s() == BROWSER_CONFIG["PRE_RELOAD_DELAY_S"] + 1.0


# --- fetch_once: conditional reload + saved session ---


class _FetchOncePage:
    def __init__(self):
        self.reloads = 0

    def goto(self, *args, **kwargs):
        pass

    def reload(self, *args, **kwargs):
        self.reloads += 1

    def content(self):
        return "<html></html>"


class _FetchOnceSession:
    def __init__(self, page):
        self._page = page

    def __enter__(self):
        return self._page

    def __exit__(self, *args):
        return False


def _stub_fetch_once(monkeypatch, page, limited_sequence):
    import gmaps_review_filter.scraper as scraper_mod

    monkeypatch.setattr(
        scraper_mod, "ScrapeSession", lambda *a, **k: _FetchOnceSession(page)
    )
    monkeypatch.setattr(scraper_mod, "wait_for_title", lambda p: None)
    seq = list(limited_sequence)
    monkeypatch.setattr(scraper_mod, "is_limited_view", lambda p: seq.pop(0))
    monkeypatch.setattr(MapsScraper, "_open_reviews_tab", lambda self, p: True)
    monkeypatch.setattr(scraper_mod, "scroll_reviews", lambda p, target, progress: 5)
    monkeypatch.setattr(time, "sleep", lambda s: None)


def test_fetch_once_skips_reload_when_clean(monkeypatch):
    page = _FetchOncePage()
    _stub_fetch_once(monkeypatch, page, [False, False, False])
    MapsScraper(PLACE)._fetch_once()
    assert page.reloads == 0


def test_fetch_once_reloads_only_when_limited(monkeypatch):
    page = _FetchOncePage()
    _stub_fetch_once(monkeypatch, page, [True, False, False, False])
    MapsScraper(PLACE)._fetch_once()
    assert page.reloads == 1


def test_scraper_passes_saved_state_to_session(tmp_path, monkeypatch):
    state = tmp_path / "auth.json"
    state.write_text("{}", encoding="utf-8")
    seen = {}

    class _CapturingSession(_FetchOnceSession):
        def __init__(self, storage_state=None):
            seen["state"] = storage_state
            super().__init__(_FetchOncePage())

    import gmaps_review_filter.scraper as scraper_mod

    monkeypatch.setattr(scraper_mod, "ScrapeSession", _CapturingSession)
    monkeypatch.setattr(scraper_mod, "wait_for_title", lambda p: None)
    monkeypatch.setattr(scraper_mod, "is_limited_view", lambda p: False)
    monkeypatch.setattr(MapsScraper, "_open_reviews_tab", lambda self, p: True)
    monkeypatch.setattr(scraper_mod, "scroll_reviews", lambda p, target, progress: 5)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    MapsScraper(PLACE, auth_state=str(state))._fetch_once()
    assert seen["state"] == str(state)


# --- retry separator (Terminal) ---


def _fake_terminal(monkeypatch, columns):
    monkeypatch.setattr(
        "gmaps_review_filter.ui.shutil.get_terminal_size",
        lambda fallback=None: os.terminal_size((columns, 24)),
    )


def _separator_lines(err):
    return [line for line in err.splitlines() if line and set(line) == {"-"}]


def test_retry_prints_separator_between_attempts(monkeypatch, capsys):
    _fake_terminal(monkeypatch, 80)
    calls = _run_fetch(
        monkeypatch,
        [TransientScrapeError("a"), TransientScrapeError("b"), ("<html>", 5)],
    )
    assert MapsScraper(PLACE).fetch_html() == "<html>"
    assert len(calls) == 3
    separators = _separator_lines(capsys.readouterr().err)
    assert separators == ["-" * 60, "-" * 60]


def test_separator_fits_narrow_terminal(monkeypatch, capsys):
    _fake_terminal(monkeypatch, 40)
    _run_fetch(
        monkeypatch,
        [TransientScrapeError("a"), ("<html>", 5)],
    )
    MapsScraper(PLACE).fetch_html()
    separators = _separator_lines(capsys.readouterr().err)
    assert separators == ["-" * 39]


def test_no_separator_when_first_attempt_succeeds(monkeypatch, capsys):
    _run_fetch(monkeypatch, [("<html>", 5)])
    MapsScraper(PLACE).fetch_html()
    assert not _separator_lines(capsys.readouterr().err)


# --- progress factory wiring ---


class _RecordingProgress:
    instances = []

    def __init__(self, total, *, enabled=None, label="Loading reviews"):
        self.total = total
        # Match ReviewProgress: None resolves to "bar on" (TTY).
        self.enabled = True if enabled is None else enabled
        self.label = label
        self.updates = []
        self.finished = 0
        _RecordingProgress.instances.append(self)

    def update(self, loaded):
        self.updates.append(loaded)

    def finish(self):
        if self.finished:
            return
        self.finished += 1


def _progress_label_for(monkeypatch, attempt, attempts):
    page = _FetchOncePage()
    _stub_fetch_once(monkeypatch, page, [False, False, False])
    calls = []

    class _Factory(_RecordingProgress):
        def __init__(self, total, *, enabled=None, label="Loading reviews"):
            calls.append(label)
            super().__init__(total, enabled=enabled, label=label)

    _RecordingProgress.instances = []
    scraper = MapsScraper(PLACE, progress_factory=_Factory)
    scraper._fetch_once(attempt, attempts)
    return calls[-1]


def test_progress_label_names_the_attempt(monkeypatch):
    assert _progress_label_for(monkeypatch, 2, 5) == "Attempt 2/5"
    assert _progress_label_for(monkeypatch, 1, 1) == "Loading reviews"


def test_progress_disabled_flag_is_forwarded(monkeypatch):
    page = _FetchOncePage()
    _stub_fetch_once(monkeypatch, page, [False, False, False])
    _RecordingProgress.instances = []
    scraper = MapsScraper(
        PLACE, progress_factory=_RecordingProgress, progress_enabled=False
    )
    scraper._fetch_once()
    assert _RecordingProgress.instances[-1].enabled is False


# --- selector registry ---


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
