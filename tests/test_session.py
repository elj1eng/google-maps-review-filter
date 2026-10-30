"""Offline tests for ScrapeSession's lifecycle and cookie restore."""

from pathlib import Path

import pytest

import gmaps_review_filter.session as session_mod
from gmaps_review_filter.config import DEFAULT_SETTINGS
from gmaps_review_filter.session import WEBDRIVER_MASK_JS, ScrapeSession
from tests.fakes import FakeContext, FakePage, FakePlaywright, FakePlaywrightManager


@pytest.fixture
def fake_stack(monkeypatch, tmp_path):
    page = FakePage()
    context = FakeContext(page)
    playwright = FakePlaywright(context)
    manager = FakePlaywrightManager(playwright)
    monkeypatch.setattr(session_mod, "sync_playwright", lambda: manager)
    monkeypatch.setattr(
        session_mod.tempfile, "mkdtemp", lambda prefix: str(tmp_path / "profile")
    )
    (tmp_path / "profile").mkdir()
    return page, context, playwright, manager


def test_session_starts_a_headless_context_and_masks_webdriver(fake_stack):
    page, context, playwright, _ = fake_stack
    with ScrapeSession() as opened:
        assert opened is page
        profile_path, kwargs = playwright.chromium.calls[0]
        assert Path(profile_path).name == "profile"
        assert kwargs["headless"] is True
        assert kwargs["user_agent"] == DEFAULT_SETTINGS.user_agent
        assert kwargs["viewport"] == DEFAULT_SETTINGS.viewport
        assert page.init_scripts == [WEBDRIVER_MASK_JS]


def test_session_removes_the_profile_and_stops_the_driver(fake_stack, tmp_path):
    _, _, playwright, manager = fake_stack
    with ScrapeSession():
        pass
    assert playwright.stopped is True
    assert manager.started == 1
    assert not (tmp_path / "profile").exists()


def test_session_restores_saved_cookies(fake_stack, monkeypatch, tmp_path):
    _, context, _, _ = fake_stack
    cookies = [{"name": "SID", "value": "abc"}]
    monkeypatch.setattr(session_mod, "load_storage_cookies", lambda path: cookies)
    state = tmp_path / "auth.json"
    state.write_text("{}", encoding="utf-8")
    with ScrapeSession(str(state)):
        pass
    assert context.added_cookies == [cookies]


def test_session_skips_add_cookies_when_the_file_is_empty(fake_stack, tmp_path):
    _, context, _, _ = fake_stack
    state = tmp_path / "auth.json"
    state.write_text("{}", encoding="utf-8")
    with ScrapeSession(str(state)):
        pass
    assert context.added_cookies == []


def test_session_warns_and_stays_anonymous_when_cookies_are_rejected(
    fake_stack, monkeypatch, tmp_path, caplog
):
    _, context, _, _ = fake_stack
    context.add_cookies_error = RuntimeError("expired")
    monkeypatch.setattr(
        session_mod, "load_storage_cookies", lambda path: [{"name": "SID"}]
    )
    state = tmp_path / "auth.json"
    state.write_text("{}", encoding="utf-8")
    with ScrapeSession(str(state)):
        pass
    assert "Saved session rejected" in caplog.text


def test_session_does_not_stop_a_driver_that_never_started(fake_stack):
    _, _, playwright, _ = fake_stack
    session = ScrapeSession()
    session.__exit__(None, None, None)
    assert playwright.stopped is False
