# google-maps-review-filter

CLI tool that scrapes Google Maps reviews for a place URL, then filters out low-activity reviewers to compute a trusted rating.

## How it works

1. `main.py` validates the URL (reachable + a `/maps/place/` page — search-list URLs are rejected before any browser launches), runs `MapsScraper.fetch_html()`, then `ReviewAnalyzer`.
2. `scraper.py` uses Playwright (headless Chromium) to open the place page, click the Reviews tab, filter hotel tabs to Google-only reviews, auto-scroll to load review cards, and return page HTML. Transient failures (anti-bot limited view, render flakes) retry with backoff; deterministic failures raise immediately.
3. `analyzer.py` parses with BeautifulSoup:
   - Spot summary: name, rating, total reviews
   - Per-review: star rating + reviewer review count
   - Split by `THRESHOLDS["MIN_REVIEWS_FOR_TRUST"]` (default 10, or `--trust-threshold`): `<= limit` = filtered, `> limit` = trusted
   - Reports trusted ratio, all avg vs trusted avg. Cards with no parseable rating are excluded from both averages and reported as unparseable (a markup-change tripwire, not silent data loss).

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

over classified cards only (`N/A` when none classified).

- The report shows `Average rating (all)` vs `Average rating (trusted only)` so you can see how much low-activity reviews skew the score. Both averages cover classified cards only; cards with no parseable rating are counted separately (`Unparseable cards`), and the ratio reads `N/A` when nothing was classified.

> [!NOTE]
> **Tuning for your use case:** Prefer the CLI flags (`--target`, `--trust-threshold`); `constants.py` holds the defaults.
> - `--trust-threshold` (default `10`): raise it (e.g. `20-50`) to filter more aggressively (stricter burner detection for high-trust vetting), or lower it (e.g. `3-5`) to filter only the barest accounts in tourist-heavy areas.
> - `BROWSER_CONFIG["SCROLL_LIMIT"]`, `["SCROLL_STALL_LIMIT"]`, `["DYNAMIC_WAIT_MS"]`: increase for large spots (1000+ reviews) to enlarge `N_total` and reduce sampling bias; decrease for faster CI / low-bandwidth runs.
> - `BROWSER_CONFIG["MIN_EXPECTED_REVIEWS"]`: warns when fewer cards loaded than expected — a smoke signal for slow loads or rate limiting, not a second scroll pass.
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

> `requirements.txt` is generated from `pyproject.toml` — do not hand-edit:
> `uv pip compile pyproject.toml --group dev -o requirements.txt`.

## Usage

```bash
uv run main.py "https://www.google.com/maps/place/..."
# or interactive:
uv run main.py
# (pip/venv equivalent: python main.py ...)
```

Options:

| Flag | Default | Meaning |
|---|---|---|
| `--target N` | 210 | reviews to load per spot |
| `--trust-threshold N` | 10 | reviewers above N reviews count as trusted |
| `--json` | off | machine-readable JSON on stdout (diagnostics go to stderr) |
| `--verbose` | off | debug logging from the scraper |

Exit codes: `0` success, `1` scrape/analysis failure, `2` bad URL.

### Example Output

Real output carries ANSI colors; shown here plain. Figures below are from an
actual run (The Ritz London, `--trust-threshold 10`):

```
The Ritz London
Rating: 4.6 (7904)

--- ANALYSIS (Sample: 210 reviews) ---
Filtered out (<= 10 reviews): 56
Trusted reviewers (> 10 reviews): 154
Trusted review ratio: 73.3%

Average rating (all): 4.38 / 5
Average rating (trusted only): 4.44 / 5
```

## Project Structure

- `main.py`: argparse CLI (`--target`, `--trust-threshold`, `--json`, `--verbose`), exit codes 0/1/2, human/JSON report rendering.
- `scraper.py`: Playwright navigation, Reviews tab handling, hotel Google-only filter, dynamic scroll; transient vs permanent error taxonomy.
- `analyzer.py`: BeautifulSoup parsing, spot summary, trusted/filtered split (fully typed, 100% covered).
- `constants.py`: CSS selectors + `SELECTOR_META` volatility registry, thresholds, browser tuning, `LOCALE_STRINGS` (en/vi UI matching).
- `pyproject.toml` / `uv.lock`: dependencies (`requirements.txt` generated from these).
- `tests/`: pytest suite — analyzer golden fixtures plus offline scraper-policy and CLI tests. Only `tests/refresh_fixtures.py` needs the network.

## Testing

```bash
uv run pytest tests/   # offline, ~1s
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict analyzer.py constants.py
uv run coverage run -m pytest tests/ -q
uv run coverage report --include="analyzer.py,constants.py" --fail-under=100
```

All of the above runs in CI on push/PR. Fixtures are trimmed excerpts of
real pages (`tests/fixtures/`, provenance in file headers); refresh them
with `uv run python tests/refresh_fixtures.py` (live Google load — sparing),
then update the frozen assertions to match. `hotel_mixed.html` is archival
and intentionally not refreshed (live scrapes are Google-only by design).

## Limitations

- Sample-based: analyzes loaded review cards (`N_total`), not all reviews for the spot. Averages and ratio cover classified cards; unparseable cards are reported, not silently dropped.
- Place pages only: `/maps/place/` URLs (or Maps short links). Search-list URLs are rejected up front.
- Selector fragility: Google Maps markup changes frequently; `SELECTOR_META` in `constants.py` records when each selector was last observed — bump it when you update one.
- Locales: UI matching covers English and Vietnamese (`LOCALE_STRINGS`); other locales fall back to English keywords and may fail to open tabs.
- Rate limiting / CAPTCHA: aggressive scrolling may trigger throttling; slow down `SCROLL_*` settings if so.
- Heuristic only: review count per reviewer is a weak fraud signal — high-activity accounts can still be fake, low-activity accounts can still be genuine.

## Disclaimer

For educational and research purposes only. Not affiliated with, endorsed by, or sponsored by Google. Scraping Google Maps may violate Google's Terms of Service - use at your own risk, respect `robots.txt` / ToS, throttle requests, and do not use output to defame businesses. Results indicate *suspicion*, not proof of fraud.

## License

MIT — see [LICENSE](LICENSE).

## Notes

- Each scrape attempt runs in a throwaway browser profile (`tempfile.mkdtemp`), auto-cleaned afterwards — concurrent runs never share state.
- Selectors live in `constants.py` and may need updates if Google changes Maps markup (see `SELECTOR_META`).
