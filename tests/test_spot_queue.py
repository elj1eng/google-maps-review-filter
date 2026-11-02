"""Tests for queue-mode URL intake (no browser, no terminal)."""

import io
import threading

import pytest

from gmaps_review_filter.spot_queue import (
    STOP_WORDS,
    EnqueueResult,
    QueueStatus,
    SpotQueue,
)

PLACE_A = "https://www.google.com/maps/place/A"
PLACE_B = "https://www.google.com/maps/place/B"
SHORT = "https://maps.app.goo.gl/abc123"


def test_enqueue_accepts_a_place_url():
    queue = SpotQueue()
    assert queue.enqueue(PLACE_A) == EnqueueResult(QueueStatus.QUEUED, PLACE_A)
    assert queue.pending() == 1


def test_enqueue_strips_surrounding_whitespace():
    assert SpotQueue().enqueue(f"  {PLACE_A}  ").url == PLACE_A


def test_enqueue_accepts_a_short_link():
    queue = SpotQueue(resolve=lambda url: None)
    assert queue.enqueue(SHORT).status is QueueStatus.QUEUED


def test_enqueue_resolves_a_short_link_label():
    queue = SpotQueue(resolve=lambda url: PLACE_A)
    assert queue.enqueue(SHORT).status is QueueStatus.QUEUED
    assert queue.label(SHORT) == "A"


def test_short_link_label_falls_back_when_resolution_fails():
    queue = SpotQueue(resolve=lambda url: None)
    queue.enqueue(SHORT)
    assert queue.label(SHORT) == "maps.app.goo.gl/abc123"


def test_stop_during_short_link_resolution_is_refused():
    def stop_then_fail(url):
        queue.request_stop()
        return None

    queue = SpotQueue(resolve=stop_then_fail)
    assert queue.enqueue(SHORT).status is QueueStatus.STOPPED
    assert queue.pending() == 0


def test_concurrent_short_link_enqueue_dedupes_after_resolution():
    """Two readers can pass the first dedupe check before either resolves."""
    barrier = threading.Barrier(2, timeout=2)
    queue = SpotQueue(resolve=lambda url: (barrier.wait(), None)[1])
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(queue.enqueue(SHORT)))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
    assert {result.status for result in results} == {
        QueueStatus.QUEUED,
        QueueStatus.DUPLICATE,
    }
    assert len(results) == 2
    assert queue.pending() == 1


def test_place_url_label_comes_from_the_slug():
    queue = SpotQueue(resolve=lambda url: None)
    queue.enqueue(PLACE_A)
    assert queue.label(PLACE_A) == "A"


def test_unknown_url_label_decodes_the_slug():
    queue = SpotQueue(resolve=lambda url: None)
    assert queue.label(PLACE_B) == "B"


def test_enqueue_rejects_a_non_google_url():
    result = SpotQueue().enqueue("https://example.com/maps/place/A")
    assert result.status is QueueStatus.REJECTED
    assert "not a Google Maps link" in (result.reason or "")


def test_enqueue_rejects_a_search_list_url():
    result = SpotQueue().enqueue("https://www.google.com/maps/search/coffee")
    assert result.status is QueueStatus.REJECTED
    assert "/maps/place/" in (result.reason or "")


def test_enqueue_rejects_empty_input():
    result = SpotQueue().enqueue("   ")
    assert result.status is QueueStatus.REJECTED
    assert result.reason == "empty input"


def test_enqueue_dedupes_after_validation():
    queue = SpotQueue()
    assert queue.enqueue(PLACE_A).status is QueueStatus.QUEUED
    duplicate = queue.enqueue(f" {PLACE_A} ")
    assert duplicate.status is QueueStatus.DUPLICATE
    assert queue.pending() == 1


def test_next_returns_urls_in_fifo_order():
    queue = SpotQueue()
    queue.enqueue(PLACE_A)
    queue.enqueue(PLACE_B)
    assert queue.next() == PLACE_A
    assert queue.next() == PLACE_B
    assert queue.pending() == 0


def test_pending_urls_is_a_fifo_snapshot():
    queue = SpotQueue()
    queue.enqueue(PLACE_A)
    queue.enqueue(PLACE_B)
    assert queue.pending_urls() == [PLACE_A, PLACE_B]
    queue.next()
    assert queue.pending_urls() == [PLACE_B]
    queue.pending_urls().append("mutated")
    assert queue.pending_urls() == [PLACE_B]


def test_next_blocks_until_a_url_arrives():
    queue = SpotQueue()
    received = []
    waiter = threading.Thread(target=lambda: received.append(queue.next()))
    waiter.start()
    queue.enqueue(PLACE_A)
    waiter.join(timeout=2)
    assert not waiter.is_alive()
    assert received == [PLACE_A]


def test_close_drains_then_returns_none():
    queue = SpotQueue()
    queue.enqueue(PLACE_A)
    queue.close()
    assert queue.closed is True
    assert queue.next() == PLACE_A
    assert queue.next() is None


def test_request_stop_wakes_and_discards_pending():
    queue = SpotQueue()
    queue.enqueue(PLACE_A)
    queue.request_stop()
    assert queue.stopped is True
    assert queue.next() is None
    # Kept so the CLI can report how many were dropped.
    assert queue.pending() == 1


def test_enqueue_after_stop_is_refused():
    queue = SpotQueue()
    queue.request_stop()
    assert queue.enqueue(PLACE_A).status is QueueStatus.STOPPED
    assert queue.pending() == 0


def test_stop_during_validation_is_refused(monkeypatch):
    """A stop word can arrive while the URL is being validated."""
    queue = SpotQueue()

    def stopping_validate(url):
        queue.request_stop()
        return url

    monkeypatch.setattr(
        "gmaps_review_filter.spot_queue.validate_place_url", stopping_validate
    )
    assert queue.enqueue(PLACE_A).status is QueueStatus.STOPPED
    assert queue.pending() == 0


def test_reader_enqueues_lines_and_closes_on_eof():
    queue = SpotQueue(resolve=lambda url: None)
    results = []
    queue.start_reader(
        io.StringIO(f"\n {PLACE_A} \n{SHORT}\n"),
        on_result=results.append,
    )
    queue.join_reader(timeout=2)
    assert [result.status for result in results] == [
        QueueStatus.QUEUED,
        QueueStatus.QUEUED,
    ]
    assert queue.closed is True
    assert queue.next() == PLACE_A
    assert queue.next() == SHORT
    assert queue.next() is None


def test_reader_reanchors_the_prompt_on_a_stop_word():
    queue = SpotQueue()
    calls = []
    queue.start_reader(io.StringIO("q\n"), on_blank=lambda: calls.append(1))
    queue.join_reader(timeout=2)
    assert calls == [1]
    assert queue.stopped is True


@pytest.mark.parametrize("word", sorted(STOP_WORDS))
def test_reader_stop_words_request_a_stop(word):
    queue = SpotQueue()
    queue.start_reader(io.StringIO(f"{PLACE_A}\n{word.upper()}\n{PLACE_B}\n"))
    queue.join_reader(timeout=2)
    assert queue.stopped is True
    assert queue.next() is None
    # PLACE_A was accepted before the stop word; PLACE_B never was.
    assert queue.pending() == 1


def test_reader_reports_blank_lines_without_enqueuing():
    """A bare Enter moves the terminal cursor; the CLI re-anchors its prompt."""
    queue = SpotQueue()
    blanks = []
    results = []
    queue.start_reader(
        io.StringIO(f"\n{PLACE_A}\n\n"),
        on_result=results.append,
        on_blank=lambda: blanks.append(1),
    )
    queue.join_reader(timeout=2)
    assert blanks == [1, 1]
    assert [result.status for result in results] == [QueueStatus.QUEUED]
    assert queue.pending() == 1


def test_reader_reports_rejected_lines():
    queue = SpotQueue()
    results = []
    queue.start_reader(
        io.StringIO("https://example.com/x\n"),
        on_result=results.append,
    )
    queue.join_reader(timeout=2)
    assert [result.status for result in results] == [QueueStatus.REJECTED]
    assert queue.pending() == 0


def test_reader_survives_an_unreadable_stream():
    class Broken:
        def __iter__(self):
            raise OSError("closed")

    queue = SpotQueue()
    queue.start_reader(Broken())  # type: ignore[arg-type]
    queue.join_reader(timeout=2)
    assert queue.closed is True
    assert queue.next() is None
