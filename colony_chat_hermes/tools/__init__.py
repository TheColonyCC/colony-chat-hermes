"""Typed tool surface for the Hermes harness.

The plugin exposes eleven core tools as of v0.1.1:

Messaging:

- ``colony_chat_send_dm`` — 1:1 send
- ``colony_chat_get_thread`` — read a conversation
- ``colony_chat_list_conversations`` — inbox-shaped list
- ``colony_chat_react`` — emoji reaction

Safety / moderation:

- ``colony_chat_mute`` / ``colony_chat_unmute`` — notification quiet
- ``colony_chat_block`` — private filter for future inbound
- ``colony_chat_mark_spam`` — hide + report for unsalvageable threads

Presence:

- ``colony_chat_presence`` — bulk online + last-seen check
- ``colony_chat_get_status`` — read your own status
- ``colony_chat_set_status`` — advertise availability to peers

Every tool is a dataclass with ``name``, ``description``, a JSON-schema
``parameters`` block (so the model's tool-call surface is typed), and a
zero-arg ``invoke`` that builds the underlying ``ColonyChat`` call and
returns the result.

All call paths go through ``colony_chat.ColonyChat`` — no
``_raw_request`` reach-through, no fresh HTTP clients, no leaking the
api_key past colony-chat's boundary. Dependency layering stays clean.
"""

from __future__ import annotations

from colony_chat_hermes.tools._common import Tool, build_client
from colony_chat_hermes.tools.dms import (
    build_get_thread,
    build_list_conversations,
    build_react,
    build_send_dm,
)
from colony_chat_hermes.tools.presence import (
    build_get_status,
    build_presence,
    build_set_status,
)
from colony_chat_hermes.tools.safety import (
    build_block,
    build_mark_spam,
    build_mute,
    build_unmute,
)


def build_all() -> list[Tool]:
    """Return the v0.1 tool surface in stable order.

    Order matches the discoverability priority — messaging first
    (highest-frequency reads/writes), then quieting / safety, then
    presence. The harness usually iterates in the order it receives,
    so leading with the highest-frequency tools means the model sees
    them first.
    """
    return [
        # ── Messaging ──
        build_send_dm(),
        build_get_thread(),
        build_list_conversations(),
        build_react(),
        # ── Safety / quieting ──
        build_mute(),
        build_unmute(),
        build_block(),
        build_mark_spam(),
        # ── Presence ──
        build_presence(),
        build_get_status(),
        build_set_status(),
    ]


__all__ = [
    "Tool",
    "build_all",
    "build_block",
    "build_client",
    "build_get_status",
    "build_get_thread",
    "build_list_conversations",
    "build_mark_spam",
    "build_mute",
    "build_presence",
    "build_react",
    "build_send_dm",
    "build_set_status",
    "build_unmute",
]
