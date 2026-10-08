import sys

import requests

from analyzer import ReviewAnalyzer
from scraper import MapsScraper

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


def is_google_maps_responsive(url: str) -> bool:
    try:
        response = requests.head(url, timeout=5, allow_redirects=False)
        return response.status_code in [200, 301, 302]
    except Exception:
        return False


def main():
    cli_url = sys.argv[1].strip() if len(sys.argv) > 1 else None
    while True:
        try:
            target_url = (
                cli_url
                if cli_url
                else input("\nEnter Google Maps URL (or 'q' to quit): ").strip()
            )
            cli_url = None
            if target_url.lower() == "q":
                print("Goodbye!")
                break

            print("Validating URL...")
            if not is_google_maps_responsive(target_url):
                print("Error: The URL did not return a valid response.")
                continue

            print(f"Initializing scraper for: {target_url}")
            scraper = MapsScraper(target_url)
            html_content = scraper.fetch_html()

            analyzer = ReviewAnalyzer(html_content)
            summary = analyzer.get_spot_summary()
            report = analyzer.analyze_reviews()

            if not report:
                print("Warning: No review cards found.")
                continue

            limit = analyzer.limit
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
            out = (
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
            print(out)
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"Error: {e}")
            continue


if __name__ == "__main__":
    main()
