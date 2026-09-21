import sys

import requests

from scraper import MapsScraper
from analyzer import ReviewAnalyzer

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
            target_url = cli_url if cli_url else input("\nEnter Google Maps URL (or 'q' to quit): ").strip()
            cli_url = None
            if target_url.lower() == 'q':
                print("Goodbye!")
                break
                print("Error: No URL provided.")
                continue

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
            ratio = report["trusted_count"] / (report["filtered_count"] + report["trusted_count"]) * 100 if report["filtered_count"] else 100.0
            out = (
                f"\n{summary['name']}\n"
                f"--- SPOT SUMMARY ---\n"
                f"Rating: {summary['rating']} | Total Reviews: {summary['total']}\n"
                f"\n"
                f"--- ANALYSIS (Sample: {report['total_scanned']} reviews) ---\n"
                f"{red}Filtered out (<= {limit} reviews): {report['filtered_count']}{reset}\n"
                f"Trusted reviewers (> {limit} reviews): {report['trusted_count']}\n"
                f"{green}Trusted review ratio: {ratio:.1f}%{reset}\n"
                f"\n"
                f"Average rating (all): {report['all_avg']:.2f} / 5\n"
                f"{green}Average rating (trusted only): {report['trusted_avg']:.2f} / 5{reset}\n"
            )
            print(out)
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break


if __name__ == "__main__":
    main()
