import argparse
import json
import logging
import sys

import requests

from analyzer import ReviewAnalyzer
from constants import BROWSER_CONFIG, THRESHOLDS
from scraper import MapsScraper, PermanentScrapeError

EXIT_OK = 0
EXIT_SCRAPE_FAILED = 1
EXIT_BAD_URL = 2

logger = logging.getLogger(__name__)

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Scrape Google Maps reviews; split trusted vs low-activity."
    )
    parser.add_argument(
        "url", nargs="?", help="Google Maps place URL (omit for interactive mode)"
    )
    parser.add_argument(
        "--target",
        type=int,
        default=BROWSER_CONFIG.get("TARGET_REVIEWS", 210),
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
    return parser


def is_google_maps_responsive(url: str) -> bool:
    try:
        response = requests.head(url, timeout=5, allow_redirects=False)
        return response.status_code in [200, 301, 302]
    except Exception:
        return False


def report_to_dict(summary, report, limit):
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


def format_human(summary, report, limit):
    green = "\033[32m"
    red = "\033[31m"
    reset = "\033[0m"
    n_filt = report["filtered_count"]
    n_trust = report["trusted_count"]
    n_bad = report["no_rating_count"]
    n_classified = n_filt + n_trust
    ratio = f"{n_trust / n_classified * 100:.1f}%" if n_classified else "N/A"
    unparseable = (
        f"\n{red}Warning: {n_bad} cards had no parseable "
        f"rating (markup may have changed).{reset}"
        if n_bad
        else ""
    )
    return (
        f"\n{summary['name']}\n"
        f"Rating: {summary['rating']} ({summary['total']})\n"
        f"\n"
        f"--- ANALYSIS (Sample: {report['total_scanned']} reviews) ---\n"
        f"{red}Filtered out (<= {limit} reviews): {n_filt}{reset}\n"
        f"Trusted reviewers (> {limit} reviews): {n_trust}\n"
        f"{green}Trusted review ratio: {ratio}{reset}\n"
        f"{unparseable}\n"
        f"\n"
        f"Average rating (all): {report['all_avg']:.2f} / 5\n"
        f"{green}Average rating (trusted only): "
        f"{report['trusted_avg']:.2f} / 5{reset}\n"
    )


def run_once(url, *, target, trust_threshold, as_json):
    """Scrape and report one URL. Returns a process exit code.

    Diagnostics go to stderr; only the final report goes to stdout so
    --json output stays pipeable.
    """
    print("Validating URL...", file=sys.stderr)
    if not is_google_maps_responsive(url):
        print("Error: The URL did not return a valid response.", file=sys.stderr)
        return EXIT_BAD_URL
    try:
        print(f"Initializing scraper for: {url}", file=sys.stderr)
        html_content = MapsScraper(url, target_reviews=target).fetch_html()
    except PermanentScrapeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_BAD_URL
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_SCRAPE_FAILED

    analyzer = ReviewAnalyzer(html_content, trust_limit=trust_threshold)
    summary = analyzer.get_spot_summary()
    report = analyzer.analyze_reviews()

    if not report:
        print("Warning: No review cards found.", file=sys.stderr)
        return EXIT_SCRAPE_FAILED

    if as_json:
        print(json.dumps(report_to_dict(summary, report, trust_threshold), indent=2))
    else:
        print(format_human(summary, report, trust_threshold))
    return EXIT_OK


def main(argv=None):
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    opts = {
        "target": args.target,
        "trust_threshold": args.trust_threshold,
        "as_json": args.json,
    }
    if args.url:
        return run_once(args.url, **opts)
    while True:
        try:
            target_url = input("\nEnter Google Maps URL (or 'q' to quit): ").strip()
            if target_url.lower() == "q":
                print("Goodbye!")
                return EXIT_OK
            code = run_once(target_url, **opts)
            if code != EXIT_OK:
                logger.debug("run exited with code %d; continuing loop", code)
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            return EXIT_OK
        except Exception as exc:
            print(f"Error: {exc}")


if __name__ == "__main__":
    sys.exit(main())
