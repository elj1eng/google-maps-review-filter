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


def test_write_erases_an_active_bar_frame():
    """A queued-URL confirmation must not land inside the bar frame."""
    stream = io.StringIO()
    terminal = Terminal(stream)
    bar = terminal.progress(10, label="Loading reviews", enabled=True)
    bar.update(4)
    terminal.write("Queued: x")
    text = stream.getvalue()
    assert "\r\033[K" in text
    assert text.endswith("Queued: x\n")


def test_finished_bar_is_not_erased_again():
    stream = io.StringIO()
    terminal = Terminal(stream)
    bar = terminal.progress(10, label="Loading reviews", enabled=True)
    bar.update(4)
    bar.finish()
    terminal.write("Queue finished")
    assert "\033[K" not in stream.getvalue().split("Queue finished")[1]


def test_disabled_bar_never_erases_the_line():
    stream = io.StringIO()
    terminal = Terminal(stream)
    bar = terminal.progress(10, label="Loading reviews", enabled=False)
    bar.update(4)
    terminal.write("Queued: x")
    assert "\033[" not in stream.getvalue()


FOOTER = "queue - Enter Google Maps URL (or 'q' to quit):"


def test_footer_prompt_is_drawn_below_the_bar_and_redrawn_in_place():
    stream = io.StringIO()
    terminal = Terminal(stream)
    terminal.footer = FOOTER
    bar = terminal.progress(10, label="Loading reviews", enabled=True)
    bar.update(4)
    assert stream.getvalue().endswith("\n" + FOOTER)
    before = stream.getvalue()
    bar.update(8)
    # The bar is redrawn above the prompt; the prompt is not reprinted.
    text = stream.getvalue()
    assert text.startswith(before)
    assert text.endswith("\033[u")
    assert text.count(FOOTER) == 1


def test_write_is_inserted_above_a_live_footer():
    stream = io.StringIO()
    terminal = Terminal(stream)
    terminal.footer = FOOTER
    bar = terminal.progress(10, label="Loading reviews", enabled=True)
    bar.update(4)
    terminal.write("Queued: x")
    text = stream.getvalue()
    # No bar erase/redraw: the block lands between the bar and the prompt,
    # which is reprinted underneath it.
    assert "\033[1A\r\033[KQueued: x\n" + FOOTER in text
    assert text.count("Loading reviews") == 1


def test_refresh_footer_reprints_the_prompt_after_a_blank_line():
    stream = io.StringIO()
    terminal = Terminal(stream)
    terminal.footer = FOOTER
    bar = terminal.progress(10, label="Loading reviews", enabled=True)
    bar.update(4)
    terminal.refresh_footer()
    assert stream.getvalue().endswith("\033[1A\r\033[K" + FOOTER)


def test_deferred_rows_are_drawn_below_the_first_bar_frame():
    stream = io.StringIO()
    terminal = Terminal(stream)
    terminal.footer = FOOTER
    terminal.defer(lambda: "0. A")
    assert stream.getvalue() == ""
    bar = terminal.progress(10, label="Loading reviews", enabled=True)
    bar.update(4)
    text = stream.getvalue()
    assert text.index("Loading reviews") < text.index("0. A")
    assert text.endswith("0. A\n" + FOOTER)
    # The inserted row is counted when the bar redraws.
    bar.update(8)
    assert stream.getvalue().endswith("\033[s\033[2A\r" + bar._frame(8) + "\033[u")


def test_deferred_renderer_runs_only_when_the_bar_starts():
    stream = io.StringIO()
    terminal = Terminal(stream)
    terminal.footer = FOOTER
    calls = []

    def render():
        calls.append(stream.getvalue())
        return "0. A"

    terminal.defer(render)
    bar = terminal.progress(10, label="Loading reviews", enabled=True)
    assert calls == []
    bar.update(4)
    assert len(calls) == 1
    assert "Loading reviews" in calls[0]


def test_deferred_rows_flush_when_no_bar_ever_drew():
    stream = io.StringIO()
    terminal = Terminal(stream)
    assert terminal.bar_live is False
    terminal.defer(lambda: "0. A")
    terminal.flush_deferred()
    assert stream.getvalue() == "0. A\n"
    terminal.flush_deferred()
    assert stream.getvalue() == "0. A\n"


def test_finished_footer_bar_falls_back_to_normal_writes():
    stream = io.StringIO()
    terminal = Terminal(stream)
    terminal.footer = FOOTER
    bar = terminal.progress(10, label="Loading reviews", enabled=True)
    bar.update(4)
    bar.finish()
    terminal.write("Completed in 1.0s.")
    assert stream.getvalue().endswith("\r\033[KCompleted in 1.0s.\n")
