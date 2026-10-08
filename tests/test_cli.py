"""Tests for CLI helpers and locale matching (offline)."""

import json

import main
from main import build_parser, format_human, report_to_dict, run_once
from scraper import MapsScraper, _matches_any, detect_lang
from tests.test_analyzer import FIXTURES

SUMMARY = {"name": "Test Spot", "rating": "4.5", "total": "1250"}
FULL_REPORT = {
    "total_scanned": 10,
    "filtered_count": 3,
    "trusted_count": 7,
    "trusted_avg": 4.5,
    "all_avg": 4.3,
    "no_rating_count": 0,
}


def test_report_to_dict_shape():
    d = report_to_dict(SUMMARY, FULL_REPORT, 10)
    assert d["name"] == "Test Spot"
    assert d["sample"] == 10
    assert d["trust_threshold"] == 10
    assert d["trusted_ratio"] == 0.7
    assert d["unparseable_count"] == 0


def test_report_to_dict_ratio_none_when_nothing_classified():
    empty = dict(FULL_REPORT, filtered_count=0, trusted_count=0)
    assert report_to_dict(SUMMARY, empty, 10)["trusted_ratio"] is None


def test_format_human_shows_na_ratio_and_unparseable_warning():
    report = dict(FULL_REPORT, filtered_count=0, trusted_count=0, no_rating_count=4)
    out = format_human(SUMMARY, report, 10)
    assert "N/A" in out
    assert "4 cards had no parseable rating" in out


def test_format_human_clean_report_has_no_warning():
    out = format_human(SUMMARY, FULL_REPORT, 10)
    assert "70.0%" in out
    assert "parseable" not in out


def test_parser_defaults():
    args = build_parser().parse_args([])
    assert args.url is None
    assert args.target == 210
    assert args.trust_threshold == 10
    assert args.json is False
    assert args.verbose is False


def test_parser_flags():
    args = build_parser().parse_args(
        ["https://x", "--target", "50", "--trust-threshold", "5", "--json", "--verbose"]
    )
    assert args.url == "https://x"
    assert args.target == 50
    assert args.trust_threshold == 5
    assert args.json is True
    assert args.verbose is True


def test_detect_lang():
    assert detect_lang("vi") == "vi"
    assert detect_lang("vi-VN") == "vi"
    assert detect_lang("en-US") == "en"
    assert detect_lang("") == "en"
    assert detect_lang(None) == "en"


def test_matches_any_english_and_vietnamese():
    assert _matches_any("Reviews", ("review",))
    assert _matches_any("Bài đánh giá", ("đánh giá", "review"))
    assert not _matches_any("Most relevant", ("review",))
    assert not _matches_any("", ("review",))


def test_run_once_json_stdout_is_pure_json(monkeypatch, capsys):
    """Diagnostics go to stderr; stdout parses as JSON (pipeable)."""
    html = (FIXTURES / "restaurant.html").read_text(encoding="utf-8")
    monkeypatch.setattr(main, "is_google_maps_responsive", lambda url: True)
    monkeypatch.setattr(MapsScraper, "fetch_html", lambda self: html)
    code = run_once(
        "https://www.google.com/maps/place/X",
        target=50,
        trust_threshold=10,
        as_json=True,
    )
    assert code == 0
    out, err = capsys.readouterr()
    data = json.loads(out)
    assert data["sample"] == 4
    assert data["trust_threshold"] == 10
    assert "Validating" in err
