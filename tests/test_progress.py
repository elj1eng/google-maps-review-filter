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
