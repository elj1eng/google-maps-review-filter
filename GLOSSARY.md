# Glossary

Domain vocabulary used in code, reports and tests. Each entry points at the
identifier that implements it, so the glossary stays checkable. Workflow and
commands live in [`AGENTS.md`](AGENTS.md); usage lives in [`README.md`](README.md).

## Sampling and scraping

### Spot / place
One Google Maps place page (a business, hotel, restaurant). The two words are
used interchangeably in logs and tests.
- **In code:** `MapsScraper.url`, `PlaceSummary`.
- **Not:** a search-results list — those are rejected by the URL gate.

### Place URL gate
Validation before any browser launches. Accepts `/maps/place/` paths on Google
hosts and Maps short links; everything else raises `PermanentScrapeError`.
- **In code:** `urls.validate_place_url`, `GOOGLE_DOMAINS`, `SHORT_HOSTS`.
- **Not:** a redirect resolver — short links are passed through untouched.

### Review card
One rendered review in the feed (`div.jftiEf`), holding a star rating and the
reviewer's total review count.
- **In code:** `CSS_SELECTORS["REVIEW_CARD"]`, `analyzer.ReviewAnalyzer.report`.
- **Not:** the spot header, which carries the place's aggregate rating.

### Sample
The review cards present in the final DOM after scrolling. All analysis is
sample-based, never over the spot's full review history.
- **In code:** `ReviewReport.total_scanned`; JSON `sample.size`.
- **Not:** the spot total (below).

### Target
How many reviews to try to load, from `--target` (default 210).
- **In code:** `Settings.target_reviews`, `MapsScraper.target`,
  JSON `source.requested_target`.
- **Not:** a guarantee — stalls, the spot total or throttling can stop earlier.

### Spot total
The place's own review count parsed from the header, e.g. `6 reviews`.
- **In code:** `dom.spot_review_total`, `PlaceSummary.total_reviews`;
  JSON `place.total_reviews` (`null` when unparseable).
- **Not:** the sample size.

### Goal
The effective stop point for scrolling: `min(target, spot total)` when the
header total is known and non-zero, otherwise `target`.
- **In code:** `goal` in `scroll.scroll_reviews`.
- **Why:** a 36-review spot should finish at 36 instead of waiting out the
  stall window.

### Stall
A scroll iteration that adds no new cards. Consecutive stalls end the loop.
- **In code:** `no_change_count`, `Settings.scroll_stall_limit` (default 5),
  `Settings.scroll_limit` (max 40 iterations).
- **Not:** a failed scroll — the pane is re-resolved and wheel input retried.

### Throttled stub
A sample that loaded strictly fewer cards than `min(target, spot total)`,
usually because Google served a degraded page. Treated as transient and
retried with a fresh session.
- **In code:** `scraper.is_throttled_stub`, raised as `TransientScrapeError`
  ("likely a throttled stub").
- **Not:** a small spot — if `loaded` equals the spot total, it is complete.

### Limited view
Google's anti-bot banner ("limited view of Google Maps"), which caps anonymous
feeds at roughly five reviews.
- **In code:** `dom.is_limited_view`, `scraper.LIMITED_VIEW_MSG`.
- **Not:** the 5-review wall itself — a saved session (`--login`) lifts that.

## Trust analysis

### Reviewer count
How many reviews the review's author has posted across Google Maps, parsed from
the card metadata. Missing/unparseable metadata reads as `0`.
- **In code:** `analyzer.ReviewAnalyzer._reviewer_count`,
  `parsing.parse_review_count`.
- **Not:** the review's star rating, or the spot total.

### Trust threshold
The cutoff for "trusted": a reviewer counts as trusted when their count is
**strictly greater** than the threshold. Default 10, so a reviewer with exactly
10 reviews is filtered.
- **In code:** `Settings.min_reviews_for_trust`, `--trust-threshold`,
  `ReviewAnalyzer(trust_limit=...)`; JSON `trust.threshold`.
- **Not:** `Settings.min_expected_reviews`, which is a load-health warning.

### Trusted
A review whose author is above the trust threshold.
- **In code:** `ReviewReport.trusted_count`; JSON `trust.trusted`.

### Filtered
A review whose author is at or below the trust threshold.
- **In code:** `ReviewReport.filtered_count`; JSON `trust.filtered`;
  human line `Filtered out (<= N reviews)`.

### Classified
A card that yielded a parseable star rating. Only classified cards contribute
to averages and the ratio; a missing reviewer count is treated as `0`.
- **In code:** `ReviewReport.classified` (`filtered_count + trusted_count`);
  JSON `sample.classified`.
- **Not:** "all cards" — unrated cards are excluded.

### Unrated
A card with no parseable rating (markup change or a rating-less review).
- **In code:** `ReviewReport.no_rating_count`; JSON `sample.unrated`; triggers
  the human `Warning: N cards had no parseable rating` line.

### Trusted ratio
`trusted / classified`, the share of classified reviews that survived the
filter.
- **In code:** `ReviewReport.trusted_ratio`; JSON `trust.ratio`.
- **Note:** `null` in JSON (and `N/A` in the human report) when nothing was
  classified. A real `0.0` means classified cards exist but none are trusted.

### All average / trusted average
Mean star rating over all classified cards vs. trusted ones only. Both are
`0.0` when their bucket is empty, and the human report always prints two
decimals.
- **In code:** `ReviewReport.all_avg`, `ReviewReport.trusted_avg`;
  JSON `ratings.all`, `ratings.trusted`.
- **Not:** the place's headline rating (`place.rating`), which comes from the
  header.

## Browser mechanics

### Attempt
One full browser run: new profile, navigation, tabs, filter, scroll. Attempts
are 1-based and capped by `Settings.fetch_attempts` (default 5).
- **In code:** `MapsScraper.fetch_html` loop, `_fetch_once(attempt, attempts)`;
  bar label `Attempt i/n` when more than one attempt is possible.

### Backoff
Jittered exponential wait between attempts:
`min(5 * 2^(attempt-1), 30) + U(0, 2)` seconds. A dash rule is printed before
the wait so attempts are visually separated.
- **In code:** `scraper._retry_delay_s`; `Settings.retry_base_s`,
  `retry_cap_s`, `retry_jitter_s`.
- **Not:** the shorter pre-reload delay used inside one attempt.

### Session
One throwaway headless Chromium context with its own temp profile
(`gmaps_*`), a masked `navigator.webdriver`, and no shared state across
attempts. Torn down even on failure.
- **In code:** `session.ScrapeSession`, `WEBDRIVER_MASK_JS`.
- **Not:** a saved Google login — that is the auth state.

### Auth state
The saved Google storage-state JSON (`~/.config/gmaps-filter/auth.json`,
mode 600, gitignored). Restored as cookies; a missing or rejected file means an
anonymous run, not an error.
- **In code:** `auth.resolve_auth_state`, `auth.load_storage_cookies`,
  `GOOGLE_SESSION_COOKIES` (`SID`, `SAPISID`, `SSID`).
- **Not:** a browser profile — only cookies are restored.

### Settle-wait
Event-driven wait until a card count repeats the same value `polls` times in a
row (3 polls, 0.5 s apart, 8 s timeout), instead of a fixed sleep.
- **In code:** `dom.wait_for_stable_count`; used after opening the Reviews tab
  and after switching the filter.
- **Not:** `Settings.dynamic_wait_ms` (the per-batch growth wait).

### Scrollable pane
The actual scrolling container for the review list, chosen as the candidate
with the largest `scrollHeight - clientHeight`.
- **In code:** `dom.find_scrollable_pane`, `SCROLLABLE_PANE_CANDIDATES`.
- **Note:** handles go stale as Maps re-renders; re-resolve after stalls.

### Wheel scroll
Trusted mouse-wheel input over the pane. This is what makes Maps lazy-load the
next batch; the JS `scrollTop` fallback does not.
- **In code:** `dom.wheel_scroll` (returns `False` when only JS ran).
- **Not:** `scroll_into_view_if_needed`, which is only a wake-up fallback.

### Google-only feed
A hotel review feed filtered to Google reviews, dropping third-party excerpts
(e.g. Tripadvisor). Restaurant pages have no filter chip, so this is a no-op
there.
- **In code:** `MapsScraper._select_google_source`, `REVIEW_FILTER_CHIP`,
  `REVIEW_SOURCE_OPTION`, `LOCALE_STRINGS[...]["google_source"]`.
- **Failure modes:** missing menu options → transient; no "Google" option →
  permanent.

## Output

### Schema v1
The `--json` contract: `schema_version`, `source{url,requested_target}`,
`place{name,rating,total_reviews}`, `sample{size,classified,unrated}`,
`trust{threshold,trusted,filtered,ratio}`, `ratings{all,trusted}`. Unknown
facts are `null`.
- **In code:** `report.to_dict`, `report.SCHEMA_VERSION`.
- **Not:** the human layout, which is separate and intentionally unchanged.

### Terminal
The stderr presenter shared by CLI and scraper: complete lines, a single
status line, separators, and progress-bar creation. Nothing else writes to
stderr directly.
- **In code:** `ui.Terminal` (`write`, `status`, `separator`, `progress`).

### Status line
A no-newline stderr line such as `Opening Google Maps...`. It is either closed
with a newline or overwritten by the progress bar's leading `\r`.
- **In code:** `ui.StatusLine` (`show`, `supersede`, `close`).

### Progress bar
Single-line redraw of loaded cards (`Loading reviews [██░░] 42/210 (20%)`),
width-fitted so it never wraps, TTY-only, and disabled by `--verbose`.
- **In code:** `progress.ReviewProgress`.
- **Not:** the status line, though the bar takes over the same terminal line.

### Dash rule
A `-` separator between retry attempts and between interactive spots:
`max(10, min(columns - 1, 60))` characters wide.
- **In code:** `ui.Terminal.rule`, `MIN_RULE`, `MAX_RULE`.

## Testing

### Golden fixture
A trimmed excerpt of a real Maps page with provenance in its header, used for
frozen analyzer assertions.
- **In code:** `tests/fixtures/*.html`, `tests/test_analyzer.py::FIXTURES`.
- **Refresh:** `uv run python -m scripts.refresh_fixtures` (live, sparing).

### Archival fixture
`tests/fixtures/hotel_mixed.html` — a pre-filter mixed-platform feed kept to
exercise third-party review parsing. Deliberately not refreshed, because live
scrapes now return Google-only feeds.

### Live spot
One of three fixed places (Brisnie Matcha, Marina Bay Sands, Peter Luger) used
for end-to-end checks against real Google.
- **In code:** `tests/spots.py`, `tests/test_live_spots.py`.
- **Note:** skipped without a saved session unless `GMAPS_LIVE=1`.

### Fake stack
In-memory Playwright stand-ins (page, context, locator, element, keyboard,
mouse, manager) that let offline tests drive browser logic deterministically.
- **In code:** `tests/fakes.py`.
