"""Tests for CLI helpers and locale matching (offline)."""

import json
import logging
import os
import re
import runpy
import subprocess
import sys
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

import gmaps_review_filter.cli as main
from gmaps_review_filter import __version__
from gmaps_review_filter.cli import build_parser, run_once
from gmaps_review_filter.config import DEFAULT_SETTINGS
from gmaps_review_filter.errors import (
    PermanentScrapeError,
)
from gmaps_review_filter.locale import detect_lang, matches_any
from gmaps_review_filter.scraper import MapsScraper
from tests.test_analyzer import FIXTURES


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
        def __init__(self, url, target_reviews=None, progress_enabled=True, **kwargs):
            seen["progress_enabled"] = progress_enabled

        def fetch_html(self):
            return (FIXTURES / "restaurant.html").read_text(encoding="utf-8")

    assert _run(factory=FakeScraper, verbose=True) == 0
    assert seen["progress_enabled"] is False


def test_default_run_keeps_progress_bar(capsys):
    seen = {}

    class FakeScraper:
        def __init__(self, url, target_reviews=None, **kwargs):
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
        def __init__(self, url, target_reviews=None, auth_state=None, **kwargs):
            seen["auth_state"] = auth_state

        def fetch_html(self):
            return (FIXTURES / "restaurant.html").read_text(encoding="utf-8")

    code = _run(factory=FakeScraper, auth_state=str(state))
    assert code == 0
    assert seen["auth_state"] == str(state)
    assert "no saved Google session" not in capsys.readouterr().err


def test_run_once_hints_when_no_session(monkeypatch, capsys, tmp_path):
    settings = replace(DEFAULT_SETTINGS, auth_state_path=str(tmp_path / "absent.json"))
    code = _run(factory=_fixture_factory("restaurant.html"), settings=settings)
    assert code == 0
    assert "Run --login" in capsys.readouterr().err


def test_detect_lang():
    assert detect_lang("vi") == "vi"
    assert detect_lang("vi-VN") == "vi"
    assert detect_lang("en-US") == "en"
    assert detect_lang("") == "en"
    assert detect_lang(None) == "en"


def test_matches_any_english_and_vietnamese():
    assert matches_any("Reviews", ("review",))
    assert matches_any("Bài đánh giá", ("đánh giá", "review"))
    assert not matches_any("Most relevant", ("review",))
    assert not matches_any("", ("review",))


def _fixture_factory(html_name=None, error=None):
    """Build a MapsScraper-compatible factory for run_once injection.

    Serves frozen fixture HTML (or raises) without launching a browser.
    """
    html = (FIXTURES / html_name).read_text(encoding="utf-8") if html_name else ""

    class FakeScraper:
        def __init__(self, url, target_reviews=None, **kwargs):
            self.url = url
            self.target_reviews = target_reviews
            self.kwargs = kwargs

        def fetch_html(self):
            if error is not None:
                raise error
            return html

    return FakeScraper


def _run(url="https://www.google.com/maps/place/X", factory=None, **kwargs):
    opts = {"target": 50, "trust_threshold": 10, "as_json": True}
    opts.update(kwargs)
    return run_once(url, scraper_factory=factory, **opts)


def test_tty_run_hides_setup_lines_behind_one_status_line(monkeypatch, capsys):
    """The bar overwrites the status line, so no setup chatter is left."""
    seen = {}

    class FakeScraper:
        def __init__(self, url, target_reviews=None, **kwargs):
            seen["kwargs"] = kwargs

        def fetch_html(self):
            return (FIXTURES / "restaurant.html").read_text(encoding="utf-8")

    monkeypatch.setattr(main, "_use_progress_bar", lambda verbose, terminal: True)
    assert _run(factory=FakeScraper) == 0
    err = capsys.readouterr().err
    assert "Opening Google Maps..." in err
    assert "Validating URL..." not in err
    assert "Initializing scraper for:" not in err
    assert "clear_startup_lines" not in seen["kwargs"]


def test_tty_error_closes_the_status_line(monkeypatch, capsys):
    monkeypatch.setattr(main, "_use_progress_bar", lambda verbose, terminal: True)
    code = _run(factory=_fixture_factory(error=PermanentScrapeError("no Google")))
    assert code == 2
    err = capsys.readouterr().err
    assert "Opening Google Maps...\n" in err
    assert "Error: no Google" in err


def test_non_tty_run_keeps_setup_lines(monkeypatch, capsys):
    seen = {}

    class FakeScraper:
        def __init__(self, url, target_reviews=None, **kwargs):
            seen["kwargs"] = kwargs

        def fetch_html(self):
            return (FIXTURES / "restaurant.html").read_text(encoding="utf-8")

    monkeypatch.setattr(main, "_use_progress_bar", lambda verbose, terminal: False)
    assert _run(factory=FakeScraper) == 0
    err = capsys.readouterr().err
    assert "ui" in seen["kwargs"]
    assert "progress_enabled" not in seen["kwargs"]
    assert "Validating URL..." in err
    assert "Initializing scraper for:" in err
    assert "Opening Google Maps..." not in err


def _run_interactive(monkeypatch, responses, argv=None, code=0):
    """Drive main() with fake input and a fake run_once; return URLs."""
    calls = []

    def fake_run_once(url, **opts):
        calls.append(url)
        return code

    monkeypatch.setattr(main, "run_once", fake_run_once)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    monkeypatch.setattr(
        "gmaps_review_filter.ui.shutil.get_terminal_size",
        lambda fallback=None: os.terminal_size((80, 24)),
    )
    assert main.main(argv or []) == 0
    return calls


def _rules(err):
    return [line for line in err.splitlines() if line and set(line) == {"-"}]


def test_interactive_run_prints_a_rule_after_each_spot(monkeypatch, capsys):
    calls = _run_interactive(monkeypatch, iter(["url1", "url2", "q"]))
    assert calls == ["url1", "url2"]
    assert _rules(capsys.readouterr().err) == ["-" * 60, "-" * 60]


def test_interactive_rule_follows_a_failed_spot(monkeypatch, capsys):
    calls = _run_interactive(monkeypatch, iter(["bad", "q"]), code=1)
    assert calls == ["bad"]
    assert _rules(capsys.readouterr().err) == ["-" * 60]


def test_interactive_json_run_has_no_spot_rule(monkeypatch, capsys):
    calls = _run_interactive(monkeypatch, iter(["url1", "q"]), argv=["--json"])
    assert calls == ["url1"]
    assert _rules(capsys.readouterr().err) == []


def test_single_url_run_has_no_spot_rule(monkeypatch, capsys):
    _run_interactive(monkeypatch, iter([]), argv=["https://x"])
    assert _rules(capsys.readouterr().err) == []


def test_run_once_json_stdout_is_pure_json(monkeypatch, capsys):
    """Diagnostics go to stderr; stdout parses as JSON (pipeable)."""
    code = _run(factory=_fixture_factory("restaurant.html"))
    assert code == 0
    out, err = capsys.readouterr()
    data = json.loads(out)
    assert data["sample"]["size"] == 4
    assert data["trust"]["threshold"] == 10
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
    assert data["sample"]["size"] == 4


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


# --- argument validation / version ---


def test_version_flag_prints_the_package_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main.main(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == f"gmaps-filter {__version__}"


@pytest.mark.parametrize("value", ["0", "-1"])
def test_target_must_be_positive(value):
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--target", value])
    assert excinfo.value.code == 2


def test_trust_threshold_accepts_zero():
    assert build_parser().parse_args(["--trust-threshold", "0"]).trust_threshold == 0


def test_trust_threshold_rejects_negative():
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--trust-threshold", "-1"])
    assert excinfo.value.code == 2


def test_version_matches_pyproject():
    pyproject = tomllib.loads(
        (Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert pyproject["project"]["version"] == __version__


def test_python_dash_m_entry_point_prints_version():
    result = subprocess.run(
        [sys.executable, "-m", "gmaps_review_filter", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"gmaps-filter {__version__}"


# --- terminal/color helpers ---


def test_ensure_utf8_reconfigures_a_non_utf8_stdout(monkeypatch):
    class _Stdout:
        encoding = "latin-1"

        def __init__(self):
            self.calls = []

        def reconfigure(self, **kwargs):
            self.calls.append(kwargs)

    stdout = _Stdout()
    monkeypatch.setattr(sys, "stdout", stdout)
    main.ensure_utf8_stdout()
    assert stdout.calls == [{"encoding": "utf-8"}]


def test_use_color_respects_no_color_and_tty(monkeypatch):
    class _Stdout:
        def __init__(self, tty):
            self._tty = tty

        def isatty(self):
            if isinstance(self._tty, Exception):
                raise self._tty
            return self._tty

    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(sys, "stdout", _Stdout(True))
    assert main._use_color() is False

    monkeypatch.delenv("NO_COLOR")
    assert main._use_color() is True
    monkeypatch.setattr(sys, "stdout", _Stdout(False))
    assert main._use_color() is False
    monkeypatch.setattr(sys, "stdout", _Stdout(RuntimeError("no tty")))
    assert main._use_color() is False


def test_progress_bar_falls_back_when_stderr_is_unreadable():
    class _Stream:
        def isatty(self):
            raise RuntimeError("closed")

        def write(self, text):
            pass

        def flush(self):
            pass

    from gmaps_review_filter.ui import Terminal

    assert main._use_progress_bar(False, Terminal(_Stream())) is False
    assert main._use_progress_bar(True, Terminal(_Stream())) is False


def test_interactive_eof_says_goodbye(monkeypatch, capsys):
    def eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert main.main([]) == 0
    assert "Goodbye!" in capsys.readouterr().out


def test_module_guard_runs_main(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["gmaps-filter", "--version"])
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("gmaps_review_filter", run_name="__main__")
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == f"gmaps-filter {__version__}"


def test_human_report_path_prints_the_text_report(monkeypatch, capsys):
    code = _run(factory=_fixture_factory("restaurant.html"), as_json=False)
    assert code == 0
    out = capsys.readouterr().out
    assert "ANALYSIS (Sample:" in out
    assert "Peter Luger Steak House" in out
    assert '"schema_version"' not in out
