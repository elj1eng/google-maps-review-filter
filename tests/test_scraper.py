"""Offline tests for scraper policy: URL gate, retry taxonomy, registry.

No browser is launched: the gate is pure URL parsing, retry is driven by
stubbing _fetch_once, and _locate is exercised with a fake page.
"""

import json
import time
from pathlib import Path

import pytest

from gmaps_review_filter.config import BROWSER_CONFIG
from gmaps_review_filter.scraper import (
    MapsScraper,
    PermanentScrapeError,
    TransientScrapeError,
    _pre_reload_delay_s,
    _retry_delay_s,
    _wait_for_stable_count,
    has_google_session,
    is_throttled_stub,
    load_storage_cookies,
    resolve_auth_state,
    save_storage_state,
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
    monkeypatch.setattr(MapsScraper, "_wait_for_title", lambda self, p: None)
    seq = list(limited_sequence)
    monkeypatch.setattr(MapsScraper, "_is_limited_view", lambda self, p: seq.pop(0))
    monkeypatch.setattr(MapsScraper, "_open_reviews_tab", lambda self, p: True)
    monkeypatch.setattr(
        MapsScraper, "_scroll_reviews", lambda self, p, progress=None: 5
    )
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


# --- saved Google session (login flow, offline) ---


def test_login_url_is_the_google_sign_in_page():
    assert BROWSER_CONFIG["LOGIN_URL"].startswith("https://accounts.google.com/")


def test_has_google_session_detects_auth_cookies():
    assert has_google_session([{"name": "SID", "value": "abc"}])
    assert has_google_session([{"name": "SAPISID", "value": "abc"}])
    assert not has_google_session([{"name": "NID", "value": "abc"}])
    assert not has_google_session([{"name": "SID", "value": ""}])
    assert not has_google_session([])


def test_resolve_auth_state_missing_file_is_none(tmp_path):
    assert resolve_auth_state(str(tmp_path / "nope.json")) is None


def test_resolve_auth_state_uses_explicit_path(tmp_path):
    state = tmp_path / "auth.json"
    state.write_text("{}", encoding="utf-8")
    assert resolve_auth_state(str(state)) == str(state)


def test_resolve_auth_state_falls_back_to_config(tmp_path, monkeypatch):
    state = tmp_path / "auth.json"
    state.write_text("{}", encoding="utf-8")
    monkeypatch.setitem(BROWSER_CONFIG, "AUTH_STATE_PATH", str(state))
    assert resolve_auth_state(None) == str(state)


def test_save_storage_state_writes_owner_only_file(tmp_path):
    target = tmp_path / "nested" / "auth.json"
    saved = save_storage_state(_RecordingContext(), target)
    assert saved == str(target)
    assert target.read_text(encoding="utf-8") == "{}"
    assert target.stat().st_mode & 0o777 == 0o600


class _RecordingContext:
    def storage_state(self, path):
        Path(path).write_text("{}", encoding="utf-8")


def test_load_storage_cookies_valid_file(tmp_path):
    state = tmp_path / "auth.json"
    cookies = [{"name": "SID", "value": "abc", "domain": ".google.com", "path": "/"}]
    state.write_text(json.dumps({"cookies": cookies, "origins": []}), encoding="utf-8")
    assert load_storage_cookies(state) == cookies


def test_load_storage_cookies_bad_files_are_empty(tmp_path):
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("not json", encoding="utf-8")
    assert load_storage_cookies(corrupt) == []
    assert load_storage_cookies(tmp_path / "missing.json") == []
    shape = tmp_path / "shape.json"
    shape.write_text(json.dumps({"cookies": "nope"}), encoding="utf-8")
    assert load_storage_cookies(shape) == []


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
    monkeypatch.setattr(MapsScraper, "_wait_for_title", lambda self, p: None)
    monkeypatch.setattr(MapsScraper, "_is_limited_view", lambda self, p: False)
    monkeypatch.setattr(MapsScraper, "_open_reviews_tab", lambda self, p: True)
    monkeypatch.setattr(
        MapsScraper, "_scroll_reviews", lambda self, p, progress=None: 5
    )
    monkeypatch.setattr(time, "sleep", lambda s: None)

    MapsScraper(PLACE, auth_state=str(state))._fetch_once()
    assert seen["state"] == str(state)


# --- scroll progress policy (fake page, no browser) ---


class _RecordingProgress:
    instances = []

    def __init__(self, total, *, enabled=None, label="Loading reviews"):
        self.total = total
        self.enabled = enabled
        self.label = label
        self.updates = []
        self.finished = 0
        _RecordingProgress.instances.append(self)

    def update(self, loaded):
        self.updates.append(loaded)

    def finish(self):
        # Mirrors ReviewProgress: finish is idempotent.
        if self.finished:
            return
        self.finished += 1


class _ScrollPage:
    """Fake page whose card count grows inside wait_for_function."""

    def __init__(self, growth):
        self.current = 0
        self._growth = list(growth)
        self.keyboard = self

    def locator(self, sel):
        return self

    def count(self):
        return self.current

    def nth(self, i):
        return self

    def scroll_into_view_if_needed(self, timeout=None):
        pass

    def wait_for_function(self, *args, **kwargs):
        if self._growth:
            self.current += self._growth.pop(0)

    def press(self, key):
        pass


def _scroll_scraper(monkeypatch, growth, *, target=210):
    page = _ScrollPage(growth)
    monkeypatch.setattr(MapsScraper, "_find_scrollable_pane", lambda self, p: object())
    monkeypatch.setattr(MapsScraper, "_wheel_scroll", lambda self, p, pane, px: True)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    _RecordingProgress.instances = []
    scraper = MapsScraper(
        PLACE, target_reviews=target, progress_factory=_RecordingProgress
    )
    return scraper, page


def test_scroll_reports_each_growth_then_finishes(monkeypatch):
    scraper, page = _scroll_scraper(monkeypatch, [100, 110])
    final = scraper._scroll_reviews(page)
    progress = _RecordingProgress.instances[-1]
    assert final == 210
    assert progress.updates == [100, 210]
    assert progress.finished == 1


def test_scroll_finishes_without_drawing_when_it_stalls(monkeypatch):
    scraper, page = _scroll_scraper(monkeypatch, [50, 0, 0, 0, 0, 0])
    final = scraper._scroll_reviews(page)
    progress = _RecordingProgress.instances[-1]
    assert final == 50
    assert progress.updates == [50]
    assert progress.finished == 1


def test_missing_pane_finishes_cleanly(monkeypatch):
    scraper, page = _scroll_scraper(monkeypatch, [])
    monkeypatch.setattr(MapsScraper, "_find_scrollable_pane", lambda self, p: None)
    assert scraper._scroll_reviews(page) == 0
    progress = _RecordingProgress.instances[-1]
    assert progress.updates == []
    assert progress.finished == 1


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
    assert _progress_label_for(monkeypatch, 2, 5) == "Attempt 2/5 — loading reviews"
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
