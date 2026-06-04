"""Tests for the Mode B notification poller (v0.2.1 enriched shape)."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from colony_chat_hermes.daemon.message_queue import MessageQueue
from colony_chat_hermes.daemon.notification_poller import NotificationPoller


def _notif(mid: str, display: str = "Alice") -> dict[str, object]:
    return {
        "id": mid,
        "notification_type": "direct_message",
        "message": f"{display}: hi",
        "created_at": "2026-06-04T12:00:00Z",
        "is_read": False,
    }


def _conv(*, username: str, display: str, conv_id: str = "c1") -> dict[str, object]:
    return {
        "id": conv_id,
        "unread_count": 1,
        "other_user": {"username": username, "display_name": display},
    }


class TestPollOnce:
    def test_enriches_from_contacts(self) -> None:
        client = MagicMock()
        client.unread.return_value = [_notif("n1", "Alice"), _notif("n2", "Bob")]
        client.contacts.return_value = [
            _conv(username="alice", display="Alice", conv_id="c-alice"),
            _conv(username="bob", display="Bob", conv_id="c-bob"),
        ]
        q = MessageQueue(maxsize=10)
        poller = NotificationPoller(client=client, message_queue=q, interval=60)
        assert poller.poll_once() == 2

        events = [q.dequeue(timeout=0.05) for _ in range(2)]
        assert events[0] is not None and events[1] is not None
        assert events[0].notification_id == "n1"
        assert events[0].from_handle == "alice"
        assert events[0].from_display == "Alice"
        assert events[0].conversation_id == "c-alice"
        assert events[0].body == "hi"
        assert events[1].from_handle == "bob"

    def test_skips_contacts_call_when_no_unread(self) -> None:
        # Steady-state hot path: idle polls only hit /notifications.
        client = MagicMock()
        client.unread.return_value = []
        q = MessageQueue(maxsize=10)
        poller = NotificationPoller(client=client, message_queue=q, interval=60)
        assert poller.poll_once() == 0
        client.contacts.assert_not_called()

    def test_dedupes_across_polls(self) -> None:
        client = MagicMock()
        client.unread.return_value = [_notif("n1")]
        client.contacts.return_value = [_conv(username="alice", display="Alice")]
        q = MessageQueue(maxsize=10)
        poller = NotificationPoller(client=client, message_queue=q, interval=60)
        assert poller.poll_once() == 1
        assert poller.poll_once() == 0
        assert q.qsize() == 1

    def test_swallows_unread_exception(self) -> None:
        client = MagicMock()
        client.unread.side_effect = RuntimeError("network down")
        q = MessageQueue(maxsize=10)
        poller = NotificationPoller(client=client, message_queue=q, interval=60)
        assert poller.poll_once() == 0

    def test_swallows_contacts_exception(self) -> None:
        # If contacts() fails but unread() succeeded, we still emit
        # events with empty enrichment.
        client = MagicMock()
        client.unread.return_value = [_notif("n1")]
        client.contacts.side_effect = RuntimeError("contacts borked")
        q = MessageQueue(maxsize=10)
        poller = NotificationPoller(client=client, message_queue=q, interval=60)
        assert poller.poll_once() == 1
        evt = q.dequeue(timeout=0.05)
        assert evt is not None
        assert evt.from_display == "Alice"
        assert evt.from_handle == ""  # unresolved

    def test_skips_malformed_payloads(self) -> None:
        client = MagicMock()
        client.unread.return_value = [
            _notif("n1"),
            {"notification_type": "comment_reply", "id": "x"},  # wrong type
            {"foo": "bar"},  # no id
        ]
        client.contacts.return_value = [_conv(username="alice", display="Alice")]
        q = MessageQueue(maxsize=10)
        poller = NotificationPoller(client=client, message_queue=q, interval=60)
        assert poller.poll_once() == 1

    def test_ignores_malformed_contacts_entries(self) -> None:
        client = MagicMock()
        client.unread.return_value = [_notif("n1")]
        client.contacts.return_value = [
            "not-a-dict",
            None,
            {"other_user": "not-a-dict"},
            {"id": "c1", "other_user": {"username": "alice", "display_name": "Alice"}},
        ]
        q = MessageQueue(maxsize=10)
        poller = NotificationPoller(client=client, message_queue=q, interval=60)
        assert poller.poll_once() == 1


class TestLifecycle:
    def test_start_and_stop(self) -> None:
        client = MagicMock()
        client.unread.return_value = [_notif("n1")]
        client.contacts.return_value = [_conv(username="alice", display="Alice")]
        q = MessageQueue(maxsize=10)
        stop = threading.Event()
        poller = NotificationPoller(client=client, message_queue=q, interval=0.05, stop_event=stop)
        poller.start()
        time.sleep(0.15)
        poller.stop(timeout=2.0)
        assert q.qsize() == 1
        assert poller.stats()["polls"] >= 1

    def test_start_is_idempotent(self) -> None:
        client = MagicMock()
        client.unread.return_value = []
        q = MessageQueue(maxsize=10)
        poller = NotificationPoller(client=client, message_queue=q, interval=0.05)
        poller.start()
        poller.start()
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
