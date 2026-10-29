import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Protocol

from gmaps_review_filter.analyzer import ReviewAnalyzer
from gmaps_review_filter.auth import login_and_save_state
from gmaps_review_filter.config import BROWSER_CONFIG, THRESHOLDS
from gmaps_review_filter.errors import PermanentScrapeError
from gmaps_review_filter.report import format_human, to_dict
from gmaps_review_filter.scraper import MapsScraper
from gmaps_review_filter.ui import Terminal

EXIT_OK = 0
EXIT_SCRAPE_FAILED = 1
EXIT_BAD_URL = 2

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape Google Maps reviews; split trusted vs low-activity."
    )
    parser.add_argument(
        "url", nargs="?", help="Google Maps place URL (omit for interactive mode)"
    )
    parser.add_argument(
        "--target",
        type=int,
        default=BROWSER_CONFIG["TARGET_REVIEWS"],
        help="reviews to load per spot (default: %(default)s)",
    )
    parser.add_argument(
        "--trust-threshold",
        type=int,
        default=THRESHOLDS["MIN_REVIEWS_FOR_TRUST"],
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
        "--login",
        action="store_true",
        help="open a headed browser to sign in to Google, then save the session",
    )
    parser.add_argument(
        "--auth",
        metavar="PATH",
        dest="auth_state",
        default=None,
        help=(
            f"saved Google session JSON (default: {BROWSER_CONFIG['AUTH_STATE_PATH']})"
        ),
    )
    return parser


def _use_color() -> bool:
    """ANSI only for an interactive stdout that has not opted out."""
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def _auth_hint(auth_state: str | None, terminal: Terminal) -> None:
    """Tell the user once when scraping anonymously (5-review wall)."""
    path = Path(auth_state or BROWSER_CONFIG["AUTH_STATE_PATH"]).expanduser()
    if not path.is_file():
        terminal.write(
            f"Note: no saved Google session at {path}; anonymous runs only "
            "see ~5 reviews. Run --login to save one."
        )


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
) -> int:
    """Scrape and report one URL. Returns a process exit code.

    Diagnostics go to stderr; only the final report goes to stdout so
    --json output stays pipeable. scraper_factory is injectable for
    tests (must accept (url, target_reviews=...) like MapsScraper).
    Wall-clock processing time is reported on stderr on every path.
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
) -> int:
    bar_mode = _use_progress_bar(verbose, terminal)
    if not bar_mode:
        terminal.write("Validating URL...")
    _auth_hint(auth_state, terminal)
    if bar_mode:
        # No newline: the first bar frame starts with \r and overwrites
        # this status line, so the crawl shows only the bar.
        terminal.status("Opening Google Maps...")
    try:
        if not bar_mode:
            terminal.write(f"Initializing scraper for: {url}")
        kwargs: dict[str, Any] = {"target_reviews": target, "ui": terminal}
        if auth_state:
            kwargs["auth_state"] = auth_state
        if verbose:
            # Debug logs and a redrawn bar share stderr; keep the logs.
            kwargs["progress_enabled"] = False
        html_content = scraper_factory(url, **kwargs).fetch_html()
    except PermanentScrapeError as exc:
        terminal.write(f"Error: {exc}")  # also closes the status line
        return EXIT_BAD_URL
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
        print(json.dumps(payload, indent=2))
    else:
        print(format_human(place, report, trust_threshold, color=_use_color()))
    return EXIT_OK


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
