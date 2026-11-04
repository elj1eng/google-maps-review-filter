"""Terminal presentation shared by the CLI and the scraper.

One object owns the dash rule, the single status line, and the progress
bar, so the two layers cannot fight over stderr. The scraper no longer
prints: it asks the terminal for a progress bar and a separator, and the
status line closes itself when the bar takes over or the crawl ends.
"""

import shutil
import sys
import threading
from collections.abc import Callable
from typing import TextIO

from gmaps_review_filter.progress import ReviewProgress

MIN_RULE = 10
MAX_RULE = 60
DEFAULT_COLUMNS = 80
# Carriage return + erase-to-end-of-line: wipes a live bar frame.
ERASE_LINE = "\r\033[K"


class StatusLine:
    """One stderr line that a progress bar may take over.

    ``supersede()`` is called by the bar's first frame: its leading ``\\r``
    overwrites the line, so no newline is needed. ``close()`` ends the
    line when nothing overwrote it (error, missing pane, quiet crawl).
    """

    def __init__(self, terminal: "Terminal"):
        self._terminal = terminal
        self._open = False

    def show(self, text: str) -> None:
        self._terminal._write(text)
        self._open = True

    def supersede(self) -> None:
        self._open = False

    def close(self) -> None:
        if self._open:
            self._open = False
            self._terminal._write("\n")


class Terminal:
    """Stderr presenter: status line, dash rule, progress bar factory."""

    def __init__(self, stream: TextIO | None = None):
        self.stream = sys.stderr if stream is None else stream
        self._status: StatusLine | None = None
        # The queue reader reports accepted URLs from another thread while
        # the scrape redraws the bar; one reentrant lock serializes both.
        self._lock = threading.RLock()
        self._bar_active = False
        # Queue mode asks the next bar to print this prompt below itself.
        # Set by the CLI around a scrape; None keeps the old single line.
        self.footer: str | None = None
        self._bar: ReviewProgress | None = None
        # Queue rows that belong below the bar but arrived before it drew.
        # Renderers (not text) so the list reflects URLs queued meanwhile.
        self._deferred: list[Callable[[], str]] = []

    def _write(self, text: str) -> None:
        with self._lock:
            self.stream.write(text)
            self.stream.flush()

    def write(self, text: str = "", *, end: str = "\n") -> None:
        """Write a complete line, closing any open status line first.

        When the bar has a footer (queue mode), the line is inserted above
        that footer and the bar stays where it is. Otherwise a live bar
        frame ends without a newline, so it is erased before the text
        starts; the next frame redraws from column zero.
        """
        with self._lock:
            bar = self._bar
            if bar is not None and bar.enabled and bar.footer_live:
                bar.notify(text)
                return
            if self._bar_active:
                self._write(ERASE_LINE)
                self._bar_active = False
            self.close_status()
            self._write(text + end)

    def refresh_footer(self) -> None:
        """Reprint a live bar footer after a bare Enter moved the cursor."""
        with self._lock:
            bar = self._bar
            if bar is not None and bar.footer_live:
                bar.reanchor()

    @property
    def bar_live(self) -> bool:
        """True once the bar has drawn its footer, so rows can go beneath it."""
        with self._lock:
            bar = self._bar
            return bool(bar is not None and bar.enabled and bar.footer_live)

    def defer(self, render: Callable[[], str]) -> None:
        """Hold one queue block until it can be placed below the bar.

        Before the first frame the bar does not exist yet, so writing now
        would leave the list above it. A renderer keeps the eventual text
        current (URLs can still arrive before the bar appears).
        """
        with self._lock:
            bar = self._bar
            if bar is None or not bar.enabled or not bar.footer_live:
                self._deferred.append(render)
                return
        self.write(render())

    def flush_deferred(self) -> None:
        """Write held queue blocks now (bar-start callback or spot teardown)."""
        with self._lock:
            deferred, self._deferred = self._deferred, []
        for render in deferred:
            self.write(render())

    def status(self, text: str) -> StatusLine:
        """Show a no-newline status line for the next step."""
        with self._lock:
            self.close_status()
            line = StatusLine(self)
            line.show(text)
            self._status = line
            return line

    def close_status(self) -> None:
        with self._lock:
            if self._status is not None:
                self._status.close()

    def rule(self) -> str:
        """Dash rule between attempts/spots: readable, never wrapping."""
        columns = shutil.get_terminal_size((DEFAULT_COLUMNS, 24)).columns
        return "-" * max(MIN_RULE, min(columns - 1, MAX_RULE))

    def separator(self) -> None:
        self.write(self.rule())

    def progress(
        self,
        total: int,
        *,
        label: str,
        enabled: bool | None = None,
    ) -> ReviewProgress:
        """A bar bound to this terminal's stream and open status line."""
        with self._lock:
            bar = ReviewProgress(
                total,
                stream=self.stream,
                enabled=enabled,
                label=label,
                status=self._status,
                lock=self._lock,
                on_finish=self._bar_finished,
                footer=self.footer,
                on_first_frame=self.flush_deferred,
            )
            # Only a bar that will actually draw owns the terminal line;
            # piped stderr must stay free of erase sequences.
            self._bar_active = bar.enabled
            self._bar = bar
            return bar

    def _bar_finished(self) -> None:
        with self._lock:
            self._bar_active = False
            self._bar = None
