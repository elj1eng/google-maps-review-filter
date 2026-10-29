"""Offline tests for saved-session handling and the login flow."""

import json
from dataclasses import replace
from pathlib import Path

from gmaps_review_filter.auth import (
    has_google_session,
    load_storage_cookies,
    resolve_auth_state,
    save_storage_state,
)
from gmaps_review_filter.config import DEFAULT_SETTINGS


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
