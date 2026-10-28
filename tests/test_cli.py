"""Tests for CLI helpers and locale matching (offline)."""

import json
import logging
import re
import sys

import gmaps_review_filter.cli as main
from gmaps_review_filter.cli import (
    build_parser,
    format_human,
    report_to_dict,
    run_once,
)
from gmaps_review_filter.config import BROWSER_CONFIG
from gmaps_review_filter.scraper import (
    MapsScraper,
    PermanentScrapeError,
    _matches_any,
    detect_lang,
)
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


def test_interactive_loop_logs_unexpected_errors(monkeypatch, caplog):
    """The loop's except-Exception must log a traceback, not print() it away."""
    responses = iter([RuntimeError("boom"), "q"])

    def fake_input(prompt=""):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr("builtins.input", fake_input)
    with caplog.at_level(logging.ERROR):
        assert main.main([]) == 0
    assert "boom" in caplog.text
    assert "Traceback" in caplog.text


def test_parser_flags():
    args = build_parser().parse_args(
        ["https://x", "--target", "50", "--trust-threshold", "5", "--json", "--verbose"]
    )
    assert args.url == "https://x"
    assert args.target == 50
    assert args.trust_threshold == 5
    assert args.json is True
    assert args.verbose is True
    assert args.login is False
    assert args.auth_state is None


def test_parser_login_and_auth():
    args = build_parser().parse_args(["--login", "--auth", "session.json"])
    assert args.login is True
    assert args.auth_state == "session.json"


def test_verbose_disables_progress_bar(capsys):
    seen = {}

    class FakeScraper:
        def __init__(self, url, target_reviews=None, progress_enabled=True):
            seen["progress_enabled"] = progress_enabled

        def fetch_html(self):
            return (FIXTURES / "restaurant.html").read_text(encoding="utf-8")

    assert _run(factory=FakeScraper, verbose=True) == 0
    assert seen["progress_enabled"] is False


def test_default_run_keeps_progress_bar(capsys):
    seen = {}

    class FakeScraper:
        def __init__(self, url, target_reviews=None):
            seen["kwargs"] = "no progress flag"

        def fetch_html(self):
            return (FIXTURES / "restaurant.html").read_text(encoding="utf-8")

    assert _run(factory=FakeScraper) == 0
    assert seen["kwargs"] == "no progress flag"


def test_login_saves_state_and_exits_ok(monkeypatch, capsys):
    monkeypatch.setattr(main, "login_and_save_state", lambda path: "/saved/auth.json")
    assert main.main(["--login"]) == 0
    assert "Saved Google session to /saved/auth.json" in capsys.readouterr().err


def test_login_timeout_exits_scrape_failed(monkeypatch, capsys):
    def boom(path):
        raise PermanentScrapeError("no Google sign-in detected")

    monkeypatch.setattr(main, "login_and_save_state", boom)
    assert main.main(["--login"]) == 1
    assert "no Google sign-in detected" in capsys.readouterr().err


def test_run_once_passes_auth_state_to_factory(monkeypatch, capsys, tmp_path):
    seen = {}
    state = tmp_path / "auth.json"
    state.write_text("{}", encoding="utf-8")

    class FakeScraper:
        def __init__(self, url, target_reviews=None, auth_state=None):
            seen["auth_state"] = auth_state

        def fetch_html(self):
            return (FIXTURES / "restaurant.html").read_text(encoding="utf-8")

    code = _run(factory=FakeScraper, auth_state=str(state))
    assert code == 0
    assert seen["auth_state"] == str(state)
    assert "no saved Google session" not in capsys.readouterr().err


def test_run_once_hints_when_no_session(monkeypatch, capsys, tmp_path):
    monkeypatch.setitem(
        BROWSER_CONFIG, "AUTH_STATE_PATH", str(tmp_path / "absent.json")
    )
    code = _run(factory=_fixture_factory("restaurant.html"))
    assert code == 0
    assert "Run --login" in capsys.readouterr().err


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


def test_run_once_reports_elapsed_time_on_stderr(capsys):
    """Wall-clock processing time goes to stderr, never stdout."""
    code = _run(factory=_fixture_factory("restaurant.html"))
    assert code == 0
    out, err = capsys.readouterr()
    assert re.search(r"Completed in \d+\.\d+s\.", err)
    assert "Completed in" not in out


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
