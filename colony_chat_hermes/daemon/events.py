"""Inbound event shape, normalized across delivery channels.

The poller (Mode B) builds events from Colony's notification stream;
the receiver (Mode A) builds events from webhook delivery bodies.
Both produce ``InboundEvent`` instances with the same fields so the
queue and the invoker don't care which channel they came from.

v0.2.0 → v0.2.1 shape change
-----------------------------
The v0.2.0 shape (``message_id`` / ``from_handle`` / ``body`` /
``conversation_id``) assumed Colony's notification envelope carried
structured message data. It doesn't — the actual shape is
``{id, notification_type, message: "<Display>: <body>", created_at,
post_id, comment_id, is_read}``. v0.2.1 refits the dataclass to
match reality:

* ``notification_id`` — the server-unique key per inbound event;
  what the dedup window keys on. Was named ``message_id`` in v0.2.0.
* ``from_display`` — sender display name parsed from the notification
  prefix. Best-effort, may be empty for malformed envelopes.
* ``from_handle`` — sender username, populated by the poller's
  conversation lookup. Empty when the lookup can't resolve.
* ``body`` — message body, parsed from the notification suffix.
* ``conversation_id`` — populated by the poller's conversation
  lookup. Empty when the lookup can't resolve.

Since v0.2.0 shipped only hours ago and the dataclass is internal
plumbing (no operator code consumed it yet), the rename is safe.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

EventSource = Literal["poller", "webhook"]


@dataclass(frozen=True)
class InboundEvent:
    """Normalized inbound DM event.

    All fields default to empty strings rather than ``None`` so
    invokers can use the data without ``None``-checks; the empty
    string is the "unresolved" signal.
    """

    notification_id: str
    from_handle: str
    from_display: str
    body: str
    conversation_id: str
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
        display_to_handle: dict[str, str] | None = None,
        handle_to_conv_id: dict[str, str] | None = None,
    ) -> InboundEvent | None:
        """Build from a Colony ``direct_message`` notification.

        Two enrichment dicts can be supplied to populate fields the
        notification doesn't carry directly:

        * ``display_to_handle`` — display-name → username. The poller
          builds this from ``client.contacts()`` once per poll cycle.
        * ``handle_to_conv_id`` — username → conversation_id. Same
          source.

        Returns ``None`` for malformed payloads.
        """
        if not isinstance(payload, dict):
            return None

        # Colony's webhook payload may nest under ``data``; the
        # notifications endpoint surfaces fields at the top level.
        nested = payload.get("data")
        data: dict[str, Any] = nested if isinstance(nested, dict) else payload

        nt = payload.get("notification_type") or data.get("notification_type")
        if nt is not None and nt != "direct_message":
            return None

        # ``id`` is the notification id on the notifications endpoint;
        # webhook bodies may use ``id`` too. Either way, it's the
        # server-unique per-inbound-event key.
        notif_id = payload.get("id") or data.get("id")
        if not notif_id:
            return None

        # Parse "<Display>: <body>" from the notification's ``message``
        # field. ``partition`` splits on the FIRST ``": "``; the body
        # may contain further colons, so we keep them intact.
        msg_text = str(payload.get("message") or data.get("message") or "")
        display, sep, body = msg_text.partition(": ")
        if not sep:
            # No display-prefix — could be a webhook delivery with
            # structured fields. Try the alt-shape fallback.
            display = ""
            body = msg_text
            # Webhook bodies sometimes carry structured fields directly:
            structured_body = data.get("body") or data.get("text")
            structured_from = data.get("from_username") or data.get("from") or data.get("sender")
            if structured_body:
                body = str(structured_body)
            if structured_from:
                display = str(structured_from)

        from_handle = ""
        if display and display_to_handle is not None:
            from_handle = display_to_handle.get(display, "")
        if not from_handle:
            # Webhook bodies may carry a username field directly.
            structured_handle = data.get("from_username") or data.get("from")
            if structured_handle:
                from_handle = str(structured_handle)

        conversation_id = ""
        if handle_to_conv_id is not None and from_handle:
            conversation_id = handle_to_conv_id.get(from_handle, "")
        if not conversation_id:
            cid = data.get("conversation_id")
            if cid:
                conversation_id = str(cid)

        return cls(
            notification_id=str(notif_id),
            from_handle=from_handle,
            from_display=display,
            body=body,
            conversation_id=conversation_id,
            ts=str(payload.get("created_at") or data.get("created_at") or ""),
            source=source,
            raw=payload,
        )
