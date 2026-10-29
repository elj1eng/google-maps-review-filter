"""Tests for the frozen Settings object."""

from dataclasses import FrozenInstanceError, replace

import pytest

from gmaps_review_filter.config import DEFAULT_SETTINGS, Settings


def test_defaults_match_the_documented_cli_values():
    assert DEFAULT_SETTINGS.target_reviews == 210
    assert DEFAULT_SETTINGS.min_reviews_for_trust == 10
    assert DEFAULT_SETTINGS.fetch_attempts == 5


def test_settings_are_frozen():
    with pytest.raises(FrozenInstanceError):
        DEFAULT_SETTINGS.target_reviews = 1  # type: ignore[misc]


def test_replace_builds_an_independent_variant():
    tuned = replace(
        DEFAULT_SETTINGS, scroll_limit=2, auth_state_path="~/.config/x.json"
    )
    assert tuned.scroll_limit == 2
    assert tuned.auth_state_path == "~/.config/x.json"
    assert DEFAULT_SETTINGS.scroll_limit == 40


def test_viewport_property_returns_a_fresh_dict():
    first = DEFAULT_SETTINGS.viewport
    first["width"] = 1
    assert DEFAULT_SETTINGS.viewport == {"width": 1280, "height": 900}


def test_settings_are_slotted_and_hashable():
    assert Settings.__slots__
    assert hash(DEFAULT_SETTINGS) == hash(Settings())
