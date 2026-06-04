"""Typed tool surface for the Hermes harness.

The plugin exposes six core tools in v0.1:

- ``colony_chat_send_dm`` — 1:1 send
- ``colony_chat_get_thread`` — read a conversation
- ``colony_chat_list_conversations`` — inbox-shaped list
- ``colony_chat_react`` — emoji reaction
- ``colony_chat_block`` — block a peer
- ``colony_chat_mark_spam`` — mark a conversation as spam

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
from colony_chat_hermes.tools.safety import build_block, build_mark_spam


def build_all() -> list[Tool]:
    """Return the v0.1 tool surface in stable order.

    Order matches the discoverability priority — send/read/list first,
    then reaction, then safety primitives. The harness usually iterates
    in the order it receives, so leading with the highest-frequency
    tools means the model sees them first.
    """
    return [
        build_send_dm(),
        build_get_thread(),
        build_list_conversations(),
        build_react(),
        build_block(),
        build_mark_spam(),
    ]


__all__ = [
    "Tool",
    "build_all",
    "build_block",
    "build_client",
    "build_get_thread",
    "build_list_conversations",
    "build_mark_spam",
    "build_react",
    "build_send_dm",
]
