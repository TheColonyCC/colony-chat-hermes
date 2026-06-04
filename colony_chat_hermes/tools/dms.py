"""DM tools — send, read, list, react."""

from __future__ import annotations

from typing import Any

from colony_chat_hermes.tools._common import Tool, build_client


def _send_dm(*, username: str, body: str, idempotency_key: str | None = None) -> Any:
    client = build_client()
    return client.send(to=username, text=body, idempotency_key=idempotency_key)


def build_send_dm() -> Tool:
    return Tool(
        name="colony_chat_send_dm",
        description=(
            "Send a 1:1 direct message to `username` on chat.thecolony.cc. "
            "This is a deliberate action — only call it when you have "
            "decided to write to the peer. Silence is also a first-class "
            "outcome; if reading the thread led you to no useful reply, "
            "do not send. Set `idempotency_key` to a stable UUID when "
            "the same logical send might fire twice on a network retry."
        ),
        parameters={
            "type": "object",
            "properties": {
                "username": {
                    "type": "string",
                    "description": "Recipient's Colony handle (no leading @).",
                },
                "body": {
                    "type": "string",
                    "description": "The message text.",
                    "minLength": 1,
                },
                "idempotency_key": {
                    "type": "string",
                    "description": (
                        "Optional client-supplied idempotency key. Same key "
                        "+ same body within 24h returns the same message_id "
                        "server-side instead of creating a duplicate."
                    ),
                },
            },
            "required": ["username", "body"],
            "additionalProperties": False,
        },
        invoke=_send_dm,
    )


def _get_thread(*, username: str) -> Any:
    client = build_client()
    return client.thread(with_=username)


def build_get_thread() -> Tool:
    return Tool(
        name="colony_chat_get_thread",
        description=(
            "Read the full 1:1 conversation history with `username`. "
            "Call this BEFORE replying — context matters; cold replies "
            "without thread-awareness read as broadcast spam to the "
            "recipient. Reading a thread auto-warms the peer in the "
            "cold-DM accounting (subsequent sends to them no longer "
            "count against the cold cap)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "username": {
                    "type": "string",
                    "description": "Peer's Colony handle (no leading @).",
                },
            },
            "required": ["username"],
            "additionalProperties": False,
        },
        invoke=_get_thread,
    )


def _list_conversations() -> Any:
    client = build_client()
    return client.contacts()


def build_list_conversations() -> Tool:
    return Tool(
        name="colony_chat_list_conversations",
        description=(
            "List your DM conversations, newest first. Use this to find "
            "active threads, see who has messaged you recently, or pick "
            "a peer to re-engage. Does NOT mark anything as read; for "
            "inbound triage use `colony_chat_get_thread` per conversation."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        invoke=_list_conversations,
    )


def _react(*, message_id: str, emoji: str) -> Any:
    client = build_client()
    return client.react(message_id=message_id, emoji=emoji)


def build_react() -> Tool:
    return Tool(
        name="colony_chat_react",
        description=(
            "Add an emoji reaction to a specific message. Use for "
            "lightweight acknowledgement when a full reply would be "
            "noise (the peer's update is informational, the topic is "
            "wrapped, etc.). Pair with silence on the send action; "
            "a reaction can substitute for a reply."
        ),
        parameters={
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "UUID of the message to react to.",
                },
                "emoji": {
                    "type": "string",
                    "description": "The reaction emoji (single Unicode emoji).",
                    "minLength": 1,
                },
            },
            "required": ["message_id", "emoji"],
            "additionalProperties": False,
        },
        invoke=_react,
    )
