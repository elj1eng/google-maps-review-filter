"""Thread-safe URL intake for queue mode.

Playwright's sync API keeps the scrape on the main thread, so URLs typed
while a spot loads are collected by a small daemon reader thread. That
thread touches only stdin and this queue: it never imports Playwright and
never prints. The CLI drains the queue between spots and renders the
confirmations through the thread-safe :class:`~gmaps_review_filter.ui.Terminal`.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import TextIO

from gmaps_review_filter.errors import PermanentScrapeError
from gmaps_review_filter.urls import (
    is_short_link,
    place_label,
    resolve_short_link,
    validate_place_url,
)

# Lines that ask the reader to stop instead of adding a spot.
STOP_WORDS = frozenset({"q", "quit", "exit"})


class QueueStatus(StrEnum):
    """Outcome of one enqueue attempt."""

    QUEUED = "queued"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    """What happened to one input line."""

    status: QueueStatus
    url: str
    reason: str | None = None


class SpotQueue:
    """FIFO of spot URLs with a stop switch and a blocking ``next()``.

    Two shutdown paths, deliberately different:

    - ``request_stop()`` (the ``q`` stop word) drops anything pending and
      wakes a blocked ``next()`` immediately.
    - ``close()`` (stdin EOF) accepts nothing new but drains the URLs that
      were already queued before ``next()`` returns ``None``.
    """

    def __init__(self, *, resolve: Callable[[str], str | None] | None = None) -> None:
        self._items: deque[str] = deque()
        self._seen: set[str] = set()
        self._labels: dict[str, str] = {}
        # Short links that already got their one extra lookup at spot start;
        # a dead link must not cost a timeout before every later spot.
        self._retried: set[str] = set()
        # Short links carry no name; one redirect resolves the real place
        # slug for display. Injectable so tests never touch the network.
        self._resolve = resolve_short_link if resolve is None else resolve
        # The between-spot retry is a single quick attempt: a dead link must
        # not stall the next spot for the full multi-try budget.
        self._retry_resolve = (
            partial(resolve_short_link, attempts=1) if resolve is None else resolve
        )
        self._cond = threading.Condition()
        self._closed = False
        self._stopped = False
        self._reader: threading.Thread | None = None

    @property
    def stopped(self) -> bool:
        """True once a stop word was read (pending URLs are discarded)."""
        with self._cond:
            return self._stopped

    @property
    def closed(self) -> bool:
        """True once stdin reached EOF (queued URLs still drain)."""
        with self._cond:
            return self._closed

    def pending(self) -> int:
        """Number of accepted URLs not handed out yet."""
        with self._cond:
            return len(self._items)

    def pending_urls(self) -> list[str]:
        """Snapshot of accepted URLs not handed out yet, oldest first."""
        with self._cond:
            return list(self._items)

    def label(self, url: str) -> str:
        """Display name for a queued URL (resolved short link when known)."""
        with self._cond:
            label = self._labels.get(url)
        return place_label(url) if label is None else label

    def refresh_labels(self, urls: Sequence[str]) -> None:
        """Retry display names for short links that are still unresolved.

        Called on the main thread between spots: a link whose enqueue-time
        lookup failed (throttled, flaky network) gets another chance before
        the queue is redrawn, so the list stops showing a raw URL. Regular
        place URLs already carry their name in the slug and are skipped.
        """
        for url in urls:
            with self._cond:
                if self._stopped:
                    return
                if (
                    url in self._labels
                    or url in self._retried
                    or not is_short_link(url)
                ):
                    continue
                self._retried.add(url)
            final = self._retry_resolve(url)
            if final is None:
                continue
            with self._cond:
                self._labels[url] = place_label(final)

    def position(self, url: str) -> int | None:
        """1-based place in the pending list, or None once it was handed out."""
        with self._cond:
            try:
                return self._items.index(url) + 1
            except ValueError:
                return None

    def enqueue(self, raw: str) -> EnqueueResult:
        """Validate, dedupe and append one URL. Never blocks on the reader."""
        url = (raw or "").strip()
        if not url:
            return EnqueueResult(QueueStatus.REJECTED, url, "empty input")
        with self._cond:
            if self._stopped:
                return EnqueueResult(QueueStatus.STOPPED, url)
        try:
            url = validate_place_url(url)
        except PermanentScrapeError as exc:
            return EnqueueResult(QueueStatus.REJECTED, url, str(exc))
        with self._cond:
            if self._stopped:
                return EnqueueResult(QueueStatus.STOPPED, url)
            if url in self._seen:
                return EnqueueResult(QueueStatus.DUPLICATE, url)
        # Resolving follows a redirect chain, so keep it out of the lock: the
        # main thread must still be able to drain and redraw meanwhile.
        final = self._resolve(url) if is_short_link(url) else None
        with self._cond:
            if self._stopped:
                return EnqueueResult(QueueStatus.STOPPED, url)
            if url in self._seen:
                return EnqueueResult(QueueStatus.DUPLICATE, url)
            self._seen.add(url)
            if final is not None:
                self._labels[url] = place_label(final)
            self._items.append(url)
            self._cond.notify()
        return EnqueueResult(QueueStatus.QUEUED, url)

    def next(self) -> str | None:
        """Block until the next URL; ``None`` once stopped or drained+closed."""
        with self._cond:
            while not self._items and not self._closed and not self._stopped:
                self._cond.wait()
            if self._stopped:
                return None
            if self._items:
                return self._items.popleft()
            return None

    def close(self) -> None:
        """Accept no more input; drain already-queued URLs, then stop."""
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def request_stop(self) -> None:
        """Stop now: wake ``next()`` and discard anything pending."""
        with self._cond:
            self._stopped = True
            self._cond.notify_all()

    def start_reader(
        self,
        stream: TextIO,
        *,
        on_result: Callable[[EnqueueResult], None] | None = None,
        on_blank: Callable[[], None] | None = None,
    ) -> threading.Thread:
        """Consume URL lines from ``stream`` on a daemon thread.

        Blank lines are ignored, stop words call :meth:`request_stop`, and
        EOF closes the queue so the CLI exits once pending work drains.
        ``on_result`` (optional) reports each outcome to the caller;
        ``on_blank`` fires for a consumed line that yields no URL (blank
        input or a stop word). Enter's echo moves the terminal cursor, so
        the CLI re-anchors a live prompt there.
        """
        reader = threading.Thread(
            target=self._read,
            args=(stream, on_result, on_blank),
            name="gmaps-url-reader",
            daemon=True,
        )
        self._reader = reader
        reader.start()
        return reader

    def join_reader(self, timeout: float | None = None) -> None:
        """Wait for the reader thread to finish (tests and shutdown)."""
        reader = self._reader
        if reader is not None:
            reader.join(timeout)

    def _read(
        self,
        stream: TextIO,
        on_result: Callable[[EnqueueResult], None] | None,
        on_blank: Callable[[], None] | None = None,
    ) -> None:
        try:
            for line in stream:
                text = line.strip()
                if not text:
                    if on_blank is not None:
                        on_blank()
                    continue
                if text.lower() in STOP_WORDS:
                    if on_blank is not None:
                        on_blank()
                    self.request_stop()
                    return
                result = self.enqueue(text)
                if on_result is not None:
                    on_result(result)
        except (OSError, ValueError):
            # A closed or unreadable stdin means no more URLs, not a crash.
            pass
        finally:
            self.close()
