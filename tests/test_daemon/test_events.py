"""Tests for the inbound event normalization layer."""

from __future__ import annotations

import json

import pytest

from colony_chat_hermes.daemon.events import InboundEvent


class TestFromNotification:
    def test_flat_envelope(self) -> None:
        evt = InboundEvent.from_notification(
            {
                "id": "m1",
                "notification_type": "direct_message",
                "conversation_id": "c1",
                "from_username": "alice",
                "body": "hi",
                "created_at": "2026-06-04T12:00:00Z",
            },
            source="poller",
        )
        assert evt is not None
        assert evt.message_id == "m1"
        assert evt.conversation_id == "c1"
        assert evt.from_handle == "alice"
        assert evt.body == "hi"
        assert evt.ts == "2026-06-04T12:00:00Z"
        assert evt.source == "poller"

    def test_nested_data_envelope(self) -> None:
        evt = InboundEvent.from_notification(
            {
                "notification_type": "direct_message",
                "data": {
                    "message_id": "m2",
                    "conversation_id": "c2",
                    "from_username": "bob",
                    "body": "yo",
                    "created_at": "2026-06-04T12:01:00Z",
                },
            },
            source="webhook",
        )
        assert evt is not None
        assert evt.message_id == "m2"
        assert evt.from_handle == "bob"
        assert evt.body == "yo"
        assert evt.source == "webhook"

    def test_rejects_non_direct_message(self) -> None:
        evt = InboundEvent.from_notification(
            {"id": "n1", "notification_type": "comment_reply"},
            source="poller",
        )
        assert evt is None

    def test_accepts_when_notification_type_absent(self) -> None:
        # Some webhook deliveries omit notification_type because the
        # event ARG is implied by the subscription. Accept anyway —
        # other fields make up the shape.
        evt = InboundEvent.from_notification(
            {"id": "m3", "conversation_id": "c3", "from_username": "carol", "body": "hi"},
            source="webhook",
        )
        assert evt is not None
        assert evt.message_id == "m3"

    def test_rejects_missing_id(self) -> None:
        evt = InboundEvent.from_notification(
            {"notification_type": "direct_message", "body": "hi"},
            source="poller",
        )
        assert evt is None

    @pytest.mark.parametrize("payload", [None, [], "string", 42])
    def test_rejects_non_dict(self, payload: object) -> None:
        assert InboundEvent.from_notification(payload, source="poller") is None

    def test_falls_back_to_alternative_field_names(self) -> None:
        # ``from`` instead of ``from_username``; ``text`` instead of
        # ``body``; ``ts`` instead of ``created_at``.
        evt = InboundEvent.from_notification(
            {"id": "m4", "from": "dave", "text": "hey", "ts": "2026-06-04"},
            source="poller",
        )
        assert evt is not None
        assert evt.from_handle == "dave"
        assert evt.body == "hey"
        assert evt.ts == "2026-06-04"

    def test_falls_back_through_sender_field(self) -> None:
        evt = InboundEvent.from_notification({"id": "m5", "sender": "ed"}, source="poller")
        assert evt is not None
        assert evt.from_handle == "ed"

    def test_handles_missing_optional_fields(self) -> None:
        evt = InboundEvent.from_notification({"id": "m6"}, source="poller")
        assert evt is not None
        assert evt.conversation_id == ""
        assert evt.from_handle == ""
        assert evt.body == ""
        assert evt.ts == ""


class TestToJson:
    def test_round_trips(self) -> None:
        evt = InboundEvent(
            message_id="m1",
            conversation_id="c1",
            from_handle="alice",
            body="hi",
            ts="2026-06-04T12:00:00Z",
            source="poller",
            raw={"id": "m1"},
        )
        parsed = json.loads(evt.to_json())
        assert parsed["message_id"] == "m1"
        assert parsed["source"] == "poller"
        assert parsed["raw"] == {"id": "m1"}
