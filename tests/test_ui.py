"""Tests for the Terminal/StatusLine presentation layer."""

import io
import os

from gmaps_review_filter.ui import Terminal


def _fake_terminal(monkeypatch, columns):
    monkeypatch.setattr(
        "gmaps_review_filter.ui.shutil.get_terminal_size",
        lambda fallback=None: os.terminal_size((columns, 24)),
    )


def test_rule_is_width_aware_and_never_wraps(monkeypatch):
    _fake_terminal(monkeypatch, 80)
    assert Terminal(io.StringIO()).rule() == "-" * 60
    _fake_terminal(monkeypatch, 40)
    assert Terminal(io.StringIO()).rule() == "-" * 39
    _fake_terminal(monkeypatch, 5)
    assert Terminal(io.StringIO()).rule() == "-" * 10


def test_separator_writes_the_rule(monkeypatch):
    _fake_terminal(monkeypatch, 80)
    stream = io.StringIO()
    Terminal(stream).separator()
    assert stream.getvalue() == "-" * 60 + "\n"


def test_status_line_has_no_newline_until_closed():
    stream = io.StringIO()
    terminal = Terminal(stream)
    status = terminal.status("Opening Google Maps...")
    assert stream.getvalue() == "Opening Google Maps..."
    status.close()
    assert stream.getvalue() == "Opening Google Maps...\n"


def test_writing_a_line_closes_the_status_line():
    stream = io.StringIO()
    terminal = Terminal(stream)
    terminal.status("Opening Google Maps...")
    terminal.write("Error: boom")
    assert stream.getvalue() == "Opening Google Maps...\nError: boom\n"


def test_progress_supersedes_the_status_line():
    stream = io.StringIO()
    terminal = Terminal(stream)
    terminal.status("Opening Google Maps...")
    progress = terminal.progress(10, label="Attempt 1/5", enabled=True)
    progress.update(4)
    progress.finish()
    # The first frame overwrites the status with \r; only one newline total.
    text = stream.getvalue()
    assert text.startswith("Opening Google Maps...\r")
    assert text.endswith("\n")
    assert text.count("\n") == 1


def test_progress_finish_closes_an_untouched_status_line():
    stream = io.StringIO()
    terminal = Terminal(stream)
    terminal.status("Opening Google Maps...")
    progress = terminal.progress(10, label="Attempt 1/5", enabled=True)
    progress.finish()
    assert stream.getvalue() == "Opening Google Maps...\n"


def test_close_status_is_idempotent():
    stream = io.StringIO()
    terminal = Terminal(stream)
    terminal.status("x")
    terminal.close_status()
    terminal.close_status()
    assert stream.getvalue() == "x\n"
