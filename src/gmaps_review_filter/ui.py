"""Terminal presentation shared by the CLI and the scraper.

One object owns the dash rule, the single status line, and the progress
bar, so the two layers cannot fight over stderr. The scraper no longer
prints: it asks the terminal for a progress bar and a separator, and the
status line closes itself when the bar takes over or the crawl ends.
"""

import shutil
import sys
from typing import TextIO

from gmaps_review_filter.progress import ReviewProgress

MIN_RULE = 10
MAX_RULE = 60
DEFAULT_COLUMNS = 80


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

    def _write(self, text: str) -> None:
        self.stream.write(text)
        self.stream.flush()

    def write(self, text: str = "", *, end: str = "\n") -> None:
        """Write a complete line, closing any open status line first."""
        self.close_status()
        self._write(text + end)

    def status(self, text: str) -> StatusLine:
        """Show a no-newline status line for the next step."""
        self.close_status()
        line = StatusLine(self)
        line.show(text)
        self._status = line
        return line

    def close_status(self) -> None:
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
        return ReviewProgress(
            total,
            stream=self.stream,
            enabled=enabled,
            label=label,
            status=self._status,
        )
