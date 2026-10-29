"""Single-line progress rendering for the review scroll.

Kept out of the scraper so the frame contract is testable without a
browser, and out of logging so a TTY bar never pollutes piped output.
"""

import shutil
import sys
from typing import Protocol, TextIO

BAR_WIDTH = 30
DEFAULT_COLUMNS = 80
FILLED = "\u2588"
EMPTY = "\u2591"
_LABEL = "Loading reviews"


class StatusHandle(Protocol):
    """The slice of StatusLine the bar needs (keeps ui/progress acyclic)."""

    def supersede(self) -> None: ...

    def close(self) -> None: ...


class ReviewProgress:
    """Redraw one stderr line as review cards accumulate.

    enabled=None auto-detects a TTY: piped/CI output stays free of
    carriage-return spam. Explicit False forces silence (--verbose),
    True forces rendering (tests).

    Frames are width-fitted to the terminal: a wrapped line breaks the
    \r redraw, so the label would scroll away on narrow windows.
    """

    def __init__(
        self,
        total: int,
        *,
        stream: TextIO | None = None,
        enabled: bool | None = None,
        width: int = BAR_WIDTH,
        label: str = _LABEL,
        columns: int | None = None,
        status: StatusHandle | None = None,
    ) -> None:
        self.total = max(total, 0)
        self.stream = sys.stderr if stream is None else stream
        self.width = width
        self.label = label
        self.status = status
        if columns is None:
            columns = shutil.get_terminal_size((DEFAULT_COLUMNS, 24)).columns
        self.columns = max(columns, 1)
        if enabled is None:
            enabled = self._is_tty()
        self.enabled = enabled
        self._last: int | None = None

    def _is_tty(self) -> bool:
        try:
            return bool(self.stream.isatty())
        except Exception:
            return False

    def _frame(self, loaded: int) -> str:
        ratio = min(loaded / self.total, 1.0) if self.total else 0.0
        suffix = f"{loaded}/{self.total} ({int(ratio * 100)}%)"
        # label + " [" + bar + "] " + suffix, leaving the last column
        # free so terminals do not wrap and break the \r redraw.
        max_width = self.columns - 1 - len(self.label) - len(" [] ") - len(suffix)
        width = max(0, min(self.width, max_width))
        filled = round(ratio * width)
        bar = FILLED * filled + EMPTY * (width - filled)
        return f"{self.label} [{bar}] {suffix}\033[K"

    def update(self, loaded: int) -> None:
        """Draw the frame; repeated counts are a no-op."""
        if not self.enabled or loaded == self._last:
            return
        if self._last is None and self.status is not None:
            # The leading \r overwrites the status line: no newline.
            self.status.supersede()
        self._last = loaded
        self.stream.write("\r" + self._frame(loaded))
        self.stream.flush()

    def finish(self) -> None:
        """Terminate the bar line so the next log message starts clean."""
        if self.status is not None:
            self.status.close()
        if not self.enabled or self._last is None:
            return
        self._last = None
        self.stream.write("\n")
        self.stream.flush()
