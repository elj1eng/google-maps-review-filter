"""Offline tests for saved-session handling and the login flow."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

import gmaps_review_filter.auth as auth_mod
from gmaps_review_filter.auth import (
    has_google_session,
    load_storage_cookies,
    resolve_auth_state,
    save_storage_state,
)
from gmaps_review_filter.config import DEFAULT_SETTINGS
from gmaps_review_filter.errors import PermanentScrapeError
from tests.fakes import FakeContext, FakePage, FakePlaywright, FakePlaywrightManager


class _RecordingContext:
    def storage_state(self, path):
        Path(path).write_text("{}", encoding="utf-8")


def test_login_url_is_the_google_sign_in_page():
    assert DEFAULT_SETTINGS.login_url.startswith("https://accounts.google.com/")


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
    settings = replace(DEFAULT_SETTINGS, auth_state_path=str(state))
    assert resolve_auth_state(None, settings=settings) == str(state)


def test_save_storage_state_writes_owner_only_file(tmp_path):
    target = tmp_path / "nested" / "auth.json"
    saved = save_storage_state(_RecordingContext(), target)
    assert saved == str(target)
    assert target.read_text(encoding="utf-8") == "{}"
    assert target.stat().st_mode & 0o777 == 0o600


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


# --- login flow (fake browser) ---


def _fake_login(monkeypatch, tmp_path, cookies):
    page = FakePage()
    context = FakeContext(page, cookies=cookies)
    playwright = FakePlaywright(context)
    manager = FakePlaywrightManager(playwright)
    monkeypatch.setattr(auth_mod, "sync_playwright", lambda: manager)
    monkeypatch.setattr(auth_mod.time, "sleep", lambda seconds: None)
    profile = tmp_path / "login-profile"
    profile.mkdir()
    monkeypatch.setattr(auth_mod.tempfile, "mkdtemp", lambda prefix: str(profile))
    return page, context, playwright, manager, profile


def test_login_saves_the_session_and_removes_the_profile(monkeypatch, tmp_path):
    cookies = [[], [{"name": "SID", "value": "abc"}]]
    page, context, playwright, manager, profile = _fake_login(
        monkeypatch, tmp_path, lambda call: cookies[min(call - 1, 1)]
    )
    target = tmp_path / "auth.json"

    saved = auth_mod.login_and_save_state(str(target))

    assert saved == str(target)
    assert target.is_file()
    assert target.stat().st_mode & 0o777 == 0o600
    assert page.gotos == [(DEFAULT_SETTINGS.login_url, "domcontentloaded")]
    _, kwargs = playwright.chromium.calls[0]
    assert kwargs["headless"] is False
    assert kwargs["viewport"] == DEFAULT_SETTINGS.viewport
    assert context.closed is True
    assert manager.exited is True
    assert not profile.exists()


def test_login_uses_the_configured_default_path(monkeypatch, tmp_path):
    _, _, _, _, _ = _fake_login(
        monkeypatch, tmp_path, [{"name": "SAPISID", "value": "abc"}]
    )
    target = tmp_path / "configured" / "auth.json"
    settings = replace(DEFAULT_SETTINGS, auth_state_path=str(target))
    assert auth_mod.login_and_save_state(settings=settings) == str(target)
    assert target.is_file()


def test_login_timeout_is_permanent_and_cleans_up(monkeypatch, tmp_path):
    _, context, _, _, profile = _fake_login(monkeypatch, tmp_path, [])
    with pytest.raises(PermanentScrapeError, match="no Google sign-in detected"):
        auth_mod.login_and_save_state(str(tmp_path / "auth.json"), timeout_s=0)
    assert context.closed is True
    assert not profile.exists()
