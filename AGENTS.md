# AGENTS.md

Guidance for coding agents in this repository. User-facing docs are in
[`README.md`](README.md); domain vocabulary is in [`GLOSSARY.md`](GLOSSARY.md).

## What this is

A Python 3.12 CLI that scrapes one Google Maps place page with Playwright,
parses the loaded review cards with BeautifulSoup, and reports the share of
"trusted" reviewers plus the trusted average rating. Runtime dependencies are
only `beautifulsoup4` and `playwright`.

## Commands

```bash
uv sync --group dev                  # create/refresh .venv
uv run playwright install chromium   # browser binary (once per machine)

uv run pytest -m "not live" -q       # fast offline suite, no network (~2s)
uv run pytest                        # full suite; live spots run if signed in
uv run pytest -m live                # 3 fixed spots, real Google (~30s, needs auth)

uv run ruff check . && uv run ruff format --check .
uv run mypy                          # strict over src/ + scripts/ (pyproject.toml)

uv run coverage run -m pytest -m "not live"
uv run coverage report               # overall floor: 90%
uv run coverage report \
  --include='*/analyzer.py,*/parsing.py,*/models.py,*/report.py,*/config.py,*/errors.py' \
  --fail-under=100                   # pure modules: 100%

uv build                             # wheel + sdist in dist/
uv run python -m scripts.refresh_fixtures   # live fixture refresh (sparingly)
uv run gmaps-filter --version
```

CI (`.github/workflows/ci.yml`) runs lint, format check, `mypy`, the full
`pytest` suite, and both coverage gates on push/PR.

## Architecture

Pipeline: `cli.run_once` → `MapsScraper.fetch_html` (retry loop) →
`ScrapeSession` → `dom` + `scroll` → HTML → `ReviewAnalyzer` → `models` →
`report` → stdout. Diagnostics go through `Terminal` to stderr.

| Module | Responsibility |
|---|---|
| `cli.py` | argparse, exit codes, one-shot/interactive/queue flow, report printing |
| `spot_queue.py` | Queue-mode stdin reader: FIFO, dedupe, stop/EOF semantics |
| `scraper.py` | Retry policy, Reviews tab, hotel Google-only filter, stub guard |
| `session.py` | One throwaway headless browser context per attempt |
| `auth.py` | Saved-session detection/restore, `--login` flow |
| `dom.py` | Locator probing, settle-waits, wheel input, limited-view detection |
| `scroll.py` | Lazy-load loop, early stop at target or spot total |
| `urls.py` | Place-URL gate (exact Google host allowlist) |
| `analyzer.py` | BeautifulSoup parse → `PlaceSummary` / `ReviewReport` |
| `parsing.py` | Shared review-count/rating regexes |
| `models.py` | Frozen `PlaceSummary`, `ReviewReport` |
| `report.py` | JSON schema v1 + human report (pure) |
| `ui.py`, `progress.py` | Status line, dash rule, progress bar (stderr) |
| `config.py` | Frozen `Settings`, `DEFAULT_SETTINGS`, `LOCALE_STRINGS` |
| `locale.py` | `<html lang>` detection, keyword matching |
| `errors.py` | `ScrapeError` taxonomy |
| `selectors.py` | CSS selectors + `SELECTOR_META` volatility registry |

## Hard invariants

- **Python >= 3.12. No new runtime dependencies.** Dev tools go in
  `[dependency-groups].dev`; runtime deps in `[project].dependencies`.
- **Never mutate settings or models.** `Settings` and the domain records are
  frozen; build variants with `dataclasses.replace(DEFAULT_SETTINGS, ...)`.
- **Missing data is `None`, never `"N/A"`.** The sentinel exists only in
  `report.format_human`. JSON emits `null`.
- **JSON schema v1 is a public contract.** Changing field names/shape requires
  a `schema_version` bump and README update; additive fields are fine. In queue
  mode `--json` is NDJSON (one compact v1 object per line); one-shot keeps the
  pretty single object.
- **Only the CLI prints the report.** `scraper.py`, `dom.py`, `scroll.py`,
  `session.py` must not `print()`; use the injected `Terminal` (stderr).
  stdout carries the report only, so `--json` stays pipeable.
- **The queue reader thread never touches Playwright.** It only reads stdin and
  appends to `SpotQueue`; scraping stays on the main thread. All stderr output
  goes through the thread-safe `Terminal`. A bar with a footer *inserts* the
  line between bar and prompt; a bare bar erases its frame before the line.
- **ANSI is gated.** Color only when stdout is a TTY and `NO_COLOR` is unset;
  the bar only when stderr is a TTY and `--verbose` is off.
- **Host allowlist is deliberate:** `google.com`, `google.com.vn` and their
  subdomains, plus `maps.app.goo.gl`/`goo.gl`. Do not broaden it casually
  (`google.co.uk` is intentionally rejected).
- **Respect the error taxonomy.** Transient failures retry with a fresh
  session; permanent failures (bad URL, no Google filter option, missing
  title) exit 2 without retrying; exhaustion raises `RetriesExhaustedError`.
  Map codes only through `cli.exit_code_for`.
- **Retry only wraps `_fetch_once`.** Every attempt gets a new
  `ScrapeSession`; no browser state is shared across attempts.
- **Tests are not weakened.** Do not delete or relax assertions, and do not
  reduce the number of tests to make a change pass.

## Where to change what

| Change | Files (and tests) |
|---|---|
| Maps markup / selector breaks | `selectors.py` (bump `SELECTOR_META[...]["observed"]`), fixtures, `test_dom.py`/`test_scraper_flow.py` |
| Review-count or rating parsing | `parsing.py`, `test_parsing.py`, golden assertions |
| Trust math / report fields | `analyzer.py`, `models.py`, `test_analyzer.py`, `test_report.py` |
| JSON or human layout | `report.py`, `test_report.py`, README schema example |
| CLI flag or exit code | `cli.py` (`build_parser`), `test_cli.py`, README options |
| Retry/flow/tab/filter behavior | `scraper.py`, `test_scraper_flow.py` |
| Queue mode / stdin intake | `spot_queue.py`, `cli.run_queue`, `test_spot_queue.py`, `test_cli.py` |
| Browser/session/login | `session.py`, `auth.py`, `test_session.py`, `test_auth.py` |
| Scroll behavior | `scroll.py`, `dom.py`, `test_scroll.py`, `test_dom.py` |
| Defaults/tuning | `config.py`, `test_config.py`, README tuning note |
| URL acceptance | `urls.py`, `test_urls.py` |

## Testing conventions

- Offline tests must never open a browser or touch the network. Drive the
  scraper through `tests/fakes.py` (fake Playwright stack).
- Inject settings with `replace(DEFAULT_SETTINGS, ...)` instead of patching
  globals; patch `time.sleep` in flow tests to keep them instant.
- pytest's stdin is not a TTY, so queue mode never activates by itself in
  tests. Drive `run_queue` with `spot_queue=SpotQueue()` (treated as complete)
  or `stdin=StringIO(...)`; pre-fill the queue instead of sleeping on threads.
- Golden fixtures are trimmed real pages (`tests/fixtures/`, provenance in the
  file header). `hotel_mixed.html` is archival (pre-filter mixed feed) and is
  intentionally not refreshed; live scrapes return Google-only feeds.
- Live spots are centralized in `tests/spots.py`; they skip without a saved
  session unless `GMAPS_LIVE=1`. Never make offline tests depend on them.

## Gotchas

- Maps lazy-loads only on **trusted wheel input**. Assigning `scrollTop` via JS
  does not fetch the next batch (`dom.wheel_scroll` reports `False` for it).
- The limited view can appear after `goto` **and** mid-scroll; both are
  checked. If a reload is still limited, fail before opening tabs.
- The place name comes from the document title first: the signed-in "Recents"
  rail reuses the same `data-bundle-id` and can leak the previous spot.
- `_open_reviews_tab` returns `False` when no cards render; the caller treats
  that as a transient "no reviews list".
- The stub guard compares loaded cards to `min(target, spot_total)`; when the
  header total is unparseable (`None`) it never retries blind.
- Re-resolve the scroll pane after stalls: Playwright handles go stale as Maps
  re-renders the list.
- The progress bar and logging share stderr. Call `progress.finish()` before
  logging, or the log text appends to the bar frame. A live bar with a footer
  (`Terminal.footer`, set by `run_queue` only when the bar will draw) owns two
  rows: the bar and the queue prompt below it. `Terminal.write` inserts notices
  between them via cursor save/restore; redraws move up `_notice_rows + 1`, so
  the bar updates in place instead of being recreated per confirmation.
- Raised/blank input lines move the terminal cursor one row down. The reader
  calls `Terminal.refresh_footer` (`on_blank`) to reprint the prompt; without
  it the next bar frame would land on the prompt row. `finish()` erases the
  footer row so the report starts clean.
- Typed characters can still visually collide with a redrawing bar until Enter
  is pressed; the confirmation line (`Queued: <name>` when idle, or the
  appended `N. <name>` row while a spot runs) is the source of truth. Do not
  add raw-mode/termios handling for this.
- Queue labels come from the `/maps/place/<name>/` slug (`urls.place_label`).
  Short links are resolved at enqueue time (`urls.resolve_short_link`, bounded
  redirect chain with a 5 s per-try timeout, transient failures retried, no
  page body) so the real name shows immediately; on any failure they fall
  back to host + path, and `_QueueView.start` retries unresolved labels before
  listing the queue. Never scrape or launch a browser from the reader thread
  for a label.
- Queue rows are append-only: `_QueueView.notice` writes the plain
  confirmation when nothing runs, and one numbered row for the new spot while
  a spot runs — never a reprint of the whole list (that is `_QueueView.start`,
  once per spot). A URL the main thread already popped has no pending
  position, so it falls back to the confirmation instead of a wrong number.
- The version is duplicated in `src/gmaps_review_filter/__init__.py` and
  `pyproject.toml`; `test_cli.py` enforces they match.
- `auth.json` is a credential: mode 600, gitignored, never commit it.

## Definition of done

1. `uv run ruff check .` and `uv run ruff format --check .`
2. `uv run mypy`
3. `uv run pytest -m "not live" -q`
4. Both coverage gates above
5. Behavior/contract changes also update `README.md`, `GLOSSARY.md` if the
   vocabulary shifted, and this file if commands or invariants changed
6. Scraper/selector changes: run `uv run pytest -m live` when a saved session
   is available
