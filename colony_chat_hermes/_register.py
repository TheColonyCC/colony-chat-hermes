"""Hermes plugin registration hook.

The harness loads plugins by walking the ``hermes_agent.plugins``
entry-point group and calling each entry's ``register(harness)``.
We delegate to :func:`register_plugin` below so the public
``colony_chat_hermes.register`` symbol stays small.

The returned ``PluginRegistration`` carries the tools the harness
should add to its tool registry. v0.1 shipped six core tools;
v0.1.1 added mute/unmute + presence (now 11); the daemon-side
runtime (notification poller, webhook receiver, message queue,
agent invoker) lives in :mod:`colony_chat_hermes.daemon` as of
v0.2.0 — see ``colony-chat-hermes daemon --help``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from colony_chat_hermes import tools
from colony_chat_hermes._version import __version__

PLUGIN_NAME = "colony_chat"
TOOL_PREFIX = "colony_chat_"


@dataclass
class PluginRegistration:
    """Returned to the harness on plugin load.

    Shape is intentionally simple — the harness reads ``tools`` and
    adds each entry to its tool registry, keyed by ``name``. Other
    fields are diagnostic.
    """

    name: str = PLUGIN_NAME
    version: str = __version__
    tool_prefix: str = TOOL_PREFIX
    tools: list[tools.Tool] = field(default_factory=list)


def register_plugin(harness: object) -> PluginRegistration:
    """Build the plugin's registration record.

    ``harness`` is opaque to this plugin — we don't poke at its
    internals. The daemon-side runtime is a separate process started
    via ``colony-chat-hermes daemon`` and wires into the harness
    through a pluggable invoker callable (see ``--invoker``), so
    this entry point only needs to register tools.
    """
    return PluginRegistration(tools=tools.build_all())
