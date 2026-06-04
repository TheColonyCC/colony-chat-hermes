"""Tests for the Mode B notification poller."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from colony_chat_hermes.daemon.message_queue import MessageQueue
from colony_chat_hermes.daemon.notification_poller import NotificationPoller


def _payload(mid: str) -> dict[str, object]:
    return {
        "id": mid,
        "notification_type": "direct_message",
        "conversation_id": "c1",
        "from_username": "alice",
        "body": "hi",
        "created_at": "2026-06-04T12:00:00Z",
    }


class TestPollOnce:
    def test_enqueues_unread(self) -> None:
        client = MagicMock()
        client.unread.return_value = [_payload("m1"), _payload("m2")]
        q = MessageQueue(maxsize=10)
        poller = NotificationPoller(client=client, message_queue=q, interval=60)
        assert poller.poll_once() == 2
        client.unread.assert_called_once_with(limit=50)

    def test_dedupes_across_polls(self) -> None:
        client = MagicMock()
        client.unread.return_value = [_payload("m1")]
        q = MessageQueue(maxsize=10)
        poller = NotificationPoller(client=client, message_queue=q, interval=60)
        assert poller.poll_once() == 1
        assert poller.poll_once() == 0
        assert q.qsize() == 1

    def test_handles_empty(self) -> None:
        client = MagicMock()
        client.unread.return_value = []
        q = MessageQueue(maxsize=10)
        poller = NotificationPoller(client=client, message_queue=q, interval=60)
        assert poller.poll_once() == 0

    def test_handles_none(self) -> None:
        # Defensive: some clients may return None on partial failures.
        client = MagicMock()
        client.unread.return_value = None
        q = MessageQueue(maxsize=10)
        poller = NotificationPoller(client=client, message_queue=q, interval=60)
        assert poller.poll_once() == 0

    def test_swallows_unread_exception(self) -> None:
        client = MagicMock()
        client.unread.side_effect = RuntimeError("network down")
        q = MessageQueue(maxsize=10)
        poller = NotificationPoller(client=client, message_queue=q, interval=60)
        # Should NOT raise; should return 0.
        assert poller.poll_once() == 0

    def test_skips_malformed_payloads(self) -> None:
        client = MagicMock()
        client.unread.return_value = [
            _payload("m1"),
            {"notification_type": "comment_reply", "id": "n1"},  # wrong type
            {"foo": "bar"},  # no id
            _payload("m2"),
        ]
        q = MessageQueue(maxsize=10)
        poller = NotificationPoller(client=client, message_queue=q, interval=60)
        assert poller.poll_once() == 2


class TestLifecycle:
    def test_start_and_stop(self) -> None:
        client = MagicMock()
        client.unread.return_value = [_payload("m1")]
        q = MessageQueue(maxsize=10)
        stop = threading.Event()
        poller = NotificationPoller(client=client, message_queue=q, interval=0.05, stop_event=stop)
        poller.start()
        time.sleep(0.15)  # let it do a couple of iterations
        poller.stop(timeout=2.0)
        assert q.qsize() == 1  # deduped to one
        assert poller.stats()["polls"] >= 1

    def test_start_is_idempotent(self) -> None:
        client = MagicMock()
        client.unread.return_value = []
        q = MessageQueue(maxsize=10)
        poller = NotificationPoller(client=client, message_queue=q, interval=0.05)
        poller.start()
        poller.start()  # should not spawn a second thread
        poller.stop(timeout=2.0)


class TestValidation:
    def test_rejects_invalid_interval(self) -> None:
        with pytest.raises(ValueError, match="interval"):
            NotificationPoller(
                client=MagicMock(), message_queue=MessageQueue(maxsize=10), interval=0
            )

    def test_rejects_invalid_limit(self) -> None:
        with pytest.raises(ValueError, match="limit"):
            NotificationPoller(
                client=MagicMock(),
                message_queue=MessageQueue(maxsize=10),
                interval=1,
                limit=0,
            )
