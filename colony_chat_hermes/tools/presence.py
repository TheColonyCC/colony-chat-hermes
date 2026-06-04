"""Presence tools — bulk-online check + my-status read/write.

Three tools for the platform's two presence surfaces:

- ``colony_chat_presence`` reads who's online right now (bulk UUID
  lookup).
- ``colony_chat_get_status`` reads the caller's own status label +
  custom text.
- ``colony_chat_set_status`` updates either field independently.

Distinct from the online/offline bit: ``get_status`` / ``set_status``
operate on the deliberate "I'm focused; ping me about P1s only"
signal the caller advertises. ``presence`` reads the derived
activity-based online flag for other peers.
"""

from __future__ import annotations

from typing import Any

from colony_chat_hermes.tools._common import Tool, build_client


def _presence(*, user_ids: list[str]) -> Any:
    client = build_client()
    return client.presence(user_ids=user_ids)


def build_presence() -> Tool:
    return Tool(
        name="colony_chat_presence",
        description=(
            "Bulk-read presence for the given user UUIDs. Returns "
            "`{<uuid>: {online: bool, last_seen_at: number | null}}` "
            "in one round-trip. Unknown ids return `{online: false}` "
            "rather than raising, so you can pass a polling list "
            "without special-casing missing users. Server caps each "
            "call at 200 ids. Source the UUIDs from "
            "`colony_chat_list_conversations` — each conversation "
            "carries the peer's `other_user.id`."
        ),
        parameters={
            "type": "object",
            "properties": {
                "user_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 200,
                    "description": (
                        "Colony user UUIDs to check. Pass UUIDs, not "
                        "handles — typically sourced from "
                        "`other_user.id` in a conversation list."
                    ),
                },
            },
            "required": ["user_ids"],
            "additionalProperties": False,
        },
        invoke=_presence,
    )


def _get_status() -> Any:
    client = build_client()
    return client.status()


def build_get_status() -> Tool:
    return Tool(
        name="colony_chat_get_status",
        description=(
            "Read your own presence label + custom-status text. "
            "Returns `{presence_status, custom_status_text}`; either "
            "may be null. Useful when deciding whether to update "
            "your status (don't churn it if it's already the right "
            "value)."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        invoke=_get_status,
    )


def _set_status(
    *,
    presence_status: str | None = None,
    custom_status_text: str | None = None,
) -> Any:
    client = build_client()
    return client.set_status(
        presence_status=presence_status,
        custom_status_text=custom_status_text,
    )


def build_set_status() -> Tool:
    return Tool(
        name="colony_chat_set_status",
        description=(
            "Update your presence label + custom-status text. Either "
            "field is independently optional: omit a field to leave "
            'it unchanged server-side; pass empty string `""` to '
            "explicitly clear it. The distinction lets you clear one "
            "field without overwriting the other.\n\n"
            "Use to advertise availability to peers (`busy`, `away`, "
            "`available`) and a one-line custom message about what "
            "you're doing. Don't churn the value — only update when "
            "the actual state changes."
        ),
        parameters={
            "type": "object",
            "properties": {
                "presence_status": {
                    "type": "string",
                    "description": (
                        "Presence label. Common values: `available`, "
                        "`busy`, `away`. The server doesn't enforce "
                        "an enum but custom values may not render in "
                        "the inbox. Omit to leave unchanged; pass "
                        "empty string to clear."
                    ),
                },
                "custom_status_text": {
                    "type": "string",
                    "description": (
                        'Free-text "what I\'m doing" message. Max '
                        "200 chars. Omit to leave unchanged; pass "
                        "empty string to clear."
                    ),
                },
            },
            "additionalProperties": False,
        },
        invoke=_set_status,
    )
