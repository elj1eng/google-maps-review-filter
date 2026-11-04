"""Frame-level tests for the scroll progress renderer (no browser)."""

from gmaps_review_filter.progress import ReviewProgress

FILLED = "\u2588"
EMPTY = "\u2591"


class _TtyStream:
    """StringIO stand-in that reports itself as a terminal."""

    def __init__(self):
        self.parts = []

    def write(self, text):
        self.parts.append(text)

    def flush(self):
        pass

    def isatty(self):
        return True

    @property
    def text(self):
        return "".join(self.parts)


class _PipeStream(_TtyStream):
    def isatty(self):
        return False


def _bar(*, total=210, width=20, label="Loading reviews", enabled=True, columns=200):
    stream = _TtyStream()
    return ReviewProgress(
        total,
        stream=stream,
        width=width,
        label=label,
        enabled=enabled,
        columns=columns,
    ), stream


def test_update_draws_count_percent_and_bar():
    progress, stream = _bar()
    progress.update(120)
    assert stream.text == (
        f"\rLoading reviews [{FILLED * 11}{EMPTY * 9}] 120/210 (57%)\033[K"
    )


def test_narrow_terminal_shrinks_the_bar_to_fit():
    progress, stream = _bar(columns=50)
    progress.update(120)
    frame = stream.text.removeprefix("\r").removesuffix("\033[K")
    assert len(frame) <= 49  # never wraps: wrapping breaks \r redraws
    assert frame.endswith("120/210 (57%)")


def test_update_is_deduplicated_per_count():
    progress, stream = _bar()
    progress.update(50)
    progress.update(50)
    assert stream.text.count("\r") == 1


def test_non_tty_writes_nothing():
    stream = _PipeStream()
    progress = ReviewProgress(210, stream=stream, width=20)
    progress.update(50)
    progress.finish()
    assert stream.text == ""


def test_explicit_enabled_forces_output_on_any_stream():
    stream = _PipeStream()
    progress = ReviewProgress(210, stream=stream, width=20, enabled=True)
    progress.update(0)
    assert stream.text.startswith("\r")


def test_finish_terminates_the_line_once():
    progress, stream = _bar()
    progress.update(10)
    progress.finish()
    progress.finish()
    assert stream.text.endswith("\n")
    assert stream.text.count("\n") == 1


def test_finish_without_updates_is_silent():
    progress, stream = _bar()
    progress.finish()
    assert stream.text == ""


def test_count_above_total_clamps_to_full_bar():
    progress, stream = _bar()
    progress.update(300)
    assert stream.text.endswith(f"[{FILLED * 20}] 300/210 (100%)\033[K")


def test_zero_total_never_divides_by_zero():
    progress, stream = _bar(total=0)
    progress.update(5)
    assert stream.text.endswith(f"[{EMPTY * 20}] 5/0 (0%)\033[K")


def test_finish_calls_on_finish_once():
    calls = []
    stream = _TtyStream()
    progress = ReviewProgress(
        10, stream=stream, enabled=True, on_finish=lambda: calls.append(1)
    )
    progress.finish()
    progress.finish()
    assert calls == [1]


def test_finish_calls_on_finish_even_without_updates():
    """An unused bar still has to release the terminal line."""
    calls = []
    progress = ReviewProgress(
        10,
        stream=_PipeStream(),
        enabled=False,
        on_finish=lambda: calls.append(1),
    )
    progress.finish()
    assert calls == [1]


FOOTER = "queue - Enter Google Maps URL (or 'q' to quit):"


def _footer_bar(*, enabled=True):
    stream = _TtyStream()
    progress = ReviewProgress(
        210,
        stream=stream,
        width=20,
        label="Loading reviews",
        enabled=enabled,
        columns=200,
        footer=FOOTER,
    )
    return progress, stream


def test_first_frame_prints_the_footer_below_the_bar():
    progress, stream = _footer_bar()
    progress.update(10)
    assert stream.text == "\r" + progress._frame(10) + "\n" + FOOTER
    assert progress.footer_live is True


def test_later_frames_redraw_the_bar_above_the_footer():
    progress, stream = _footer_bar()
    progress.update(10)
    progress.update(20)
    # One row up (the footer), redraw, restore the cursor after the prompt.
    assert stream.text.endswith("\033[s\033[1A\r" + progress._frame(20) + "\033[u")


def test_notify_inserts_rows_between_bar_and_footer():
    progress, stream = _footer_bar()
    progress.update(10)
    progress.notify("Queued: X")
    assert stream.text.endswith("\033[1A\r\033[KQueued: X\n" + FOOTER)
    # The new row is accounted for when the bar redraws.
    progress.update(20)
    assert stream.text.endswith("\033[s\033[2A\r" + progress._frame(20) + "\033[u")


def test_multi_line_notice_moves_the_footer_down_by_every_row():
    progress, stream = _footer_bar()
    progress.update(10)
    progress.notify("Queued: X\n0. A\n1. B")
    assert stream.text.endswith("\033[1A\r\033[KQueued: X\n0. A\n1. B\n" + FOOTER)
    progress.update(20)
    assert stream.text.endswith("\033[s\033[4A\r" + progress._frame(20) + "\033[u")


def test_empty_notice_reanchors_without_adding_a_row():
    progress, stream = _footer_bar()
    progress.update(10)
    progress.notify("")
    assert stream.text.endswith("\033[1A\r\033[K" + FOOTER)
    progress.update(20)
    assert stream.text.endswith("\033[s\033[1A\r" + progress._frame(20) + "\033[u")


def test_notify_without_a_live_bar_is_a_plain_line():
    progress, stream = _footer_bar()
    progress.notify("Queued: X")
    assert stream.text == "Queued: X\n"


def test_on_first_frame_fires_once_right_after_the_footer_frame():
    stream = _TtyStream()
    calls = []
    progress = ReviewProgress(
        210,
        stream=stream,
        width=20,
        enabled=True,
        columns=200,
        footer=FOOTER,
        on_first_frame=lambda: calls.append(stream.text),
    )
    progress.update(10)
    progress.update(20)
    assert len(calls) == 1
    assert calls[0].endswith(FOOTER)
    assert progress.footer_live is True


def test_finish_erases_the_footer_without_an_extra_newline():
    progress, stream = _footer_bar()
    progress.update(10)
    progress.finish()
    assert stream.text.endswith("\r\033[K")
    assert not stream.text.endswith("\n")
    assert progress.footer_live is False


def test_disabled_footer_bar_stays_silent():
    progress, stream = _footer_bar(enabled=False)
    progress.update(10)
    progress.notify("Queued: X")
    progress.finish()
    assert stream.text == ""


def test_unreadable_stream_disables_the_bar():
    class _Broken:
        def isatty(self):
            raise RuntimeError("closed")

        def write(self, text):
            pass

        def flush(self):
            pass

    assert ReviewProgress(10, stream=_Broken()).enabled is False
