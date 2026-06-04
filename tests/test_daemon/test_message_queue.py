"""Tests for the bounded dedup queue."""

from __future__ import annotations

import pytest

from colony_chat_hermes.daemon.events import InboundEvent
from colony_chat_hermes.daemon.message_queue import MessageQueue


def _evt(mid: str) -> InboundEvent:
    return InboundEvent(
        notification_id=mid,
        from_handle="alice",
        from_display="Alice",
        body="hi",
        conversation_id="c",
        ts="2026-06-04T12:00:00Z",
        source="poller",
    )


class TestEnqueue:
    def test_accepts_first(self) -> None:
        q = MessageQueue(maxsize=10)
        assert q.enqueue(_evt("m1")) is True
        assert q.qsize() == 1

    def test_rejects_duplicate_id(self) -> None:
        q = MessageQueue(maxsize=10)
        assert q.enqueue(_evt("m1")) is True
        assert q.enqueue(_evt("m1")) is False
        assert q.qsize() == 1
        assert q.stats()["dropped_dup"] == 1

    def test_drops_when_full_and_rolls_back_dedup(self) -> None:
        # When full, the event should be dropped AND NOT recorded as
        # seen, so a future retry can succeed once capacity frees up.
        q = MessageQueue(maxsize=2)
        assert q.enqueue(_evt("m1")) is True
        assert q.enqueue(_evt("m2")) is True
        assert q.enqueue(_evt("m3")) is False  # full
        assert q.stats()["dropped_full"] == 1

        # Drain and retry m3 — should succeed (not deduplicated).
        assert q.dequeue(timeout=0.1) is not None  # m1
        assert q.enqueue(_evt("m3")) is True

    def test_dedup_window_eviction(self) -> None:
        # When the dedup window fills, the oldest id falls off and a
        # re-enqueue of that id becomes a fresh entry.
        q = MessageQueue(maxsize=100, dedup_window=2)
        assert q.enqueue(_evt("m1")) is True
        assert q.enqueue(_evt("m2")) is True
        # Window is now [m1, m2]. m3 evicts m1.
        assert q.enqueue(_evt("m3")) is True
        # m1 should now be re-acceptable.
        assert q.enqueue(_evt("m1")) is True

    def test_rejects_invalid_maxsize(self) -> None:
        with pytest.raises(ValueError, match="maxsize"):
            MessageQueue(maxsize=0)

    def test_rejects_invalid_dedup_window(self) -> None:
        with pytest.raises(ValueError, match="dedup_window"):
            MessageQueue(maxsize=10, dedup_window=0)


class TestDequeue:
    def test_blocks_until_event_available(self) -> None:
        q = MessageQueue(maxsize=10)
        q.enqueue(_evt("m1"))
        evt = q.dequeue(timeout=1.0)
        assert evt is not None
        assert evt.notification_id == "m1"

    def test_returns_none_on_empty(self) -> None:
        q = MessageQueue(maxsize=10)
        assert q.dequeue(timeout=0.05) is None


class TestStats:
    def test_counters_separate(self) -> None:
        q = MessageQueue(maxsize=1)
        q.enqueue(_evt("m1"))
        q.enqueue(_evt("m1"))  # dup
        q.enqueue(_evt("m2"))  # full
        stats = q.stats()
        assert stats["dropped_dup"] == 1
        assert stats["dropped_full"] == 1
        assert stats["qsize"] == 1
