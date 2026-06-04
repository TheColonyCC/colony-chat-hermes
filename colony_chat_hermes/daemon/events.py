"""Inbound event shape, normalized across delivery channels.

Both the notification poller (Mode B) and the webhook receiver (Mode A)
produce events of the same shape so the queue and the invoker don't
care which channel they came from. The ``source`` field is preserved
for observability — useful when a duplicate event arrives via both
channels and the dedup window resolves it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

EventSource = Literal["poller", "webhook"]


@dataclass(frozen=True)
class InboundEvent:
    """Normalized inbound DM event.

    Built from a Colony ``/notifications`` envelope (poller) or a
    webhook delivery body (receiver). Both shapes are direct-message
    notifications with the same top-level fields, so a single
    ``from_notification`` constructor handles both.
    """

    message_id: str
    conversation_id: str
    from_handle: str
    body: str
    ts: str
    source: EventSource
    raw: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), default=str)

    @classmethod
    def from_notification(
        cls,
        payload: Any,
        *,
        source: EventSource,
    ) -> InboundEvent | None:
        """Build from a ``direct_message`` notification envelope.

        Returns ``None`` for malformed payloads (missing id, wrong
        notification_type, etc.) so the producer can drop rather than
        raise.
        """
        if not isinstance(payload, dict):
            return None
        # Colony's notification envelope nests the message under either
        # ``data`` or surfaces fields at the top level depending on
        # delivery channel. Accept both shapes. Pin to a local so the
        # type narrows after the isinstance check.
        nested = payload.get("data")
        data: dict[str, Any] = nested if isinstance(nested, dict) else payload
        nt = payload.get("notification_type") or data.get("notification_type")
        if nt is not None and nt != "direct_message":
            return None
        message_id = data.get("message_id") or data.get("id") or payload.get("id")
        if not message_id:
            return None
        return cls(
            message_id=str(message_id),
            conversation_id=str(data.get("conversation_id") or ""),
            from_handle=str(
                data.get("from_username") or data.get("from") or data.get("sender") or ""
            ),
            body=str(data.get("body") or data.get("text") or ""),
            ts=str(data.get("created_at") or data.get("ts") or ""),
            source=source,
            raw=payload,
        )
