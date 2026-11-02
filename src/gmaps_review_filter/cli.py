import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, TextIO

from gmaps_review_filter import __version__
from gmaps_review_filter.analyzer import ReviewAnalyzer
from gmaps_review_filter.auth import login_and_save_state
from gmaps_review_filter.config import DEFAULT_SETTINGS, Settings
from gmaps_review_filter.errors import PermanentScrapeError, ScrapeError
from gmaps_review_filter.report import format_human, to_dict
from gmaps_review_filter.scraper import MapsScraper
from gmaps_review_filter.spot_queue import (
    EnqueueResult,
    QueueStatus,
    SpotQueue,
)
from gmaps_review_filter.ui import Terminal

EXIT_OK = 0
EXIT_SCRAPE_FAILED = 1
EXIT_BAD_URL = 2
EXIT_INTERRUPTED = 130

logger = logging.getLogger(__name__)


class Scraper(Protocol):
    def fetch_html(self) -> str: ...


class ScraperFactory(Protocol):
    def __call__(
        self,
        url: str,
        target_reviews: int | None = ...,
        auth_state: str | None = ...,
    ) -> Scraper: ...


def ensure_utf8_stdout() -> None:
    """Reconfigure stdout to UTF-8 when possible; never raise.

    stdout can lack a reconfigure method (captured/piped streams) or
    report encoding None (embedded contexts) — both must be no-ops.
    """
    stdout = sys.stdout
    reconfigure = getattr(stdout, "reconfigure", None)
    encoding = (getattr(stdout, "encoding", None) or "").lower()
    if callable(reconfigure) and encoding != "utf-8":
        reconfigure(encoding="utf-8")


def positive_int(value: str) -> int:
    """argparse type: an integer strictly greater than zero."""
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError(f"must be greater than 0 (got {number})")
    return number


def non_negative_int(value: str) -> int:
    """argparse type: an integer of zero or more."""
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or greater (got {number})")
    return number


def build_parser(settings: Settings = DEFAULT_SETTINGS) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gmaps-filter",
        description="Scrape Google Maps reviews; split trusted vs low-activity.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "url", nargs="?", help="Google Maps place URL (omit for interactive mode)"
    )
    parser.add_argument(
        "--target",
        type=positive_int,
        default=settings.target_reviews,
        help="reviews to load per spot (default: %(default)s)",
    )
    parser.add_argument(
        "--trust-threshold",
        type=non_negative_int,
        default=settings.min_reviews_for_trust,
        dest="trust_threshold",
        help="reviewers above this count as trusted (default: %(default)s)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of the human report",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="debug logging from the scraper"
    )
    parser.add_argument(
        "--no-queue",
        action="store_true",
        dest="no_queue",
        help=(
            "disable queue mode: exit after one spot instead of accepting "
            "more URLs while it loads"
        ),
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="open a headed browser to sign in to Google, then save the session",
    )
    parser.add_argument(
        "--auth",
        metavar="PATH",
        dest="auth_state",
        default=None,
        help=(f"saved Google session JSON (default: {settings.auth_state_path})"),
    )
    return parser


def exit_code_for(exc: ScrapeError) -> int:
    """Process exit code for a scrape failure: 2 only for bad URLs.

    Centralized so every caller maps the same taxonomy the same way.
    """
    return EXIT_BAD_URL if isinstance(exc, PermanentScrapeError) else EXIT_SCRAPE_FAILED


def _use_color() -> bool:
    """ANSI only for an interactive stdout that has not opted out."""
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def _auth_hint(
    auth_state: str | None,
    terminal: Terminal,
    *,
    settings: Settings = DEFAULT_SETTINGS,
) -> None:
    """Tell the user once when scraping anonymously (5-review wall)."""
    path = Path(auth_state or settings.auth_state_path).expanduser()
    if not path.is_file():
        terminal.write(
            f"Note: no saved Google session at {path}; anonymous runs only "
            "see ~5 reviews. Run --login to save one."
        )


def _stdin_is_tty() -> bool:
    """Queue mode needs a terminal; piped stdin keeps the scripted loop."""
    try:
        return bool(sys.stdin.isatty())
    except Exception:
        return False


def _queue_notice(result: EnqueueResult, terminal: Terminal) -> None:
    """One stderr line per input URL, safe to call from the reader thread."""
    if result.status is QueueStatus.QUEUED:
        terminal.write(f"Queued: {result.url}")
    elif result.status is QueueStatus.DUPLICATE:
        terminal.write(f"Already queued: {result.url}")
    elif result.status is QueueStatus.REJECTED:
        terminal.write(f"Skipped ({result.reason}): {result.url}")
    # STOPPED: the run is shutting down; stay quiet.


def _announce_waiting(queue: SpotQueue, terminal: Terminal) -> None:
    """Tell the user the queue is idle and still accepting input."""
    if queue.pending() == 0 and not queue.closed:
        terminal.write("Waiting for URLs (q to quit)...")


def run_once(
    url: str,
    *,
    target: int,
    trust_threshold: int,
    as_json: bool,
    auth_state: str | None = None,
    verbose: bool = False,
    scraper_factory: ScraperFactory = MapsScraper,
    terminal: Terminal | None = None,
    settings: Settings = DEFAULT_SETTINGS,
    ndjson: bool = False,
    auth_hint: bool = True,
) -> int:
    """Scrape and report one URL. Returns a process exit code.

    Diagnostics go to stderr; only the final report goes to stdout so
    --json output stays pipeable. scraper_factory is injectable for
    tests (must accept (url, target_reviews=...) like MapsScraper).
    Wall-clock processing time is reported on stderr on every path.

    ``ndjson`` emits one compact JSON object per spot (queue mode);
    ``auth_hint`` lets the queue print the anonymous-run note only once.
    """
    term = terminal or Terminal()
    started = time.monotonic()
    try:
        return _scrape_and_report(
            url,
            target=target,
            trust_threshold=trust_threshold,
            as_json=as_json,
            auth_state=auth_state,
            verbose=verbose,
            scraper_factory=scraper_factory,
            terminal=term,
            settings=settings,
            ndjson=ndjson,
            auth_hint=auth_hint,
        )
    finally:
        term.write(f"Completed in {time.monotonic() - started:.1f}s.")


def _use_progress_bar(verbose: bool, terminal: Terminal) -> bool:
    """The bar only owns stderr when it is a TTY and logs are not wanted.

    --verbose keeps debug logs readable; piped output keeps the periodic
    "... loaded N reviews" lines instead of carriage-return frames.
    """
    if verbose:
        return False
    try:
        return bool(terminal.stream.isatty())
    except Exception:
        return False


def _scrape_and_report(
    url: str,
    *,
    target: int,
    trust_threshold: int,
    as_json: bool,
    auth_state: str | None,
    verbose: bool,
    scraper_factory: ScraperFactory,
    terminal: Terminal,
    settings: Settings = DEFAULT_SETTINGS,
    ndjson: bool = False,
    auth_hint: bool = True,
) -> int:
    bar_mode = _use_progress_bar(verbose, terminal)
    if not bar_mode:
        terminal.write("Validating URL...")
    if auth_hint:
        _auth_hint(auth_state, terminal, settings=settings)
    if bar_mode:
        # No newline: the first bar frame starts with \r and overwrites
        # this status line, so the crawl shows only the bar.
        terminal.status("Opening Google Maps...")
    try:
        if not bar_mode:
            terminal.write(f"Initializing scraper for: {url}")
        kwargs: dict[str, Any] = {
            "target_reviews": target,
            "ui": terminal,
            "settings": settings,
        }
        if auth_state:
            kwargs["auth_state"] = auth_state
        if verbose:
            # Debug logs and a redrawn bar share stderr; keep the logs.
            kwargs["progress_enabled"] = False
        html_content = scraper_factory(url, **kwargs).fetch_html()
    except ScrapeError as exc:
        terminal.write(f"Error: {exc}")  # also closes the status line
        return exit_code_for(exc)
    except Exception as exc:
        terminal.write(f"Error: {exc}")
        return EXIT_SCRAPE_FAILED

    analyzer = ReviewAnalyzer(html_content, trust_limit=trust_threshold)
    place = analyzer.place()
    report = analyzer.report()

    if report is None:
        print("Warning: No review cards found.", file=sys.stderr)
        return EXIT_SCRAPE_FAILED

    if as_json:
        payload = to_dict(
            place,
            report,
            source_url=url,
            requested_target=target,
            trust_threshold=trust_threshold,
        )
        if ndjson:
            print(json.dumps(payload, separators=(",", ":")))
        else:
            print(json.dumps(payload, indent=2))
    else:
        print(format_human(place, report, trust_threshold, color=_use_color()))
    return EXIT_OK


def run_queue(
    urls: Sequence[str],
    *,
    target: int,
    trust_threshold: int,
    as_json: bool,
    auth_state: str | None = None,
    verbose: bool = False,
    scraper_factory: ScraperFactory = MapsScraper,
    terminal: Terminal | None = None,
    settings: Settings = DEFAULT_SETTINGS,
    spot_queue: SpotQueue | None = None,
    stdin: TextIO | None = None,
) -> int:
    """Process URLs in order, accepting more from stdin while a spot loads.

    The reader thread only touches stdin and the queue; scraping stays on
    this thread. ``q`` finishes the current spot then drops the pending
    queue; Ctrl-C aborts immediately (exit 130). With ``--json`` each spot
    is one compact line, so stdout stays a valid NDJSON stream.

    ``spot_queue``/``stdin`` are injectable so tests can pre-fill the queue
    without a terminal; an injected queue is treated as already complete.
    """
    term = terminal or Terminal()
    queue = spot_queue if spot_queue is not None else SpotQueue()
    follow_stdin = spot_queue is None
    if follow_stdin:
        queue.start_reader(
            sys.stdin if stdin is None else stdin,
            on_result=lambda result: _queue_notice(result, term),
        )
    term.write("Queue mode: paste more Google Maps URLs while a spot loads.")
    term.write("'q' stops after the current spot; Ctrl-C exits now.")
    for raw in urls:
        _queue_notice(queue.enqueue(raw), term)
    if not follow_stdin:
        # No reader owns an injected queue, so nothing more can arrive.
        queue.close()
    codes: list[int] = []
    first = True
    try:
        while not queue.stopped:
            _announce_waiting(queue, term)
            url = queue.next()
            if url is None:
                break
            if not first and not as_json:
                term.separator()
            term.write(f"Spot: {url}")
            codes.append(
                run_once(
                    url,
                    target=target,
                    trust_threshold=trust_threshold,
                    as_json=as_json,
                    auth_state=auth_state,
                    verbose=verbose,
                    scraper_factory=scraper_factory,
                    terminal=term,
                    settings=settings,
                    ndjson=as_json,
                    auth_hint=first,
                )
            )
            first = False
            if not as_json and not queue.stopped:
                term.write(f"Queue: {queue.pending()} spot(s) waiting.")
    except KeyboardInterrupt:
        term.write(f"Interrupted; {queue.pending()} queued spot(s) discarded.")
        return EXIT_INTERRUPTED
    finally:
        queue.request_stop()

    ok = codes.count(EXIT_OK)
    failed = len(codes) - ok
    term.write(
        f"Queue finished: {ok} ok, {failed} failed, {queue.pending()} discarded."
    )
    # 2 (bad URL) outranks 1 (scrape failure); 0 only when every spot ran.
    return max(codes, default=EXIT_OK)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ensure_utf8_stdout()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    terminal = Terminal()
    opts = {
        "target": args.target,
        "trust_threshold": args.trust_threshold,
        "as_json": args.json,
        "auth_state": args.auth_state,
        "verbose": args.verbose,
        "terminal": terminal,
    }
    if args.login:
        terminal.write("Opening a browser window to sign in to Google...")
        try:
            saved = login_and_save_state(args.auth_state)
        except PermanentScrapeError as exc:
            terminal.write(f"Error: {exc}")
            return EXIT_SCRAPE_FAILED
        terminal.write(f"Saved Google session to {saved}")
        return EXIT_OK
    if not args.no_queue and _stdin_is_tty():
        # A terminal gets queue mode: the reader thread collects URLs while
        # the main thread scrapes, so typing never waits for the crawl.
        return run_queue([args.url] if args.url else [], **opts)
    if args.url:
        return run_once(args.url, **opts)
    while True:
        try:
            target_url = input("\nEnter Google Maps URL (or 'q' to quit): ").strip()
            if target_url.lower() == "q":
                print("Goodbye!")
                return EXIT_OK
            code = run_once(target_url, **opts)
            if not args.json:
                # Close this spot's output before the next prompt; --json
                # keeps stderr free of framing too.
                terminal.separator()
            if code != EXIT_OK:
                logger.debug("run exited with code %d; continuing loop", code)
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            return EXIT_OK
        except Exception:
            logger.exception("Unexpected error in interactive loop; continuing")


if __name__ == "__main__":
    sys.exit(main())
