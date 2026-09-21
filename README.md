# google-maps-review-filter

CLI tool that scrapes Google Maps reviews for a place URL, then filters out low-activity reviewers to compute a trusted rating.

## How it works

1. `main.py` validates the URL, runs `MapsScraper.fetch_html()`, then `ReviewAnalyzer`.
2. `scraper.py` uses Playwright (headless Chromium) to open the place page, click the Reviews tab, auto-scroll to load review cards, and return page HTML.
3. `analyzer.py` parses with BeautifulSoup:
   - Spot summary: name, rating, total reviews
   - Per-review: star rating + reviewer review count
   - Split by `THRESHOLDS["MIN_REVIEWS_FOR_TRUST"]` (default 10): `<= limit` = filtered, `> limit` = trusted
   - Reports trusted ratio, all avg vs trusted avg.

## The Algorithms

The script evaluates the sampled reviews based on two primary statistical metrics:

### 1. Low-Activity Density

Measures the proportion of low-history accounts.

$$
P_{Filtered} = \frac{N_{filtered}}{N_{total}}
$$

where `N_filtered` = reviewers with <= 10 reviews, `N_total` = total scanned.

- **Logic:** Reviewers with `<= 10` reviews (`THRESHOLDS["MIN_REVIEWS_FOR_TRUST"]` in `constants.py`) are counted as filtered. A high $P_{Filtered}$ suggests the rating may be inflated by one-off / low-activity accounts.

### 2. Trusted Reviewer Metric

Calculates the average rating by excluding low-activity accounts.

$$
Avg_{Trusted} = \frac{\sum ratings_{trusted}}{N_{trusted}}
$$

where `trusted_ratings` = ratings from reviewers with > 10 reviews.

- **Logic:** Isolates reviewers who have submitted `> 10` reviews across Google Maps and recalculates the average star rating, alongside the trusted ratio:

$$
Ratio_{Trusted} = \frac{N_{trusted}}{N_{filtered} + N_{trusted}} \times 100\%
$$

- The report shows `Average rating (all)` vs `Average rating (trusted only)` so you can see how much low-activity reviews skew the score.

> [!NOTE]
> **Tuning for your use case:** All thresholds live in `constants.py` and are intentionally conservative defaults, not hard rules.
> - `THRESHOLDS["MIN_REVIEWS_FOR_TRUST"]` (default `10`): lower it (e.g. `3-5`) for stricter burner detection in tourist-heavy areas, or raise it (e.g. `20-50`) for high-trust professional vetting.
> - `BROWSER_CONFIG["SCROLL_LIMIT"]`, `["SCROLL_STALL_LIMIT"]`, `["DYNAMIC_WAIT_MS"]`: increase for large spots (1000+ reviews) to enlarge `N_total` and reduce sampling bias; decrease for faster CI / low-bandwidth runs.
> - `BROWSER_CONFIG["MIN_EXPECTED_REVIEWS"]`: safety-net trigger for a second scroll pass, raise it if you routinely expect 100+ cards.
> Validate any change against 3-5 known spots (1 clearly legit, 1 clearly spammy, 1 borderline) before treating the output as production signal. These heuristics flag *suspicion*, not proof of fraud.

## Requirements

- Python >=3.12
- [`uv`](https://github.com/astral-sh/uv) *(Recommended)* or standard `pip`

## Setup

### Option A: Using `uv` (Recommended)
```bash
# Sync dependencies & create virtual environment
uv sync

# Install Playwright browser binaries
uv run playwright install chromium
```

### Option B: Using Traditional `pip` (Fallback)
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Usage

### With `uv`:
```bash
uv run main.py "https://www.google.com/maps/place/..."
# or interactive:
uv run main.py
```

### With traditional `pip` / activated venv:
```bash
python main.py "https://www.google.com/maps/place/..."
```

Enter `q` to quit.

### Example Output

```
Cafe Example
--- SPOT SUMMARY ---
Rating: 4.5 | Total Reviews: 1250

--- ANALYSIS (Sample: 120 reviews) ---
Filtered out (<= 10 reviews): 35
Trusted reviewers (> 10 reviews): 85
Trusted review ratio: 70.8%

Average rating (all): 4.62 / 5
Average rating (trusted only): 4.31 / 5
```

## Project Structure

- `main.py`: CLI entry point, URL validation, report printing.
- `scraper.py`: Playwright navigation, Reviews tab handling, dynamic scroll.
- `analyzer.py`: BeautifulSoup parsing, spot summary, trusted/filtered split.
- `constants.py`: CSS selectors, thresholds, browser tuning knobs.
- `pyproject.toml` / `uv.lock`: dependencies.

## Limitations

- Sample-based: analyzes loaded review cards (`N_total`), not all reviews for the spot.
- Selector fragility: Google Maps markup changes frequently; update `constants.py` when parsing returns empty.
- Rate limiting / CAPTCHA: aggressive scrolling may trigger throttling; slow down `SCROLL_*` settings if so.
- Heuristic only: review count per reviewer is a weak fraud signal — high-activity accounts can still be fake, low-activity accounts can still be genuine.

## Disclaimer

For educational and research purposes only. Not affiliated with, endorsed by, or sponsored by Google. Scraping Google Maps may violate Google's Terms of Service - use at your own risk, respect `robots.txt` / ToS, throttle requests, and do not use output to defame businesses. Results indicate *suspicion*, not proof of fraud.

## License

MIT — see [LICENSE](LICENSE).

## Notes

- Uses a temporary `temp_profile/` dir for Playwright (ignored by git, auto-cleaned).
- Selectors live in `constants.py` and may need updates if Google changes Maps markup.
