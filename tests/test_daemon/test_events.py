"""Tests for the v0.2.1 inbound event shape.

The v0.2.1 shape matches Colony's actual notification envelope:
``{id, notification_type, message: "Display: body", created_at, ...}``
The poller passes ``display_to_handle`` and ``handle_to_conv_id``
enrichment dicts so each event can be populated with structured
sender + conversation fields the notification stream doesn't carry.
"""

from __future__ import annotations

import json

import pytest

from colony_chat_hermes.daemon.events import InboundEvent


class TestFromNotification:
    def test_notification_endpoint_shape_parses_message_prefix(self) -> None:
        evt = InboundEvent.from_notification(
            {
                "id": "n1",
                "notification_type": "direct_message",
                "message": "Alice: hello there",
                "created_at": "2026-06-04T12:00:00Z",
                "is_read": False,
            },
            source="poller",
            display_to_handle={"Alice": "alice"},
            handle_to_conv_id={"alice": "c-alice"},
        )
        assert evt is not None
        assert evt.notification_id == "n1"
        assert evt.from_display == "Alice"
        assert evt.from_handle == "alice"
        assert evt.body == "hello there"
        assert evt.conversation_id == "c-alice"
        assert evt.ts == "2026-06-04T12:00:00Z"
        assert evt.source == "poller"

    def test_body_with_colon_preserves_suffix(self) -> None:
        # Only the FIRST ": " is the separator.
        evt = InboundEvent.from_notification(
            {
                "id": "n1",
                "notification_type": "direct_message",
                "message": "Alice: status: green; eta: 9am",
            },
            source="poller",
            display_to_handle={"Alice": "alice"},
        )
        assert evt is not None
        assert evt.from_display == "Alice"
        assert evt.body == "status: green; eta: 9am"

    def test_no_display_to_handle_map_leaves_handle_empty(self) -> None:
        evt = InboundEvent.from_notification(
            {
                "id": "n1",
                "notification_type": "direct_message",
                "message": "Alice: hi",
            },
            source="poller",
        )
        assert evt is not None
        assert evt.from_display == "Alice"
        assert evt.from_handle == ""
        assert evt.conversation_id == ""

    def test_unresolved_display_leaves_handle_empty(self) -> None:
        evt = InboundEvent.from_notification(
            {
                "id": "n1",
                "notification_type": "direct_message",
                "message": "Bob: hey",
            },
            source="poller",
            display_to_handle={"Alice": "alice"},  # Bob isn't here
        )
        assert evt is not None
        assert evt.from_display == "Bob"
        assert evt.from_handle == ""

    def test_message_without_prefix_uses_whole_as_body(self) -> None:
        evt = InboundEvent.from_notification(
            {
                "id": "n1",
                "notification_type": "direct_message",
                "message": "no-colon-here",
            },
            source="poller",
        )
        assert evt is not None
        assert evt.from_display == ""
        assert evt.body == "no-colon-here"

    def test_webhook_payload_with_structured_fields(self) -> None:
        # If Colony's webhook delivery uses structured top-level
        # fields (rather than the formatted ``message`` string),
        # from_notification picks them up via the fallback path.
        evt = InboundEvent.from_notification(
            {
                "id": "w1",
                "notification_type": "direct_message",
                "from_username": "carol",
                "from": "carol",
                "body": "via webhook",
                "conversation_id": "c-carol",
                "created_at": "2026-06-04T12:00:00Z",
            },
            source="webhook",
        )
        assert evt is not None
        assert evt.notification_id == "w1"
        assert evt.from_handle == "carol"
        assert evt.body == "via webhook"
        assert evt.conversation_id == "c-carol"

    def test_webhook_nested_data_envelope(self) -> None:
        # Some webhooks wrap the message under ``data``.
        evt = InboundEvent.from_notification(
            {
                "id": "w2",
                "notification_type": "direct_message",
                "data": {
                    "from_username": "dave",
                    "body": "nested",
                    "conversation_id": "c-dave",
                },
            },
            source="webhook",
        )
        assert evt is not None
        assert evt.from_handle == "dave"
        assert evt.body == "nested"
        assert evt.conversation_id == "c-dave"

    def test_rejects_non_direct_message(self) -> None:
        evt = InboundEvent.from_notification(
            {"id": "n1", "notification_type": "comment_reply"},
            source="poller",
        )
        assert evt is None

    def test_rejects_missing_id(self) -> None:
        evt = InboundEvent.from_notification(
            {"notification_type": "direct_message", "message": "Alice: hi"},
            source="poller",
        )
        assert evt is None

    @pytest.mark.parametrize("payload", [None, [], "string", 42])
    def test_rejects_non_dict(self, payload: object) -> None:
        assert InboundEvent.from_notification(payload, source="poller") is None


class TestToJson:
    def test_round_trips(self) -> None:
        evt = InboundEvent(
            notification_id="n1",
            from_handle="alice",
            from_display="Alice",
            body="hi",
            conversation_id="c1",
            ts="2026-06-04T12:00:00Z",
            source="poller",
            raw={"id": "n1"},
        )
        parsed = json.loads(evt.to_json())
        assert parsed["notification_id"] == "n1"
        assert parsed["from_handle"] == "alice"
        assert parsed["from_display"] == "Alice"
        assert parsed["body"] == "hi"
        assert parsed["source"] == "poller"
