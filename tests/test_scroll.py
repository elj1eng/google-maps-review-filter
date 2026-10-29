"""Offline tests for the scroll loop (fake page, no browser)."""

import logging
import time

import gmaps_review_filter.scroll as scroll_mod
from gmaps_review_filter.scroll import scroll_reviews

PLACE = "https://www.google.com/maps/place/Peter+Luger+Steak+House/@40.7,-73.9,17z"


class _RecordingProgress:
    def __init__(self, total, *, enabled=True, label="Loading reviews"):
        self.total = total
        self.enabled = True if enabled is None else enabled
        self.label = label
        self.updates = []
        self.finished = 0

    def update(self, loaded):
        self.updates.append(loaded)

    def finish(self):
        # Mirrors ReviewProgress: finish is idempotent.
        if self.finished:
            return
        self.finished += 1


class _ScrollPage:
    """Fake page whose card count grows inside wait_for_function."""

    def __init__(self, growth):
        self.current = 0
        self._growth = list(growth)
        self.keyboard = self

    def locator(self, sel):
        return self

    def count(self):
        return self.current

    def nth(self, i):
        return self

    def scroll_into_view_if_needed(self, timeout=None):
        pass

    def wait_for_function(self, *args, **kwargs):
        if self._growth:
            self.current += self._growth.pop(0)

    def press(self, key):
        pass


def _scroll(monkeypatch, growth, *, target=210, spot_total=None):
    page = _ScrollPage(growth)
    monkeypatch.setattr(scroll_mod, "find_scrollable_pane", lambda p: object())
    monkeypatch.setattr(scroll_mod, "wheel_scroll", lambda p, pane, px, **kw: True)
    monkeypatch.setattr(scroll_mod, "spot_review_total", lambda p: spot_total)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    progress = _RecordingProgress(target)
    return scroll_reviews(page, target, progress), page, progress


def test_scroll_reports_each_growth_then_finishes(monkeypatch):
    final, _, progress = _scroll(monkeypatch, [100, 110])
    assert final == 210
    assert progress.updates == [0, 100, 210]
    assert progress.finished == 1


def test_scroll_finishes_without_drawing_when_it_stalls(monkeypatch):
    final, _, progress = _scroll(monkeypatch, [50, 0, 0, 0, 0, 0])
    assert final == 50
    assert progress.updates == [0, 50]
    assert progress.finished == 1


def test_missing_pane_finishes_cleanly(monkeypatch):
    monkeypatch.setattr(scroll_mod, "find_scrollable_pane", lambda p: None)
    progress = _RecordingProgress(210)
    assert scroll_reviews(_ScrollPage([]), 210, progress) == 0
    assert progress.updates == []
    assert progress.finished == 1


def test_scroll_stops_once_the_spot_total_is_loaded(monkeypatch):
    final, page, progress = _scroll(monkeypatch, [36, 100], spot_total=36)
    assert final == 36
    assert progress.updates == [0, 36]
    assert page.current == 36  # the extra 100 was never fetched


def test_scroll_still_targets_when_the_spot_is_larger(monkeypatch):
    final, _, progress = _scroll(monkeypatch, [100, 110], spot_total=500)
    assert final == 210
    assert progress.updates == [0, 100, 210]


def test_scroll_without_a_spot_total_falls_back_to_target(monkeypatch):
    final, _, _ = _scroll(monkeypatch, [100, 110], spot_total=None)
    assert final == 210


def test_no_short_sample_warning_for_a_small_spot(monkeypatch, caplog):
    """A 6-review spot matching its header total is complete, not slow."""
    with caplog.at_level(logging.WARNING):
        _scroll(monkeypatch, [6], spot_total=6)
    assert "Only 6 reviews loaded" not in caplog.text


def test_short_sample_warning_fires_for_a_real_shortfall(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        _scroll(monkeypatch, [6, 0, 0, 0, 0, 0], spot_total=36)
    assert "Only 6 reviews loaded" in caplog.text


def test_bar_mode_hides_setup_and_completion_logs(monkeypatch, caplog):
    """Any log during the crawl scrolls the bar away; keep the bar alone."""
    with caplog.at_level(logging.INFO):
        _scroll(monkeypatch, [50, 0, 0, 0, 0, 0])
    messages = [r.getMessage() for r in caplog.records]
    assert not any(m.startswith("Scrolling review list") for m in messages)
    assert not any(m.startswith("Scrolling done") for m in messages)


def test_piped_mode_keeps_setup_and_completion_logs(monkeypatch, caplog):
    page = _ScrollPage([50, 0, 0, 0, 0, 0])
    monkeypatch.setattr(scroll_mod, "find_scrollable_pane", lambda p: object())
    monkeypatch.setattr(scroll_mod, "wheel_scroll", lambda p, pane, px, **kw: True)
    monkeypatch.setattr(scroll_mod, "spot_review_total", lambda p: None)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    progress = _RecordingProgress(210, enabled=False)
    with caplog.at_level(logging.INFO):
        scroll_reviews(page, 210, progress)
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("Scrolling review list") for m in messages)
    assert any(m.startswith("Scrolling done") for m in messages)


def test_scroll_finishes_bar_before_completion_logs(monkeypatch):
    """The bar line must be closed before 'Scrolling done' is logged.

    Otherwise the log is appended mid-bar: '72/210 (34%)INFO: ...'.
    """
    page = _ScrollPage([50, 0, 0, 0, 0, 0])
    events = []

    class _OrderedProgress(_RecordingProgress):
        def finish(self):
            if not self.finished:
                events.append("finish")
            super().finish()

    class _Collector(logging.Handler):
        def emit(self, record):
            events.append(record.getMessage())

    monkeypatch.setattr(scroll_mod, "find_scrollable_pane", lambda p: object())
    monkeypatch.setattr(scroll_mod, "wheel_scroll", lambda p, pane, px, **kw: True)
    monkeypatch.setattr(scroll_mod, "spot_review_total", lambda p: None)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    progress = _OrderedProgress(210, enabled=False)
    logger = logging.getLogger("gmaps_review_filter.scroll")
    collector = _Collector()
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(collector)
    try:
        scroll_reviews(page, 210, progress)
    finally:
        logger.removeHandler(collector)
        logger.setLevel(old_level)

    assert events.index("finish") < next(
        i for i, e in enumerate(events) if e.startswith("Scrolling done")
    )


def test_scroll_does_not_log_redundant_finished_line(monkeypatch):
    """The bar + 'Scrolling done' already state the count."""
    messages = []

    class _Collector(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    logger = logging.getLogger("gmaps_review_filter.scroll")
    collector = _Collector()
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(collector)
    try:
        _scroll(monkeypatch, [50, 0, 0, 0, 0, 0])
    finally:
        logger.removeHandler(collector)
        logger.setLevel(old_level)

    assert not any(m.startswith("Finished scrolling") for m in messages)
