"""Tests for CLI helpers and locale matching (offline)."""

import json
import sys

import main
from main import build_parser, format_human, report_to_dict, run_once
from scraper import MapsScraper, PermanentScrapeError, _matches_any, detect_lang
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


def test_stdout_hardening_tolerates_bare_stdout(monkeypatch):
    """No reconfigure attr / encoding None must not raise (old code crashed
    at import with AttributeError on encoding.lower())."""

    class BareStdout:
        encoding = None

    monkeypatch.setattr(sys, "stdout", BareStdout())
    main.ensure_utf8_stdout()


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


def _fixture_factory(html_name=None, error=None):
    """Build a MapsScraper-compatible factory for run_once injection.

    Serves frozen fixture HTML (or raises) without launching a browser.
    """
    html = (FIXTURES / html_name).read_text(encoding="utf-8") if html_name else ""

    class FakeScraper:
        def __init__(self, url, target_reviews=None):
            self.url = url
            self.target_reviews = target_reviews

        def fetch_html(self):
            if error is not None:
                raise error
            return html

    return FakeScraper


def _run(url="https://www.google.com/maps/place/X", factory=None, **kwargs):
    opts = {"target": 50, "trust_threshold": 10, "as_json": True}
    opts.update(kwargs)
    return run_once(url, scraper_factory=factory, **opts)


def test_run_once_json_stdout_is_pure_json(monkeypatch, capsys):
    """Diagnostics go to stderr; stdout parses as JSON (pipeable)."""
    code = _run(factory=_fixture_factory("restaurant.html"))
    assert code == 0
    out, err = capsys.readouterr()
    data = json.loads(out)
    assert data["sample"] == 4
    assert data["trust_threshold"] == 10
    assert "Validating" in err


def test_no_head_request_gate():
    """The HEAD-request pre-check is gone: URL verdicts come from the scraper.

    A bare HEAD without User-Agent gets 403/405 from Google for valid
    place URLs, so the check wrongly rejected them with EXIT_BAD_URL.
    """
    assert not hasattr(main, "is_google_maps_responsive")


def test_run_once_uses_injected_factory_not_maps_scraper(monkeypatch, capsys):
    """The factory seam is real: MapsScraper is never touched."""
    monkeypatch.setattr(
        MapsScraper,
        "fetch_html",
        lambda self: (_ for _ in ()).throw(AssertionError("must not launch a browser")),
    )
    code = _run(factory=_fixture_factory("restaurant.html"))
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["sample"] == 4


def test_run_once_permanent_error_exits_bad_url(monkeypatch, capsys):
    code = _run(factory=_fixture_factory(error=PermanentScrapeError("no Google")))
    assert code == 2
    assert "no Google" in capsys.readouterr().err


def test_run_once_generic_error_exits_scrape_failed(monkeypatch, capsys):
    code = _run(factory=_fixture_factory(error=ValueError("boom")))
    assert code == 1
    assert "boom" in capsys.readouterr().err


def test_run_once_empty_report_exits_scrape_failed(monkeypatch, capsys):
    code = _run(factory=_fixture_factory())
    assert code == 1
    assert "No review cards" in capsys.readouterr().err
