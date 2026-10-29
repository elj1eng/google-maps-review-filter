import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Protocol

from gmaps_review_filter.analyzer import ReviewAnalyzer, ReviewReport, SpotSummary
from gmaps_review_filter.config import BROWSER_CONFIG, THRESHOLDS
from gmaps_review_filter.errors import PermanentScrapeError
from gmaps_review_filter.scraper import (
    MapsScraper,
    _attempt_separator,
    login_and_save_state,
)

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


def report_to_dict(
    summary: SpotSummary, report: ReviewReport, limit: int
) -> dict[str, Any]:
    total_classified = report["filtered_count"] + report["trusted_count"]
    return {
        "name": summary["name"],
        "rating": summary["rating"],
        "total_reviews": summary["total"],
        "sample": report["total_scanned"],
        "trust_threshold": limit,
        "filtered_count": report["filtered_count"],
        "trusted_count": report["trusted_count"],
        "trusted_ratio": (
            report["trusted_count"] / total_classified if total_classified else None
        ),
        "all_avg": report["all_avg"],
        "trusted_avg": report["trusted_avg"],
        "unparseable_count": report["no_rating_count"],
    }


def format_human(summary: SpotSummary, report: ReviewReport, limit: int) -> str:
    green = "\033[32m"
    red = "\033[31m"
    reset = "\033[0m"
    n_filt = report["filtered_count"]
    n_trust = report["trusted_count"]
    n_bad = report["no_rating_count"]
    n_classified = n_filt + n_trust
    ratio = f"{n_trust / n_classified * 100:.1f}%" if n_classified else "N/A"
    unparseable = (
        f"{red}Warning: {n_bad} cards had no parseable "
        f"rating (markup may have changed).{reset}"
        if n_bad
        else ""
    )
    parts = [
        "",
        summary["name"],
        f"Rating: {summary['rating']} ({summary['total']})",
        "",
        f"--- ANALYSIS (Sample: {report['total_scanned']} reviews) ---",
        f"{red}Filtered out (<= {limit} reviews): {n_filt}{reset}",
        f"Trusted reviewers (> {limit} reviews): {n_trust}",
        f"{green}Trusted review ratio: {ratio}{reset}",
    ]
    if unparseable:
        parts += ["", unparseable]
    parts += [
        "",
        f"Average rating (all): {report['all_avg']:.2f} / 5",
        f"{green}Average rating (trusted only): {report['trusted_avg']:.2f} / 5{reset}",
        "",
    ]
    return "\n".join(parts)


def _auth_hint(auth_state: str | None) -> None:
    """Tell the user once when scraping anonymously (5-review wall)."""
    path = Path(auth_state or BROWSER_CONFIG["AUTH_STATE_PATH"]).expanduser()
    if not path.is_file():
        print(
            f"Note: no saved Google session at {path}; anonymous runs only "
            "see ~5 reviews. Run --login to save one.",
            file=sys.stderr,
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
) -> int:
    """Scrape and report one URL. Returns a process exit code.

    Diagnostics go to stderr; only the final report goes to stdout so
    --json output stays pipeable. scraper_factory is injectable for
    tests (must accept (url, target_reviews=...) like MapsScraper).
    Wall-clock processing time is reported on stderr on every path.
    """
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
        )
    finally:
        print(f"Completed in {time.monotonic() - started:.1f}s.", file=sys.stderr)


def _use_progress_bar(verbose: bool) -> bool:
    """The bar only owns stderr when it is a TTY and logs are not wanted.

    --verbose keeps debug logs readable; piped output keeps the periodic
    "... loaded N reviews" lines instead of carriage-return frames.
    """
    if verbose:
        return False
    try:
        return bool(sys.stderr.isatty())
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
) -> int:
    bar_mode = _use_progress_bar(verbose)
    if not bar_mode:
        print("Validating URL...", file=sys.stderr)
    _auth_hint(auth_state)
    if bar_mode:
        # No newline: the first bar frame starts with \r and overwrites
        # this status line, so the crawl shows only the bar. (Saving and
        # restoring the cursor is unreliable once output scrolls.)
        print("Opening Google Maps...", end="", flush=True, file=sys.stderr)
    try:
        if not bar_mode:
            print(f"Initializing scraper for: {url}", file=sys.stderr)
        kwargs: dict[str, Any] = {"target_reviews": target}
        if auth_state:
            kwargs["auth_state"] = auth_state
        if verbose:
            # Debug logs and a redrawn bar share stderr; keep the logs.
            kwargs["progress_enabled"] = False
        html_content = scraper_factory(url, **kwargs).fetch_html()
    except PermanentScrapeError as exc:
        if bar_mode:
            print(file=sys.stderr)  # close the status line
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_BAD_URL
    except Exception as exc:
        if bar_mode:
            print(file=sys.stderr)  # close the status line
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_SCRAPE_FAILED

    analyzer = ReviewAnalyzer(html_content, trust_limit=trust_threshold)
    summary = analyzer.get_spot_summary()
    report = analyzer.analyze_reviews()

    if report is None:
        print("Warning: No review cards found.", file=sys.stderr)
        return EXIT_SCRAPE_FAILED

    if as_json:
        print(json.dumps(report_to_dict(summary, report, trust_threshold), indent=2))
    else:
        print(format_human(summary, report, trust_threshold))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ensure_utf8_stdout()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    opts = {
        "target": args.target,
        "trust_threshold": args.trust_threshold,
        "as_json": args.json,
        "auth_state": args.auth_state,
        "verbose": args.verbose,
    }
    if args.login:
        print("Opening a browser window to sign in to Google...", file=sys.stderr)
        try:
            saved = login_and_save_state(args.auth_state)
        except PermanentScrapeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return EXIT_SCRAPE_FAILED
        print(f"Saved Google session to {saved}", file=sys.stderr)
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
                print(_attempt_separator(), file=sys.stderr)
            if code != EXIT_OK:
                logger.debug("run exited with code %d; continuing loop", code)
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            return EXIT_OK
        except Exception:
            logger.exception("Unexpected error in interactive loop; continuing")


if __name__ == "__main__":
    sys.exit(main())
