# google-maps-review-filter

CLI tool that scrapes Google Maps reviews for a place URL, then filters out low-activity reviewers to compute a trusted rating.

## How it works

1. `cli.py` validates the URL (a `/maps/place/` page or Maps short link — search-list URLs are rejected before any browser launches), runs `MapsScraper.fetch_html()`, then `ReviewAnalyzer`, and reports wall-clock time on stderr.
2. `scraper.py` uses Playwright (headless Chromium) to open the place page, click the Reviews tab, filter hotel tabs to Google-only reviews, auto-scroll to load review cards, and return page HTML. Scrolling stops at `--target` or the spot's own review count, whichever is smaller, so small spots finish immediately instead of waiting out the stall window. A saved Google session (`--login`) is loaded when present; anonymous sessions only see ~5 reviews. Transient failures (anti-bot limited view, render flakes) retry with backoff; deterministic failures raise immediately.
3. `analyzer.py` parses with BeautifulSoup:
   - Spot summary: name, rating, total reviews
   - Per-review: star rating + reviewer review count
   - Split by `Settings.min_reviews_for_trust` (default 10, or `--trust-threshold`): `<= limit` = filtered, `> limit` = trusted
   - Reports trusted ratio, all avg vs trusted avg. Cards with no parseable rating are excluded from both averages and reported as unparseable (a markup-change tripwire, not silent data loss).

## The Algorithm

The script splits sampled reviewers on history depth, then compares the trusted slice against the whole sample.

### Trusted Reviewer Metric

Calculates the average rating by excluding low-activity accounts.

$$
Avg_{Trusted} = \frac{\sum ratings_{trusted}}{N_{trusted}}
$$

where `trusted_ratings` = ratings from reviewers with > 10 reviews.

- **Logic:** Isolates reviewers who have submitted `> 10` reviews across Google Maps and recalculates the average star rating, alongside the trusted ratio:

$$
Ratio_{Trusted} = \frac{N_{trusted}}{N_{filtered} + N_{trusted}} \times 100\%
$$

over classified cards only (`N/A` when none classified). A high filtered share suggests the rating may be inflated by one-off / low-activity accounts.

- The report shows `Average rating (all)` vs `Average rating (trusted only)` so you can see how much low-activity reviews skew the score. Both averages cover classified cards only; cards with no parseable rating are counted separately (`Unparseable cards`), and the ratio reads `N/A` when nothing was classified.

> [!NOTE]
> **Tuning for your use case:** Prefer the CLI flags (`--target`, `--trust-threshold`); `constants.py` holds the defaults.
> - `--trust-threshold` (default `10`): raise it (e.g. `20-50`) to filter more aggressively (stricter burner detection for high-trust vetting), or lower it (e.g. `3-5`) to filter only the barest accounts in tourist-heavy areas.
> - `Settings.scroll_limit`, `.scroll_stall_limit`, `.dynamic_wait_ms`: increase for large spots (1000+ reviews) to enlarge `N_total` and reduce sampling bias; decrease for faster CI / low-bandwidth runs.
> - `Settings.min_expected_reviews`: warns when fewer cards loaded than expected — a smoke signal for slow loads or rate limiting, not a second scroll pass.
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
uv run gmaps-filter "https://www.google.com/maps/place/..."
# or interactive:
uv run gmaps-filter
# (pip/venv equivalent: gmaps-filter ... after pip install -e .)
```

In interactive mode, each spot's report is closed with the same width-aware
dash rule before the next prompt (skipped with `--json`).

Options:

| Flag | Default | Meaning |
|---|---|---|
| `--target N` | 210 | reviews to load per spot |
| `--trust-threshold N` | 10 | reviewers above N reviews count as trusted |
| `--json` | off | machine-readable JSON on stdout (diagnostics go to stderr) |
| `--verbose` | off | debug logging from the scraper |
| `--login` | off | open the `accounts.google.com` sign-in page in a headed browser, then save the session |
| `--auth PATH` | `~/.config/gmaps-filter/auth.json` | saved session file used for scraping |
| `--version` | — | print the installed version and exit |

Exit codes: `0` success, `1` scrape/analysis failure, `2` bad URL or invalid
arguments (`--target` must be > 0, `--trust-threshold` >= 0).

Human output is colored only when stdout is a TTY; piped output and
`NO_COLOR=1` are plain text. `--json` emits schema v1:

```json
{
  "schema_version": 1,
  "source": { "url": "<input URL>", "requested_target": 210 },
  "place": { "name": "The Ritz London", "rating": 4.6, "total_reviews": 7904 },
  "sample": { "size": 210, "classified": 210, "unrated": 0 },
  "trust": { "threshold": 10, "trusted": 154, "filtered": 56, "ratio": 0.7333 },
  "ratings": { "all": 4.38, "trusted": 4.44 }
}
```

Unparseable header facts are `null`, never `"N/A"`; `trust.ratio` is `null`
when no card could be classified.

While reviews load, a single-line progress bar (`Loading reviews [███░░░] 120/210 (57%)`)
is drawn on stderr as soon as scrolling starts and redrawn each time another
batch arrives. It appears only when stderr is a TTY and fits the terminal width
(so the redraw never wraps). Setup chatter (`Validating URL...`,
`Initializing scraper...`) is hidden and completion logs are suppressed while
the bar owns the line, leaving just the bar; a dash rule (up to 60 columns)
separates retry attempts and consecutive spots in the interactive loop. Under
`--verbose` or piped output the bar is replaced with the usual log lines
(`--json` stdout stays clean either way).

### Signed-in sessions (`--login`)

Google truncates anonymous Maps sessions to ~5 reviews behind a sign-in
wall, regardless of scrolling or clicking "See more reviews". A saved
session lifts that wall:

```bash
uv run gmaps-filter --login
# sign in to Google on the accounts.google.com page that opens
```

The session is written to `~/.config/gmaps-filter/auth.json` (mode `600`,
gitignored). It is a credential: treat it like a password. Sessions expire;
when runs start hitting the 5-review wall again, re-run `--login`. Scraping
works without it, but only sees ~5 reviews per spot.

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

- `src/gmaps_review_filter/cli.py`: argparse CLI (`--target`, `--trust-threshold`, `--json`, `--verbose`, `--login`, `--auth`), exit codes 0/1/2, human/JSON report rendering (`gmaps-filter` entry point).
- `src/gmaps_review_filter/scraper.py`: Playwright navigation, Reviews tab handling, hotel Google-only filter, dynamic scroll, saved-session restore, `--login` helper.
- `src/gmaps_review_filter/errors.py`: transient vs permanent error taxonomy.
- `src/gmaps_review_filter/analyzer.py`: BeautifulSoup parsing, spot summary, trusted/filtered split (fully typed, 100% covered).
- `src/gmaps_review_filter/selectors.py`: CSS selectors + `SELECTOR_META` volatility registry.
- `src/gmaps_review_filter/config.py`: thresholds, browser tuning, `LOCALE_STRINGS` (en/vi UI matching), saved-session path and login URL/timeout.
- `pyproject.toml` / `uv.lock`: dependencies (`requirements.txt` generated from these).
- `tests/`: pytest suite — analyzer golden fixtures plus offline scraper-policy and CLI tests. Only the refresh tool needs the network.

## Testing

```bash
uv run pytest               # offline suite, ~1s (live spots skip without a session)
uv run pytest -m live       # 3 fixed spots end-to-end (needs --login; minutes)
uv run pytest -m "not live" # force the fast offline-only suite
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict src/gmaps_review_filter/analyzer.py src/gmaps_review_filter/config.py
uv run coverage run -m pytest -m "not live"
uv run coverage report      # include + fail-under=100 configured in pyproject.toml
```

`tests/test_live_spots.py` scrapes three fixed spots (Brisnie Matcha,
Marina Bay Sands, Peter Luger) through the real CLI and checks the names,
totals, sample size and report invariants. It runs automatically when a
saved session exists (`--login`), because anonymous sessions only see ~5
reviews; otherwise it skips. `GMAPS_LIVE=1` forces it on.

All of the above runs in CI on push/PR. Fixtures are trimmed excerpts of
real pages (`tests/fixtures/`, provenance in file headers); refresh them
with `uv run python -m scripts.refresh_fixtures` (live Google
load — sparing),
then update the frozen assertions to match. `hotel_mixed.html` is archival
and intentionally not refreshed (live scrapes are Google-only by design).

## Limitations

- Sample-based: analyzes loaded review cards (`N_total`), not all reviews for the spot. Averages and ratio cover classified cards; unparseable cards are reported, not silently dropped.
- Place pages only: `/maps/place/` URLs (or Maps short links). Search-list URLs are rejected up front.
- Selector fragility: Google Maps markup changes frequently; `SELECTOR_META` in `constants.py` records when each selector was last observed — bump it when you update one.
- Locales: UI matching covers English and Vietnamese (`LOCALE_STRINGS`); other locales fall back to English keywords and may fail to open tabs.
- Rate limiting / CAPTCHA: aggressive scrolling may trigger throttling; slow down `SCROLL_*` settings if so.
- Anonymous sessions: Google truncates the review feed to ~5 cards behind a sign-in wall that reloading or "See more reviews" cannot bypass. Run `--login` once to scrape signed in; the saved session expires eventually and must be refreshed.
- Heuristic only: review count per reviewer is a weak fraud signal — high-activity accounts can still be fake, low-activity accounts can still be genuine.

## Disclaimer

For educational and research purposes only. Not affiliated with, endorsed by, or sponsored by Google. Scraping Google Maps may violate Google's Terms of Service - use at your own risk, respect `robots.txt` / ToS, throttle requests, and do not use output to defame businesses. Results indicate *suspicion*, not proof of fraud.

## License

MIT — see [LICENSE](LICENSE).

## Notes

- Each scrape attempt runs in a throwaway browser profile (`tempfile.mkdtemp`), auto-cleaned afterwards — concurrent runs never share state.
- Selectors live in `constants.py` and may need updates if Google changes Maps markup (see `SELECTOR_META`).
