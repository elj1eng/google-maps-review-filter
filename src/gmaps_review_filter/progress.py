"""Single-line progress rendering for the review scroll.

Kept out of the scraper so the frame contract is testable without a
browser, and out of logging so a TTY bar never pollutes piped output.
"""

import shutil
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
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
        lock: AbstractContextManager[object] | None = None,
        on_finish: Callable[[], None] | None = None,
        footer: str | None = None,
    ) -> None:
        self.total = max(total, 0)
        self.stream = sys.stderr if stream is None else stream
        self.width = width
        self.label = label
        self.status = status
        # A footer (queue prompt) stays on the line below the bar. The bar
        # redraws above it with cursor save/restore, so the prompt and any
        # text typed after it survive every frame.
        self.footer = footer or None
        self._footer_live = False
        self._notice_rows = 0
        # Shared with the Terminal when the queue reader writes from another
        # thread; a private lock keeps standalone bars safe either way.
        self._lock: AbstractContextManager[object] = (
            nullcontext() if lock is None else lock
        )
        self.on_finish = on_finish
        self._finished = False
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

    @property
    def footer_live(self) -> bool:
        """True once the bar owns the line above its footer."""
        return self._footer_live

    def update(self, loaded: int) -> None:
        """Draw the frame; repeated counts are a no-op."""
        with self._lock:
            if not self.enabled or loaded == self._last:
                return
            if self._last is None and self.status is not None:
                # The leading \r overwrites the status line: no newline.
                self.status.supersede()
            self._last = loaded
            frame = self._frame(loaded)
            if self.footer is None:
                self.stream.write("\r" + frame)
            elif not self._footer_live:
                # The prompt sits on the row below the bar; nothing typed
                # after it is overwritten by later frames.
                self._footer_live = True
                self.stream.write("\r" + frame + "\n" + self.footer)
            else:
                # Cursor save/restore: go up over the footer and its notice
                # rows, redraw only the bar, then put the cursor back after
                # the prompt so typing continues where it was.
                up = self._notice_rows + 1
                self.stream.write(f"\033[s\033[{up}A\r{frame}\033[u")
            self.stream.flush()

    def notify(self, text: str) -> None:
        """Insert complete lines between the bar and the live footer.

        Called when a URL is accepted mid-scrape. The bar itself is left
        alone (only the rows above the footer are rewritten), so the bar
        keeps updating in place instead of being recreated per notice.
        """
        with self._lock:
            if not self.enabled:
                return
            if not self._footer_live:
                self.stream.write(text + "\n")
                self.stream.flush()
                return
            body = text.rstrip("\n")
            if not body:
                # A bare Enter: step back onto the prompt line and reprint
                # it, without adding a notice row.
                self.stream.write("\033[1A\r\033[K" + (self.footer or ""))
                self.stream.flush()
                return
            # Enter's echo left the cursor on the line below the prompt.
            rows = body.count("\n") + 1
            self.stream.write("\033[1A\r\033[K" + body + "\n" + (self.footer or ""))
            self._notice_rows += rows
            self.stream.flush()

    def reanchor(self) -> None:
        """Reprint the footer after an empty input line moved the cursor."""
        self.notify("")

    def finish(self) -> None:
        """Terminate the bar line so the next log message starts clean."""
        with self._lock:
            if self.status is not None:
                self.status.close()
            if not self._finished:
                self._finished = True
                if self.on_finish is not None:
                    self.on_finish()
            if not self.enabled or self._last is None:
                return
            self._last = None
            if self._footer_live:
                # Wipe the prompt (and any half-typed URL); the report then
                # starts on the same row instead of leaving a blank line.
                self._footer_live = False
                self._notice_rows = 0
                self.stream.write("\r\033[K")
            else:
                self.stream.write("\n")
            self.stream.flush()
