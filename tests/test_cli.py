"""Tests for CLI helpers and locale matching (offline)."""

import io
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
from gmaps_review_filter.spot_queue import (
    EnqueueResult,
    QueueStatus,
    SpotQueue,
)
from gmaps_review_filter.ui import Terminal
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


# --- queue mode ---

PLACE_A = "https://www.google.com/maps/place/A"
PLACE_B = "https://www.google.com/maps/place/B"


def _queue_run(monkeypatch, urls, *, code=0, as_json=True, queue=None):
    """Drive run_queue with an injected queue and a fake run_once."""
    calls = []

    def fake_run_once(url, **opts):
        calls.append(url)
        return code(url) if callable(code) else code

    monkeypatch.setattr(main, "run_once", fake_run_once)
    result = main.run_queue(
        urls,
        target=50,
        trust_threshold=10,
        as_json=as_json,
        spot_queue=queue if queue is not None else SpotQueue(),
    )
    return result, calls


def test_queue_processes_seeded_urls_in_order(monkeypatch):
    result, calls = _queue_run(monkeypatch, [PLACE_A, PLACE_B])
    assert result == 0
    assert calls == [PLACE_A, PLACE_B]


def test_queue_picks_up_a_url_added_while_a_spot_loads(monkeypatch):
    """A URL typed mid-scrape is processed after the current spot."""
    queue = SpotQueue()
    queue.enqueue(PLACE_A)
    calls = []

    def fake_run_once(url, **opts):
        calls.append(url)
        if len(calls) == 1:
            queue.enqueue(PLACE_B)
        return 0

    monkeypatch.setattr(main, "run_once", fake_run_once)
    result = main.run_queue(
        [], target=50, trust_threshold=10, as_json=False, spot_queue=queue
    )
    assert result == 0
    assert calls == [PLACE_A, PLACE_B]


def test_queue_continues_after_a_failed_spot(monkeypatch):
    result, calls = _queue_run(
        monkeypatch,
        [PLACE_A, PLACE_B],
        code=lambda url: 1 if url == PLACE_A else 0,
    )
    assert calls == [PLACE_A, PLACE_B]
    assert result == 1


def test_queue_exit_code_keeps_the_worst_failure(monkeypatch):
    result, _ = _queue_run(
        monkeypatch,
        [PLACE_A, PLACE_B],
        code=lambda url: 2 if url == PLACE_A else 1,
    )
    assert result == 2


def test_queue_stop_word_discards_pending_spots(monkeypatch, capsys):
    queue = SpotQueue()
    queue.enqueue(PLACE_A)
    queue.enqueue(PLACE_B)
    calls = []

    def fake_run_once(url, **opts):
        calls.append(url)
        queue.request_stop()  # the user typed q while this spot loaded
        return 0

    monkeypatch.setattr(main, "run_once", fake_run_once)
    result = main.run_queue(
        [], target=50, trust_threshold=10, as_json=True, spot_queue=queue
    )
    assert result == 0
    assert calls == [PLACE_A]
    assert "1 discarded" in capsys.readouterr().err


def test_queue_ctrl_c_exits_130(monkeypatch, capsys):
    def interrupted(url, **opts):
        raise KeyboardInterrupt

    queue = SpotQueue()
    queue.enqueue(PLACE_A)
    queue.enqueue(PLACE_B)
    monkeypatch.setattr(main, "run_once", interrupted)
    result = main.run_queue(
        [], target=50, trust_threshold=10, as_json=True, spot_queue=queue
    )
    assert result == main.EXIT_INTERRUPTED == 130
    err = capsys.readouterr().err
    assert "Interrupted" in err
    assert "1 queued spot(s) discarded" in err


def test_queue_prints_numbered_spots_and_the_queue_prompt(monkeypatch, capsys):
    _queue_run(monkeypatch, [PLACE_A, PLACE_B], as_json=False)
    err = capsys.readouterr().err
    assert "0. A" in err
    assert "0. B" in err
    assert "1. B" in err  # B waits while A is scraped
    assert "Spot:" not in err
    assert err.count(main.QUEUE_PROMPT) == 2
    # Injected queues are complete, so no idle prompt is printed on its own.
    assert main.IDLE_PROMPT not in err.splitlines()
    assert "Queue mode: paste" not in err
    assert len(_rules(err)) == 1
    assert "Queue finished: 2 ok, 0 failed, 0 discarded." in err


def test_queue_json_is_one_object_per_line(capsys):
    result = main.run_queue(
        [PLACE_A, PLACE_B],
        target=50,
        trust_threshold=10,
        as_json=True,
        scraper_factory=_fixture_factory("restaurant.html"),
        stdin=io.StringIO(""),
    )
    assert result == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 2
    payloads = [json.loads(line) for line in lines]
    assert [payload["source"]["url"] for payload in payloads] == [PLACE_A, PLACE_B]
    assert all(payload["schema_version"] == 1 for payload in payloads)


def test_queue_prints_the_auth_hint_once(capsys, tmp_path):
    settings = replace(DEFAULT_SETTINGS, auth_state_path=str(tmp_path / "absent.json"))
    main.run_queue(
        [PLACE_A, PLACE_B],
        target=50,
        trust_threshold=10,
        as_json=True,
        scraper_factory=_fixture_factory("restaurant.html"),
        settings=settings,
        stdin=io.StringIO(""),
    )
    assert capsys.readouterr().err.count("no saved Google session") == 1


def test_queue_notice_renders_each_status_with_place_names():
    stream = io.StringIO()
    terminal = Terminal(stream)
    main._queue_notice(EnqueueResult(QueueStatus.QUEUED, PLACE_A), terminal)
    main._queue_notice(EnqueueResult(QueueStatus.DUPLICATE, PLACE_A), terminal)
    main._queue_notice(EnqueueResult(QueueStatus.REJECTED, "x", "bad"), terminal)
    main._queue_notice(EnqueueResult(QueueStatus.STOPPED, "y"), terminal)
    assert stream.getvalue() == "Queued: A\nAlready queued: A\nSkipped (bad): x\n"


def test_queue_notice_decodes_the_place_slug():
    url = "https://www.google.com/maps/place/Tr%C3%B2n+Kitchen/@10.7,106.6,17z"
    stream = io.StringIO()
    main._queue_notice(EnqueueResult(QueueStatus.QUEUED, url), Terminal(stream))
    assert stream.getvalue() == "Queued: Tròn Kitchen\n"


def test_queue_view_numbers_current_and_pending_spots():
    queue = SpotQueue()
    queue.enqueue(PLACE_B)
    stream = io.StringIO()
    view = main._QueueView(queue, Terminal(stream))
    view.start(PLACE_A)
    assert stream.getvalue() == "0. A\n1. B\n"

    view.notice(queue.enqueue("https://www.google.com/maps/place/C"))
    assert stream.getvalue().endswith("Queued: C\n0. A\n1. B\n2. C\n")


def test_queue_view_reports_queued_spots_while_idle():
    queue = SpotQueue()
    stream = io.StringIO()
    view = main._QueueView(queue, Terminal(stream))
    view.notice(queue.enqueue(PLACE_A))
    assert stream.getvalue() == "Queued: A\n"


def test_queue_view_stops_listing_after_finish():
    queue = SpotQueue()
    stream = io.StringIO()
    view = main._QueueView(queue, Terminal(stream))
    view.start(PLACE_A)
    view.finish()
    view.notice(queue.enqueue(PLACE_B))
    assert stream.getvalue().endswith("Queued: B\n")
    assert "1. B" not in stream.getvalue()


def test_show_idle_prompt_only_when_the_queue_is_idle_and_open():
    stream = io.StringIO()
    terminal = Terminal(stream)
    main._show_idle_prompt(SpotQueue(), terminal)
    assert stream.getvalue() == main.IDLE_PROMPT + "\n"

    stream = io.StringIO()
    terminal = Terminal(stream)
    pending = SpotQueue()
    pending.enqueue(PLACE_A)
    main._show_idle_prompt(pending, terminal)
    assert stream.getvalue() == ""

    stream = io.StringIO()
    terminal = Terminal(stream)
    closed = SpotQueue()
    closed.close()
    main._show_idle_prompt(closed, terminal)
    assert stream.getvalue() == ""


def test_run_once_ndjson_is_one_compact_line(capsys):
    code = _run(factory=_fixture_factory("restaurant.html"), ndjson=True)
    assert code == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out)["schema_version"] == 1


def test_stdin_is_tty_tolerates_unreadable_stdin(monkeypatch):
    class Broken:
        def isatty(self):
            raise RuntimeError("closed")

    monkeypatch.setattr(sys, "stdin", Broken())
    assert main._stdin_is_tty() is False


def test_stdin_is_tty_true(monkeypatch):
    class Tty:
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", Tty())
    assert main._stdin_is_tty() is True


def test_main_uses_queue_mode_on_a_tty(monkeypatch):
    seen = {}

    def fake_queue(urls, **opts):
        seen["urls"] = urls
        return 0

    monkeypatch.setattr(main, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(main, "run_queue", fake_queue)
    assert main.main([PLACE_A]) == 0
    assert seen["urls"] == [PLACE_A]


def test_no_queue_flag_keeps_the_one_shot_path(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(main, "run_queue", lambda *a, **k: pytest.fail("queue used"))
    monkeypatch.setattr(main, "run_once", lambda url, **opts: calls.append(url) or 0)
    assert main.main([PLACE_A, "--no-queue"]) == 0
    assert calls == [PLACE_A]


def test_piped_stdin_keeps_the_scripted_loop(monkeypatch, capsys):
    monkeypatch.setattr(main, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(main, "run_queue", lambda *a, **k: pytest.fail("queue used"))
    monkeypatch.setattr("builtins.input", lambda prompt="": "q")
    assert main.main([]) == 0
    assert "Goodbye!" in capsys.readouterr().out
