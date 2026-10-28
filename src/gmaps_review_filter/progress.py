"""Single-line progress rendering for the review scroll.

Kept out of the scraper so the frame contract is testable without a
browser, and out of logging so a TTY bar never pollutes piped output.
"""

import sys

BAR_WIDTH = 30
FILLED = "\u2588"
EMPTY = "\u2591"
_LABEL = "Loading reviews"


class ReviewProgress:
    """Redraw one stderr line as review cards accumulate.

    enabled=None auto-detects a TTY: piped/CI output stays free of
    carriage-return spam. Explicit False forces silence (--verbose),
    True forces rendering (tests).
    """

    def __init__(
        self,
        total: int,
        *,
        stream=None,
        enabled: bool | None = None,
        width: int = BAR_WIDTH,
        label: str = _LABEL,
    ):
        self.total = max(total, 0)
        self.stream = sys.stderr if stream is None else stream
        self.width = width
        self.label = label
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
        filled = round(ratio * self.width)
        bar = FILLED * filled + EMPTY * (self.width - filled)
        return f"{self.label} [{bar}] {loaded}/{self.total} ({int(ratio * 100)}%)"

    def update(self, loaded: int) -> None:
        """Draw the frame; repeated counts are a no-op."""
        if not self.enabled or loaded == self._last:
            return
        self._last = loaded
        self.stream.write("\r" + self._frame(loaded))
        self.stream.flush()

    def finish(self) -> None:
        """Terminate the bar line so the next log message starts clean."""
        if not self.enabled or self._last is None:
            return
        self._last = None
        self.stream.write("\n")
        self.stream.flush()
